import struct
from types import SimpleNamespace as NS

import pytest

from controller.monitor import PROBE_ETHERTYPE, NetworkMonitor
from topology.fat_tree import FatTree

MBPS100 = 100e6 / 8  # bytes/s


@pytest.fixture
def ctl():
    topo = FatTree(k=4)
    flow = NS(id='10.0.0.1->10.0.0.5', src='e1', dst='e3')
    return NS(graph=topo.get_switch_graph(), ports=topo.port_map(),
              dpid_to_name={d: s for s, d in topo.dpid_map().items()},
              flows={flow.id: flow}, datapaths={},
              get_port=lambda a, b: topo.port_map()[(a, b)])


def dp_id(ctl, name):
    return next(d for d, s in ctl.dpid_to_name.items() if s == name)


def port_reply(ctl, sw, port, tx_bytes, t):
    stat = NS(port_no=port, tx_bytes=tx_bytes, duration_sec=int(t),
              duration_nsec=int((t % 1) * 1e9))
    return NS(msg=NS(datapath=NS(id=dp_id(ctl, sw)), body=[stat]))


def flow_reply(ctl, sw, byte_count, t, priority=100):
    stat = NS(priority=priority, byte_count=byte_count, packet_count=byte_count // 1000,
              duration_sec=int(t), duration_nsec=0,
              match={'ipv4_src': '10.0.0.1', 'ipv4_dst': '10.0.0.5'})
    return NS(msg=NS(datapath=NS(id=dp_id(ctl, sw)), body=[stat]))


def test_capacity_from_topology_both_directions(ctl):
    m = NetworkMonitor(ctl, NS())
    assert m.capacity[('e1', 'a1')] == m.capacity[('a1', 'e1')] == MBPS100


def test_first_reply_gives_no_rate(ctl):
    m = NetworkMonitor(ctl, NS())
    m.on_port_stats_reply(port_reply(ctl, 'e1', ctl.ports[('e1', 'a1')], 1000, 10))
    assert m.link_stats == {}


def test_utilisation_from_tx_byte_delta(ctl):
    m = NetworkMonitor(ctl, NS())
    port = ctl.ports[('e1', 'a1')]
    m.on_port_stats_reply(port_reply(ctl, 'e1', port, 0, 10))
    m.on_port_stats_reply(port_reply(ctl, 'e1', port, MBPS100, 12))  # half rate for 2 s
    s = m.link_stats[('e1', 'a1')]
    assert s['bytes_per_sec'] == pytest.approx(MBPS100 / 2)
    assert s['utilisation'] == pytest.approx(0.5)
    assert ('a1', 'e1') not in m.link_stats  # direction matters


def test_host_ports_are_not_links(ctl):
    m = NetworkMonitor(ctl, NS())
    port = ctl.ports[('e1', 'h1')]
    m.on_port_stats_reply(port_reply(ctl, 'e1', port, 0, 10))
    m.on_port_stats_reply(port_reply(ctl, 'e1', port, 1000, 12))
    assert m.link_stats == {}


def test_flow_rate_only_counted_at_first_switch(ctl):
    m = NetworkMonitor(ctl, NS())
    for sw in ('e1', 'a1'):
        m.on_flow_stats_reply(flow_reply(ctl, sw, 0, 10))
        m.on_flow_stats_reply(flow_reply(ctl, sw, 4000, 12))
    assert m.get_flow_rate('10.0.0.1->10.0.0.5') == pytest.approx(2000)
    assert ('a1', '10.0.0.1->10.0.0.5') not in m._flow_prev


def test_table_miss_and_other_priorities_ignored(ctl):
    m = NetworkMonitor(ctl, NS())
    m.on_flow_stats_reply(flow_reply(ctl, 'e1', 0, 10, priority=0))
    assert m.flow_stats == {}


def probe_frame(a, b, sent):
    payload = f'{a},{b},{sent!r}'.encode().ljust(46, b'\0')
    return b'\x01\x80\xc2\x00\x00\x0e' + b'\x02\x00\x00\x00\x00\x01' + struct.pack('!H', PROBE_ETHERTYPE) + payload


def test_probe_latency_subtracts_control_channel(ctl, monkeypatch):
    m = NetworkMonitor(ctl, NS())
    m._echo_rtt = {'e1': 0.002, 'a1': 0.004}
    monkeypatch.setattr('controller.monitor.time.time', lambda: 100.010)
    m.on_probe('a1', probe_frame('e1', 'a1', 100.0), ctl.ports[('a1', 'e1')])
    # 10 ms elapsed - (2 + 4) / 2 ms control channel = 7 ms
    assert m.latency[('e1', 'a1')] == pytest.approx(7.0)


def test_forged_probe_from_a_host_port_ignored(ctl):
    # A host under e1 sends a probe claiming to come from a1
    m = NetworkMonitor(ctl, NS())
    m.on_probe('e1', probe_frame('a1', 'e1', 1.0), ctl.ports[('e1', 'h1')])
    assert m.latency == {}


def test_probe_for_non_neighbour_ignored(ctl):
    m = NetworkMonitor(ctl, NS())
    m.on_probe('e1', probe_frame('c1', 'e1', 1.0), ctl.ports[('e1', 'a1')])
    assert m.latency == {}


def test_probe_for_another_switch_ignored(ctl):
    m = NetworkMonitor(ctl, NS())
    m.on_probe('a2', probe_frame('e1', 'a1', 1.0), ctl.ports[('a2', 'e1')])
    assert m.latency == {}


def test_garbage_probe_ignored(ctl):
    m = NetworkMonitor(ctl, NS())
    m.on_probe('a1', b'\0' * 14 + b'not,a,probe,at all', 1)
    m.on_probe('a1', b'\0' * 14 + b'\xff\xfe', 1)
    assert m.latency == {}
