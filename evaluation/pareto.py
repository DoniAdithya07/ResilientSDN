"""Pareto analysis of ResilientSDN's path choices.

Every rerouting decision is logged with each candidate's objectives
[C, L, K] (capacity headroom and latency score: higher is better; rule cost:
lower is better) and the index the weighted score selected. The hit rate is
the share of decisions where the selected path is Pareto-optimal.

    python -m evaluation.pareto results/<experiment>
"""
import json
import sys
from pathlib import Path

from evaluation.metrics import read_events


def dominates(p, q) -> bool:
    """p is at least as good as q on every objective and better on one."""
    at_least = p[0] >= q[0] and p[1] >= q[1] and p[2] <= q[2]
    better = p[0] > q[0] or p[1] > q[1] or p[2] < q[2]
    return at_least and better


class ParetoAnalysis:
    def compute_pareto_front(self, objectives) -> list:
        """Indices of candidates that no other candidate dominates."""
        return [i for i, p in enumerate(objectives)
                if not any(dominates(q, p) for j, q in enumerate(objectives) if j != i)]

    def scalarisation_hit_rate(self, instances) -> float:
        """instances: [{'objectives': [[C, L, K], ...], 'selected': index}]"""
        if not instances:
            return float('nan')
        hits = sum(i['selected'] in self.compute_pareto_front(i['objectives'])
                   for i in instances)
        return hits / len(instances)

    def instances_from_results(self, results_dir) -> list:
        return [e for run in sorted(Path(results_dir).glob('runs/*'))
                for e in read_events(run) if e['kind'] == 'decision']


def main():
    results_dir = Path(sys.argv[1])
    pa = ParetoAnalysis()
    instances = pa.instances_from_results(results_dir)
    summary = {'decisions': len(instances),
               'hit_rate': pa.scalarisation_hit_rate(instances),
               'mean_candidates': (sum(len(i['objectives']) for i in instances)
                                   / len(instances)) if instances else 0}
    (results_dir / 'pareto.json').write_text(json.dumps(summary, indent=1))
    print(summary)


if __name__ == '__main__':
    main()
