"""Install, update, and remove OpenFlow 1.3 flow rules on switches.

Paths are node lists that end at the destination host, e.g.
['e1', 'a1', 'c1', 'a3', 'e3', 'h5']: every node except the last is a switch
that gets a rule forwarding towards the next node.

The controller must provide get_datapath(switch_name) and
get_port(switch_name, neighbour_name).
"""


class FlowManager:
    def __init__(self, controller):
        self.controller = controller
        self.installed_rules = {}   # {(switch, match key): out_port}
        self.rule_change_count = 0  # Metric for paper

    @staticmethod
    def _match_key(match_fields):
        return tuple(sorted(match_fields.items()))

    def install_path(self, path, match_fields, priority=100) -> bool:
        """
        Install flow rules along an entire path.
        Rules are written from the destination end backwards, so a packet
        never reaches a switch that does not have its rule yet.
        Switches whose rule already points the right way are left alone.
        Returns False, sending nothing, if a switch on the path is not
        connected (e.g. while Mininet is shutting down): no half-installed paths.
        """
        if not all(self.controller.is_connected(s) for s in path[:-1]):
            return False
        key = self._match_key(match_fields)
        for i in reversed(range(len(path) - 1)):
            switch, next_hop = path[i], path[i + 1]
            out_port = self.controller.get_port(switch, next_hop)
            if self.installed_rules.get((switch, key)) == out_port:
                continue
            self._install_rule(switch, match_fields, out_port, priority)
            self.installed_rules[(switch, key)] = out_port
            self.rule_change_count += 1
        return True

    def remove_old_rules(self, old_path, new_path, match_fields, priority=100):
        """
        Remove rules from switches that are on old_path but not new_path.
        Call after install_path(new_path) (make-before-break); RoutingBase
        calls it RULE_REMOVAL_DELAY later so queued packets drain first.
        """
        key = self._match_key(match_fields)
        keep = set(new_path[:-1])
        for switch in old_path[:-1]:
            if switch not in keep and (switch, key) in self.installed_rules:
                del self.installed_rules[(switch, key)]
                if self.controller.is_connected(switch):  # gone = its rules are gone
                    self._delete_rule(switch, match_fields, priority)
                    self.rule_change_count += 1

    def _install_rule(self, switch, match_fields, out_port, priority):
        """Low-level OpenFlow rule installation via Ryu API."""
        datapath = self.controller.get_datapath(switch)
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        actions = [parser.OFPActionOutput(out_port)]
        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions
        )]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=parser.OFPMatch(**match_fields),
            instructions=inst
        )
        datapath.send_msg(mod)

    def _delete_rule(self, switch, match_fields, priority=100):
        """Remove a specific flow rule from a switch."""
        datapath = self.controller.get_datapath(switch)
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        mod = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE_STRICT,
            priority=priority,
            out_port=ofproto.OFPP_ANY,   # OF1.3: without these the delete
            out_group=ofproto.OFPG_ANY,  # only matches out_port 0, i.e. nothing
            match=parser.OFPMatch(**match_fields)
        )
        datapath.send_msg(mod)

    def get_rule_change_count(self) -> int:
        return self.rule_change_count

    def reset_rule_change_count(self):
        self.rule_change_count = 0
