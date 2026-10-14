"""Per-run metrics, computed from the files a run leaves in its directory:
events.jsonl (controller), <tag>_<i>.json (iperf3), <tag>_<i>_ping.txt, meta.json.
Standard library only: runs in the container and on Windows.

Definitions
  throughput / loss / latency   legitimate flows that cross the target link
  recovery_time_s   first time the target link goes above threshold after the
                    attack starts, until it is back at or below threshold
                    (0 if never congested; censored at run end if never recovered)
  time_above_s      seconds the target link spent above threshold during the attack
                    (a flapping controller cannot hide here)
  stable_recovery_s first congestion until the link is below threshold for the
                    rest of the attack (censored at attack end if it still is
                    above threshold in the last sample; 0 if never congested)
  secondary_congestion   distinct links, other than the target, that go above
                    threshold after a reroute moved a flow onto them
  rule_changes      flow-table changes caused by reroutes
  oscillations      reroutes that put a flow back on a path it used before
"""
import csv
import json
import re
from pathlib import Path

FIELDS = ['timestamp', 'system', 'scenario', 'topology', 'run',
          'throughput_mbps', 'packet_loss_pct', 'latency_ms', 'recovery_time_s',
          'recovered', 'time_above_s', 'stable_recovery_s', 'stable_recovered',
          'secondary_congestion', 'rule_changes', 'oscillations',
          'reroutes', 'unresolved', 'controller_cpu_pct']


class MetricsCollector:
    def __init__(self):
        self.data = []

    def record(self, **row):
        self.data.append(row)

    def to_csv(self, path, append=False):
        path = Path(path)
        new = not (append and path.exists())
        with open(path, 'a' if append else 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='ignore')
            if new:
                w.writeheader()
            w.writerows(self.data)


def read_events(run_dir):
    path = Path(run_dir) / 'events.jsonl'
    if not path.exists():
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def iperf_result(path):
    """(received throughput Mbps, loss %) from an iperf3 -J UDP client log.
    `sum` / `sum_sent` are the sender's offered rate; `sum_received` is what
    arrived."""
    try:
        end = json.loads(Path(path).read_text())['end']
        s = end.get('sum_received', end['sum'])
        return s['bits_per_second'] / 1e6, s['lost_percent']
    except (OSError, ValueError, KeyError):
        return None, None


def ping_avg_ms(path):
    try:
        m = re.search(r'= [\d.]+/([\d.]+)/', Path(path).read_text())
        return float(m.group(1)) if m else None
    except OSError:
        return None


def recovery(events, target, threshold, attack_start, end):
    # Only while the attack runs: the link emptying after it stops is not recovery
    samples = [(e['t'], e['util'].get(target, 0.0)) for e in events
               if e['kind'] == 'stats' and attack_start <= e['t'] <= end]
    t_cong = next((t for t, u in samples if u > threshold), None)
    if t_cong is None:
        return 0.0, True
    t_rec = next((t for t, u in samples if t > t_cong and u <= threshold), None)
    return (t_rec - t_cong, True) if t_rec else (end - t_cong, False)


def _attack_samples(events, target, attack_start, end):
    return [(e['t'], e['util'].get(target, 0.0)) for e in events
            if e['kind'] == 'stats' and attack_start <= e['t'] <= end]


def stable_recovery(events, target, threshold, attack_start, end):
    """(time_above_s, stable_recovery_s, stable_recovered), see the module docstring."""
    samples = _attack_samples(events, target, attack_start, end)
    if not samples:
        return 0.0, 0.0, True
    times = [t for t, _ in samples]
    gaps = [b - a for a, b in zip(times, times[1:])]
    typical = sorted(gaps)[len(gaps) // 2] if gaps else 0.0
    time_above = sum((gaps[i] if i < len(gaps) else typical)
                     for i, (_, u) in enumerate(samples) if u > threshold)
    above = [t for t, u in samples if u > threshold]
    if not above:
        return 0.0, 0.0, True
    if samples[-1][1] > threshold:
        return time_above, end - above[0], False
    calm_from = next(t for t, _ in samples if t > above[-1])
    return time_above, calm_from - above[0], True


def secondary_congestion(events, target, threshold):
    moved_onto = {}  # {link: first time a reroute put a flow on it}
    hot = set()
    for e in events:
        if e['kind'] == 'reroute':
            for a, b in zip(e['new'], e['new'][1:]):
                moved_onto.setdefault(f'{a}-{b}', e['t'])
        elif e['kind'] == 'stats':
            hot |= {l for l, u in e['util'].items()
                    if u > threshold and l != target and l in moved_onto
                    and e['t'] > moved_onto[l]}
    return len(hot)


def oscillations(events):
    history, count = {}, 0
    for e in events:
        if e['kind'] == 'new_flow':
            history[e['flow']] = [e['path']]
        elif e['kind'] == 'reroute':
            seen = history.setdefault(e['flow'], [e['old']])
            count += e['new'] in seen
            seen.append(e['new'])
    return count


def compute_run_metrics(run_dir):
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / 'meta.json').read_text())
    events = read_events(run_dir)
    n = len(meta['legit'])
    flows = [iperf_result(run_dir / f'legit_{i}.json') for i in range(n)]
    reroutes = [e for e in events if e['kind'] == 'reroute']
    rec, recovered = recovery(events, meta['target_link'], meta['threshold'],
                              meta['attack_start'], meta['end'])
    above, stable, stable_ok = stable_recovery(events, meta['target_link'], meta['threshold'],
                                               meta['attack_start'], meta['end'])
    return {
        'throughput_mbps': _mean(f[0] for f in flows),
        'packet_loss_pct': _mean(f[1] for f in flows),
        'latency_ms': _mean(ping_avg_ms(run_dir / f'legit_{i}_ping.txt') for i in range(n)),
        'recovery_time_s': rec,
        'recovered': recovered,
        'time_above_s': above,
        'stable_recovery_s': stable,
        'stable_recovered': stable_ok,
        'secondary_congestion': secondary_congestion(events, meta['target_link'],
                                                     meta['threshold']),
        'rule_changes': sum(e['rule_changes'] for e in reroutes),
        'oscillations': oscillations(events),
        'reroutes': len(reroutes),
        'unresolved': sum(e['kind'] == 'unresolved' for e in events),
        'controller_cpu_pct': meta.get('controller_cpu_pct'),
    }
