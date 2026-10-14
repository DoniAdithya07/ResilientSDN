"""Shared Ryu app for every system (static, simple_reactive, resilient_sdn):
proxy ARP, per-flow IPv4 rules along a chosen path, statistics polling and an
event log. Systems differ only in choose_path() and on_interval().

The topology is known in advance (we build it), so switch names, dpids, port
numbers and host addresses all come from the topology object; no discovery.
Environment:
    RESILIENT_TOPO     topology name (default fat_tree)
    RESILIENT_CONFIG   controller config file (default config/controller_config.yaml)
    RESILIENT_RUN_DIR  if set, events are appended to <dir>/events.jsonl
"""
import json
import os
import time
from collections import namedtuple

import networkx as nx
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER,
    DEAD_DISPATCHER,
    MAIN_DISPATCHER,
    set_ev_cls,
)
from ryu.lib import hub
from ryu.lib.packet import arp, ether_types, ethernet, ipv4, packet
from ryu.ofproto import ofproto_v1_3

from controller.config import load_config
from controller.flow_manager import FlowManager
from controller.monitor import FLOW_PRIORITY, PROBE_ETHERTYPE, NetworkMonitor
from controller.path_engine import path_links
from topology import load_topology

Flow = namedtuple('Flow', 'id src dst src_ip dst_ip')  # src/dst = edge switches


def link_key(link):
    return f'{link[0]}-{link[1]}'


class RoutingBase(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    RULE_REMOVAL_DELAY = 1.0  # s; > a full 1000-packet tc queue at 100 Mbit (~120 ms)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = load_config()
        self.topo = load_topology(os.environ.get('RESILIENT_TOPO', 'fat_tree'))
        self.graph = self.topo.get_switch_graph()
        self.ports = self.topo.port_map()
        self.hosts = self.topo.host_info()                 # {ip: info}
        self.dpid_to_name = {d: s for s, d in self.topo.dpid_map().items()}
        self.datapaths = {}                                # {name: datapath}
        self.flow_mgr = FlowManager(self)
        self.monitor = NetworkMonitor(self, self.config)
        self.flows = {}                                    # {flow_id: Flow}
        self.current_paths = {}                            # {flow_id: node path}
        run_dir = os.environ.get('RESILIENT_RUN_DIR')
        self._events = (open(os.path.join(run_dir, 'events.jsonl'), 'a')
                        if run_dir else None)
        self.log_event('start', system=type(self).__name__)
        self.threads.append(hub.spawn(self._poll_loop))

    # --- lookups used by FlowManager ----------------------------------------
    def get_datapath(self, name):
        return self.datapaths[name]

    def is_connected(self, name):
        return name in self.datapaths

    def get_port(self, node, neighbour):
        return self.ports[(node, neighbour)]

    # --- policy hooks --------------------------------------------------------
    def choose_path(self, flow) -> list:
        """Switch path for a new flow. Default: lowest latency."""
        return nx.shortest_path(self.graph, flow.src, flow.dst, weight='latency')

    def on_interval(self, link_stats):
        """Called once per polling interval with fresh link statistics."""

    # --- helpers for subclasses ---------------------------------------------
    def switch_path(self, flow_id):
        return self.current_paths[flow_id][:-1]

    def flows_on(self, link):
        """Flows whose current path uses directed link, largest rate first."""
        on = [self.flows[f] for f in self.current_paths
              if link in path_links(self.switch_path(f))]
        return sorted(on, key=lambda f: self.monitor.get_flow_rate(f.id),
                      reverse=True)

    def set_path(self, flow, switch_path, reason=''):
        """Move a flow to a new switch path, make-before-break: new rules now,
        old ones RULE_REMOVAL_DELAY later, so packets still queued on the old
        path's links are delivered instead of hitting a switch with no rule."""
        old = self.current_paths.get(flow.id, [])
        new = self.full_path(flow, switch_path)
        before = self.flow_mgr.get_rule_change_count()
        if not self.flow_mgr.install_path(new, self.match_fields(flow)):
            self.log_event('reroute_skipped', flow=flow.id, reason='switch not connected')
            return False
        self.current_paths[flow.id] = new
        removals = len(set(old[:-1]) - set(new[:-1]))
        if removals:
            hub.spawn_after(self.RULE_REMOVAL_DELAY, self._remove_stale_rules, flow, old)
        self.log_event('reroute', flow=flow.id, old=old[:-1], new=list(switch_path),
                       reason=reason,
                       rule_changes=self.flow_mgr.get_rule_change_count() - before + removals)
        return True

    def _remove_stale_rules(self, flow, old):
        """Drop rules on old-path switches the flow's *current* path no longer uses."""
        self.flow_mgr.remove_old_rules(old, self.current_paths[flow.id],
                                       self.match_fields(flow))

    def full_path(self, flow, switch_path):
        """Switch path plus the destination host, as FlowManager expects."""
        return list(switch_path) + [self.hosts[flow.dst_ip]['name']]

    @staticmethod
    def match_fields(flow):
        return {'eth_type': ether_types.ETH_TYPE_IP,
                'ipv4_src': flow.src_ip, 'ipv4_dst': flow.dst_ip}

    def log_event(self, kind, **data):
        if self._events is not None:
            self._events.write(json.dumps({'t': time.time(), 'kind': kind, **data}) + '\n')
            self._events.flush()

    # --- polling --------------------------------------------------------------
    def _poll_loop(self):
        while True:
            for dp in list(self.datapaths.values()):
                self.monitor.request_stats(dp)
            if self.config.latency_probe:
                self.monitor.send_probes()
            hub.sleep(self.config.polling_interval)
            link_stats = self.monitor.get_all_link_stats()
            if not link_stats:
                continue
            self.log_event(
                'stats',
                util={link_key(l): round(s['utilisation'], 4) for l, s in link_stats.items()},
                flows={f: round(s['rate'], 1) for f, s in self.monitor.flow_stats.items()},
                latency={link_key(l): round(v, 3) for l, v in self.monitor.latency.items()},
                rules=self.flow_mgr.get_rule_change_count())
            try:
                self.on_interval(link_stats)
            except Exception:  # keep polling even if one decision round fails
                self.logger.exception('on_interval failed')

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats(self, ev):
        self.monitor.on_port_stats_reply(ev)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats(self, ev):
        self.monitor.on_flow_stats_reply(ev)

    @set_ev_cls(ofp_event.EventOFPEchoReply, MAIN_DISPATCHER)
    def _echo_reply(self, ev):
        self.monitor.on_echo_reply(ev)

    # --- switch lifecycle --------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change(self, ev):
        dp = ev.datapath
        name = self.dpid_to_name.get(dp.id)
        if name is None:
            return
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[name] = dp
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(name, None)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _switch_features(self, ev):
        """Table-miss rule: send unmatched packets to the controller.
        IPv6 (neighbour discovery etc.) is dropped at the switch: the
        experiments are IPv4-only and it would only load the controller."""
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=0,
                                      match=parser.OFPMatch(), instructions=inst))
        dp.send_msg(parser.OFPFlowMod(
            datapath=dp, priority=1, instructions=[],
            match=parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IPV6)))

    # --- packet handling ---------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in(self, ev):
        msg = ev.msg
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        if eth.ethertype == PROBE_ETHERTYPE:
            self.monitor.on_probe(self.dpid_to_name.get(msg.datapath.id), msg.data,
                                  msg.match['in_port'])
            return
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt is not None:
            self._handle_arp(msg.datapath, msg.match['in_port'], eth, arp_pkt)
            return
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt is not None:
            self._handle_ipv4(msg, ip_pkt)
            return
        self.logger.debug('ignored packet-in at %s: ethertype 0x%04x %s -> %s',
                          self.dpid_to_name.get(msg.datapath.id),
                          eth.ethertype, eth.src, eth.dst)

    def _handle_arp(self, dp, in_port, eth, arp_pkt):
        """Proxy ARP: answer requests for known hosts, never flood."""
        target = self.hosts.get(arp_pkt.dst_ip)
        self.logger.debug('arp op %s %s -> %s at %s', arp_pkt.opcode,
                          arp_pkt.src_ip, arp_pkt.dst_ip,
                          self.dpid_to_name.get(dp.id))
        if arp_pkt.opcode != arp.ARP_REQUEST or target is None:
            return
        reply = packet.Packet()
        reply.add_protocol(ethernet.ethernet(
            ethertype=ether_types.ETH_TYPE_ARP, dst=eth.src, src=target['mac']))
        reply.add_protocol(arp.arp(
            opcode=arp.ARP_REPLY, src_mac=target['mac'], src_ip=arp_pkt.dst_ip,
            dst_mac=arp_pkt.src_mac, dst_ip=arp_pkt.src_ip))
        reply.serialize()
        self._packet_out(dp, in_port, reply.data)

    def _handle_ipv4(self, msg, ip_pkt):
        here = self.dpid_to_name[msg.datapath.id]
        match = {'eth_type': ether_types.ETH_TYPE_IP,
                 'ipv4_src': ip_pkt.src, 'ipv4_dst': ip_pkt.dst}
        src, dst = self.hosts.get(ip_pkt.src), self.hosts.get(ip_pkt.dst)
        flow_id = f'{ip_pkt.src}->{ip_pkt.dst}'
        new = flow_id not in self.current_paths
        # Traffic that can't be routed would reach the controller packet by
        # packet (a host could flood it): unknown addresses, and a new flow
        # whose first packet doesn't come from its source host's port (spoofed).
        if (src is None or dst is None
                or new and (here, msg.match['in_port']) != (src['switch'], src['port'])):
            self._install_drop(msg.datapath, match)
            self.log_event('dropped', flow=flow_id, at=here)
            return
        flow = Flow(flow_id, src['switch'], dst['switch'], ip_pkt.src, ip_pkt.dst)

        if new:
            path = self.full_path(flow, self.choose_path(flow))
            if not self.flow_mgr.install_path(path, self.match_fields(flow)):
                return  # a switch on the path is not connected
            self.flows[flow_id] = flow
            self.current_paths[flow_id] = path
            self.log_event('new_flow', flow=flow_id, path=path[:-1])
            self.logger.debug('new flow %s at %s: %s', flow_id, here, path)
        path = self.current_paths[flow_id]

        # Send this first packet on towards its next hop
        if here in path[:-1]:
            nxt = path[path.index(here) + 1]
            self._packet_out(msg.datapath, self.get_port(here, nxt), msg.data)
        else:
            # Late packet from an old path, or a spoofed one: stop further
            # packet-ins for it here (a later install_path overwrites this).
            self._install_drop(msg.datapath, match)

    DROP_SECONDS = 10

    def _install_drop(self, dp, match_fields):
        """Temporary drop rule, same priority as flow rules (replaced by them)."""
        parser = dp.ofproto_parser
        dp.send_msg(parser.OFPFlowMod(
            datapath=dp, priority=FLOW_PRIORITY, hard_timeout=self.DROP_SECONDS,
            match=parser.OFPMatch(**match_fields), instructions=[]))

    @staticmethod
    def _packet_out(dp, port, data):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        dp.send_msg(parser.OFPPacketOut(
            datapath=dp, buffer_id=ofp.OFP_NO_BUFFER, in_port=ofp.OFPP_CONTROLLER,
            actions=[parser.OFPActionOutput(port)], data=data))
