"""Independent check of the key claims: recompute numbers from the RAW run files (iperf3 JSON + controller
events), compare with all_results.csv and run the paired tests. Run from the project folder:
    .venv\Scripts\python -W ignore -m analysis.verify_claims

not from all_results.csv, then compare to the CSV and run the statistical tests."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path('.')
SYS = ['static', 'simple_reactive', 'resilient_sdn']
frames = []
for part in ('fattree_5runs', 'wan_5runs'):
    d = pd.read_csv(ROOT / 'results' / part / 'all_results.csv')
    d['part'] = part
    frames.append(d)
df = pd.concat(frames, ignore_index=True)


def raw(row):
    run = ROOT / 'results' / row.part / 'runs' / f'{row.topology}_{row.system}_{row.scenario}_{row.run}'
    meta = json.loads((run / 'meta.json').read_text())
    losses = []
    for i in range(len(meta['legit'])):
        end = json.loads((run / f'legit_{i}.json').read_text())['end']
        losses.append(end.get('sum_received', end['sum'])['lost_percent'])
    events = [json.loads(line) for line in (run / 'events.jsonl').read_text().splitlines() if line.strip()]
    reroutes = sum(e['kind'] == 'reroute' for e in events)
    tgt, thr = meta['target_link'], meta['threshold']
    st = [(e['t'], e['util'].get(tgt, 0)) for e in events
          if e['kind'] == 'stats' and meta['attack_start'] <= e['t'] <= meta['end']]
    gaps = [b[0] - a[0] for a, b in zip(st, st[1:])]
    typ = sorted(gaps)[len(gaps) // 2] if gaps else 0
    above = sum((gaps[i] if i < len(gaps) else typ) for i, (_, u) in enumerate(st) if u > thr)
    return pd.Series({'raw_loss': float(np.mean(losses)), 'raw_reroutes': reroutes, 'raw_above': above})


print('=== 1. RAW FILES vs CSV (all 390 runs) ===')
r = df.apply(raw, axis=1)
df = pd.concat([df, r], axis=1)
print('loss   max |raw - csv| =', float((df.raw_loss - df.packet_loss_pct).abs().max()))
print('reroutes max |raw - csv| =', float((df.raw_reroutes - df.reroutes).abs().max()))
print('time above max |raw - csv| =', round(float((df.raw_above - df.time_above_s).abs().max()), 6))

print('\n=== 2. OVERLOAD (S6, S11): legitimate loss %, paired by topology/scenario/run (raw values) ===')
ov = df[df.scenario.isin(['S6', 'S11'])]
key = ['topology', 'scenario', 'run']
piv = ov.pivot_table(index=key, columns='system', values='raw_loss')
print(f'runs: {len(piv)}   mean loss: ' + ', '.join(f'{s} {piv[s].mean():.2f}' for s in SYS))
for a in ('static', 'simple_reactive'):
    d = piv[a] - piv['resilient_sdn']
    wins, n = int((d > 0).sum()), int((d != 0).sum())
    print(f'ResilientSDN lower than {a}: {wins}/{n} runs   sign test p={stats.binomtest(wins, n, .5).pvalue:.2g}   '
          f'Wilcoxon p={stats.wilcoxon(piv[a], piv["resilient_sdn"]).pvalue:.2g}   '
          f'paired mean diff={d.mean():.2f} pp (95% CI ±{stats.sem(d) * stats.t.ppf(.975, len(d) - 1):.2f})')
print('per topology, mean loss static / reactive / ResilientSDN:')
for t, g in ov.groupby('topology'):
    print(f'  {t:10s}', ' / '.join(f'{g[g.system == s].raw_loss.mean():5.2f}' for s in SYS))

print('\n=== 3. STABILITY: reroutes per run in congested cases (raw event logs) ===')
st = df[df.system == 'static'].groupby(['topology', 'scenario']).raw_above.mean()
cong = set(st[st > 5].index)
c = df[[(t, s) in cong for t, s in zip(df.topology, df.scenario)]]
for s in SYS:
    print(f'  {s:16s} reroutes/run {c[c.system == s].raw_reroutes.mean():7.1f}   median time above threshold {c[c.system == s].raw_above.median():5.0f} s')
p = c.pivot_table(index=key, columns='system', values='raw_reroutes')
print(f'  ResilientSDN made fewer reroutes than simple reactive in {int((p.resilient_sdn < p.simple_reactive).sum())}/{len(p)} runs')

print('\n=== 4. WHERE IT FAILS (raw): runs where ResilientSDN did NOT relieve the link ===')
tot = bad = 0
for (t, sc), g in c.groupby(['topology', 'scenario']):
    thr = 0.5 * g[g.system == 'static'].raw_above.mean()
    r_ = g[g.system == 'resilient_sdn']
    k = int((r_.raw_above >= thr).sum())
    tot += len(r_)
    bad += k
    if k:
        print(f'  {t:10s} {sc:4s} not relieved in {k}/{len(r_)} runs')
print(f'  overall: relieved in {tot - bad}/{tot} runs, failed in {bad}')
print('  fat_tree S7 reroutes by ResilientSDN:', df[(df.topology == 'fat_tree') & (df.scenario == 'S7') & (df.system == 'resilient_sdn')].raw_reroutes.tolist())
print('  nsfnet S9 loss static vs ResilientSDN per run:',
      df[(df.topology == 'nsfnet') & (df.scenario == 'S9') & (df.system == 'static')].raw_loss.round(2).tolist(), 'vs',
      df[(df.topology == 'nsfnet') & (df.scenario == 'S9') & (df.system == 'resilient_sdn')].raw_loss.round(2).tolist())

print('\n=== 5. ABLATION B (hysteresis), S3, 10 runs each ===')
b = pd.read_csv(ROOT / 'results' / 'ablation_B' / 'all_results.csv')
b3 = b[b.scenario == 'S3']
x, y = b3[b3.system == 'rsdn_no_hysteresis'].oscillations, b3[b3.system == 'resilient_sdn'].oscillations
print(f'  oscillations/run: no hysteresis {x.mean():.1f}, full {y.mean():.2f}; Welch p={stats.ttest_ind(x, y, equal_var=False).pvalue:.2g}; '
      f'Mann-Whitney p={stats.mannwhitneyu(x, y).pvalue:.2g}')
