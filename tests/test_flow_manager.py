from types import SimpleNamespace

import pytest

from controller.flow_manager import FlowManager

MATCH = {'eth_type': 0x0800, 'ipv4_src': '10.0.0.1', 'ipv4_dst': '10.0.0.5'}


class FakeParser:
    """Records the arguments instead of building real OpenFlow messages."""
    OFPMatch = staticmethod(lambda **kw: kw)
    OFPActionOutput = staticmethod(lambda port: ('output', port))
    OFPInstructionActions = staticmethod(lambda kind, actions: actions)
    OFPFlowMod = staticmethod(lambda **kw: kw)


class FakeDatapath:
    def __init__(self, name, log):
        self.name, self.log = name, log
        self.ofproto = SimpleNamespace(
            OFPIT_APPLY_ACTIONS=4, OFPFC_DELETE_STRICT=4,
            OFPP_ANY=0xffffffff, OFPG_ANY=0xffffffff)
        self.ofproto_parser = FakeParser

    def send_msg(self, mod):
        self.log.append((self.name, mod))


class FakeController:
    def __init__(self):
        self.log = []
        self.dps = {}
        self.down = set()

    def is_connected(self, name):
        return name not in self.down

    def get_datapath(self, name):
        return self.dps.setdefault(name, FakeDatapath(name, self.log))

    def get_port(self, node, neighbour):
        return ord(neighbour[-1])  # any deterministic port number


@pytest.fixture
def ctl():
    return FakeController()


def installs(log):
    return [(sw, m['instructions'][0][0][1]) for sw, m in log if 'command' not in m]


def deletes(log):
    return [sw for sw, m in log if 'command' in m]


def test_install_writes_one_rule_per_switch_destination_first(ctl):
    fm = FlowManager(ctl)
    fm.install_path(['e1', 'a1', 'e2', 'h5'], MATCH)
    assert [sw for sw, _ in installs(ctl.log)] == ['e2', 'a1', 'e1']
    assert fm.get_rule_change_count() == 3


def test_reinstalling_same_path_sends_nothing(ctl):
    fm = FlowManager(ctl)
    fm.install_path(['e1', 'a1', 'e2', 'h5'], MATCH)
    fm.install_path(['e1', 'a1', 'e2', 'h5'], MATCH)
    assert fm.get_rule_change_count() == 3


def test_reroute_is_make_before_break_and_only_touches_changed_switches(ctl):
    fm = FlowManager(ctl)
    fm.install_path(['e1', 'a1', 'e2', 'h5'], MATCH)
    ctl.log.clear()
    fm.install_path(['e1', 'a2', 'e2', 'h5'], MATCH)
    fm.remove_old_rules(['e1', 'a1', 'e2', 'h5'], ['e1', 'a2', 'e2', 'h5'], MATCH)
    # new a2 rule, e1 now points at a2; e2 unchanged; then a1 deleted
    assert [sw for sw, _ in installs(ctl.log)] == ['a2', 'e1']
    assert deletes(ctl.log) == ['a1']
    assert ctl.log[-1][0] == 'a1'  # delete comes last
    assert fm.get_rule_change_count() == 3 + 3


def test_delete_uses_any_port_and_group(ctl):
    fm = FlowManager(ctl)
    fm.install_path(['e1', 'a1', 'e2', 'h5'], MATCH)
    fm.remove_old_rules(['e1', 'a1', 'e2', 'h5'], ['e1', 'e2', 'h5'], MATCH)
    mod = [m for _, m in ctl.log if 'command' in m][0]
    assert mod['out_port'] == 0xffffffff and mod['out_group'] == 0xffffffff
    assert mod['match'] == MATCH


def test_reset_count(ctl):
    fm = FlowManager(ctl)
    fm.install_path(['e1', 'a1', 'h5'], MATCH)
    fm.reset_rule_change_count()
    assert fm.get_rule_change_count() == 0


def test_path_with_disconnected_switch_is_not_installed_at_all(ctl):
    fm = FlowManager(ctl)
    ctl.down.add('a1')
    assert fm.install_path(['e1', 'a1', 'e2', 'h5'], MATCH) is False
    assert ctl.log == [] and fm.get_rule_change_count() == 0


def test_removal_skips_disconnected_switch(ctl):
    fm = FlowManager(ctl)
    assert fm.install_path(['e1', 'a1', 'e2', 'h5'], MATCH)
    ctl.log.clear()
    ctl.down.add('a1')
    fm.remove_old_rules(['e1', 'a1', 'e2', 'h5'], ['e1', 'a2', 'e2', 'h5'], MATCH)
    assert deletes(ctl.log) == []
    assert ('a1', fm._match_key(MATCH)) not in fm.installed_rules
