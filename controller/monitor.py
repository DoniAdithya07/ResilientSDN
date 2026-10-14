"""Link/flow statistics from OpenFlow counters, plus link latency probes.

Utilisation of directed link (a, b) = tx rate of a's port towards b / capacity.
Flow rate is read at the flow's first switch (its source host's switch).
Latency: a timestamped probe is sent out of a towards b; when b hands it back
to the controller, latency = elapsed - (echo RTT to a + echo RTT to b) / 2.
Probes pass through the link's tc queue, so queueing delay is included.
"""
import struct
import time

# IEEE "local experimental" ethertype; switches have no rule for it, so the
# receiving switch sends the probe to the controller (table-miss).
PROBE_ETHERTYPE = 0x88b5
PROBE_DST = b'\x01\x80\xc2\x00\x00\x0e'  # link-local multicast, never forwarded
FLOW_PRIORITY = 100


class NetworkMonitor:
    def __init__(self, controller, config):
        self.controller = controller
        self.config = config
        self.link_stats = {}   # {(sw_i, sw_j): {bytes_per_sec, capacity, utilisation}}
        self.flow_stats = {}   # {flow_id: {bytes, packets, rate}}
        self.latency = {}      # {(sw_i, sw_j): latency_ms}
        self.capacity = {}     # {(sw_i, sw_j): bytes/s}
        for a, b, d in controller.graph.edges(data=True):
            self.capacity[(a, b)] = self.capacity[(b, a)] = d['bw'] * 1e6 / 8
        self.port_to_neighbour = {(n, p): nb
                                  for (n, nb), p in controller.ports.items()}
        self._port_prev = {}   # {(sw, port): (tx_bytes, t)}
        self._flow_prev = {}   # {(sw, flow_id): (bytes, t)}
        self._echo_rtt = {}    # {sw: seconds}

    # --- requests (called once per polling interval) ------------------------
    def request_stats(self, datapath):
        ofp, parser = datapath.ofproto, datapath.ofproto_parser
        datapath.send_msg(parser.OFPPortStatsRequest(datapath, 0, ofp.OFPP_ANY))
        datapath.send_msg(parser.OFPFlowStatsRequest(datapath))
        datapath.send_msg(parser.OFPEchoRequest(
            datapath, data=struct.pack('!d', time.time())))

    def send_probes(self):
        """One probe per directed switch link."""
        for a, b in self.capacity:
            dp = self.controller.datapaths.get(a)
            if dp is None:
                continue
            payload = f'{a},{b},{time.time()!r}'.encode()
            frame = (PROBE_DST + b'\x02\x00\x00\x00\x00\x01'
                     + struct.pack('!H', PROBE_ETHERTYPE) + payload.ljust(46, b'\0'))
            ofp, parser = dp.ofproto, dp.ofproto_parser
            dp.send_msg(parser.OFPPacketOut(
                datapath=dp, buffer_id=ofp.OFP_NO_BUFFER,
                in_port=ofp.OFPP_CONTROLLER,
                actions=[parser.OFPActionOutput(self.controller.get_port(a, b))],
                data=frame))

    # --- replies ------------------------------------------------------------
    def on_port_stats_reply(self, ev):
        sw = self.controller.dpid_to_name.get(ev.msg.datapath.id)
        for stat in ev.msg.body:
            key = (sw, stat.port_no)
            t = stat.duration_sec + stat.duration_nsec / 1e9
            prev = self._port_prev.get(key)
            self._port_prev[key] = (stat.tx_bytes, t)
            link = (sw, self.port_to_neighbour.get(key))
            if prev is None or link not in self.capacity or t <= prev[1]:
                continue
            rate = (stat.tx_bytes - prev[0]) / (t - prev[1])
            cap = self.capacity[link]
            self.link_stats[link] = {'bytes_per_sec': rate, 'capacity': cap,
                                     'utilisation': rate / cap}

    def on_flow_stats_reply(self, ev):
        sw = self.controller.dpid_to_name.get(ev.msg.datapath.id)
        for stat in ev.msg.body:
            if stat.priority != FLOW_PRIORITY:
                continue
            src, dst = stat.match.get('ipv4_src'), stat.match.get('ipv4_dst')
            flow_id = f'{src}->{dst}'
            flow = self.controller.flows.get(flow_id)
            if flow is None or flow.src != sw:
                continue  # count each flow once, at its first switch
            t = stat.duration_sec + stat.duration_nsec / 1e9
            prev = self._flow_prev.get((sw, flow_id))
            self._flow_prev[(sw, flow_id)] = (stat.byte_count, t)
            rate = self.flow_stats.get(flow_id, {}).get('rate', 0.0)
            if prev is not None and t > prev[1]:
                rate = (stat.byte_count - prev[0]) / (t - prev[1])
            self.flow_stats[flow_id] = {'bytes': stat.byte_count,
                                        'packets': stat.packet_count,
                                        'rate': rate}

    def on_echo_reply(self, ev):
        sw = self.controller.dpid_to_name.get(ev.msg.datapath.id)
        (sent,) = struct.unpack('!d', ev.msg.data[:8])
        self._echo_rtt[sw] = time.time() - sent

    def on_probe(self, receiving_switch, data, in_port):
        """Hosts can send this ethertype too, so a probe only counts if it
        arrived on the receiving switch's port that faces its claimed sender."""
        now = time.time()
        try:
            a, b, sent = data[14:].rstrip(b'\0').decode().split(',')
            sent = float(sent)
        except (ValueError, UnicodeDecodeError):
            return
        if b != receiving_switch or (a, b) not in self.capacity \
                or self.controller.get_port(b, a) != in_port:
            return
        control = (self._echo_rtt.get(a, 0.0) + self._echo_rtt.get(b, 0.0)) / 2
        self.latency[(a, b)] = max(0.0, now - sent - control) * 1000

    # --- accessors ----------------------------------------------------------
    def get_utilisation(self, link) -> float:
        return self.link_stats.get(link, {}).get('utilisation', 0.0)

    def get_all_link_stats(self) -> dict:
        return {link: dict(s) for link, s in self.link_stats.items()}

    def get_flow_rate(self, flow_id) -> float:
        return self.flow_stats.get(flow_id, {}).get('rate', 0.0)
