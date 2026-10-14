import pytest

from controller.path_engine import PathEngine, path_links
from tests.conftest import make_link_stats


def test_path_links():
    assert path_links(['A', 'B', 'C']) == [('A', 'B'), ('B', 'C')]
    assert path_links(['A']) == []


def test_candidates_skip_congested_link(config, diamond):
    engine = PathEngine(diamond, config)
    stats = make_link_stats(diamond)
    cands = engine.find_candidates('A', 'D', ('A', 'B'), 100, stats)
    assert ['A', 'B', 'D'] not in cands
    assert ['A', 'C', 'D'] in cands
    assert ['A', 'E', 'F', 'D'] in cands


def test_reverse_direction_of_congested_link_is_still_usable(config, diamond):
    # Full duplex: congestion on A->B says nothing about B->A.
    engine = PathEngine(diamond, config)
    stats = make_link_stats(diamond)
    cands = engine.find_candidates('D', 'A', ('A', 'B'), 100, stats)
    assert ['D', 'B', 'A'] in cands


def test_capacity_filter_rejects_path_that_would_exceed_safe_threshold(config, diamond):
    engine = PathEngine(diamond, config)
    # C->D at 70%; moving 200 B/s makes it 90% > 80%
    stats = make_link_stats(diamond, {('C', 'D'): 700})
    cands = engine.find_candidates('A', 'D', ('A', 'B'), 200, stats)
    assert ['A', 'C', 'D'] not in cands
    assert ['A', 'E', 'F', 'D'] in cands


def test_capacity_filter_accepts_exactly_safe_threshold(config, diamond):
    engine = PathEngine(diamond, config)
    stats = make_link_stats(diamond, {('C', 'D'): 600})
    assert engine._passes_capacity_filter(['A', 'C', 'D'], 200, stats)


def test_link_without_stats_is_rejected(config, diamond):
    engine = PathEngine(diamond, config)
    stats = make_link_stats(diamond)
    del stats[('C', 'D')]
    assert not engine._passes_capacity_filter(['A', 'C', 'D'], 0, stats)


def test_post_reroute_utilisation_is_worst_link(config, diamond):
    engine = PathEngine(diamond, config)
    stats = make_link_stats(diamond, {('E', 'F'): 300, ('F', 'D'): 500})
    assert engine.compute_post_reroute_utilisation(
        ['A', 'E', 'F', 'D'], 100, stats) == pytest.approx(0.6)


def test_split_is_proportional_to_residual(config, diamond):
    engine = PathEngine(diamond, config)
    # residual at 80%: A-C-D -> 800-600 = 200, A-E-F-D -> 800-400 = 400
    stats = make_link_stats(diamond, {('C', 'D'): 600, ('F', 'D'): 400})
    alloc = engine.split_traffic([['A', 'C', 'D'], ['A', 'E', 'F', 'D']],
                                 300, stats)
    assert alloc[('A', 'C', 'D')] == pytest.approx(100)
    assert alloc[('A', 'E', 'F', 'D')] == pytest.approx(200)


def test_split_never_exceeds_a_paths_residual(config, diamond):
    engine = PathEngine(diamond, config)
    stats = make_link_stats(diamond, {('C', 'D'): 600, ('F', 'D'): 400})
    alloc = engine.split_traffic([['A', 'C', 'D'], ['A', 'E', 'F', 'D']],
                                 10_000, stats)
    assert alloc[('A', 'C', 'D')] == pytest.approx(200)
    assert alloc[('A', 'E', 'F', 'D')] == pytest.approx(400)


def test_split_returns_empty_when_no_residual(config, diamond):
    engine = PathEngine(diamond, config)
    stats = make_link_stats(diamond, {('C', 'D'): 900})
    assert engine.split_traffic([['A', 'C', 'D']], 100, stats) == {}
