"""Recompute every metric of a results folder from its saved run files (no re-run).
Use after metrics.py changes: the old CSV is kept as all_results.before_recompute.csv.

    .venv\Scripts\python -m evaluation.recompute results\<experiment>
"""
import csv
import shutil
import sys
from pathlib import Path

from evaluation.metrics import FIELDS, MetricsCollector, compute_run_metrics


def recompute(results):
    results = Path(results)
    csv_path = results / 'all_results.csv'
    with open(csv_path, newline='') as f:
        rows = list(csv.DictReader(f))
    shutil.copy(csv_path, results / 'all_results.before_recompute.csv')
    mc = MetricsCollector()
    for r in rows:
        run_dir = results / 'runs' / f"{r['topology']}_{r['system']}_{r['scenario']}_{r['run']}"
        keys = {k: r[k] for k in ('timestamp', 'system', 'scenario', 'topology', 'run')}
        mc.record(**keys, **compute_run_metrics(run_dir))
    mc.to_csv(csv_path)
    return len(mc.data), FIELDS


if __name__ == '__main__':
    print(recompute(sys.argv[1])[0], 'rows recomputed')
