import networkx as nx

from controller.path_engine import ecmp_choice


def square():
    g = nx.Graph()
    for a, b in [('A', 'B'), ('B', 'D'), ('A', 'C'), ('C', 'D')]:
        g.add_edge(a, b, latency=1.0)
    return g


def test_ecmp_picks_an_equal_cost_path_deterministically():
    g = square()
    p = ecmp_choice(g, 'A', 'D', '10.0.0.1->10.0.0.2')
    assert p in (['A', 'B', 'D'], ['A', 'C', 'D'])
    assert ecmp_choice(g, 'A', 'D', '10.0.0.1->10.0.0.2') == p


def test_ecmp_spreads_flows_over_equal_cost_paths():
    g = square()
    used = {tuple(ecmp_choice(g, 'A', 'D', f'10.0.0.{i}->10.0.0.99')) for i in range(20)}
    assert used == {('A', 'B', 'D'), ('A', 'C', 'D')}


def test_ecmp_never_uses_a_longer_path():
    g = square()
    g.add_edge('A', 'D', latency=1.5)          # direct link is now the unique shortest path
    assert all(ecmp_choice(g, 'A', 'D', f'f{i}') == ['A', 'D'] for i in range(10))
