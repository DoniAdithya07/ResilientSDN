import networkx as nx
import pytest

from topology import load_topology
from topology.wan import great_circle_km


def test_great_circle_known_distance():
    # London -> Paris is ~344 km
    assert great_circle_km((51.5074, -0.1278), (48.8566, 2.3522)) == pytest.approx(344, abs=5)


@pytest.mark.parametrize('name, nodes, links', [
    ('internet2', 34, 42),
    ('geant', 40, 61),
    ('nsfnet', 13, 15),
])
def test_size_and_connectivity(name, nodes, links):
    t = load_topology(name)
    g = t.get_switch_graph()
    assert g.number_of_nodes() == nodes
    assert g.number_of_edges() == links
    assert nx.is_connected(g)
    assert len(t.hosts()) == nodes
    assert len(t.host_info()) == nodes


@pytest.mark.parametrize('name', ['internet2', 'geant', 'nsfnet'])
def test_links_are_scaled_and_have_latency(name):
    t = load_topology(name)
    g = t.get_switch_graph()
    bws = [d['bw'] for *_, d in g.edges(data=True)]
    assert max(bws) == 100
    assert all(d['latency'] > 0 for *_, d in g.edges(data=True))


@pytest.mark.parametrize('name', ['internet2', 'geant', 'nsfnet'])
def test_interface_names_fit_linux_limit(name):
    t = load_topology(name)
    g = t.get_graph()
    for node in g.nodes:
        assert len(f'{node}-eth{g.degree(node)}') <= 15


def test_internet2_latency_from_distance():
    t = load_topology('internet2')
    sw = t.switch_ids
    g = t.get_switch_graph()
    # Seattle - Vancouver ~ 190 km -> ~0.95 ms at 200 km/ms
    assert g[sw['Seattle']][sw['Vancouver']]['latency'] == pytest.approx(0.95, abs=0.1)
    # Every Internet2 link is 100 Gbps -> 100 Mbps
    assert {d['bw'] for *_, d in g.edges(data=True)} == {100}


def test_geant_speeds_keep_their_ratios():
    t = load_topology('geant')
    sw = t.switch_ids
    g = t.get_switch_graph()
    assert g[sw['BG']][sw['MK']]['bw'] == pytest.approx(1.55)   # 155 Mbps
    assert g[sw['DE']][sw['CY']]['bw'] == pytest.approx(10)     # 1 Gbps
    assert g[sw['NL']][sw['UK']]['bw'] == pytest.approx(25)     # 2.5 Gbps
    assert g[sw['NL']][sw['DE']]['bw'] == pytest.approx(100)    # unlabelled core -> 10 Gbps


def test_geant_missing_coordinates_filled():
    t = load_topology('geant')
    assert t.coords[t.switch_ids['UA']] == (50.4501, 30.5234)


def test_labels_round_trip():
    t = load_topology('internet2')
    assert t.labels[t.switch_ids['Chicago']] == 'Chicago'


def test_any_added_zoo_topology_loads_and_is_usable():
    """Every extra .graphml in topology/data/ must give a connected, Mininet-safe topology."""
    from topology import discovered
    extra = discovered()
    assert extra, 'expected at least one added topology'
    for name in extra:
        t = load_topology(name)
        g = t.get_switch_graph()
        assert nx.is_connected(g) and len(t.hosts()) == g.number_of_nodes()
        assert max(d['bw'] for *_, d in g.edges(data=True)) == 100
        assert all(d['latency'] >= 0 for *_, d in g.edges(data=True))
        full = t.get_graph()
        assert all(len(f'{n}-eth{full.degree(n)}') <= 15 for n in full.nodes)
        assert 6 <= t.max_path_hops <= 12
