"""Candidate path generation and capacity filter.

link_stats is keyed by directed links (u, v): the traffic u sends towards v.
Links are full duplex, so (u, v) and (v, u) are separate entries, and a path
only loads the direction it travels in. Each entry holds:
    {'bytes_per_sec': float, 'capacity': float (bytes/s), 'utilisation': float}
"""
import networkx as nx


def path_links(path) -> list:
    """Convert node list to edge list: [A,B,C] → [(A,B),(B,C)]"""
    return [(path[i], path[i + 1]) for i in range(len(path) - 1)]


class PathEngine:
    def __init__(self, graph: nx.Graph, config):
        self.G = graph
        self.safe_threshold = config.safe_threshold  # 0.80
        self.max_hops = getattr(config, 'max_path_hops', 6)

    def all_paths(self, src, dst) -> list:
        return list(nx.all_simple_paths(
            self.G, source=src, target=dst, cutoff=self.max_hops
        ))

    def find_candidates(self, src, dst, congested_link,
                        traffic_to_move, link_stats) -> list:
        """
        1. Find all simple paths from src to dst
        2. Remove paths that use the congested link
        3. Apply capacity filter to each remaining path
        4. Return only paths that pass
        """
        candidates = []
        for path in self.all_paths(src, dst):
            if congested_link in path_links(path):
                continue  # skip paths using congested link
            if self._passes_capacity_filter(path, traffic_to_move, link_stats):
                candidates.append(path)
        return candidates

    def _passes_capacity_filter(self, path, traffic_to_move, link_stats) -> bool:
        """
        For every link in the path:
            post_reroute = current_load + traffic_to_move
            If any link post_reroute > safe_threshold → REJECT
        A link with no stats yet is rejected: its load is unknown.
        """
        if any(link not in link_stats for link in path_links(path)):
            return False
        return (self.compute_post_reroute_utilisation(
            path, traffic_to_move, link_stats) <= self.safe_threshold)

    def compute_post_reroute_utilisation(self, path, traffic_to_move,
                                         link_stats) -> float:
        """Returns worst-case post-reroute utilisation across all links in path."""
        worst = 0.0
        for link in path_links(path):
            current = link_stats[link]['bytes_per_sec']
            capacity = link_stats[link]['capacity']
            worst = max(worst, (current + traffic_to_move) / capacity)
        return worst

    def split_traffic(self, candidates, traffic_to_move,
                      link_stats) -> dict:
        """
        Fallback when no single path can handle all traffic.
        Distribute proportionally by residual capacity (up to safe_threshold).
        Returns {tuple(path): traffic_allocated}; empty if nothing fits.
        Allocation never exceeds a path's residual, so part of the traffic
        may stay unplaced when total residual < traffic_to_move.
        """
        residuals = {}
        for path in candidates:
            links = path_links(path)
            if any(link not in link_stats for link in links):
                continue
            min_residual = min(
                link_stats[l]['capacity'] * self.safe_threshold
                - link_stats[l]['bytes_per_sec']
                for l in links
            )
            if min_residual > 0:
                residuals[tuple(path)] = min_residual

        total_residual = sum(residuals.values())
        if total_residual == 0:
            return {}

        to_place = min(traffic_to_move, total_residual)
        return {path: (residual / total_residual) * to_place
                for path, residual in residuals.items()}
