"""Baseline B: if utilisation > threshold → move every flow on that link to
the shortest alternate path. No capacity check, no scoring, no hysteresis,
single-interval trigger.

Run: ryu-manager baselines/simple_reactive.py
"""
import networkx as nx

from controller.routing_base import RoutingBase


class SimpleReactive(RoutingBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.threshold = self.config.utilisation_threshold

    def on_interval(self, link_stats):
        for link, stats in link_stats.items():
            if stats['utilisation'] <= self.threshold:
                continue
            # Find shortest path avoiding this link
            g_temp = self.graph.copy()
            g_temp.remove_edge(*link)
            for flow in self.flows_on(link):
                try:
                    alt = nx.shortest_path(g_temp, flow.src, flow.dst,
                                           weight='latency')
                except nx.NetworkXNoPath:
                    continue  # No alternate exists
                self.set_path(flow, alt, reason=f'threshold on {link[0]}-{link[1]}')
