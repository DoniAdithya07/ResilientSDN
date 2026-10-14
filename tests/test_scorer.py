import pytest

from controller.scorer import PathScorer
from tests.conftest import make_link_stats

SHORT = ['A', 'C', 'D']
LONG = ['A', 'E', 'F', 'D']
CURRENT = ['A', 'B', 'D']


def scorer_for(config, graph, loads=None):
    return PathScorer(config, make_link_stats(graph, loads))


def test_capacity_score_is_headroom_after_reroute(config, diamond):
    s = scorer_for(config, diamond, {('C', 'D'): 500})
    assert s._capacity_score(SHORT, 100) == pytest.approx(0.4)


def test_latency_score_is_relative_to_best_candidate(config, diamond):
    s = scorer_for(config, diamond)
    lat = {}  # default 5 ms per link: SHORT = 10 ms, LONG = 15 ms
    assert s._latency_score(SHORT, lat, reference=10) == pytest.approx(1.0)
    assert s._latency_score(LONG, lat, reference=10) == pytest.approx(2 / 3)


def test_rule_cost_counts_new_switches(config, diamond):
    s = scorer_for(config, diamond)
    assert s._rule_cost(SHORT, CURRENT) == 1       # only C is new
    assert s._rule_cost(LONG, CURRENT) == 2        # E and F
    assert s._rule_cost(SHORT, []) == 3
    assert s._normalised_rule_cost(SHORT, CURRENT) == pytest.approx(1 / 3)


def test_score_formula(config, diamond):
    s = scorer_for(config, diamond, {('C', 'D'): 500})
    # C = 0.4, L = 1.0 (own reference), K = 1/3
    expected = 0.4 * 0.4 + 0.3 * 1.0 - 0.3 * (1 / 3)
    assert s.score(SHORT, 100, CURRENT, {}) == pytest.approx(expected)


def test_ranking_prefers_short_idle_path(config, diamond):
    s = scorer_for(config, diamond)
    ranked = s.rank_candidates([LONG, SHORT], 100, CURRENT, {})
    assert ranked[0][0] == SHORT


def test_ranking_prefers_headroom_when_short_path_is_loaded(config, diamond):
    s = scorer_for(config, diamond, {('C', 'D'): 700})
    ranked = s.rank_candidates([SHORT, LONG], 50, CURRENT, {})
    assert ranked[0][0] == LONG


def test_rank_empty(config, diamond):
    assert scorer_for(config, diamond).rank_candidates([], 1, [], {}) == []


def test_update_stats_changes_scores(config, diamond):
    s = scorer_for(config, diamond)
    before = s._capacity_score(SHORT, 0)
    s.update_stats(make_link_stats(diamond, {('C', 'D'): 500}))
    assert s._capacity_score(SHORT, 0) < before


def test_dpp_score_is_negative_drift_minus_rule_cost(config, diamond):
    s = scorer_for(config, diamond, {('A', 'C'): 200, ('C', 'D'): 500})
    # post-move utilisation: A-C 0.3, C-D 0.6 ; K = 1/3
    assert s.dpp_score(SHORT, 100, CURRENT) == pytest.approx(-(0.3 + 0.6) - 1 / 3)


def test_dpp_prefers_idle_detour_over_congested_current_path(config, diamond):
    # Current path A-B-D is congested; the idle detour A-C-D must score higher
    s = scorer_for(config, diamond, {('A', 'B'): 900, ('B', 'D'): 900})
    current = s.dpp_score(CURRENT, 0, CURRENT)          # -(0.9 + 0.9), K = 0
    detour = s.dpp_score(SHORT, 100, CURRENT)           # -(0.1 + 0.1) - 1/3
    assert detour - current > config.hysteresis_epsilon


def test_dpp_penalises_longer_paths(config, diamond):
    s = scorer_for(config, diamond)
    assert s.dpp_score(SHORT, 100, CURRENT) > s.dpp_score(LONG, 100, CURRENT)


def long_detour_stats():
    """Short path with one hot link versus a 6-hop idle-ish detour (like an Internet2 detour)."""
    import networkx as nx
    g = nx.Graph()
    g.add_edges_from([('A', 'B'), ('B', 'C'), ('A', 'P'), ('P', 'Q'), ('Q', 'R'), ('R', 'S'), ('S', 'T'), ('T', 'C')])
    loads = {('A', 'B'): 100, ('B', 'C'): 950}                            # current path: 0.1 and 0.95
    loads.update({l: 250 for l in [('A', 'P'), ('P', 'Q'), ('Q', 'R'), ('R', 'S'), ('S', 'T'), ('T', 'C')]})
    return make_link_stats(g, loads)


CUR = ['A', 'B', 'C']
DETOUR = ['A', 'P', 'Q', 'R', 'S', 'T', 'C']


def test_sum_drift_penalises_a_long_detour_even_when_it_avoids_a_hot_link(config):
    s = PathScorer(config, long_detour_stats())     # default dpp_term = 'sum': the published behaviour
    assert s.dpp_score(DETOUR, 0, CUR) < s.dpp_score(CUR, 0, CUR)
