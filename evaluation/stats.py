"""Mean ± 95% CI per (system, scenario, topology, metric), plus Welch's t-test
against simple_reactive. Run on Windows (needs scipy):

    .venv\\Scripts\\python -m evaluation.stats results\\<experiment>\\all_results.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

METRICS = ['throughput_mbps', 'packet_loss_pct', 'latency_ms',
           'recovery_time_s', 'time_above_s', 'stable_recovery_s', 'secondary_congestion',
           'rule_changes', 'oscillations', 'controller_cpu_pct']
REFERENCE = 'simple_reactive'


class StatisticalAnalysis:
    def confidence_interval(self, data, confidence=0.95):
        data = np.asarray(data, dtype=float)
        data = data[~np.isnan(data)]
        n = len(data)
        if n == 0:
            return np.nan, np.nan
        mean = data.mean()
        if n < 2:
            return mean, np.nan
        ci = stats.sem(data) * stats.t.ppf((1 + confidence) / 2, n - 1)
        return mean, ci

    def welch_t_test(self, group1, group2):
        """Compare two systems. Returns t-statistic and p-value."""
        g1 = pd.Series(group1, dtype=float).dropna()
        g2 = pd.Series(group2, dtype=float).dropna()
        if len(g1) < 2 or len(g2) < 2:
            return np.nan, np.nan
        t, p = stats.ttest_ind(g1, g2, equal_var=False)
        return t, p

    def compute_all_intervals(self, results_csv, out_csv=None):
        results_csv = Path(results_csv)
        df = pd.read_csv(results_csv)
        summary = []
        for (topology, scenario), group in df.groupby(['topology', 'scenario']):
            ref = group[group.system == REFERENCE]
            for system, subset in group.groupby('system'):
                for metric in METRICS:
                    mean, ci = self.confidence_interval(subset[metric])
                    t, p = (self.welch_t_test(subset[metric], ref[metric])
                            if system != REFERENCE and len(ref) else (np.nan, np.nan))
                    summary.append({'system': system, 'scenario': scenario,
                                    'topology': topology, 'metric': metric,
                                    'n': int(subset[metric].notna().sum()),
                                    'mean': mean, 'ci_95': ci,
                                    't_vs_simple_reactive': t,
                                    'p_vs_simple_reactive': p})
        out_csv = out_csv or results_csv.with_name('summary.csv')
        pd.DataFrame(summary).to_csv(out_csv, index=False)
        return out_csv


if __name__ == '__main__':
    print(StatisticalAnalysis().compute_all_intervals(sys.argv[1]))
