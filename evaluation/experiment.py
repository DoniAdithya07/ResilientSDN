"""Automated experiment runner. Run inside the container (needs root + Mininet):

    python3 -m evaluation.experiment --out results/smoke \\
        --topologies fat_tree --systems static resilient_sdn --scenarios S1 S3 --runs 1

Every combination of systems x scenarios x topologies x runs. Each run gets
its own directory under <out>/runs/ (controller log, events, iperf/ping
output) and one row in <out>/all_results.csv, written as soon as the run ends.
Traffic is seeded by (topology, scenario, run), so all systems in the same run
face identical traffic. Statistics: evaluation/stats.py (on Windows).
"""
import argparse
import copy
import csv
import json
import os
import random
import socket
import subprocess
import time
import traceback
from pathlib import Path

import yaml

from attack.adversarial import ALT_PORT_OFFSET, PRELOAD_PORT_OFFSET
from attack.attacker import Attacker, TrafficPlanner
from attack.scenarios import SCENARIOS
from controller.config import PROJECT_ROOT, load_yaml
from evaluation.metrics import MetricsCollector, compute_run_metrics
from topology import TOPOLOGIES, load_topology
from traffic.legitimate import LegitimateTraffic, start_server

APPS = {
    'static': 'baselines/static_routing.py',
    'simple_reactive': 'baselines/simple_reactive.py',
    'resilient_sdn': 'controller/resilient_sdn.py',
    'qosentry': 'baselines/qosentry_stub.py',
}
# Extra system names: {name: (app key, controller config overrides)}.
# evaluation/ablation.py adds the ablation variants here.
VARIANTS = {
    # EXPERIMENTAL, NOT ADOPTED (negative result, docs/PROGRESS.md): a flow leaving a congested link is not
    # held by the DPP deadband. Fixed Internet2 S11 time-above-threshold but raised secondary congestion in S3.
    'rsdn_relief_bypass': ('resilient_sdn', {'controller': {'relief_bypass': True}}),
}

LEGIT_PORT, BURST_PORT, BACKGROUND_PORT, ATTACK_PORT = 5201, 5251, 5301, 5401


def deep_merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def wait_for_port(port, timeout=30):
    """Block until something listens on localhost:port (Ryu's OpenFlow port)."""
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                return
        time.sleep(0.2)
    raise TimeoutError(f'nothing listening on port {port} after {timeout}s')


def cpu_seconds(pid):
    """utime + stime of a process, from /proc (fields 14 and 15)."""
    with open(f'/proc/{pid}/stat') as f:
        fields = f.read().rsplit(')', 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK')


def completed_runs(csv_path):
    """{(topology, system, scenario, run)} of the rows already in a results CSV."""
    path = Path(csv_path)
    if not path.exists():
        return set()
    with open(path, newline='') as f:
        return {(r['topology'], r['system'], r['scenario'], int(r['run']))
                for r in csv.DictReader(f) if (r.get('run') or '').isdigit()}


class ExperimentRunner:
    def __init__(self, config_path='config/experiment_config.yaml', out=None):
        self.cfg = load_yaml(config_path)
        self.out = PROJECT_ROOT / (out or self.cfg['results_dir'])
        self.metrics = MetricsCollector()

    def resolve(self, topologies=None, systems=None, scenarios=None, runs=None):
        """Config defaults for arguments left out; ValueError on unknown names."""
        topologies = self.cfg['topologies'] if topologies is None else topologies
        systems = self.cfg['systems'] if systems is None else systems
        scenarios = self.cfg['scenarios'] if scenarios is None else scenarios
        runs = self.cfg['n_runs'] if runs is None else runs
        for kind, given, valid in (('topology', topologies, TOPOLOGIES),
                                   ('system', systems, [*APPS, *VARIANTS]),
                                   ('scenario', scenarios, SCENARIOS)):
            if not given:
                raise ValueError(f'no {kind} given (valid: {", ".join(valid)})')
            bad = [x for x in given if x not in valid]
            if bad:
                raise ValueError(f'unknown {kind}: {", ".join(map(str, bad))} '
                                 f'(valid: {", ".join(valid)})')
        if type(runs) is not int or runs < 1:
            raise ValueError(f'runs must be an integer >= 1, got {runs!r}')
        return topologies, systems, scenarios, runs

    def run_all(self, topologies=None, systems=None, scenarios=None, runs=None,
                bursty=False):
        """Every combination; skips runs already in all_results.csv (resume) and
        keeps going when a run fails (traceback in its run dir, no CSV row)."""
        topologies, systems, scenarios, runs = self.resolve(topologies, systems, scenarios, runs)
        total, n = len(topologies) * len(systems) * len(scenarios) * runs, 0
        self.out.mkdir(parents=True, exist_ok=True)
        csv_path = self.out / 'all_results.csv'
        finished, done, skipped, failed = completed_runs(csv_path), 0, 0, []
        for topology in topologies:
            for scenario in scenarios:
                for run in range(runs):
                    for system in systems:
                        n += 1
                        key = (topology, system, scenario, run)
                        label = f'[{n}/{total}] {topology} {system} {scenario} run {run}'
                        if key in finished:
                            skipped += 1
                            print(f'{label}: skipped (already in {csv_path.name})', flush=True)
                            continue
                        try:
                            row = self.run_single(topology, system, scenario, run, bursty)
                        except Exception as e:  # noqa: BLE001 - one bad run must not end the campaign
                            failed.append(key)
                            run_dir = self.out / 'runs' / f'{topology}_{system}_{scenario}_{run}'
                            run_dir.mkdir(parents=True, exist_ok=True)
                            (run_dir / 'error.txt').write_text(traceback.format_exc())
                            print(f'{label}: FAILED {type(e).__name__}: {e} '
                                  f'(traceback in {run_dir / "error.txt"})', flush=True)
                            self.cleanup()
                            continue
                        self.metrics.data = [row]
                        self.metrics.to_csv(csv_path, append=True)
                        finished.add(key)
                        done += 1
                        print(f'{label}: loss {row["packet_loss_pct"]}% '
                              f'reroutes {row["reroutes"]}', flush=True)
        print(f'done {done}, skipped {skipped}, failed {len(failed)}', flush=True)
        for key in failed:
            print('  failed (re-run the same command to retry):', ' '.join(map(str, key)))
        return csv_path

    def run_single(self, topology, system, scenario, run_id, bursty=False):
        app, overrides = VARIANTS.get(system, (system, {}))
        overrides = copy.deepcopy(overrides)
        rng = random.Random(f'{topology}-{scenario}-{run_id}')
        s_id = scenario
        if SCENARIOS[scenario].get('randomise'):
            s_id = rng.choice(SCENARIOS[scenario]['same_as'])
            deep_merge(overrides, {'adversarial': {'randomise_N': True,
                                                   'randomise_cooldown': True}})

        run_dir = self.out / 'runs' / f'{topology}_{system}_{scenario}_{run_id}'
        run_dir.mkdir(parents=True, exist_ok=True)
        for old in run_dir.iterdir():   # a re-run replaces the previous attempt
            old.unlink()
        ctrl_cfg = deep_merge(load_yaml('config/controller_config.yaml'), overrides)
        hops = getattr(load_topology(topology), 'max_path_hops', None)
        if hops:   # per-topology detour limit (sparse WANs need more than the default of 6)
            ctrl_cfg['controller']['max_path_hops'] = hops
        (run_dir / 'controller_config.yaml').write_text(yaml.safe_dump(ctrl_cfg))

        seconds = SCENARIOS[s_id].get('duration', self.cfg['duration_seconds'])
        delay = self.cfg['attack_delay_seconds']
        legit = self.cfg['legit']
        self.cleanup()
        env = dict(os.environ, RESILIENT_TOPO=topology, RESILIENT_RUN_DIR=str(run_dir),
                   RESILIENT_CONFIG=str(run_dir / 'controller_config.yaml'))
        with open(run_dir / 'ryu.log', 'w') as log:
            ryu = subprocess.Popen(['ryu-manager', '--ofp-listen-host', '127.0.0.1', APPS[app]], env=env, cwd=PROJECT_ROOT,
                                   stdout=log, stderr=subprocess.STDOUT)
        t0, cpu0 = time.time(), cpu_seconds(ryu.pid)
        topo = None
        try:
            wait_for_port(6653)
            topo = load_topology(topology)
            topo.start()
            net = topo.net
            net.waitConnected()
            plan = TrafficPlanner(topo).plan(s_id, legit, rng)

            legit_ports = [LEGIT_PORT + i for i in range(len(plan['legit']))]
            burst_ports = [BURST_PORT + i for i in range(len(plan['legit']))]
            bg_ports = [BACKGROUND_PORT + i for i in range(len(plan['background']))]
            attack_ports = [ATTACK_PORT + i for i in range(len(plan['attack']))]
            servers = (list(zip(plan['legit'], legit_ports))
                       + list(zip(plan['legit'], burst_ports))
                       + list(zip(plan['background'], bg_ports))
                       + list(zip(plan['attack'], attack_ports))
                       + [(p, port + ALT_PORT_OFFSET) for p, port in zip(plan['attack'], attack_ports)]
                       + [((s, d), ATTACK_PORT + PRELOAD_PORT_OFFSET + i)
                          for i, (s, d, *_) in enumerate(plan['preload'])])
            for (_, dst), port in servers:
                start_server(net.get(dst), port)
            time.sleep(self.cfg['warmup_seconds'])

            traffic = LegitimateTraffic(net, run_dir)
            traffic.start(plan['legit'], legit['rate_mbps'], delay + seconds, 'legit', legit_ports)
            traffic.start(plan['background'], legit['background_rate_mbps'], delay + seconds,
                          'background', bg_ports, ping=False)
            if bursty:
                b = self.cfg['bursty']
                traffic.start_bursty(plan['legit'], b['burst_mbps'], b['burst_seconds'],
                                     b['period_seconds'], delay + seconds, burst_ports)
            trace = self.cfg.get('trace') or {}
            if trace.get('file') and plan['background']:
                from traffic.trace_replay import replay
                s, d = plan['background'][0]
                replay(net.get(s), net.get(d), trace['file'], trace['rate_mbps'],
                       delay + seconds, run_dir)

            time.sleep(delay)
            attack_start = time.time()
            attacker = Attacker(net)
            attacker.run_scenario(s_id, plan, attack_ports, seconds,
                                  legit['rate_mbps'] * len(plan['legit']))
            time.sleep(seconds + 3)  # +3: iperf3 finishes and writes its JSON
            attacker.stop()
            traffic.stop()
            cpu_pct = (cpu_seconds(ryu.pid) - cpu0) / (time.time() - t0) * 100
        finally:
            if topo is not None:
                topo.stop()
            ryu.terminate()
            ryu.wait(timeout=10)
            self.cleanup()

        meta = {'topology': topology, 'system': system, 'scenario': scenario,
                'pattern_scenario': s_id, 'run': run_id,
                'target_link': '-'.join(plan['target_link']),
                'threshold': ctrl_cfg['controller']['utilisation_threshold'],
                'legit': plan['legit'], 'background': plan['background'],
                'attack': plan['attack'], 'preload': plan['preload'],
                'attack_start': attack_start, 'end': attack_start + seconds,
                'controller_cpu_pct': round(cpu_pct, 2)}
        (run_dir / 'meta.json').write_text(json.dumps(meta, indent=1))
        return {'timestamp': round(attack_start), 'system': system, 'scenario': scenario,
                'topology': topology, 'run': run_id, **compute_run_metrics(run_dir)}

    def cleanup(self):
        """mn -c (clean Mininet state) and kill leftover traffic processes."""
        subprocess.run(['mn', '-c'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for pattern in ('iperf3', 'tcpreplay', 'ping -i', 'ryu-manager'):
            subprocess.run(['pkill', '-f', pattern], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)


def make_report(out):
    """Write <out>/report.pdf (why the results came out as they did).
    Needs matplotlib + reportlab: used directly where installed (Windows .venv); in
    WSL the Windows .venv interpreter is run on the shared folder; in the container
    (no plotting libraries) the command to run on Windows is printed."""
    out = Path(out)
    if not (out / 'all_results.csv').exists():
        return
    rel = os.path.relpath(out, PROJECT_ROOT)
    try:
        from analysis.run_report import build
        print('report:', build(out), flush=True)
        return
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001 - a report problem must not fail a finished run
        print(f'report failed ({type(e).__name__}: {e}); results are saved in {out}', flush=True)
        return
    win = PROJECT_ROOT / '.venv' / 'Scripts' / 'python.exe'
    if win.exists():
        try:
            if subprocess.run([str(win), '-m', 'analysis.run_report', rel], cwd=PROJECT_ROOT).returncode == 0:
                return
        except OSError:
            pass
    print(r'report not built here; on Windows run:  .venv\Scripts\python -m analysis.run_report ' + rel)


def positive_int(text):
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError(f'must be an integer >= 1, got {text!r}')
    return int(text)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='config/experiment_config.yaml')
    p.add_argument('--out')
    p.add_argument('--topologies', nargs='+')
    p.add_argument('--systems', nargs='+')
    p.add_argument('--scenarios', nargs='+')
    p.add_argument('--runs', type=positive_int)
    p.add_argument('--bursty', action='store_true')
    a = p.parse_args()
    runner = ExperimentRunner(a.config, a.out)
    try:
        runner.resolve(a.topologies, a.systems, a.scenarios, a.runs)
    except ValueError as e:
        p.error(str(e))
    print(runner.run_all(a.topologies, a.systems, a.scenarios, a.runs, a.bursty))
    make_report(runner.out)


if __name__ == '__main__':
    main()
