"""Baseline A: shortest path (by latency) per flow, never changed.

Run: ryu-manager baselines/static_routing.py
"""
from controller.routing_base import RoutingBase


class StaticRouting(RoutingBase):
    """RoutingBase routes every new flow on its lowest-latency path and
    its on_interval() does nothing, so flows never move."""
