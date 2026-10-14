"""IEEE-style LaTeX tables (booktabs) from summary.csv: one table per
topology, rows = scenario x system, columns = mean ± 95% CI per metric.
The best mean per scenario is bold; † marks p < 0.05 vs simple_reactive.

    .venv\\Scripts\\python -m analysis.latex_tables results\\<experiment>\\summary.csv
"""
import sys
from pathlib import Path

import pandas as pd

from analysis.style import SYSTEM_LABELS, systems_in
from attack.scenarios import SCENARIOS

COLUMNS = [  # metric, header, higher is better
    ('throughput_mbps', 'Thr. (Mbps)', True),
    ('packet_loss_pct', 'Loss (\\%)', False),
    ('latency_ms', 'Lat. (ms)', False),
    ('recovery_time_s', 'Rec. (s)', False),
    ('secondary_congestion', 'Sec. cong.', False),
    ('rule_changes', 'Rules', False),
    ('oscillations', 'Osc.', False),
]


def cell(row, best):
    if row is None or pd.isna(row['mean']):
        return '--'
    text = f"{row['mean']:.2f}"
    if not pd.isna(row['ci_95']):
        text += f" $\\pm$ {row['ci_95']:.2f}"
    if best:
        text = f'\\textbf{{{text}}}'
    p = row.get('p_vs_simple_reactive')
    if p is not None and not pd.isna(p) and p < 0.05:
        text += '$^\\dagger$'
    return text


def table(summary, topology):
    s = summary[summary.topology == topology]
    lines = ['\\begin{table*}[t]', '\\centering',
             f'\\caption{{Results on {topology.replace("_", "-")} '
             '(mean $\\pm$ 95\\% CI; $^\\dagger$ $p<0.05$ vs.\\ simple reactive)}',
             f'\\label{{tab:results_{topology}}}',
             '\\begin{tabular}{ll' + 'c' * len(COLUMNS) + '}', '\\toprule',
             'Scen. & System & ' + ' & '.join(h for _, h, _ in COLUMNS) + ' \\\\',
             '\\midrule']
    for scenario in [x for x in SCENARIOS if x in set(s.scenario)]:
        sc = s[s.scenario == scenario]
        best = {}  # {metric: systems tied for the best mean}; none if all equal
        for metric, _, higher in COLUMNS:
            means = sc[sc.metric == metric].set_index('system')['mean'].dropna().round(2)
            if means.nunique() > 1:
                top = means.max() if higher else means.min()
                best[metric] = set(means[means == top].index)
        for i, system in enumerate(systems_in(sc.system)):
            cells = []
            for metric, _, _ in COLUMNS:
                r = sc[(sc.system == system) & (sc.metric == metric)]
                cells.append(cell(r.iloc[0] if len(r) else None, system in best.get(metric, ())))
            label = scenario if i == 0 else ''
            lines.append(f'{label} & {SYSTEM_LABELS.get(system, system)} & '
                         + ' & '.join(cells) + ' \\\\')
        lines.append('\\midrule')
    lines[-1] = '\\bottomrule'
    lines += ['\\end{tabular}', '\\end{table*}']
    return '\n'.join(lines)


def main():
    summary_csv = Path(sys.argv[1])
    summary = pd.read_csv(summary_csv)
    out = summary_csv.with_name('tables.tex')
    out.write_text('% requires \\usepackage{booktabs}\n\n'
                   + '\n\n'.join(table(summary, t) for t in sorted(summary.topology.unique())))
    print(out)


if __name__ == '__main__':
    main()
