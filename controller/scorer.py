"""Multi-objective DPP-grounded path scorer.

All three terms are normalised to [0, 1] so the weights mean what they say:
    C(P) = 1 - worst post-reroute utilisation on P
    L(P) = best candidate latency / latency of P   (1.0 = fastest candidate)
    K(P) = fraction of P's switches that need a new rule
"""
from controller.path_engine import path_links

DEFAULT_LINK_LATENCY_MS = 5.0


class PathScorer:
    def __init__(self, config, link_stats=None):
        self.w1 = config.scoring_weights['w1_capacity']
        self.w2 = config.scoring_weights['w2_latency']
        self.w3 = config.scoring_weights['w3_rule_cost']
        self.V0 = config.dpp_weight_V0
        self.link_stats = link_stats or {}

    def update_stats(self, link_stats):
        """Call every polling interval so scores use current load."""
        self.link_stats = link_stats

    def objectives(self, path, traffic_to_move, current_path, latency,
                   reference_latency=None) -> tuple:
        """(C, L, K) of a candidate: the three terms score() weighs and the
        Pareto analysis compares."""
        return (self._capacity_score(path, traffic_to_move),
                self._latency_score(path, latency, reference_latency),
                self._normalised_rule_cost(path, current_path))

    def score(self, path, traffic_to_move, current_path, latency,
              reference_latency=None) -> float:
        """
        Score(P) = w1*C(P) + w2*L(P) - w3*K(P) picks the path; dpp_score()
        then decides (with hysteresis) whether moving to it is worth it.
        """
        C, L, K = self.objectives(path, traffic_to_move, current_path, latency,
                                  reference_latency)
        return self.w1 * C + self.w2 * L - self.w3 * K

    def dpp_score(self, path, traffic_to_move, current_path) -> float:
        """
        Drift-plus-penalty (higher is better):
            DPP(P) = - Σ_{ij in P} u_ij_post - V0 * K(P)

        u_ij_post is each link's utilisation with the flow on it (the queue
        backlog the flow would add to), so busy and longer paths cost more;
        K is normalised as in score(). Utilisation units keep the value
        comparable with hysteresis_epsilon. The current path is scored with
        traffic_to_move = 0 (its load already includes the flow) and K = 0.
        """
        drift = sum(
            (self.link_stats[l]['bytes_per_sec'] + traffic_to_move) / self.link_stats[l]['capacity']
            for l in path_links(path))
        return -drift - self.V0 * self._normalised_rule_cost(path, current_path)

    def _capacity_score(self, path, traffic_to_move) -> float:
        """
        Minimum residual capacity across all links in path,
        after rerouting. Normalised to [0,1].
        Higher = more headroom remaining.
        """
        worst_post = 0.0
        for link in path_links(path):
            stats = self.link_stats[link]
            post = (stats['bytes_per_sec'] + traffic_to_move) / stats['capacity']
            worst_post = max(worst_post, post)
        return max(0.0, 1.0 - worst_post)

    @staticmethod
    def path_latency(path, latency: dict) -> float:
        """Total latency (ms) of a path; floored at 0.1 so ratios stay finite."""
        return max(sum(latency.get(link, DEFAULT_LINK_LATENCY_MS)
                       for link in path_links(path)), 0.1)

    def best_latency(self, candidates, latency: dict) -> float:
        return min(self.path_latency(p, latency) for p in candidates)

    def _latency_score(self, path, latency: dict, reference=None) -> float:
        """
        reference / total_path_latency, where reference is the lowest latency
        among the candidates being compared. Higher = faster path, max 1.0.
        """
        total = self.path_latency(path, latency)
        if reference is None:
            reference = total
        return min(1.0, reference / total)

    def _rule_cost(self, path, current_path) -> int:
        """
        Number of switches where the forwarding rule changes.
        = number of nodes on the new path that are not on the current path.
        """
        return len(set(path) - set(current_path))

    def _normalised_rule_cost(self, path, current_path) -> float:
        return self._rule_cost(path, current_path) / max(len(path), 1)

    def rank_candidates(self, candidates, traffic_to_move,
                        current_path, latency) -> list:
        """Returns [(path, score)] sorted by score descending."""
        if not candidates:
            return []
        best_latency = self.best_latency(candidates, latency)
        scored = [
            (path, self.score(path, traffic_to_move, current_path,
                              latency, best_latency))
            for path in candidates
        ]
        return sorted(scored, key=lambda x: x[1], reverse=True)
