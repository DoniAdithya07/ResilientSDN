import csv

import pytest

from evaluation import experiment
from evaluation.experiment import ExperimentRunner, completed_runs
from evaluation.metrics import FIELDS, ping_avg_ms
from traffic.legitimate import LegitimateTraffic


def fake_row(topology, system, scenario, run_id, bursty=False):
    row = dict.fromkeys(FIELDS, 0)
    row.update(timestamp=1, system=system, scenario=scenario, topology=topology, run=run_id)
    return row


@pytest.fixture
def runner(tmp_path, monkeypatch):
    r = ExperimentRunner(out=tmp_path / 'out')
    calls = []
    monkeypatch.setattr(r, 'cleanup', lambda: calls.append('cleanup'))
    r.calls = calls
    return r


def rows(runner):
    with open(runner.out / 'all_results.csv', newline='') as f:
        return [(r['topology'], r['system'], r['scenario'], int(r['run']))
                for r in csv.DictReader(f)]


def test_completed_runs_missing_file_is_empty(tmp_path):
    assert completed_runs(tmp_path / 'nope.csv') == set()


def test_resume_skips_finished_runs_without_duplicates(runner, monkeypatch, capsys):
    ran = []

    def run_single(self, topology, system, scenario, run_id, bursty=False):
        ran.append((topology, system, scenario, run_id))
        return fake_row(topology, system, scenario, run_id)

    monkeypatch.setattr(ExperimentRunner, 'run_single', run_single)
    runner.run_all(['fat_tree'], ['static'], ['S1'], 2)
    assert completed_runs(runner.out / 'all_results.csv') == {
        ('fat_tree', 'static', 'S1', 0), ('fat_tree', 'static', 'S1', 1)}
    ran.clear()
    runner.run_all(['fat_tree'], ['static', 'resilient_sdn'], ['S1'], 2)
    assert ran == [('fat_tree', 'resilient_sdn', 'S1', 0), ('fat_tree', 'resilient_sdn', 'S1', 1)]
    assert sorted(rows(runner)) == sorted(set(rows(runner))) and len(rows(runner)) == 4
    out = capsys.readouterr().out
    assert 'skipped' in out and 'done 2, skipped 2, failed 0' in out


def test_failed_run_is_logged_and_campaign_continues(runner, monkeypatch, capsys):
    def run_single(self, topology, system, scenario, run_id, bursty=False):
        if run_id == 0:
            raise RuntimeError('ryu died')
        return fake_row(topology, system, scenario, run_id)

    monkeypatch.setattr(ExperimentRunner, 'run_single', run_single)
    runner.run_all(['fat_tree'], ['static'], ['S3'], 2)
    assert rows(runner) == [('fat_tree', 'static', 'S3', 1)]  # failed run: no row
    err = (runner.out / 'runs' / 'fat_tree_static_S3_0' / 'error.txt').read_text()
    assert 'RuntimeError: ryu died' in err and 'Traceback' in err
    assert 'cleanup' in runner.calls
    out = capsys.readouterr().out
    assert 'fat_tree static S3 run 0' in out and 'ryu died' in out
    assert 'done 1, skipped 0, failed 1' in out


def test_keyboard_interrupt_stops_the_campaign(runner, monkeypatch):
    ran = []

    def run_single(self, topology, system, scenario, run_id, bursty=False):
        ran.append(run_id)
        raise KeyboardInterrupt

    monkeypatch.setattr(ExperimentRunner, 'run_single', run_single)
    with pytest.raises(KeyboardInterrupt):
        runner.run_all(['fat_tree'], ['static'], ['S1'], 3)
    assert ran == [0]


@pytest.mark.parametrize('kwargs, message', [
    ({'systems': ['nope']}, 'unknown system: nope'),
    ({'scenarios': ['S99']}, 'unknown scenario: S99'),
    ({'topologies': ['ring']}, 'unknown topology: ring'),
    ({'runs': 0}, 'runs must be an integer >= 1'),
    ({'systems': []}, 'no system'),
])
def test_validation_rejects_bad_input_before_any_run(runner, monkeypatch, kwargs, message):
    monkeypatch.setattr(ExperimentRunner, 'run_single',
                        lambda *a, **k: pytest.fail('a run started'))
    args = {'topologies': ['fat_tree'], 'systems': ['static'], 'scenarios': ['S1'], 'runs': 1,
            **kwargs}
    with pytest.raises(ValueError, match=message):
        runner.run_all(**args)
    assert not runner.out.exists()


def test_validation_accepts_ablation_variants(runner, monkeypatch):
    monkeypatch.setitem(experiment.VARIANTS, 'rsdn_x', ('resilient_sdn', {}))
    monkeypatch.setattr(ExperimentRunner, 'run_single',
                        lambda self, t, s, sc, r, b=False: fake_row(t, s, sc, r))
    runner.run_all(['fat_tree'], ['rsdn_x'], ['S1'], 1)
    assert rows(runner) == [('fat_tree', 'rsdn_x', 'S1', 0)]


def test_main_turns_bad_input_into_usage_error(monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['experiment', '--systems', 'nope'])
    with pytest.raises(SystemExit) as e:
        experiment.main()
    assert e.value.code == 2 and 'unknown system: nope' in capsys.readouterr().err
    monkeypatch.setattr('sys.argv', ['experiment', '--runs', '0'])
    with pytest.raises(SystemExit) as e:
        experiment.main()
    assert e.value.code == 2


class FakeHost:
    def __init__(self, ip):
        self.ip, self.cmds = ip, []

    def IP(self):
        return self.ip

    def popen(self, cmd, stdout=None, stderr=None):
        self.cmds.append(cmd)
        if cmd[0] == 'ping':
            stdout.write('rtt min/avg/max/mdev = 1.0/2.5/4.0/0.3 ms\n')


def test_ping_output_survives_path_with_spaces(tmp_path):
    run_dir = tmp_path / 'dir with  spaces'
    run_dir.mkdir()
    hosts = {'h1': FakeHost('10.0.0.1'), 'h2': FakeHost('10.0.0.2')}
    net = type('Net', (), {'get': lambda self, n: hosts[n]})()
    LegitimateTraffic(net, run_dir).start([('h1', 'h2')], 10, 30, 'legit', [5201])
    ping = next(c for c in hosts['h1'].cmds if c[0] != 'iperf3')
    assert ping[0] == 'ping' and ping[-1] == '10.0.0.2'
    assert ping_avg_ms(run_dir / 'legit_0_ping.txt') == 2.5
