"""PDF report for one experiment run folder: what was measured and WHY it came out that way.

    .venv\\Scripts\\python -m analysis.run_report results\\<experiment> [--out report.pdf]

Writes <experiment>/report.pdf. evaluation/experiment.py calls this after every
run (or prints this command when the plotting libraries are missing, as in the
container). Every explanation sentence is built from the measured numbers of the
run folder (all_results.csv, meta.json, events.jsonl), so it stays true for any
topology, scenario or number of runs.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from analysis.style import SYSTEM_COLORS, SYSTEM_LABELS, systems_in  # noqa: E402
from attack.scenarios import SCENARIOS  # noqa: E402
from controller.config import load_yaml  # noqa: E402
from evaluation.metrics import read_events  # noqa: E402
from topology import load_topology  # noqa: E402

AUTHOR = 'Doni Adithya'
SS = getSampleStyleSheet()
P = ParagraphStyle('p', parent=SS['BodyText'], fontSize=9.5, leading=13.5, spaceAfter=5)
H1 = ParagraphStyle('h1', parent=SS['Heading1'], fontSize=15, spaceBefore=10, spaceAfter=6)
H2 = ParagraphStyle('h2', parent=SS['Heading2'], fontSize=11.5, spaceBefore=8, spaceAfter=4)
CAP = ParagraphStyle('cap', parent=P, fontSize=8, textColor=colors.HexColor('#52514e'))
CELL = ParagraphStyle('cell', parent=P, fontSize=8, leading=10, spaceAfter=0)
LABEL = lambda s: SYSTEM_LABELS.get(s, s)  # noqa: E731


# ---------------------------------------------------------------- data
def load_runs(results):
    """all_results.csv plus, per run: peak target-link utilisation, share of the
    attack window spent above the threshold, and controller decision counts."""
    df = pd.read_csv(results / 'all_results.csv')
    if df.empty:
        raise ValueError(f'{results / "all_results.csv"} has no runs')
    extra_cols = ['target', 'threshold', 'peak_util', 'above_pct', 'held', 'attack_flows']
    extra = []
    for _, r in df.iterrows():
        d = results / 'runs' / f'{r.topology}_{r.system}_{r.scenario}_{r.run}'
        try:
            meta = json.loads((d / 'meta.json').read_text())
            if not (d / 'events.jsonl').exists():
                raise OSError('no events.jsonl')
            ev = read_events(d)
        except (OSError, ValueError):   # missing or truncated file: leave the row without run data
            extra.append({})
            continue
        tgt = meta['target_link']
        meta['threshold'] = meta.get('threshold') or 0.8
        u = [e['util'].get(tgt, 0.0) for e in ev
             if e['kind'] == 'stats' and meta['attack_start'] <= e['t'] <= meta['end']]
        extra.append({'target': tgt, 'threshold': meta['threshold'],
                      'peak_util': max(u, default=float('nan')),
                      'above_pct': 100 * np.mean([x > meta['threshold'] for x in u]) if u else float('nan'),
                      'held': sum(e['kind'] == 'held' for e in ev),
                      'attack_flows': len(meta['attack'])})
    out = pd.concat([df, pd.DataFrame(extra, columns=extra_cols)], axis=1)
    out['src'] = str(results)   # which results folder a row came from
    return out


def threshold_of(sub):
    t = sub.threshold.dropna()
    return float(t.iloc[0]) if len(t) else 0.8


def scenario_name(sc):
    return SCENARIOS.get(sc, {}).get('name', sc)


def ordered_scenarios(names):
    return sorted(set(names), key=lambda s: int(s[1:]))


# ---------------------------------------------------------------- figures
def fig_topology(name, target, path):
    topo = load_topology(name)
    g = topo.get_switch_graph()
    pos = ({n: (topo.coords[n][1], topo.coords[n][0]) for n in g} if hasattr(topo, 'coords')
           else nx.kamada_kawai_layout(g))
    fig, ax = plt.subplots(figsize=(6, 3.6))
    nx.draw_networkx_edges(g, pos, ax=ax, edge_color='#9a988f', width=1)
    a, b = target.split('-')
    if g.has_edge(a, b):
        nx.draw_networkx_edges(g, pos, ax=ax, edgelist=[(a, b)], edge_color='#e34948', width=3.5)
    nx.draw_networkx_nodes(g, pos, ax=ax, node_size=90 if len(g) > 25 else 220,
                           node_color='#2a78d6', edgecolors='white')
    if len(g) <= 25:
        nx.draw_networkx_labels(g, pos, ax=ax, font_size=6, font_color='white')
    ax.set_title(f'{name}: {len(g)} switches, {g.number_of_edges()} links '
                 f'(red = attacked target link {target})', fontsize=8)
    ax.axis('off')
    fig.savefig(path, dpi=200, bbox_inches='tight', facecolor='white')
    plt.close(fig)


def fig_metrics(df, topology, path):
    sub = df[df.topology == topology]
    scen = ordered_scenarios(sub.scenario)
    systems = systems_in(sub.system)
    panels = [('packet_loss_pct', 'Legit packet loss (%)'), ('latency_ms', 'Legit latency (ms)'),
              ('reroutes', 'Reroutes'), ('above_pct', 'Attack time target link above threshold (%)')]
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 4.6))
    w = 0.8 / len(systems)
    for ax, (m, title) in zip(axes.flat, panels):
        for i, s in enumerate(systems):
            vals = [sub[(sub.scenario == c) & (sub.system == s)][m].mean() for c in scen]
            ax.bar(np.arange(len(scen)) + i * w, vals, w, label=LABEL(s), color=SYSTEM_COLORS.get(s))
        ax.set_xticks(np.arange(len(scen)) + w * (len(systems) - 1) / 2, scen, fontsize=7)
        ax.set_ylim(bottom=0)
        ax.set_title(title, fontsize=8)
        ax.grid(axis='y', lw=0.4, color='#e6e5e1')
        ax.set_axisbelow(True)
    axes.flat[0].legend(fontsize=6, frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=200, facecolor='white')
    plt.close(fig)


def fig_timeline(results, topology, scenario, systems, path):
    """Target-link load over time in run 0, one line per system, reroutes as ticks."""
    fig, ax = plt.subplots(figsize=(7.2, 2.8))
    thr = 0.8
    for s in systems:
        d = results / 'runs' / f'{topology}_{s}_{scenario}_0'
        try:
            meta = json.loads((d / 'meta.json').read_text())
            ev = read_events(d)
        except (OSError, ValueError):
            continue
        thr = meta.get('threshold') or 0.8
        t0 = meta['attack_start']
        st = [(e['t'] - t0, e['util'].get(meta['target_link'], 0.0)) for e in ev if e['kind'] == 'stats']
        if st:
            ax.plot(*zip(*st), color=SYSTEM_COLORS.get(s), lw=1.6, label=LABEL(s))
        for e in ev:
            if e['kind'] == 'reroute':
                ax.plot(e['t'] - t0, -0.04, '|', color=SYSTEM_COLORS.get(s), ms=7, clip_on=False)
    ax.axhline(thr, color='#e34948', ls='--', lw=1)
    ax.axvline(0, color='#52514e', lw=0.8)
    ax.text(0.5, 0.02, 'attack starts', fontsize=6, color='#52514e', transform=ax.get_xaxis_transform())
    ax.set_xlabel('seconds since attack start (ticks below the axis = reroutes)', fontsize=7)
    ax.set_ylabel('target-link load', fontsize=7)
    ax.set_title(f'{topology} {scenario}: target link over time (dashed = {thr:.0%} threshold)', fontsize=8)
    ax.legend(fontsize=6, frameon=False)
    ax.grid(lw=0.4, color='#e6e5e1')
    fig.tight_layout()
    fig.savefig(path, dpi=200, facecolor='white')
    plt.close(fig)


# ---------------------------------------------------------------- explanations
def m(sub, system, col):
    """Mean of a column for one system; NaN when there is no data."""
    return float(sub[sub.system == system][col].mean())


def per_system(sub, systems, col, fmt):
    return ', '.join(f'{LABEL(s)} ' + ('n/a' if pd.isna(m(sub, s, col)) else fmt.format(m(sub, s, col)))
                     for s in systems)


def num(x, fmt):
    return 'n/a' if pd.isna(x) else fmt.format(x)


def explain(sub, scenario):
    """Sentences about one (topology, scenario). Every claim is checked against the
    numbers; where data is missing the text says so instead of guessing."""
    systems = systems_in(sub.system)
    ref = 'static' if 'static' in systems else systems[0]
    thr = threshold_of(sub)
    peak = m(sub, ref, 'peak_util')
    name = scenario_name(scenario)
    loss = {s: m(sub, s, 'packet_loss_pct') for s in systems}
    loss_txt = per_system(sub, systems, 'packet_loss_pct', '{:.2f}%')
    known = [v for v in loss.values() if not pd.isna(v)]
    max_loss = max(known, default=float('nan'))
    if pd.isna(peak):
        return [f'<b>No explanation possible:</b> the run folders for {name} have no controller events '
                f'(events.jsonl / meta.json missing or unreadable), so the load on the target link is unknown. '
                f'Loss: {loss_txt}.']
    out = []
    if peak <= thr:
        moved = {s: m(sub, s, 'reroutes') for s in systems if m(sub, s, 'reroutes') > 0}
        if moved:
            out.append(f'<b>Unexpected reroutes:</b> with {LABEL(ref)} routing the target link peaked at '
                       f'{peak:.0%}, below the {thr:.0%} threshold, yet '
                       f'{", ".join(f"{LABEL(s)} rerouted {v:.1f} times" for s, v in moved.items())}. '
                       f'Nothing needed moving, so these are false alarms.')
        else:
            out.append(f'<b>Why nothing happened:</b> with {LABEL(ref)} routing the target link peaked at '
                       f'{peak:.0%}, below the {thr:.0%} congestion threshold, so no controller had a reason '
                       f'to move a flow, and none did (0 reroutes: no false alarms).')
        out.append(f'Loss: {loss_txt}' + ('; the link is not full, so loss is about zero.'
                                          if max_loss < 0.5 else '; this loss is not explained by congestion.'))
    elif peak <= 1.0:
        if max_loss < 0.5:
            out.append(f'<b>Why the link is congested but nothing is lost:</b> {name} pushed the target link to '
                       f'{peak:.0%} with {LABEL(ref)} routing: above the {thr:.0%} threshold but not above '
                       f'100%, so queues grow (latency rises) but packets are not dropped. Loss is about zero '
                       f'for every system ({loss_txt}); the systems differ in <i>how</i> they react.')
        else:
            out.append(f'<b>Loss despite load under 100%:</b> {name} pushed the target link to {peak:.0%} with '
                       f'{LABEL(ref)} routing (above the {thr:.0%} threshold, below 100% on average). Loss: '
                       f'{loss_txt}. A possible cause is short bursts overflowing the queue; this was not checked.')
    else:
        spread = max_loss - min(known) if known else 0
        out.append(f'<b>Why packets are lost:</b> {name} offers more traffic than the target link can carry '
                   f'(peak {peak:.0%} with {LABEL(ref)} routing), so the excess is dropped. Loss: {loss_txt}.')
        if spread >= 0.5:
            best = min((s for s in systems if not pd.isna(loss[s])), key=lambda s: loss[s])
            out.append(f'{LABEL(best)} lost least. Share of the attack with the target link above threshold: '
                       f'{per_system(sub, systems, "above_pct", "{:.0f}%")}. This is consistent with '
                       'getting flows off the overloaded link sooner, but the runs do not isolate that cause.')
        else:
            out.append('The systems lost about the same, so none of them clearly protected the traffic here.')
    if peak > thr:
        out.append('<b>Time above the threshold</b> (a controller that flaps cannot hide here): '
                   + per_system(sub, systems, 'time_above_s', '{:.0f} s') + '. <b>Stable recovery</b> '
                   '(until the link stays under the threshold for good): '
                   + per_system(sub, systems, 'stable_recovery_s', '{:.0f} s') + '.')
    if 'static' in systems and peak > thr:
        out.append('<b>Static routing</b> never moves a flow (0 reroutes by design), so its target link '
                   f'stayed above threshold for {num(m(sub, "static", "above_pct"), "{:.0f}")}% of the attack.')
    if 'simple_reactive' in systems and peak > thr:
        out.append(f'<b>Simple reactive</b> moves flows as soon as one poll shows congestion: '
                   f'{m(sub, "simple_reactive", "reroutes"):.1f} reroutes, '
                   f'{m(sub, "simple_reactive", "oscillations"):.1f} of them back onto a path already used, '
                   f'{m(sub, "simple_reactive", "rule_changes"):.0f} switch rule changes and '
                   f'{m(sub, "simple_reactive", "secondary_congestion"):.1f} other links pushed over the threshold. '
                   'It reacts fast but has no memory, so it can bounce flows between paths.')
    if 'resilient_sdn' in systems and peak > thr:
        out.append(f'<b>ResilientSDN</b> waits for consecutive high polls, checks that the new path stays '
                   f'under the threshold and holds a moved flow for a cooldown: {m(sub, "resilient_sdn", "reroutes"):.1f} '
                   f'reroutes, {m(sub, "resilient_sdn", "oscillations"):.1f} oscillations, '
                   f'{m(sub, "resilient_sdn", "held"):.1f} decisions held (new path not better enough) and '
                   f'{m(sub, "resilient_sdn", "unresolved"):.1f} unresolved events (no path fits all traffic). '
                   f'Target link above threshold {m(sub, "resilient_sdn", "above_pct"):.0f}% of the attack.')
    return out


# ---------------------------------------------------------------- document
def tbl(rows, widths, head='#2a78d6'):
    t = Table([[Paragraph(str(c), CELL) for c in r] for r in rows], colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(head)),
                           ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                           ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#c9c7c0')),
                           ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f3f2ee')]),
                           ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
    return t


def ci95(x):
    x = pd.Series(x, dtype=float).dropna()
    if len(x) < 2:
        return float(x.mean()) if len(x) else float('nan'), float('nan')
    from scipy import stats
    return x.mean(), stats.sem(x) * stats.t.ppf(0.975, len(x) - 1)


def pm(x, fmt='{:.1f}'):
    mean, ci = ci95(x)
    return 'n/a' if pd.isna(mean) else fmt.format(mean) + ('' if pd.isna(ci) else ' ± ' + fmt.format(ci))


def stats_rows(sub_t):
    """ResilientSDN against each other system on the metrics that matter, mean ± 95% CI, Welch p."""
    from scipy import stats
    rows = [['Scen.', 'Metric', 'ResilientSDN', 'Other system', 'Other mean ± CI', 'p (Welch)', 'n']]
    for sc in ordered_scenarios(sub_t.scenario):
        g = sub_t[sub_t.scenario == sc]
        mine = g[g.system == 'resilient_sdn']
        if mine.empty or threshold_of(g) >= g.peak_util.mean() > 0:
            continue   # nothing congested: no comparison to make
        for metric, label in (('time_above_s', 'Time above threshold (s)'),
                              ('packet_loss_pct', 'Legit loss (%)')):
            for other in systems_in(g.system):
                if other == 'resilient_sdn':
                    continue
                o = g[g.system == other]
                a, b = mine[metric].dropna(), o[metric].dropna()
                p = stats.ttest_ind(a, b, equal_var=False)[1] if len(a) > 1 and len(b) > 1 else float('nan')
                rows.append([sc, label, pm(mine[metric], '{:.2f}'), LABEL(other), pm(o[metric], '{:.2f}'),
                             'n/a' if pd.isna(p) else ('<0.001' if p < 0.001 else f'{p:.3f}'),
                             f'{len(a)} / {len(b)}'])
    return rows


LIMITS = [
    '<b>Small number of runs.</b> Confidence intervals and p-values are only meaningful with several runs '
    'per case. With n = 1 or 2 the table shows "n/a"; treat those numbers as examples, not evidence.',
    '<b>Emulation, not hardware.</b> Mininet and Open vSwitch run on one machine, so timing (polling every '
    '2 s, latency in ms) is affected by CPU load. The same seed gives similar but not identical results.',
    '<b>Scaled capacities.</b> WAN link speeds are scaled so the fastest link is 100 Mbps; ratios between '
    'links are kept, but absolute speeds are not those of the real networks.',
    '<b>No attacker/legitimate classifier.</b> On a congested link ResilientSDN moves flows largest first, '
    'which can be the legitimate flows while the attack flow stays on the link.',
    '<b>Loss is mostly an overload effect.</b> Attacks that fill a link to 90 to 98% mainly cause reroutes '
    'and time above the threshold; loss differences are clearest in the overload scenarios (S6, S11). '
    'Some loss also appears elsewhere (see the results); its cause was not diagnosed.',
    '<b>Fat-tree S9 has no preload.</b> Static shortest-path routing sends all inter-pod traffic through one '
    'core switch, so the attacker cannot fill the detours; fat-tree S9 behaves like S3.',
    '<b>Time above the threshold has a floor.</b> The controller needs 3 polls (about 8 s) to confirm '
    'congestion, so no run can show less; for static it is capped by the attack length.',
    '<b>Metrics defined after the campaign started.</b> Time above threshold and stable recovery were added because '
    'the first-dip recovery time rewards flapping (logged in PROGRESS.md before any results existed). '
    'The first-dip values are kept in the data (recovery_time_s).',
    '<b>Attempted fixes were not adopted.</b> Letting a flow leave a congested link without the improvement margin '
    '(alone or with a 75% safe limit) cut Internet2 S11 time above threshold from 96 s to 13 s but raised secondary '
    'congestion and caused some loss in S3 (2 runs per test); the original hold protects the legitimate flows.',
    '<b>Tests are unpaired and uncorrected</b> although runs share seeds across systems; use them descriptively.',
    '<b>Failures are not modelled.</b> A switch or link going down is out of scope.',
    '<b>Recovery is measured two ways.</b> "Stable recovery" counts from the first congestion until the '
    'link stays under the threshold; the older "first dip" metric rewarded controllers that flap.']


def correctness_checks(results, df):
    """[check, expected, observed, PASS/FAIL] rows computed from the run folder itself:
    they show that the emulator, traffic and controllers behaved as designed."""
    legit = load_yaml('config/experiment_config.yaml')['legit']
    legit_mbps, offered = legit['target_flows'] * legit['rate_mbps'], legit['rate_mbps']
    rows = [['Topology', 'Check', 'Expected', 'Observed', 'Result']]

    def add(topo, check, expected, observed, ok):
        rows.append([topo, check, expected, observed, 'n/a' if ok is None else ('PASS' if ok else 'FAIL')])

    failed = list((results / 'runs').glob('*/error.txt'))
    add('all', 'No run failed', '0 failed runs', f'{len(failed)} failed', not failed)
    for topo in sorted(df.topology.unique()):
        d = df[df.topology == topo]
        st = d[d.system == 'static']
        if len(st):
            add(topo, 'Static routing never reroutes', '0 reroutes', f'max {st.reroutes.max():.0f}',
                st.reroutes.max() == 0)
        s1 = d[d.scenario == 'S1']
        if len(s1):
            add(topo, 'No attack (S1): no reroute, no congestion', '0 reroutes, load below threshold',
                f'{s1.reroutes.sum():.0f} reroutes, peak {s1.peak_util.max():.0%}',
                s1.reroutes.sum() == 0 and s1.peak_util.max() <= threshold_of(s1))
        for sc in ordered_scenarios(d.scenario):
            info, g = SCENARIOS.get(sc, {}), st[st.scenario == sc]
            if info.get('type') == 'adversarial' or 'rate_mbps' not in info or g.peak_util.isna().all():
                continue
            expect = min(1.0, (info['attackers'] * info['rate_mbps'] + legit_mbps) / 100)
            got = float(g.peak_util.mean())
            add(topo, f'{sc}: measured target load matches theory',
                f'{expect:.0%} (±15 points)' if expect < 1 else 'at least 85% (overload)', f'{got:.0%}',
                abs(got - expect) <= 0.15 if expect < 1 else got >= 0.85)
        calm = d[d.packet_loss_pct < 1]
        if len(calm):
            add(topo, 'Legit throughput near the offered rate when loss < 1%', f'{0.9 * offered:.1f}-{offered:.1f} Mbps',
                f'{calm.throughput_mbps.min():.2f}-{calm.throughput_mbps.max():.2f} Mbps',
                calm.throughput_mbps.min() >= 0.9 * offered and calm.throughput_mbps.max() <= offered * 1.02)
    return rows


COLS = ['packet_loss_pct', 'latency_ms', 'throughput_mbps', 'stable_recovery_s', 'time_above_s',
        'reroutes', 'oscillations', 'rule_changes', 'secondary_congestion']


def topology_story(df, topology, fig_dir, first=False):
    """Flowables for one topology: map, charts, table, statistics, explanations, timeline."""
    story = []
    sub_t = df[df.topology == topology]
    story += [Spacer(1, 0.3 * cm) if first else PageBreak(), Paragraph(f'Topology: {topology}', H1)]
    target = sub_t.target.dropna().iloc[0] if sub_t.target.notna().any() else ''
    if target:
        fig_topology(topology, target, fig_dir / f'{topology}_topology.png')
        story.append(Image(str(fig_dir / f'{topology}_topology.png'), width=13 * cm, height=7.8 * cm))
        story.append(Paragraph('The attacker floods the red link; legitimate flows cross it too.', CAP))
    fig_metrics(df, topology, fig_dir / f'{topology}_metrics.png')
    story += [Image(str(fig_dir / f'{topology}_metrics.png'), width=16 * cm, height=10.2 * cm),
              Paragraph('Means per scenario and system.', CAP)]

    rows = [['Scen.', 'System', 'n', 'Peak load', 'Above thr. %'] + ['Loss %', 'Lat. ms',
            'Thr. Mbps', 'Stable rec. s', 'Time above s', 'Reroutes', 'Oscill.', 'Rule chg.', 'Sec. cong.']]
    for sc in ordered_scenarios(sub_t.scenario):
        for s in systems_in(sub_t.system):
            g = sub_t[(sub_t.scenario == sc) & (sub_t.system == s)]
            if g.empty:
                continue
            rows.append([sc, LABEL(s), len(g), f'{g.peak_util.mean():.0%}', f'{g.above_pct.mean():.0f}']
                        + [f'{g[c].mean():.2f}' for c in COLS])
    story += [Paragraph('Results table', H2),
              tbl(rows, [1.0 * cm, 2.1 * cm, 0.6 * cm, 1.1 * cm, 1.2 * cm] + [1.2 * cm] * 9)]

    try:
        srows = stats_rows(sub_t)
    except ImportError:   # scipy not installed
        srows = []
    if len(srows) > 1:
        story += [Paragraph('Is the difference real? (mean ± 95% confidence interval, Welch t-test)', H2),
                  tbl(srows, [1.2 * cm, 3.6 * cm, 2.9 * cm, 2.6 * cm, 2.9 * cm, 1.7 * cm, 1.6 * cm]),
                  Paragraph('Uncorrected Welch tests with few runs per case (n is shown): treat p-values as '
                            'descriptive, not proof; with many tests some fall below 0.05 by chance. '
                            '"n/a" means no variance or too few runs.', CAP)]
    story.append(Paragraph('Why the results came out this way', H2))
    shown = False
    for sc in ordered_scenarios(sub_t.scenario):
        g = sub_t[sub_t.scenario == sc]
        story.append(KeepTogether([Paragraph(f'{sc}: {scenario_name(sc)}', H2)]
                                  + [Paragraph(t, P) for t in explain(g, sc)]))
        static = g[g.system == 'static']
        if not shown and len(static) and static.peak_util.mean() > threshold_of(g):
            shown = True   # one timeline per topology: the first congested scenario
            fig_timeline(Path(sub_t.src.iloc[0]), topology, sc, systems_in(sub_t.system),
                         fig_dir / f'{topology}_timeline.png')
            story += [Image(str(fig_dir / f'{topology}_timeline.png'), width=16 * cm, height=6.2 * cm),
                      Paragraph(f'Run 0 of {sc}: what each controller did to the attacked link.', CAP)]
    return story


DEFINITIONS = [
        '<b>Loss / latency / throughput:</b> legitimate flows that cross the target link.',
        '<b>Peak load / above threshold:</b> highest utilisation of the target link during the attack, '
        'and the share of controller polls in which it was above the threshold.',
        '<b>Stable recovery:</b> from the first poll above threshold until the load stays under it for the '
        'rest of the attack (0 if never congested; cut off at the attack end if it never settles). '
        '<b>Time above:</b> seconds the target link spent above the threshold.',
        '<b>Reroutes, rule changes:</b> flows moved and switch flow-table writes caused by moving them.',
        '<b>Oscillations:</b> reroutes that put a flow back on a path it already used.',
        '<b>Secondary congestion:</b> other links pushed above the threshold after a reroute moved a flow onto them.']


def build(results, out_pdf=None):
    results = Path(results)
    if not results.is_absolute():
        results = ROOT / results
    out_pdf = Path(out_pdf) if out_pdf else results / 'report.pdf'
    df = load_runs(results)
    fig_dir = results / 'report_figures'
    fig_dir.mkdir(exist_ok=True)
    story = [Paragraph(f'ResilientSDN: results of {results.name}', H1),
             Paragraph(f'{AUTHOR} · {date.today():%d %B %Y}', P),
             Paragraph(f'{len(df)} completed runs: topologies {", ".join(sorted(df.topology.unique()))}; '
                       f'systems {", ".join(LABEL(s) for s in systems_in(df.system))}; scenarios '
                       f'{", ".join(ordered_scenarios(df.scenario))}; up to {df.run.nunique()} run(s) per case. '
                       'Numbers are means over the runs. Load = share of the target link\'s capacity; the '
                       'controllers react when it goes above the threshold.', P)]
    failed = sorted(p.parent.name for p in (results / 'runs').glob('*/error.txt'))
    if failed:
        story.append(Paragraph(f'<b>Failed runs (no result):</b> {", ".join(failed)}', P))

    checks = correctness_checks(results, df)
    story += [Paragraph('Correctness checks: is the model running properly and accurately?', H2),
              tbl(checks, [1.8 * cm, 5.6 * cm, 3.6 * cm, 3.8 * cm, 1.4 * cm]),
              Paragraph('Computed from this run folder (they show the emulator delivered the configured load and the runs are consistent; they do not validate the performance claims). Every line should be PASS; a FAIL means the emulator, '
                        'traffic or a controller did not behave as designed, and the results should not be trusted '
                        'until the cause is found. These checks show the system works; the performance claims need '
                        'the repeated runs of the full evaluation (about 17 to 19 hours of real-time emulation: '
                        '610 runs of about 1.5 minutes, one after another).', CAP)]
    for i, topology in enumerate(sorted(df.topology.unique())):
        story += topology_story(df, topology, fig_dir, first=i == 0)

    story += [PageBreak(), Paragraph('Limits of this study', H1)]
    story += [Paragraph('• ' + t, P) for t in LIMITS]
    story += [Paragraph('Metric definitions', H1)]
    story += [Paragraph(t, P) for t in DEFINITIONS]
    SimpleDocTemplate(str(out_pdf), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                      bottomMargin=1.8 * cm, title=f'ResilientSDN results: {results.name}',
                      author=AUTHOR).build(story)
    return out_pdf


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('results')
    ap.add_argument('--out')
    a = ap.parse_args()
    print(build(a.results, a.out))
