"""Shared base class for all topologies.

Subclasses implement `links()`, returning (node_a, node_b, bw_mbps, delay_ms)
tuples, and `hosts()`. The NetworkX graph is built from that list alone, so
path computation and tests work without Mininet; Mininet is imported only when
the network is actually built (Linux).
"""
import logging

import networkx as nx


class _HideHtbQuantumWarning(logging.Filter):
    """tc prints 'sch_htb: quantum of class ... is big' for every link at
    100 Mbit; it is harmless (only affects fairness between HTB classes,
    and each link has one class)."""
    def filter(self, record):
        return 'quantum of class' not in record.getMessage()


class BaseTopology:
    def __init__(self):
        self.net = None

    # --- description, implemented by subclasses ---------------------------
    def switches(self) -> list:
        raise NotImplementedError

    def hosts(self) -> list:
        raise NotImplementedError

    def links(self) -> list:
        """[(a, b, bw_mbps, delay_ms)] covering switch-switch and host links."""
        raise NotImplementedError

    # --- shared ------------------------------------------------------------
    def get_graph(self) -> nx.Graph:
        """Switch-and-host graph with bw (Mbps) and latency (ms) edge attributes."""
        g = nx.Graph()
        for s in self.switches():
            g.add_node(s, kind='switch')
        for h in self.hosts():
            g.add_node(h, kind='host')
        for a, b, bw, delay in self.links():
            g.add_edge(a, b, bw=bw, latency=delay)
        return g

    def get_switch_graph(self) -> nx.Graph:
        """Graph of switches only: the part the controller routes over."""
        g = self.get_graph()
        return g.subgraph(self.switches()).copy()

    def port_map(self) -> dict:
        """
        {(node, neighbour): port number on node} for every switch-side port.
        Mirrors Mininet: each switch numbers its ports 1, 2, ... in the order
        its links are added, which is the order of links().
        """
        switches = set(self.switches())
        next_port = {s: 1 for s in switches}
        ports = {}
        for a, b, _, _ in self.links():
            for node, other in ((a, b), (b, a)):
                if node in switches:
                    ports[(node, other)] = next_port[node]
                    next_port[node] += 1
        return ports

    def dpid_map(self) -> dict:
        """{switch name: dpid}; dpids are 1, 2, ... in switches() order."""
        return {s: i for i, s in enumerate(self.switches(), start=1)}

    def host_info(self) -> dict:
        """
        {ip: {'name', 'mac', 'switch', 'port'}}. Mininet with autoSetMacs gives
        the n-th added host 10.0.0.n and MAC 00:00:00:00:00:0n.
        """
        ports = self.port_map()
        attached = {}
        for a, b, _, _ in self.links():
            if b in self.hosts():
                attached[b] = a
            elif a in self.hosts():
                attached[a] = b
        info = {}
        for n, h in enumerate(self.hosts(), start=1):
            sw = attached[h]
            info[f'10.0.0.{n}'] = {
                'name': h,
                'mac': ':'.join(f'{(n >> (8 * i)) & 0xff:02x}'
                                for i in reversed(range(6))),
                'switch': sw,
                'port': ports[(sw, h)],
            }
        return info

    def get_hosts(self) -> list:
        return self.hosts()

    def get_switches(self) -> list:
        return self.switches()

    def build(self, controller_ip='127.0.0.1', controller_port=6653):
        """Create the Mininet network (Linux only)."""
        from mininet.link import TCLink
        from mininet.log import lg
        from mininet.net import Mininet
        from mininet.node import OVSSwitch, RemoteController

        if not any(isinstance(f, _HideHtbQuantumWarning) for f in lg.filters):
            lg.addFilter(_HideHtbQuantumWarning())

        net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                      autoSetMacs=True)
        net.addController('c0', controller=RemoteController,
                          ip=controller_ip, port=controller_port)
        for s, dpid in self.dpid_map().items():
            net.addSwitch(s, dpid=format(dpid, '016x'), protocols='OpenFlow13')
        for h in self.hosts():
            net.addHost(h)
        for a, b, bw, delay in self.links():
            net.addLink(a, b, bw=bw, delay=f'{delay}ms')
        self.net = net
        return net

    def start(self, static_arp=True):
        """
        static_arp: fill every host's ARP table up front, so no packet waits
        on ARP resolution (keeps first-packet timing and metrics clean).
        IPv6 is disabled on hosts; the experiments are IPv4-only.
        """
        if self.net is None:
            self.build()
        self.net.start()
        for h in self.net.hosts:
            h.cmd('sysctl -qw net.ipv6.conf.all.disable_ipv6=1 '
                  'net.ipv6.conf.default.disable_ipv6=1')
        if static_arp:
            self.net.staticArp()

    def stop(self):
        if self.net is not None:
            self.net.stop()
            self.net = None
