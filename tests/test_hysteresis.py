import random

from controller.hysteresis import HysteresisManager

FLOW = 'h1->h2'


def test_improvement_must_exceed_epsilon(config, clock):
    h = HysteresisManager(config, clock=clock)
    assert not h.should_reroute(FLOW, 1.04, 1.0)   # below epsilon (0.05)
    assert not h.should_reroute(FLOW, 0.9, 1.0)    # worse path
    assert h.should_reroute(FLOW, 1.06, 1.0)


def test_first_reroute_is_not_blocked_by_cooldown(config, clock):
    h = HysteresisManager(config, clock=clock)
    assert h.should_reroute(FLOW, 1.0, 0.0)


def test_cooldown_blocks_until_elapsed(config, clock):
    h = HysteresisManager(config, clock=clock)
    h.record_reroute(FLOW, 1.0)
    clock.advance(config.cooldown_seconds - 1)
    assert not h.should_reroute(FLOW, 2.0, 0.0)
    clock.advance(1)
    assert h.should_reroute(FLOW, 2.0, 0.0)


def test_cooldown_is_per_flow(config, clock):
    h = HysteresisManager(config, clock=clock)
    h.record_reroute(FLOW, 1.0)
    assert h.should_reroute('other', 2.0, 0.0)


def test_counts_reroutes_and_oscillations(config, clock):
    h = HysteresisManager(config, clock=clock)
    h.record_reroute(FLOW, 1.0, ['A', 'B', 'D'])
    h.record_reroute(FLOW, 1.0, ['A', 'C', 'D'])
    h.record_reroute(FLOW, 1.0, ['A', 'B', 'D'])  # back to an earlier path
    assert h.get_reroute_count(FLOW) == 3
    assert h.get_oscillation_count(FLOW) == 1
    assert h.get_oscillation_count() == 1
    assert h.get_reroute_count('unknown') == 0


def test_time_since_last_reroute(config, clock):
    h = HysteresisManager(config, clock=clock)
    assert h.get_time_since_last_reroute(FLOW) == float('inf')
    h.record_reroute(FLOW, 1.0)
    clock.advance(7)
    assert h.get_time_since_last_reroute(FLOW) == 7


def test_randomised_cooldown_in_range(config, clock):
    config.adversarial['randomise_cooldown'] = True
    h = HysteresisManager(config, clock=clock, rng=random.Random(3))
    for _ in range(50):
        h.record_reroute(FLOW, 1.0)
        assert (config.adversarial['cooldown_min'] <= h.cooldown
                <= config.adversarial['cooldown_max'])


def test_relief_move_skips_the_improvement_deadband_but_not_the_cooldown(config, clock):
    """A flow leaving a link that is over the threshold must not be held because the
    detour scores 'worse' (long WAN detours always do); the cooldown still applies."""
    h = HysteresisManager(config, clock=clock)
    assert not h.should_reroute(FLOW, 0.5, 1.0)                 # normal gate: detour looks worse, held
    assert h.should_reroute(FLOW, 0.5, 1.0, relief=True)        # relief move: allowed
    h.record_reroute(FLOW, 0.5)
    clock.advance(config.cooldown_seconds - 1)
    assert not h.should_reroute(FLOW, 0.5, 1.0, relief=True)    # cooldown still protects against chatter
    clock.advance(1)
    assert h.should_reroute(FLOW, 0.5, 1.0, relief=True)
