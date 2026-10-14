"""Paper figures 1-7 from experiment results (run on Windows).

    .venv\\Scripts\\python -m analysis.plot_results results\\<experiment> [--ablation results]

Figure 1: Throughput per system and scenario (bars, 95% CI)
Figure 2: Packet loss vs attack intensity (lines, S1-S5)
Figure 3: Recovery time CDF (attack scenarios)
Figure 4: Secondary congestion events per system
Figure 5: Rule changes per rerouting event
Figure 6: Ablation — each mechanism's key metric (needs ablation_A..D)
Figure 7: Adversarial robustness (S7-S10 packet loss)
One figure per topology where the data has several; files go to <results>/figures.
"""
import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from analysis.style import SYSTEM_COLORS, SYSTEM_LABELS, TEXT_2, save, setup, systems_in
from attack.scenarios import SCENARIOS
from evaluation.stats import StatisticalAnalysis

CI = StatisticalAnalysis().confidence_interval
ATTACK_MBPS = {s: v.get('attackers', 0) * v.get('rate_mbps', 0)
               for s, v in SCENARIOS.items() if 'rate_mbps' in v}


def whiskers(stats):
    """Asymmetric 95% CI whiskers; every metric is >= 0, so the lower one stops at 0."""
    means = [0 if np.isnan(m) else m for m, _ in stats]
    cis = [0 if np.isnan(c) else c for _, c in stats]
    return [[min(c, m) for m, c in zip(means, cis)], cis]


def grouped_bars(ax, df, metric, groups, systems):
    """Bars per system inside each group (scenario), 95% CI whiskers."""
    width = 0.8 / len(systems)
    x = np.arange(len(groups))
    for i, system in enumerate(systems):
        stats = [CI(df[(df.scenario == g) & (df.system == system)][metric]) for g in groups]
        means = [m for m, _ in stats]
        ax.bar(x + (i - (len(systems) - 1) / 2) * width, means, width * 0.9,
               yerr=whiskers(stats), color=SYSTEM_COLORS.get(system, '#888'),
               label=SYSTEM_LABELS.get(system, system),
               error_kw={'elinewidth': 0.8, 'capsize': 1.5, 'ecolor': TEXT_2})
    ax.set_xticks(x, groups)


def bars_by_system(ax, df, metric, systems):
    stats = [CI(df[df.system == s][metric]) for s in systems]
    ax.bar(range(len(systems)), [m for m, _ in stats], 0.6,
           yerr=whiskers(stats),
           color=[SYSTEM_COLORS.get(s, '#888') for s in systems],
           error_kw={'elinewidth': 0.8, 'capsize': 2, 'ecolor': TEXT_2})
    ax.set_xticks(range(len(systems)), [SYSTEM_LABELS.get(s, s) for s in systems],
                  rotation=20, ha='right')


def fig_throughput(df, out, topo):
    systems, groups = systems_in(df.system), [s for s in SCENARIOS if s in set(df.scenario)]
    fig, ax = plt.subplots(figsize=(7.16, 2.4))
    grouped_bars(ax, df, 'throughput_mbps', groups, systems)
    ax.set_ylabel('Legitimate throughput (Mbps)')
    ax.legend(ncol=len(systems), loc='upper center', bbox_to_anchor=(0.5, 1.15))
    save(fig, out, f'fig1_throughput_{topo}')


def fig_loss_vs_intensity(df, out, topo):
    order = sorted((s for s in ATTACK_MBPS if s in set(df.scenario)), key=ATTACK_MBPS.get)
    if len(order) < 2:
        return
    fig, ax = plt.subplots()
    for system in systems_in(df.system):
        stats = [CI(df[(df.scenario == s) & (df.system == system)]['packet_loss_pct'])
                 for s in order]
        xs = [ATTACK_MBPS[s] for s in order]
        ax.plot(xs, [m for m, _ in stats], marker='o',
                color=SYSTEM_COLORS.get(system), label=SYSTEM_LABELS.get(system, system))
    ax.set_xlabel('Total attack rate (Mbps)')
    ax.set_ylabel('Legitimate packet loss (%)')
    ax.legend()
    save(fig, out, f'fig2_loss_vs_intensity_{topo}')


def fig_recovery_cdf(df, out, topo):
    """Runs that never recovered are censored, not recovered at attack end:
    each CDF counts only recovered runs over *all* runs, so it plateaus
    below 1 by the share that never recovered."""
    attacked = df[df.scenario != 'S1']
    fig, ax = plt.subplots()
    for system in systems_in(attacked.system):
        runs = attacked[attacked.system == system]
        recovered = runs['recovered'].astype(str).str.lower() == 'true'
        v = np.sort(runs.loc[recovered, 'recovery_time_s'].dropna())
        label = f'{SYSTEM_LABELS.get(system, system)} ({(~recovered).sum()}/{len(runs)} never)'
        ax.step(np.r_[0, v], np.arange(len(v) + 1) / len(runs), where='post',
                color=SYSTEM_COLORS.get(system), label=label)
    ax.set_xlabel('Recovery time (s)')
    ax.set_ylabel('Share of attack runs recovered')
    ax.set_ylim(0, 1.02)
    ax.grid(axis='x')
    ax.legend(loc='lower right')
    save(fig, out, f'fig3_recovery_cdf_{topo}')


def fig_simple_bars(df, out, topo, metric, ylabel, name):
    fig, ax = plt.subplots()
    bars_by_system(ax, df, metric, systems_in(df.system))
    ax.set_ylabel(ylabel)
    save(fig, out, f'{name}_{topo}')


def fig_rules_per_reroute(df, out, topo):
    d = df[df.reroutes > 0].assign(rules_per_reroute=lambda x: x.rule_changes / x.reroutes)
    if len(d):
        fig_simple_bars(d, out, topo, 'rules_per_reroute',
                        'Rule changes per reroute', 'fig5_rules_per_reroute')


def fig_adversarial(df, out, topo):
    groups = [s for s in ('S7', 'S8', 'S9', 'S10') if s in set(df.scenario)]
    if not groups:
        return
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    grouped_bars(ax, df, 'packet_loss_pct', groups, systems_in(df.system))
    ax.set_ylabel('Legitimate packet loss (%)')
    ax.legend(fontsize=6)
    save(fig, out, f'fig7_adversarial_{topo}')


ABLATION_METRIC = {'A': ('secondary_congestion', 'Secondary congestion'),
                   'B': ('oscillations', 'Oscillations'),
                   'C': ('throughput_mbps', 'Throughput (Mbps)'),
                   'D': ('reroutes', 'Reroutes in S1 (false +)')}


def fig_ablation(ablation_root, out):
    panels = [(k, pd.read_csv(ablation_root / f'ablation_{k}' / 'all_results.csv'))
              for k in ABLATION_METRIC
              if (ablation_root / f'ablation_{k}' / 'all_results.csv').exists()]
    if not panels:
        return
    fig, axes = plt.subplots(1, len(panels), figsize=(7.16, 2.2))
    for ax, (k, df) in zip(np.atleast_1d(axes), panels):
        metric, label = ABLATION_METRIC[k]
        bars_by_system(ax, df, metric, systems_in(df.system))
        ax.set_title(f'{k}: {label}', color=TEXT_2)
    fig.tight_layout()
    save(fig, out, 'fig6_ablation')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('results')
    p.add_argument('--ablation', help='folder holding ablation_A..D')
    a = p.parse_args()
    setup()
    results = Path(a.results)
    out = results / 'figures'
    df = pd.read_csv(results / 'all_results.csv')
    for topo, d in df.groupby('topology'):
        fig_throughput(d, out, topo)
        fig_loss_vs_intensity(d, out, topo)
        fig_recovery_cdf(d, out, topo)
        fig_simple_bars(d, out, topo, 'secondary_congestion',
                        'Secondary congestion events', 'fig4_secondary_congestion')
        fig_rules_per_reroute(d, out, topo)
        fig_adversarial(d, out, topo)
    if a.ablation:
        fig_ablation(Path(a.ablation), out)
    print(out)


if __name__ == '__main__':
    main()
