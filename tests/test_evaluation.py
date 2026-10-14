import json

import numpy as np
import pandas as pd
import pytest

from evaluation import metrics
from evaluation.pareto import ParetoAnalysis, dominates
from evaluation.stats import METRICS, StatisticalAnalysis


def stats_event(t, util):
    return {'t': t, 'kind': 'stats', 'util': util}


def test_recovery_time():
    ev = [stats_event(10, {'a-b': 0.5}), stats_event(12, {'a-b': 0.9}),
          stats_event(14, {'a-b': 0.95}), stats_event(16, {'a-b': 0.6})]
    assert metrics.recovery(ev, 'a-b', 0.8, 10, 30) == (4, True)
    assert metrics.recovery(ev[:3], 'a-b', 0.8, 10, 30) == (18, False)  # censored
    assert metrics.recovery(ev[:1], 'a-b', 0.8, 10, 30) == (0.0, True)


def test_secondary_congestion_only_counts_links_flows_were_moved_onto():
    ev = [stats_event(1, {'x-y': 0.9}),  # hot before any reroute
          {'t': 2, 'kind': 'reroute', 'flow': 'f', 'old': ['a', 'b'], 'new': ['a', 'x', 'y']},
          stats_event(3, {'a-x': 0.95, 'x-y': 0.5, 'a-b': 0.99, 'q-r': 0.99})]
    # a-x counts; x-y was hot only before the move; a-b is the target; q-r untouched
    assert metrics.secondary_congestion(ev, 'a-b', 0.8) == 1


def test_oscillations():
    ev = [{'kind': 'new_flow', 'flow': 'f', 'path': ['a', 'b']},
          {'kind': 'reroute', 'flow': 'f', 'old': ['a', 'b'], 'new': ['a', 'c']},
          {'kind': 'reroute', 'flow': 'f', 'old': ['a', 'c'], 'new': ['a', 'b']}]
    assert metrics.oscillations(ev) == 1


def test_iperf_and_ping_parsing(tmp_path):
    (tmp_path / 'f.json').write_text(json.dumps(
        {'end': {'sum': {'bits_per_second': 10e6, 'lost_percent': 5.0},
                 'sum_received': {'bits_per_second': 9.5e6, 'lost_percent': 5.0}}}))
    assert metrics.iperf_result(tmp_path / 'f.json') == (9.5, 5.0)  # receiver side
    assert metrics.iperf_result(tmp_path / 'missing.json') == (None, None)
    (tmp_path / 'p.txt').write_text('rtt min/avg/max/mdev = 1.0/2.5/4.0/0.3 ms\n')
    assert metrics.ping_avg_ms(tmp_path / 'p.txt') == 2.5


def test_compute_run_metrics(tmp_path):
    (tmp_path / 'meta.json').write_text(json.dumps({
        'legit': [['h1', 'h5']], 'target_link': 'a-b', 'threshold': 0.8,
        'attack_start': 10, 'end': 70, 'controller_cpu_pct': 3.0}))
    (tmp_path / 'legit_0.json').write_text(json.dumps(
        {'end': {'sum': {'bits_per_second': 8e6, 'lost_percent': 20.0}}}))
    (tmp_path / 'events.jsonl').write_text('\n'.join(json.dumps(e) for e in [
        stats_event(12, {'a-b': 0.9}),
        {'t': 13, 'kind': 'reroute', 'flow': 'f', 'old': ['a', 'b'], 'new': ['a', 'c', 'b'],
         'rule_changes': 3},
        stats_event(14, {'a-b': 0.5})]))
    m = metrics.compute_run_metrics(tmp_path)
    assert m['throughput_mbps'] == 8 and m['packet_loss_pct'] == 20
    assert m['recovery_time_s'] == 2 and m['rule_changes'] == 3 and m['reroutes'] == 1
    assert m['latency_ms'] is None


def test_metrics_csv_appends_with_one_header(tmp_path):
    mc = metrics.MetricsCollector()
    mc.record(system='static', scenario='S1', throughput_mbps=1.0)
    mc.to_csv(tmp_path / 'r.csv', append=True)
    mc.to_csv(tmp_path / 'r.csv', append=True)
    df = pd.read_csv(tmp_path / 'r.csv')
    assert len(df) == 2 and list(df.columns) == metrics.FIELDS


def test_dominates_and_front():
    assert dominates([0.5, 1.0, 0.1], [0.4, 1.0, 0.1])
    assert not dominates([0.5, 1.0, 0.1], [0.5, 1.0, 0.1])
    objs = [[0.5, 1.0, 0.3], [0.4, 0.9, 0.3], [0.6, 0.5, 0.0]]
    pa = ParetoAnalysis()
    assert pa.compute_pareto_front(objs) == [0, 2]
    assert pa.scalarisation_hit_rate([{'objectives': objs, 'selected': 0},
                                      {'objectives': objs, 'selected': 1}]) == 0.5


def test_confidence_interval_and_welch():
    sa = StatisticalAnalysis()
    mean, ci = sa.confidence_interval([1, 2, 3, 4, 5])
    assert mean == 3 and ci == pytest.approx(1.963, abs=1e-3)
    assert np.isnan(sa.confidence_interval([7])[1])
    assert np.isnan(sa.welch_t_test([1], [2, 3])[1])
    assert sa.welch_t_test([1, 2, 3, 4], [10, 11, 12, 13])[1] < 0.001


def test_summary_csv(tmp_path):
    rows = [{'system': s, 'scenario': 'S3', 'topology': 'fat_tree', 'run': r,
             **{m: v + r for m in METRICS}}
            for s, v in (('simple_reactive', 0), ('resilient_sdn', 10)) for r in range(3)]
    pd.DataFrame(rows).to_csv(tmp_path / 'all_results.csv', index=False)
    out = StatisticalAnalysis().compute_all_intervals(tmp_path / 'all_results.csv')
    s = pd.read_csv(out)
    r = s[(s.system == 'resilient_sdn') & (s.metric == 'throughput_mbps')].iloc[0]
    assert r['mean'] == 11 and r['n'] == 3 and r['p_vs_simple_reactive'] < 0.05


def test_stable_recovery_is_not_fooled_by_flapping():
    def st(t, u):
        return {'t': t, 'kind': 'stats', 'util': {'a-b': u}}
    flapping = [st(10, 0.9), st(12, 0.2), st(14, 0.9), st(16, 0.2), st(18, 0.9), st(20, 0.2)]
    # old metric: "recovered" at the first dip (2 s); stable metric: calm only from t=20
    assert metrics.recovery(flapping, 'a-b', 0.8, 10, 30) == (2, True)
    assert metrics.stable_recovery(flapping, 'a-b', 0.8, 10, 30) == (6, 10, True)
    # still above threshold in the last sample: censored at attack end
    assert metrics.stable_recovery(flapping[:5], 'a-b', 0.8, 10, 30) == (6, 20, False)
    assert metrics.stable_recovery([st(10, 0.1)], 'a-b', 0.8, 10, 30) == (0.0, 0.0, True)
    assert metrics.stable_recovery([], 'a-b', 0.8, 10, 30) == (0.0, 0.0, True)


def test_ablation_module_imports_and_uses_known_scenarios():
    from attack.scenarios import SCENARIOS
    from evaluation import ablation
    for exp in ablation.EXPERIMENTS.values():
        assert all(s in SCENARIOS for s in (exp['scenarios'] or []))
