import networkx as nx
import pytest

from controller.config import load_config

CAPACITY = 1000.0  # bytes/s, keeps the arithmetic readable


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def clock():
    return FakeClock()


def make_link_stats(graph, loads=None, capacity=CAPACITY):
    """Stats for both directions of every edge; loads = {(u, v): bytes/s}."""
    loads = loads or {}
    stats = {}
    for u, v in graph.edges():
        for link in ((u, v), (v, u)):
            load = loads.get(link, 0.0)
            stats[link] = {'bytes_per_sec': load, 'capacity': capacity,
                           'utilisation': load / capacity}
    return stats


@pytest.fixture
def diamond():
    """
    Two routes from A to D plus a long detour:
        A-B-D, A-C-D, A-E-F-D
    """
    g = nx.Graph()
    g.add_edges_from([('A', 'B'), ('B', 'D'), ('A', 'C'), ('C', 'D'),
                      ('A', 'E'), ('E', 'F'), ('F', 'D')])
    return g
