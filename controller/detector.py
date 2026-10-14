"""Trend-based congestion detector: a link must stay above threshold for N
consecutive polling intervals before it triggers."""
import random
import time
from collections import deque


class CongestionDetector:
    HISTORY_LEN = 10  # intervals kept per link for get_attack_confidence

    def __init__(self, config, clock=time.time, rng=None):
        self.threshold = config.utilisation_threshold   # 0.80
        self.N = config.N_intervals                     # 3
        self.repeat_window = getattr(config, 'repeat_attack_window', 120)
        self.adversarial = getattr(config, 'adversarial', {}) or {}
        self.counters = {}        # {link: consecutive_high_count}
        self.history = {}         # {link: deque of recent above-threshold flags}
        self.attack_history = {}  # {link: last_attack_time}
        self.clock = clock
        self.rng = rng or random.Random()

    def update(self, link_stats: dict) -> list:
        """
        Called every polling interval.
        Returns list of links that have triggered congestion.
        """
        if self.adversarial.get('randomise_N'):
            self.randomise_thresholds()

        triggered = []
        now = self.clock()
        for link, stats in link_stats.items():
            high = stats['utilisation'] > self.threshold
            self.history.setdefault(
                link, deque(maxlen=self.HISTORY_LEN)).append(high)
            if high:
                self.counters[link] = self.counters.get(link, 0) + 1
                if self.counters[link] >= self.adapt_N_for_repeated_attacks(link):
                    triggered.append(link)
                    self.attack_history[link] = now
            else:
                self.counters[link] = 0  # reset counter on drop below threshold
        return triggered

    def get_attack_confidence(self, link) -> float:
        """
        Returns fraction of recent intervals that were above threshold.
        Used for logging and paper reporting.
        """
        recent = self.history.get(link)
        if not recent:
            return 0.0
        return sum(recent) / len(recent)

    def adapt_N_for_repeated_attacks(self, link) -> int:
        """
        If link was attacked recently, reduce N by 1 (respond faster).
        Adversarial robustness mechanism.
        """
        last = self.attack_history.get(link)
        if last is not None and self.clock() - last <= self.repeat_window:
            return max(1, self.N - 1)
        return self.N

    def randomise_thresholds(self) -> int:
        """
        For adversarial scenarios S10: N ~ Uniform(N_min, N_max).
        Cooldown randomisation lives in HysteresisManager.randomise_cooldown.
        """
        self.N = self.rng.randint(self.adversarial['N_min'],
                                  self.adversarial['N_max'])
        return self.N
