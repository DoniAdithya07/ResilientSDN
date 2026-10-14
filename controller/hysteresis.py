"""DPP-deadband hysteresis and cooldown. Prevents chattering near the
Lyapunov optimum."""
import random
import time


class HysteresisManager:
    def __init__(self, config, clock=time.time, rng=None):
        self.epsilon = config.hysteresis_epsilon  # DPP deadband
        self.cooldown = config.cooldown_seconds   # seconds
        self.adversarial = getattr(config, 'adversarial', {}) or {}
        self.last_reroute_time = {}  # {flow: timestamp}
        self.current_dpp = {}        # {flow: current DPP value}
        self.reroute_counts = {}     # {flow: number of reroutes}
        self.path_history = {}       # {flow: [paths used, oldest first]}
        self.oscillations = {}       # {flow: reroutes back to an earlier path}
        self.clock = clock
        self.rng = rng or random.Random()

    def should_reroute(self, flow_id, new_path_dpp,
                       current_path_dpp, link=None, relief=False) -> bool:
        """
        Reroute only if ALL conditions are satisfied:
        1. DPP improvement exceeds deadband epsilon
           (so the new path is significantly better, not marginally)
        2. Cooldown period has elapsed
        relief=True (the flow leaves a link that is over the threshold, onto a path the
        capacity filter has already cleared) skips condition 1: the deadband stops
        chattering near the optimum, it must not keep a flow on a saturated link just
        because a long detour scores worse by summed utilisation.
        """
        if not relief and new_path_dpp - current_path_dpp <= self.epsilon:
            return False

        last = self.last_reroute_time.get(flow_id)
        return last is None or self.clock() - last >= self.cooldown

    def record_reroute(self, flow_id, new_dpp, new_path=None):
        """Record that a rerouting event occurred."""
        self.last_reroute_time[flow_id] = self.clock()
        self.current_dpp[flow_id] = new_dpp
        self.reroute_counts[flow_id] = self.reroute_counts.get(flow_id, 0) + 1
        if new_path is not None:
            new_path = tuple(new_path)
            history = self.path_history.setdefault(flow_id, [])
            if new_path in history:
                self.oscillations[flow_id] = self.oscillations.get(flow_id, 0) + 1
            history.append(new_path)
        if self.adversarial.get('randomise_cooldown'):
            self.randomise_cooldown()

    def randomise_cooldown(self) -> float:
        """For adversarial scenarios S10: cooldown ~ Uniform(min, max)."""
        self.cooldown = self.rng.uniform(self.adversarial['cooldown_min'],
                                         self.adversarial['cooldown_max'])
        return self.cooldown

    def get_time_since_last_reroute(self, flow_id) -> float:
        last = self.last_reroute_time.get(flow_id)
        return float('inf') if last is None else self.clock() - last

    def get_reroute_count(self, flow_id) -> int:
        """For experiment metrics."""
        return self.reroute_counts.get(flow_id, 0)

    def get_oscillation_count(self, flow_id=None) -> int:
        """Reroutes back to a path the flow used before. None = all flows."""
        if flow_id is None:
            return sum(self.oscillations.values())
        return self.oscillations.get(flow_id, 0)
