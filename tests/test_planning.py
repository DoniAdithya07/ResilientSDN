import random

import networkx as nx
import pytest

from attack.adversarial import per_flow_mbps
from attack.attacker import TrafficPlanner
from controller.path_engine import path_links
from topology import load_topology

LEGIT = {'target_flows': 2, 'background_flows': 4}


@pytest.fixture(scope='module', params=['fat_tree', 'internet2', 'geant', 'nsfnet'])
def planner(request):
    return TrafficPlanner(load_topology(request.param))


def test_target_link_has_alternative_and_full_capacity(planner):
    a, b = planner.choose_target_link()
    g = planner.g.copy()
    assert g.edges[a, b]['bw'] == max(d['bw'] for *_, d in g.edges(data=True))
    g.remove_edge(a, b)
    assert nx.has_path(g, a, b)


def test_plan_places_legit_and_attack_on_target(planner):
    for sid in ('S3', 'S5', 'S7', 'S9'):
        plan = planner.plan(sid, LEGIT, random.Random(0))
        target = plan['target_link']
        for pair in plan['legit'] + plan['attack']:
            links = path_links(planner.paths[pair])
            assert target in links
            # the target is the bottleneck: nothing slower on the way
            assert min(planner.g.edges[l]['bw'] for l in links) == plan['target_mbps']
        for pair in plan['background']:
            assert target not in path_links(planner.paths[pair])
        assert len(plan['legit']) == 2
        all_pairs = plan['legit'] + plan['background'] + plan['attack']
        assert len(set(all_pairs)) == len(all_pairs)


def test_attack_count_matches_scenario(planner):
    assert len(planner.plan('S5', LEGIT, random.Random(1))['attack']) == 5
    assert planner.plan('S1', LEGIT, random.Random(1))['attack'] == []


def test_plan_is_reproducible(planner):
    p1 = planner.plan('S4', LEGIT, random.Random('x'))
    p2 = planner.plan('S4', LEGIT, random.Random('x'))
    assert p1 == p2


def test_presaturation_preloads_alternative_links(planner):
    plan = planner.plan('S9', LEGIT, random.Random(0))
    if planner.topo.__class__.__name__ != 'FatTree':
        # Fat-tree: static routing sends all inter-pod traffic via core c1, so
        # no host pair's traffic crosses the victims' detours (c2..c4).
        assert plan['preload']
    seen = {l for p in plan['legit'] + plan['attack'] for l in path_links(planner.paths[p])}
    for src, dst, bw, link in plan['preload']:
        links = set(path_links(planner.paths[(src, dst)]))
        assert link in links
        assert plan['target_link'] not in links
        assert not links & seen  # preload flows never share a link
        seen |= links


def test_per_flow_rate():
    # 85% of 100 Mbps with 20 Mbps legit -> 65 Mbps over 3 flows
    assert per_flow_mbps(85, 100, 20, 3) == pytest.approx(65 / 3)
    assert per_flow_mbps(10, 100, 20, 3) == 0
