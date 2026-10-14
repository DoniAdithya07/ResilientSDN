from topology.fat_tree import FatTree


def test_every_switch_uses_ports_1_to_degree():
    t = FatTree(k=4)
    ports = t.port_map()
    g = t.get_graph()
    for s in t.switches():
        used = sorted(p for (node, _), p in ports.items() if node == s)
        assert used == list(range(1, g.degree(s) + 1))


def test_port_map_covers_both_directions_of_switch_links():
    t = FatTree(k=4)
    ports = t.port_map()
    for a, b, _, _ in t.links():
        if a in t.switches() and b in t.switches():
            assert (a, b) in ports and (b, a) in ports


def test_port_order_follows_link_order():
    # c1's first link is to a1 (pod 0), so it is port 1
    assert FatTree(k=4).port_map()[('c1', 'a1')] == 1


def test_dpids_are_unique_and_start_at_1():
    dpids = FatTree(k=4).dpid_map()
    assert sorted(dpids.values()) == list(range(1, 21))


def test_host_info_matches_mininet_addressing():
    info = FatTree(k=4).host_info()
    assert len(info) == 16
    h1 = info['10.0.0.1']
    assert h1['name'] == 'h1'
    assert h1['mac'] == '00:00:00:00:00:01'
    assert h1['switch'] == 'e1'
    assert info['10.0.0.16']['mac'] == '00:00:00:00:00:10'
    assert info['10.0.0.16']['switch'] == 'e8'
