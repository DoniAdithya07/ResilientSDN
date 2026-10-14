"""Figure 8: Pareto front of one representative rerouting decision, plus the
scalarisation hit rate over all decisions.

    .venv\\Scripts\\python -m analysis.plot_pareto results\\<experiment>
"""
import sys
from pathlib import Path

import matplotlib.pyplot as plt

from analysis.style import SYSTEM_COLORS, TEXT_2, save, setup
from evaluation.pareto import ParetoAnalysis

DOMINATED, FRONT = '#b8b7b1', SYSTEM_COLORS['resilient_sdn']


def main():
    results = Path(sys.argv[1])
    setup()
    pa = ParetoAnalysis()
    instances = pa.instances_from_results(results)
    if not instances:
        print('no decision events found')
        return
    hit = pa.scalarisation_hit_rate(instances)
    # Representative decision: the one with the most candidates
    inst = max(instances, key=lambda i: len(i['objectives']))
    front = set(pa.compute_pareto_front(inst['objectives']))

    fig, ax = plt.subplots()
    for i, (c, l, k) in enumerate(inst['objectives']):
        on_front = i in front
        ax.scatter(c, l, s=20 + 60 * (1 - k), color=FRONT if on_front else DOMINATED,
                   edgecolor='white', linewidth=1, zorder=3 if on_front else 2)
    c, l, _ = inst['objectives'][inst['selected']]
    ax.scatter(c, l, s=110, facecolor='none', edgecolor='#0b0b0b', linewidth=1.2, zorder=4)
    ax.annotate('selected', (c, l), xytext=(6, -10), textcoords='offset points',
                fontsize=7, color=TEXT_2)
    ax.scatter([], [], color=FRONT, label='Pareto-optimal')
    ax.scatter([], [], color=DOMINATED, label='Dominated')
    ax.set_xlabel('Capacity headroom C(P)')
    ax.set_ylabel('Latency score L(P)')
    ax.set_title(f'Marker size: fewer rule changes. Hit rate {hit:.0%} '
                 f'({len(instances)} decisions)', color=TEXT_2)
    ax.grid(axis='x')
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.2), ncol=2)
    save(fig, results / 'figures', 'fig8_pareto')
    print(results / 'figures', f'hit rate {hit:.3f}')


if __name__ == '__main__':
    main()
