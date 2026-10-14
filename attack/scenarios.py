"""The 10 attack scenarios.

Standard (S1-S6): `attackers` UDP flows, each at `rate_mbps`, all crossing the
target link. Rates assume a 100 Mbps target link (the target is always one of
the topology's fastest links).

Adversarial (S7-S10): percentages are the *total* target-link utilisation the
attacker aims for, legitimate traffic included.
  S7 pulse: alternate high/low every `interval` s, so the link is never above
     threshold for N consecutive polls.
  S8 timed: `burst` s at `rate_pct` every `interval` s — just after the 30 s
     cooldown ends, so flows get pushed around again.
  S9 presaturation: first load the alternative paths to `preload_alt_paths`
     (just under the safe threshold), then attack at `attack_pct`.
  S10: one of S7-S9 (chosen per run) against randomised N and cooldown.
"""
SCENARIOS = {
    'S1': {'name': 'No attack', 'attackers': 0, 'rate_mbps': 0},
    'S2': {'name': 'Low single', 'attackers': 1, 'rate_mbps': 30},
    'S3': {'name': 'High single', 'attackers': 1, 'rate_mbps': 70},
    'S4': {'name': 'Distributed 3x', 'attackers': 3, 'rate_mbps': 20},
    'S5': {'name': 'Heavy 5x', 'attackers': 5, 'rate_mbps': 15},
    'S6': {'name': 'Sustained 10min', 'attackers': 3, 'rate_mbps': 30,
           'duration': 600},
    'S11': {'name': 'Overload 2min', 'attackers': 3, 'rate_mbps': 30,
            'duration': 120},
    'S7': {'name': 'Threshold evasion', 'type': 'adversarial', 'attackers': 3,
           'pattern': 'pulse', 'high': 85, 'low': 65, 'interval': 4},
    'S8': {'name': 'Cooldown exploit', 'type': 'adversarial', 'attackers': 3,
           'pattern': 'timed', 'interval': 31, 'burst': 8, 'rate_pct': 90},
    'S9': {'name': 'Capacity presaturation', 'type': 'adversarial', 'attackers': 3,
           'pattern': 'presaturate', 'preload_alt_paths': 0.79, 'attack_pct': 90},
    'S10': {'name': 'Randomised thresholds', 'type': 'adversarial',
            'same_as': ['S7', 'S8', 'S9'], 'randomise': True},
}
