"""Baseline A' (added in revision 1): static ECMP routing.

Run: ryu-manager baselines/ecmp_static.py

Every new flow is placed on one of its equal-cost (minimum-latency) paths, chosen by a
hash of the flow's source and destination address, as a switch's ECMP group would, and
is never moved. On the fat-tree this spreads inter-pod flows over the four core switches
instead of concentrating them on one, which plain static routing does.
"""
from controller.path_engine import ecmp_choice
from controller.routing_base import RoutingBase


class EcmpStatic(RoutingBase):
    def choose_path(self, flow) -> list:
        return ecmp_choice(self.graph, flow.src, flow.dst, flow.id)
