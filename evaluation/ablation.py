"""Ablation study A-E: each experiment compares variants that differ in one
mechanism. Run inside the container:

    python3 -m evaluation.ablation A B --topology fat_tree --runs 30

Results: <out>/ablation_<X>/all_results.csv (same columns as experiment.py).
"""
import argparse

from evaluation.experiment import VARIANTS, ExperimentRunner, make_report

VARIANTS.update({
    # capacity filter is the only mechanism left on
    'rsdn_capacity_only': ('resilient_sdn', {'ablation': {
        'hysteresis': False, 'scoring': 'shortest', 'trend_trigger': False}}),
    'rsdn_no_hysteresis': ('resilient_sdn', {'ablation': {'hysteresis': False}}),
    'rsdn_shortest': ('resilient_sdn', {'ablation': {'scoring': 'shortest'}}),
    'rsdn_single_threshold': ('resilient_sdn', {'ablation': {'trend_trigger': False}}),
})

EXPERIMENTS = {
    # Does capacity verification prevent secondary congestion?
    'A': {'systems': ['simple_reactive', 'rsdn_capacity_only'],
          'scenarios': ['S3', 'S4', 'S5', 'S11']},   # S11 = 2 min overload (S6 lasts 10 min)
    # Does hysteresis reduce oscillation? (S7/S8 try to provoke it)
    'B': {'systems': ['rsdn_no_hysteresis', 'resilient_sdn'],
          'scenarios': ['S3', 'S7', 'S8']},
    # Does multi-objective scoring outperform shortest alternate path?
    'C': {'systems': ['rsdn_shortest', 'resilient_sdn'],
          'scenarios': ['S3', 'S4', 'S5']},
    # Does the trend-based trigger reduce false positives? S1 + bursty traffic
    'D': {'systems': ['rsdn_single_threshold', 'resilient_sdn'],
          'scenarios': ['S1'], 'bursty': True},
    # Does the full system outperform all baselines? (all scenarios)
    'E': {'systems': None, 'scenarios': None},
}


class AblationStudy:
    def __init__(self, topology='fat_tree', runs=None, out='results'):
        self.topology, self.runs, self.out = topology, runs, out

    def run(self, key):
        e = EXPERIMENTS[key]
        runner = ExperimentRunner(out=f'{self.out}/ablation_{key}')
        csv_path = runner.run_all([self.topology], e['systems'], e['scenarios'],
                                  self.runs, e.get('bursty', False))
        make_report(runner.out)
        return csv_path

    def run_experiment_A(self):
        return self.run('A')

    def run_experiment_B(self):
        return self.run('B')

    def run_experiment_C(self):
        return self.run('C')

    def run_experiment_D(self):
        return self.run('D')

    def run_experiment_E(self):
        return self.run('E')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('experiments', nargs='+', choices=sorted(EXPERIMENTS))
    p.add_argument('--topology', default='fat_tree')
    p.add_argument('--runs', type=int)
    p.add_argument('--out', default='results')
    a = p.parse_args()
    study = AblationStudy(a.topology, a.runs, a.out)
    for key in a.experiments:
        print(key, study.run(key))


if __name__ == '__main__':
    main()
