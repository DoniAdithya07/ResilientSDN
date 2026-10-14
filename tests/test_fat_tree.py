import networkx as nx

from topology.fat_tree import FatTree


def test_k4_counts():
    t = FatTree(k=4)
    assert len(t.core) == 4 and len(t.agg) == 8 and len(t.edge) == 8
    assert len(t.switches()) == 20
    assert len(t.hosts()) == 16


def test_k4_link_count():
    # core-agg: 4*2*2 = 16, agg-edge: 4*2*2 = 16, edge-host: 16
    assert len(FatTree(k=4).links()) == 48


def test_degrees():
    g = FatTree(k=4).get_graph()
    t = FatTree(k=4)
    assert all(g.degree(c) == 4 for c in t.core)   # one agg per pod
    assert all(g.degree(a) == 4 for a in t.agg)    # 2 core + 2 edge
    assert all(g.degree(e) == 4 for e in t.edge)   # 2 agg + 2 hosts
    assert all(g.degree(h) == 1 for h in t.hosts())


def test_connected_with_multiple_core_paths():
    g = FatTree(k=4).get_graph()
    assert nx.is_connected(g)
    # hosts in different pods: 4 equal-cost shortest paths (one per core)
    paths = list(nx.all_shortest_paths(g, 'h1', 'h16'))
    assert len(paths) == 4
    assert all(len(p) == 7 for p in paths)  # h-e-a-c-a-e-h


def test_switch_graph_excludes_hosts():
    g = FatTree(k=4).get_switch_graph()
    assert g.number_of_nodes() == 20
    assert not any(n.startswith('h') for n in g.nodes)


def test_edges_carry_bw_and_latency():
    g = FatTree(k=4).get_graph()
    _, _, data = next(iter(g.edges(data=True)))
    assert data['bw'] == 100 and data['latency'] == 1
