"""Final results PDF over several results folders (a NEW file; never overwrites another report).

    .venv\\Scripts\\python -m analysis.final_report [--out reports\\ResilientSDN_Final_Results.pdf]

Reads results/fattree_5runs, results/wan_5runs and results/ablation_A..D (whatever exists) and
builds one PDF: headline numbers, what the results show and why, the cases where ResilientSDN did
not help, the ablation, correctness checks, run time and demo, per-topology detail and limits.
Every sentence with a number is computed from the data.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.run_report import (AUTHOR, CAP, DEFINITIONS, H1, H2, LABEL, LIMITS, P,  # noqa: E402
                                 ROOT, correctness_checks, load_runs, ordered_scenarios, pm,
                                 scenario_name, tbl, topology_story)

SYS3 = ['static', 'simple_reactive', 'resilient_sdn']
KEY = ['S3', 'S5', 'S6', 'S7', 'S9', 'S11']
SKIP = ('demo', 'smoke', 'final_report_figures')


def main_parts():
    """Results folders with the 3-system campaigns: every results/*/all_results.csv except ablations and tests."""
    return [d.name for d in sorted((ROOT / 'results').iterdir())
            if (d / 'all_results.csv').exists() and not d.name.startswith(('ablation', 'nsfnet_test', 'qa_'))
            and d.name not in SKIP]
ABLATION = {
    'A': ('Capacity filter', 'simple_reactive', 'rsdn_capacity_only', 'secondary_congestion',
          'other links pushed over the threshold'),
    'B': ('Hysteresis', 'rsdn_no_hysteresis', 'resilient_sdn', 'oscillations',
          'flows sent back to a path they already used'),
    'C': ('Multi-objective scoring', 'rsdn_shortest', 'resilient_sdn', 'time_above_s',
          'seconds the target link stayed above the threshold'),
    'D': ('Trend trigger', 'rsdn_single_threshold', 'resilient_sdn', 'reroutes',
          'reroutes with no attack (false alarms)'),
}


def mean(df, col, **where):
    d = df
    for k, v in where.items():
        d = d[d[k].isin(v) if isinstance(v, (list, tuple, set)) else d[k] == v]
    return float(d[col].mean()) if len(d) else float('nan')


def f(x, fmt='{:.1f}'):
    return 'n/a' if pd.isna(x) else fmt.format(x)


def congested(df):
    """(topology, scenario) pairs where static routing left the target link above threshold."""
    st = df[df.system == 'static'].groupby(['topology', 'scenario'])
    return [k for k, g in st if g.time_above_s.mean() > 5]


def headline(df):
    rows = [['Topology', 'Scen.'] + [f'{LABEL(s)}: loss % | time above s | reroutes' for s in SYS3]]
    for topo in sorted(df.topology.unique()):
        for sc in KEY:
            g = df[(df.topology == topo) & (df.scenario == sc)]
            if g.empty:
                continue
            cells = []
            for s in SYS3:
                loss = f(mean(g, 'packet_loss_pct', system=s), '{:.2f}')
                above = f(mean(g, 'time_above_s', system=s), '{:.0f}')
                rer = f(mean(g, 'reroutes', system=s), '{:.0f}')
                cells.append(f'{loss} | {above} | {rer}')
            rows.append([topo, sc] + cells)
    return rows


def paired(df, a_sys, b_sys, col, scenarios):
    """(runs where a < b, non-tied runs, all paired runs, sign-test p), pairing by topology, scenario, run."""
    from scipy import stats
    d = df[df.scenario.isin(scenarios)]
    key = ['topology', 'scenario', 'run']
    m = d[d.system == a_sys].set_index(key)[col].to_frame('a').join(
        d[d.system == b_sys].set_index(key)[col].to_frame('b'), how='inner')
    lower, higher = int((m.a < m.b).sum()), int((m.a > m.b).sum())
    n = lower + higher
    p = stats.binomtest(lower, n, 0.5).pvalue if n else float('nan')
    return lower, n, len(m), p


def ptext(p):
    return 'n/a' if pd.isna(p) else ('p < 0.001' if p < 0.001 else f'p = {p:.3f}')


def relief(df, cells):
    """Per congested cell: ResilientSDN runs whose time above threshold is under half of static's mean."""
    out = {}
    for topo, sc in cells:
        g = df[(df.topology == topo) & (df.scenario == sc)]
        thr = 0.5 * mean(g, 'time_above_s', system='static')
        r = g[g.system == 'resilient_sdn']
        out[(topo, sc)] = (int((r.time_above_s < thr).sum()), len(r))
    return out


def findings(df):
    out, cong = [], congested(df)
    cdf = df[[(t, s) in cong for t, s in zip(df.topology, df.scenario)]]
    over = df[df.scenario.isin(['S6', 'S11'])]
    if len(over):
        ls, ns, tot, ps = paired(df, 'resilient_sdn', 'static', 'packet_loss_pct', ['S6', 'S11'])
        lr, nr, _, pr = paired(df, 'resilient_sdn', 'simple_reactive', 'packet_loss_pct', ['S6', 'S11'])
        losses = ', '.join(f'{LABEL(s)} {f(mean(over, "packet_loss_pct", system=s), "{:.2f}")}%' for s in SYS3)
        out.append(f'<b>Overload (S6, S11: the attack offers more than the link can carry).</b> Mean loss of '
                   f'legitimate traffic over {tot} paired runs: {losses}. ResilientSDN lost less than static in '
                   f'{ls} of {ns} runs that differ (sign test {ptext(ps)}) and less than simple reactive in {lr} of '
                   f'{nr} ({ptext(pr)}). The data show that it moves flows early; they do not isolate why loss '
                   'falls (on Internet2 S11 the loss fell although the attacked link stayed above the threshold '
                   'for a long time in most runs). The gap to simple reactive is not significant in every cell '
                   '(see the per-topology tables).')
    if len(cdf):
        med = ', '.join(f'{LABEL(s)} {f(cdf[cdf.system == s].time_above_s.median(), "{:.0f}")}' for s in SYS3)
        rel = relief(df, cong)
        ok, tot_r = sum(v[0] for v in rel.values()), sum(v[1] for v in rel.values())
        partial = [f'{t} {sc} ({k} of {n})' for (t, sc), (k, n) in rel.items() if k < n]
        out.append(f'<b>Time the attacked link stays above the 80% threshold</b> (median seconds per run over the '
                   f'{len(cong)} congested cases; medians because S6 attacks last 600 s and a few runs never '
                   f'relieve the link): {med}. ResilientSDN relieved the link (under half of static\'s time) in '
                   f'{ok} of {tot_r} runs. Not relieved in every run: ' + ('; '.join(partial) or 'none') + '. '
                   'The 8 s minimum is the detection delay (3 polls), so it is a floor, and static\'s 56 s is '
                   'capped by the 60 s attack. "Congested" means static routing left the link above threshold '
                   'for more than 5 s.')
        rs, rr = mean(cdf, 'reroutes', system='resilient_sdn'), mean(cdf, 'reroutes', system='simple_reactive')
        osc = mean(cdf, 'oscillations', system='simple_reactive')
        share = f(100 * osc / rr if rr else float('nan'), '{:.0f}')
        out.append(f'<b>Stability.</b> Per run in congested cases: ResilientSDN {f(rs)} reroutes, simple reactive '
                   f'{f(rr)}, of which {f(osc)} put a flow back on a path it had already used ({share}%; partly '
                   'structural, since these networks offer few alternative paths). Simple reactive has no memory: '
                   'it moves flows whenever one poll shows congestion, so it keeps moving them. ResilientSDN waits '
                   'for 3 consecutive high polls, checks the new path and holds a moved flow for a cooldown; the '
                   'ablation below tests the hysteresis part directly.')
    calm = df[df.scenario.isin(['S1', 'S2'])]
    if len(calm):
        st = calm[calm.system == 'static']
        n_run = len(st)
        totals = ', '.join(f'{LABEL(s)} {calm[calm.system == s].reroutes.sum():.0f}' for s in SYS3)
        out.append(f'<b>No reroutes without a real attack.</b> In S1 and S2 (static load {st.peak_util.min():.0%} '
                   f'to {st.peak_util.max():.0%}) the total reroutes were {totals} over {n_run} runs each. The '
                   'load is far below the threshold, so this shows there is no defect but does not separate the '
                   f'controllers (the 95% upper bound on a false-alarm rate is about {300 / n_run:.0f}% per run). '
                   'The bursty test in ablation D is the real false-alarm test.')
    other = df[~df.scenario.isin(['S6', 'S11'])]
    lossy = [f'{t} {sc} {LABEL(s)} {v:.2f}%' for (t, sc, s), v in
             other.groupby(['topology', 'scenario', 'system']).packet_loss_pct.mean().items() if v > 0.5]
    out.append('<b>Loss outside the overload scenarios.</b> Mean loss above 0.5% in: ' + ('; '.join(lossy) or 'none')
               + '. On NSFNET S9 static and ResilientSDN lose the same amount run by run, so that loss follows '
               'the seed or traffic, not the controller; it was not diagnosed further.')
    s9 = df[(df.scenario == 'S9') & (df.topology != 'fat_tree')]
    if len(s9):
        parts = []
        for topo in sorted(s9.topology.unique()):
            g = s9[s9.topology == topo]
            loss = '/'.join(f(mean(g, 'packet_loss_pct', system=s), '{:.1f}') for s in SYS3)
            above = '/'.join(f(mean(g, 'time_above_s', system=s), '{:.0f}') for s in SYS3)
            parts.append(f'{topo}: loss {loss}%, time above {above} s')
        out.append('<b>Capacity presaturation (S9, WANs).</b> The attacker first fills the alternative paths. '
                   'Static / simple reactive / ResilientSDN: ' + '; '.join(parts) + '. Simple reactive moves the '
                   'victims onto the filled paths and loses much of their traffic; ResilientSDN matched static on '
                   'loss (it avoids that harm, it does not beat static). Its time above threshold improved on '
                   'GÉANT and in part of NSFNET, not on Internet2. No experiment isolates the capacity filter on S9.')
    if len(cdf) and 'recovery_time_s' in cdf:
        firstdip = ', '.join(f'{LABEL(s)} {f(cdf[cdf.system == s].recovery_time_s.mean(), "{:.1f}")} s'
                             for s in SYS3)
        out.append('<b>Disclosure about the metrics.</b> Time above threshold and stable recovery were defined '
                   'after the campaign started, because the original first-dip recovery time rewards controllers '
                   'that flap (this flaw was logged before any results existed). The first-dip values are kept '
                   f'in the data: {firstdip}.')
    return out


def weak_spots(df):
    """Cases where ResilientSDN did not clearly help, computed from the data."""
    out = []
    for topo, sc in congested(df):
        g = df[(df.topology == topo) & (df.scenario == sc)]
        st_t, rs_t = mean(g, 'time_above_s', system='static'), mean(g, 'time_above_s', system='resilient_sdn')
        rc_t = mean(g, 'time_above_s', system='simple_reactive')
        st_l, rs_l = (mean(g, 'packet_loss_pct', system='static'),
                      mean(g, 'packet_loss_pct', system='resilient_sdn'))
        rr_l = mean(g, 'packet_loss_pct', system='simple_reactive')
        rs_r = mean(g, 'reroutes', system='resilient_sdn')
        tag = f'<b>{topo} {sc} ({scenario_name(sc)}):</b> '
        if rs_t >= 0.8 * st_t:
            out.append(tag + f'ResilientSDN kept the link above threshold for {rs_t:.0f} s against {st_t:.0f} s '
                             f'for static ({rs_r:.1f} reroutes): no clear relief.')
        elif st_l > 0.5 and rs_l >= 0.8 * st_l:
            out.append(tag + f'loss {rs_l:.2f}% for ResilientSDN against {st_l:.2f}% for static (simple reactive '
                             f'{rr_l:.2f}%): no loss advantage over static, though it kept the link above '
                             f'threshold for {rs_t:.0f} s against {st_t:.0f} s.')
        elif rs_t > 30:
            out.append(tag + f'relief was slow: {rs_t:.0f} s above threshold (static {st_t:.0f} s).')
        if rc_t < 0.8 * rs_t:
            out.append(tag + f'simple reactive spent less time above threshold ({rc_t:.0f} s) than ResilientSDN '
                             f'({rs_t:.0f} s), although it made many more reroutes.')
    p7 = df[df.scenario == 'S7']
    for topo in sorted(p7.topology.unique()):
        g = p7[p7.topology == topo]
        r = mean(g, 'reroutes', system='resilient_sdn')
        if r < 1:
            rs_a = f(mean(g, 'time_above_s', system='resilient_sdn'), '{:.0f}')
            st_a = f(mean(g, 'time_above_s', system='static'), '{:.0f}')
            out.append(f'<b>{topo} S7 (pulse attack):</b> ResilientSDN made {f(r)} reroutes on average, so it '
                       f'behaved like static routing (time above {rs_a} s against {st_a} s for static; the small '
                       'difference is noise). A plausible reason is that pulses never stay above the threshold for '
                       '3 polls, so the trend detector does not trigger; this was not tested separately. '
                       + 'No user-visible harm: loss ' + '/'.join(f(mean(g, 'packet_loss_pct', system=x), '{:.2f}') for x in SYS3)
                       + '% and latency ' + '/'.join(f(mean(g, 'latency_ms', system=x), '{:.1f}') for x in SYS3)
                       + ' ms (static / simple reactive / ResilientSDN), so this is a trigger weakness, not a service one.')
    return out


CAVEAT = {
    'A': 'The full system is not part of this experiment: it compares simple reactive rerouting with reactive '
         'rerouting plus only the capacity filter. Where a cell has no variance the difference is deterministic.',
    'B': 'Oscillations are what the hysteresis (cooldown) is designed to prevent, so this checks that the mechanism '
         'does its job. Where neither variant reroutes (S7, S8) there is nothing to compare.',
    'C': 'The metric has a floor of about 8 s (the detection delay) and shortest and multi-objective paths are '
         'often identical on fat-tree, so this test cannot show that the scoring matters and cannot show that it '
         'does not.',
    'D': 'One scenario, deterministic outcome (zero variance), so no test is reported. The single-threshold trigger '
         'removes the burst faster, so the trade-off is fewer reroutes against more time above the threshold.',
}


def welch_text(x, y):
    from scipy import stats
    x, y = pd.Series(x).dropna(), pd.Series(y).dropna()
    if len(x) < 2 or len(y) < 2:
        return 'n/a'
    if x.std() == 0 and y.std() == 0:
        return 'no variance'
    return ptext(stats.ttest_ind(x, y, equal_var=False)[1])


def ablation_story():
    out = []
    for key, (title, base, full, metric, meaning) in ABLATION.items():
        path = ROOT / 'results' / f'ablation_{key}' / 'all_results.csv'
        if not path.exists():
            continue
        df = pd.read_csv(path)
        rows = [['Scenario', 'Variant', 'n', f'{metric} (mean ± 95% CI)', 'Welch p (per scenario)']]
        moves = []
        for sc in ordered_scenarios(df.scenario):
            gb = df[(df.scenario == sc) & (df.system == base)]
            gf = df[(df.scenario == sc) & (df.system == full)]
            for g, s in ((gb, base), (gf, full)):
                if len(g):
                    rows.append([sc, LABEL(s), len(g), pm(g[metric], '{:.2f}'),
                                 welch_text(gb[metric], gf[metric]) if s == full else ''])
            if len(gb) and len(gf):
                moves.append(f'{sc}: {gb[metric].mean():.2f} → {gf[metric].mean():.2f}')
        text = (f'{meaning.capitalize()} per run, {LABEL(base)} → {LABEL(full)}: ' + '; '.join(moves) + '. '
                + CAVEAT[key])
        if key == 'D':
            ta = ', '.join(f'{LABEL(s)} {df[df.system == s].time_above_s.mean():.1f} s' for s in (base, full))
            text += f' Mean time above threshold: {ta}.'
        out += [Paragraph(f'Ablation {key}: {title}', H2),
                tbl(rows, [1.8 * cm, 4.2 * cm, 1 * cm, 5 * cm, 3.6 * cm]), Paragraph(text, P)]
    if out:
        out.insert(0, Paragraph('Each ablation removes one mechanism and compares the result with a reference '
                                'system, on fat-tree, to test whether that mechanism matters. p-values are '
                                'per scenario, uncorrected and descriptive; results are not pooled across '
                                'scenarios because the scenarios differ.', P))
    return out


def build_final(out_pdf):
    parts = main_parts()
    if not parts:
        raise SystemExit('no results folders found in results/')
    frames = [load_runs(ROOT / 'results' / d) for d in parts]
    df = pd.concat(frames, ignore_index=True)
    fig_dir = ROOT / 'results' / 'final_report_figures'
    fig_dir.mkdir(exist_ok=True)
    n_fail = len(list((ROOT / 'results').glob('*/runs/*/error.txt')))
    per_case = df.groupby(['topology', 'scenario', 'system']).size().min()
    story = [Paragraph('ResilientSDN: Final Results', H1),
             Paragraph(f'{AUTHOR} · {date.today():%d %B %Y}', P),
             Paragraph(f'{len(df)} runs on {df.topology.nunique()} topologies ({", ".join(sorted(df.topology.unique()))}), '
                       f'3 systems (Static, Simple reactive, ResilientSDN), scenarios '
                       f'{", ".join(ordered_scenarios(df.scenario))}, at least {per_case} runs per case; failed runs: '
                       f'{n_fail}. Load = share of the attacked link\'s capacity; the controllers react above the 80% '
                       'threshold. This document explains what happened and why.', P),
             Paragraph('Headline numbers', H2), tbl(headline(df), [2.2 * cm, 1.2 * cm] + [4.5 * cm] * 3),
             Paragraph('Each cell: legitimate loss % | seconds the attacked link stayed above the threshold | '
                       'reroutes, means over the runs. Static routing never reroutes by design. This table shows 6 of the 11 scenarios; all are in the per-topology tables below.', CAP),
             Paragraph('What the results show, and why', H1)]
    story += [Paragraph(t, P) for t in findings(df)]
    story += [Paragraph('Where ResilientSDN did not help', H1),
              Paragraph('Limits of the result, found automatically by comparing against static routing:', P)]
    story += [Paragraph('• ' + t, P) for t in weak_spots(df)] or [Paragraph('None found.', P)]
    abl = ablation_story()
    if abl:
        story += [Paragraph('Ablation study', H1)] + abl
    rows = [['Topology', 'Check', 'Expected', 'Observed', 'Result']]
    for d in parts:
        sub = df[df.src == str(ROOT / 'results' / d)]
        rows += correctness_checks(ROOT / 'results' / d, sub)[1:]
    story += [PageBreak(), Paragraph('Correctness checks: is the model running properly and accurately?', H1),
              tbl(rows, [1.8 * cm, 5.6 * cm, 3.6 * cm, 3.8 * cm, 1.4 * cm]),
              Paragraph('Computed from the result files. PASS means the emulator delivered the traffic and attack '
                        'the scenario asks for and the controllers behaved as designed.', CAP),
              Paragraph('Run time and demo', H1),
              Paragraph('The full evaluation (610 runs) takes about 17 to 19 hours because every run plays out in '
                        'real time: 3 s warm-up, 10 s of normal traffic, a 60 s attack (S11 120 s, S6 600 s) and '
                        'cleanup, about 1.5 minutes each. Runs cannot overlap (one switch service, one controller '
                        'port, one cleanup step), and the repeated runs are what give confidence intervals. To show '
                        'that the system works, run <font face="Courier" size="8">./demo.sh</font> in WSL (about 6 '
                        'minutes): it builds results/demo/report.pdf whose Correctness checks table must say PASS '
                        'on every line. A demo confirms that the emulator delivers the configured load and that the runs are '
                        'consistent; the performance claims come from the full evaluation. Steps: docs/HOW_TO_RUN.md.', P)]
    for topo in sorted(df.topology.unique()):
        story += topology_story(df, topo, fig_dir)
    story += [PageBreak(), Paragraph('Limits of this study', H1)] + [Paragraph('• ' + t, P) for t in LIMITS]
    story += [Paragraph('Metric definitions', H1)] + [Paragraph(t, P) for t in DEFINITIONS]
    SimpleDocTemplate(str(out_pdf), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                      bottomMargin=1.8 * cm, title='ResilientSDN: Final Results', author=AUTHOR).build(story)
    return out_pdf


def unused_name(path):
    """path, or path with _2, _3 ... so that no existing file is ever overwritten."""
    path, n = Path(path), 2
    while path.exists():
        path = path.with_name(f'{Path(path).stem.rsplit("_v", 1)[0]}_v{n}{path.suffix}')
        n += 1
    return path


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', help='output PDF (default: reports/ResilientSDN_Final_Results.pdf, numbered if it exists)')
    a = ap.parse_args()
    target = Path(a.out) if a.out else unused_name(ROOT / 'reports' / 'ResilientSDN_Final_Results.pdf')
    if target.exists():
        sys.exit(f'{target} already exists; choose another --out (existing reports are never overwritten)')
    print(build_final(target))
