"""Link-flooding attack traffic and target/pair selection.

Flows are placed by static shortest path (the same choice RoutingBase makes
for a new flow), so "pairs crossing a link" is known before traffic starts.
"""
import itertools
import threading
from collections import Counter

import networkx as nx

from attack import adversarial
from attack.scenarios import SCENARIOS
from controller.path_engine import path_links
from traffic.legitimate import udp_flow


class TrafficPlanner:
    """Chooses the target link and the host pairs for every kind of flow."""

    def __init__(self, topo):
        self.topo = topo
        self.g = topo.get_switch_graph()
        self.switch_of = {v['name']: v['switch'] for v in topo.host_info().values()}
        self.paths = {}  # {(src host, dst host): static switch path}
        for s in topo.hosts():
            for d in topo.hosts():
                if self.switch_of[s] != self.switch_of[d]:
                    self.paths[(s, d)] = nx.shortest_path(
                        self.g, self.switch_of[s], self.switch_of[d], weight='latency')

    def choose_target_link(self):
        """Most-used directed link under static routing that is one of the
        fastest links and has an alternative (not a bridge)."""
        use = Counter(l for p in self.paths.values() for l in path_links(p))
        bridges = {frozenset(e) for e in nx.bridges(self.g)}
        top_bw = max(d['bw'] for *_, d in self.g.edges(data=True))
        ok = [l for l in use if frozenset(l) not in bridges
              and self.g.edges[l]['bw'] == top_bw]
        return max(ok, key=lambda l: (use[l], l))

    def pairs_crossing(self, link):
        """Pairs whose static path uses link and has no slower link on it, so
        their full rate reaches link (on GÉANT a 10 Mbps hop would otherwise
        throttle an attack before it gets to a 100 Mbps target)."""
        bw = self.g.edges[link]['bw']
        return sorted(p for p, path in self.paths.items()
                      if link in path_links(path)
                      and min(self.g.edges[l]['bw'] for l in path_links(path)) >= bw)

    def pick(self, candidates, n, used, rng):
        """n pairs, preferring sources and destinations not used yet."""
        pool = [p for p in candidates if p not in used]
        rng.shuffle(pool)
        chosen = []
        for fresh_only in (True, False):
            for p in pool:
                if len(chosen) == n:
                    return chosen
                busy = {h for q in used | set(chosen) for h in q}
                if p not in chosen and (not fresh_only or not set(p) & busy):
                    chosen.append(p)
        return chosen

    def plan(self, scenario_id, legit_cfg, rng):
        s = SCENARIOS[scenario_id]
        target = self.choose_target_link()
        crossing = self.pairs_crossing(target)
        used = set()
        legit = self.pick(crossing, legit_cfg['target_flows'], used, rng)
        used |= set(legit)
        others = [p for p in self.paths if p not in crossing]
        background = self.pick(others, legit_cfg['background_flows'], used, rng)
        used |= set(background)
        attack = self.pick(crossing, s.get('attackers', 0), used, rng)
        used |= set(attack)
        preload = []
        if s.get('pattern') == 'presaturate':
            preload = self._preload_pairs(target, legit + attack, used, rng)
        return {'target_link': target, 'legit': legit, 'background': background,
                'attack': attack, 'preload': preload,
                'target_mbps': self.g.edges[target]['bw']}

    def _preload_pairs(self, target, pairs, used, rng, max_links=4):
        """One pair per link on the paths a controller would move the first
        legitimate flow to (its lowest-latency paths avoiding the target):
        [(src, dst, link capacity Mbps, link)]."""
        src, dst = pairs[0]
        without_target = self.g.copy()
        without_target.remove_edge(*target)
        detours = nx.shortest_simple_paths(
            without_target, self.switch_of[src], self.switch_of[dst], weight='latency')
        # Links a preload flow must not use: the target, every link of the
        # legit/attack flows (the attack must only congest the target), and
        # links already carrying a preload flow.
        out = []
        taken = {target} | {l for p in pairs for l in path_links(self.paths[p])}
        for alt in itertools.islice(detours, 3):
            for link in path_links(alt):
                if len(out) == max_links:
                    return out
                if link in taken:
                    continue
                # Each preload flow sits at ~79% of its link, so two sharing any
                # link would overload it: only pairs disjoint from earlier ones.
                disjoint = [p for p in self.pairs_crossing(link)
                            if not taken & set(path_links(self.paths[p]))]
                chosen = self.pick(disjoint, 1, used, rng)
                if chosen:
                    used |= set(chosen)
                    taken |= set(path_links(self.paths[chosen[0]]))
                    out.append((*chosen[0], self.g.edges[link]['bw'], link))
        return out


class Attacker:
    def __init__(self, net):
        self.net = net
        self.stop_event = threading.Event()

    def run_scenario(self, scenario_id, plan, ports, seconds, legit_mbps):
        """Start the scenario's attack traffic; returns immediately."""
        s = SCENARIOS[scenario_id]
        if s.get('type') == 'adversarial':
            adversarial.run(self, s, plan, ports, seconds, legit_mbps)
        else:  # N iperf3 UDP flood flows at the scenario rate
            self.flows(plan['attack'], ports, s['rate_mbps'], seconds)

    def flows(self, pairs, ports, mbps, seconds):
        for (s, d), port in zip(pairs, ports):
            udp_flow(self.net.get(s), self.net.get(d), port, mbps, seconds)

    def in_background(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def stop(self):
        self.stop_event.set()
