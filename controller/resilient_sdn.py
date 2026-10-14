"""ResilientSDN: trend-based detection, capacity-filtered candidates,
multi-objective scoring and DPP hysteresis.

Run: ryu-manager controller/resilient_sdn.py

Each polling interval, for every link the detector confirms as congested,
flows on it are moved (largest first) until the link's projected load is back
under the threshold. After each move the projected link loads are updated, so
later decisions in the same round see where earlier flows went.
Ablation switches (config `ablation:`) turn single mechanisms off.
"""

from controller.detector import CongestionDetector
from controller.hysteresis import HysteresisManager
from controller.path_engine import PathEngine, path_links
from controller.routing_base import RoutingBase, link_key
from controller.scorer import PathScorer


def move_load(stats, path, traffic):
    """Add traffic (bytes/s, may be negative) to every link of a switch path."""
    for link in path_links(path):
        if link in stats:
            s = stats[link]
            s['bytes_per_sec'] = max(0.0, s['bytes_per_sec'] + traffic)
            s['utilisation'] = s['bytes_per_sec'] / s['capacity']


class ResilientSDN(RoutingBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cfg = self.config
        ablation = getattr(cfg, 'ablation', {}) or {}
        self.use_capacity_filter = ablation.get('capacity_filter', True)
        self.use_hysteresis = ablation.get('hysteresis', True)
        self.use_scoring = ablation.get('scoring', 'multi_objective') == 'multi_objective'
        self.relief_bypass = getattr(cfg, 'relief_bypass', False)   # ResilientSDN v2, see hysteresis.py
        if not ablation.get('trend_trigger', True):
            cfg.N_intervals = 1
        self.detector = CongestionDetector(cfg)
        self.path_engine = PathEngine(self.graph, cfg)
        self.scorer = PathScorer(cfg)
        self.hysteresis = HysteresisManager(cfg)
        self.log_event('config', ablation=ablation, N=cfg.N_intervals)

    def on_interval(self, link_stats):
        congested = self.detector.update(link_stats)
        if not congested:
            return
        self.log_event('congestion', links=[link_key(l) for l in congested],
                       confidence={link_key(l): self.detector.get_attack_confidence(l)
                                   for l in congested})
        projected = {l: dict(s) for l, s in link_stats.items()}
        for link in congested:
            excess = (projected[link]['bytes_per_sec']
                      - self.detector.threshold * projected[link]['capacity'])
            for flow in self.flows_on(link):
                if excess <= 0:
                    break
                traffic = self.monitor.get_flow_rate(flow.id)
                if traffic > 0 and self._try_reroute(flow, link, traffic, projected):
                    excess -= traffic
            if excess > 0:
                # ponytail: no multipath actuation; one src/dst flow cannot be
                # split without select groups + per-packet hashing. Logged instead.
                self.log_event('unresolved', link=link_key(link), excess=round(excess, 1))

    def _try_reroute(self, flow, link, traffic, projected) -> bool:
        current = self.switch_path(flow.id)
        without = {l: dict(s) for l, s in projected.items()}
        move_load(without, current, -traffic)   # the network if this flow left

        if self.use_capacity_filter:
            candidates = self.path_engine.find_candidates(
                flow.src, flow.dst, link, traffic, without)
        else:
            candidates = [p for p in self.path_engine.all_paths(flow.src, flow.dst)
                          if link not in path_links(p)]
        candidates = [p for p in candidates if p != current]
        if not candidates:
            self.log_event('no_candidate', flow=flow.id, link=link_key(link))
            return False

        latency = self._latency()
        self.scorer.update_stats(without)
        if self.use_scoring:
            best = self.scorer.rank_candidates(candidates, traffic, current, latency)[0][0]
        else:
            best = min(candidates, key=lambda p: self.scorer.path_latency(p, latency))
        self._log_decision(flow, candidates, best, traffic, current, latency)

        new_dpp = self.scorer.dpp_score(best, traffic, current)   # stats: without the flow
        self.scorer.update_stats(projected)
        current_dpp = self.scorer.dpp_score(current, 0, current)  # stats: with the flow
        if self.use_hysteresis and not self.hysteresis.should_reroute(
                flow.id, new_dpp, current_dpp, link, relief=self.relief_bypass):
            self.log_event('held', flow=flow.id, new_dpp=round(new_dpp, 4),
                           current_dpp=round(current_dpp, 4))
            return False

        if not self.set_path(flow, best, reason=f'congestion on {link_key(link)}'):
            return False
        self.hysteresis.record_reroute(flow.id, new_dpp, best)
        move_load(projected, current, -traffic)
        move_load(projected, best, traffic)
        return True

    def _latency(self):
        """Measured latency where available, configured latency otherwise."""
        latency = {}
        for a, b, d in self.graph.edges(data=True):
            latency[(a, b)] = latency[(b, a)] = d['latency']
        latency.update(self.monitor.latency)
        return latency

    def _log_decision(self, flow, candidates, best, traffic, current, latency):
        """Objectives of every candidate, for the Pareto analysis."""
        best_latency = self.scorer.best_latency(candidates, latency)
        objectives = [[round(x, 4) for x in self.scorer.objectives(
                          p, traffic, current, latency, best_latency)]
                      for p in candidates]
        self.log_event('decision', flow=flow.id, objectives=objectives,
                       selected=candidates.index(best))
