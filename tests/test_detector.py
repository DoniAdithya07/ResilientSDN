import random

from controller.detector import CongestionDetector

LINK = ('s1', 's2')


def stats(u):
    return {LINK: {'utilisation': u}}


def test_triggers_only_after_N_consecutive_intervals(config, clock):
    det = CongestionDetector(config, clock=clock)
    assert det.update(stats(0.9)) == []
    assert det.update(stats(0.9)) == []
    assert det.update(stats(0.9)) == [LINK]


def test_drop_below_threshold_resets_counter(config, clock):
    det = CongestionDetector(config, clock=clock)
    det.update(stats(0.9))
    det.update(stats(0.9))
    det.update(stats(0.5))
    assert det.update(stats(0.9)) == []
    assert det.update(stats(0.9)) == []
    assert det.update(stats(0.9)) == [LINK]


def test_exactly_threshold_is_not_high(config, clock):
    det = CongestionDetector(config, clock=clock)
    for _ in range(5):
        assert det.update(stats(0.80)) == []


def test_repeat_attack_within_window_uses_N_minus_1(config, clock):
    det = CongestionDetector(config, clock=clock)
    for _ in range(3):
        det.update(stats(0.9))
    det.update(stats(0.1))
    clock.advance(30)
    assert det.update(stats(0.9)) == []
    assert det.update(stats(0.9)) == [LINK]  # 2 intervals, not 3


def test_repeat_attack_outside_window_uses_full_N(config, clock):
    det = CongestionDetector(config, clock=clock)
    for _ in range(3):
        det.update(stats(0.9))
    det.update(stats(0.1))
    clock.advance(config.repeat_attack_window + 1)
    det.update(stats(0.9))
    assert det.update(stats(0.9)) == []


def test_attack_confidence_is_fraction_of_recent_high_intervals(config, clock):
    det = CongestionDetector(config, clock=clock)
    assert det.get_attack_confidence(LINK) == 0.0
    for u in (0.9, 0.9, 0.1, 0.9):
        det.update(stats(u))
    assert det.get_attack_confidence(LINK) == 0.75


def test_randomised_N_stays_in_configured_range(config, clock):
    config.adversarial['randomise_N'] = True
    det = CongestionDetector(config, clock=clock, rng=random.Random(1))
    seen = set()
    for _ in range(200):
        det.update(stats(0.1))
        seen.add(det.N)
    assert seen == set(range(config.adversarial['N_min'],
                             config.adversarial['N_max'] + 1))
