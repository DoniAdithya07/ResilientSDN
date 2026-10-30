"""Live view of an experiment in the browser. Runs a command (e.g. ./demo.sh),
echoes its output to the terminal unchanged, and streams it, plus the newest
run's controller events, ryu.log and run files, to a local web page.

    python3 tools/live_view.py                             (control mode: the page has
                                                            Start demo / Start complete run /
                                                            Stop buttons; asks for sudo first)
    python3 tools/live_view.py ./demo.sh
    python3 tools/live_view.py -- ./run.sh python3 -m evaluation.experiment --out results/x ...
    python3 tools/live_view.py --out results/demo          (watch only, no command)
    python3 tools/live_view.py --watch ./demo.sh           (watch only; the command just
                                                            tells the view the planned runs)

Standard library only. The page is served on 127.0.0.1 only (Server-Sent
Events on /stream). Ctrl+C stops the command; after it ends the page keeps
being served until Ctrl+C. In control mode the page can only start the two
project scripts (demo.sh, run_full.sh) or a 6-run test of one topology from the
current list, and download a Topology Zoo network by a validated name, through
POST /control, which needs the random token in the served page, this page's own
Origin and Host, and JSON. Nothing the page sends is ever passed to a shell.
"""
import argparse
import ast
import collections
import csv
import hmac
import http.server
import itertools
import json
import math
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLL_SECONDS = 0.5
SHOWN_FILES = ('meta.json', 'error.txt')   # contents shown on the page
MAX_SHOWN_BYTES = 64 * 1024
REPORT_FILE = 'report.pdf'   # written one level up from runs/, directly in the campaign folder
WATCH_BACKLOG = 12   # watch only: earlier runs replayed for the timeline
MAX_PARTIAL = 1 << 20   # output with no newline is sent on once it reaches this size
BUILTIN_TOPOLOGIES = ('fat_tree', 'internet2', 'geant', 'nsfnet')
RUN_NAME = re.compile(r'^(?P<head>.+)_(?P<scenario>S\d+)_(?P<run>\d+)$')
ANSI = re.compile(r'\x1b(\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(\x07|\x1b\\)|[@-Z\\-_])')
CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
REPORT_PATH = re.compile(r'^/report/(.*)$')   # the part after the slash is validated, not used as a path


# --- text helpers ----------------------------------------------------------------
def clean_line(text):
    """Terminal line for the page: no ANSI colour codes, only the text after the
    last carriage return (progress bars), no control characters."""
    text = text.rstrip('\r\n').split('\r')[-1]
    return CONTROL.sub('', ANSI.sub('', text))


def topology_names():
    """Built-in topologies plus the extra .graphml ones (as topology/__init__.py)."""
    extra = {p.stem.lower() for p in (ROOT / 'topology' / 'data').glob('*.graphml')}
    return set(BUILTIN_TOPOLOGIES) | extra


def parse_run_name(name):
    """<topology>_<system>_<scenario>_<run> -> dict, or None. Topology and
    system names may contain underscores; known topology names decide the split."""
    m = RUN_NAME.match(name)
    if not m:
        return None
    head = m['head']
    topology = next((t for t in sorted(topology_names(), key=len, reverse=True)
                     if head.startswith(t + '_')), None)
    if topology is None:
        topology, _, system = head.partition('_')
    else:
        system = head[len(topology) + 1:]
    return {'topology': topology, 'system': system, 'scenario': m['scenario'], 'run': m['run']}


def _path(nodes):
    return ' > '.join(map(str, nodes or [])) or '(none)'


def _describe(kind, ev):
    g = ev.get
    if kind == 'start':
        return f'Controller started: {g("system")}'
    if kind == 'config':
        return 'Controller settings: ' + ', '.join(f'{k}={v}' for k, v in ev.items()
                                                    if k not in ('t', 'kind'))
    if kind == 'new_flow':
        return f'New flow {g("flow")} on path {_path(g("path"))}'
    if kind == 'congestion':
        conf = g('confidence') or {}
        text = f'Congestion detected on {", ".join(map(str, g("links") or []))}'
        if conf:
            text += ' (attack confidence ' + ', '.join(f'{l} {c:.2f}' for l, c in conf.items()) + ')'
        return text
    if kind == 'decision':
        objectives, sel = g('objectives') or [], g('selected')
        text = f'Decision for flow {g("flow")}: {len(objectives)} candidate paths'
        if isinstance(sel, int) and 0 <= sel < len(objectives):
            text += f', chose #{sel + 1} (objectives {objectives[sel]})'
        return text
    if kind == 'held':
        return (f'Held flow {g("flow")} on its current path (hysteresis): '
                f'new path score {g("new_dpp")} vs current {g("current_dpp")}')
    if kind == 'reroute':
        return (f'Rerouted flow {g("flow")}: {_path(g("old"))}  ->  {_path(g("new"))} '
                f'({g("reason") or "no reason given"}; {g("rule_changes")} rule changes)')
    if kind == 'reroute_skipped':
        return f'Reroute of flow {g("flow")} skipped: {g("reason")}'
    if kind == 'no_candidate':
        return f'No alternative path for flow {g("flow")} that avoids link {g("link")}'
    if kind == 'unresolved':
        return f'Congestion on {g("link")} not fully resolved: {g("excess")} B/s still over the threshold'
    if kind == 'dropped':
        return f'Dropped traffic {g("flow")} at switch {g("at")} (unknown address or spoofed source)'
    if kind == 'stats':
        util = g('util') or {}
        text = f'Stats: {len(g("flows") or {})} flows measured, {g("rules")} rule changes so far'
        if util:
            worst = max(util, key=util.get)
            text += f'; busiest link {worst} at {util[worst]:.0%}'
        return text
    return None


def describe_event(ev):
    """One plain-English line for an events.jsonl entry (never raises)."""
    kind = str(ev.get('kind', 'unknown'))
    try:
        text = _describe(kind, ev)
    except (TypeError, ValueError, AttributeError):   # odd field types: fall back below
        text = None
    return text or f'{kind}: ' + ', '.join(f'{k}={v}' for k, v in ev.items()
                                           if k not in ('t', 'kind'))


def event_message(line):
    """Fields of an 'event' page message for one events.jsonl line."""
    try:
        ev = json.loads(line)
    except ValueError:
        ev = None
    if not isinstance(ev, dict):
        return {'kind': 'unparsed', 'text': clean_line(line), 'raw': line}
    return {'kind': str(ev.get('kind', 'unknown')), 'time': ev.get('t'),
            'text': describe_event(ev), 'raw': ev}


def guess_out_dir(command, out=None):
    """Results folder to watch: --out given to us, else --out in the command,
    else results/demo for demo.sh, else results. Paths in the command are
    relative to the project root (as evaluation/experiment.py treats them)."""
    if out:
        return Path(out).resolve()
    for i, arg in enumerate(command):
        if arg == '--out' and i + 1 < len(command):
            return ROOT / command[i + 1]
        if arg.startswith('--out='):
            return ROOT / arg.split('=', 1)[1]
    if any(Path(a).name == 'demo.sh' for a in command):
        return ROOT / 'results' / 'demo'
    return ROOT / 'results'


def out_dirs(out):
    """One results folder or several (the complete run writes three kinds)."""
    return [Path(p) for p in out] if isinstance(out, (list, tuple)) else [Path(out)]


def _globbed(outs, *patterns):
    return itertools.chain.from_iterable(o.glob(p) for o in outs for p in patterns)


def _finite(obj):
    """JSON-safe copy: NaN/inf become strings (the browser's JSON.parse rejects them)."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


# --- planned runs and timings (for the progress header and the graphs) ---------
DEFAULT_CONFIG = 'config/experiment_config.yaml'
CLEANUP_SECONDS = 3   # evaluation/experiment.py: iperf3 finishes after the attack ends
TIMING_KEYS = ('warmup_seconds', 'attack_delay_seconds', 'duration_seconds')
SCENARIO_WORDS = {   # plain names for the header (attack/scenarios.py has the details)
    'S1': 'no attack', 'S2': 'low-rate flooding attack', 'S3': 'link-flooding attack',
    'S4': 'distributed flooding attack', 'S5': 'heavy distributed attack',
    'S6': 'sustained 10-minute attack', 'S7': 'threshold-evasion attack',
    'S8': 'cooldown-exploit attack', 'S9': 'capacity pre-saturation attack',
    'S10': 'randomised adversarial attack', 'S11': '2-minute overload attack'}
LIST_OPTIONS = ('--topologies', '--systems', '--scenarios')


def _scalar(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '\'"':
        return text[1:-1]
    low = text.lower()
    if low in ('null', '~', ''):
        return None
    if low in ('true', 'false'):
        return low == 'true'
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


def tiny_yaml(text):
    """Top-level `key: value` and `key: [a, b]` lines of a YAML file: all the view
    needs from the experiment config, without depending on PyYAML."""
    out = {}
    for line in text.splitlines():
        if not line or line[0] in ' \t#-':
            continue
        line = re.sub(r'\s+#.*$', '', line)
        key, sep, value = line.partition(':')
        if not sep:
            continue
        value = value.strip()
        if value.startswith('[') and value.endswith(']'):
            out[key.strip()] = [_scalar(v) for v in value[1:-1].split(',') if v.strip()]
        elif value:
            out[key.strip()] = _scalar(value)
    return out


def read_config(path):
    """The experiment config as a dict ({} when missing or unreadable)."""
    try:
        text = Path(path).read_text()
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        import yaml
        cfg = yaml.safe_load(text)
        return cfg if isinstance(cfg, dict) else {}
    except ImportError:
        return tiny_yaml(text)
    except Exception:  # noqa: BLE001 - yaml.YAMLError; fall back to the simple reader
        return tiny_yaml(text)


def _scenario_table():
    """attack/scenarios.py SCENARIOS, read as a literal (nothing is imported)."""
    try:
        tree = ast.parse((ROOT / 'attack' / 'scenarios.py').read_text())
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, 'id', '') == 'SCENARIOS'
                                                    for t in node.targets):
                table = ast.literal_eval(node.value)
                return table if isinstance(table, dict) else {}
    except (OSError, SyntaxError, ValueError):
        pass
    return {}


def scenario_info(scenarios, default_duration):
    """{scenario: {label, attack, duration}} for the header and the phase track."""
    table = _scenario_table()
    info = {}
    for s in scenarios:
        spec = table.get(s, {})
        same = [table.get(x, {}) for x in spec.get('same_as', [])]
        durations = [d.get('duration', default_duration) for d in (same or [spec])]
        info[s] = {'label': SCENARIO_WORDS.get(s, spec.get('name', s)),
                   'name': spec.get('name', s),
                   'attack': s != 'S1' and (spec.get('attackers', 1) != 0 or bool(same)),
                   'duration': max(durations) if durations else default_duration}
    return info


def _experiment_arg_lists(command):
    """Argument lists given to `-m evaluation.experiment`: from the command itself,
    or from the lines of a project script such as demo.sh / run_full.sh."""
    if 'evaluation.experiment' in command:
        return [command[command.index('evaluation.experiment') + 1:]]
    if not command or not command[0].endswith('.sh'):
        return []
    script = Path(command[0])
    if not script.is_file():
        script = ROOT / script.name
    try:
        text = script.read_text().replace('\\\n', ' ')
    except (OSError, UnicodeDecodeError):
        return []
    found = []
    for line in text.splitlines():
        if 'evaluation.experiment' not in line or line.lstrip().startswith('#'):
            continue
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            continue
        if 'evaluation.experiment' not in words:
            continue
        args = words[words.index('evaluation.experiment') + 1:]
        for stop in ('|', '||', '&&', ';', '>', '>>'):
            if stop in args:
                args = args[:args.index(stop)]
        found.append(args)
    return found


def _parse_experiment_args(args):
    """--config/--topologies/--systems/--scenarios/--runs as evaluation/experiment.py reads them."""
    opts, key = {}, None
    for arg in args:
        if arg.startswith('--'):
            name, eq, value = arg.partition('=')
            key = name
            if name in LIST_OPTIONS:
                opts[name] = [value] if eq else []
                if eq:
                    key = None
            elif eq:
                opts[name], key = value, None
            else:
                opts.setdefault(name, None)
        elif key in LIST_OPTIONS:
            opts[key].append(arg)
        elif key is not None:
            opts[key], key = arg, None
    return opts


def _run_entry(topology, system, scenario, run):
    return {'name': f'{topology}_{system}_{scenario}_{run}', 'topology': topology,
            'system': system, 'scenario': scenario, 'run': str(run)}


def _plan(runs, cfg, scenarios):
    timing = {k: cfg.get(k) if isinstance(cfg.get(k), (int, float)) else None for k in TIMING_KEYS}
    timing['cleanup_seconds'] = CLEANUP_SECONDS
    return {'runs': runs, 'timing': timing,
            'scenarios': scenario_info(scenarios, timing['duration_seconds'] or 60)}


def plan_for_command(command):
    """Planned runs (in the order evaluation/experiment.py runs them), the timings
    from the config and the scenarios; None when the command is not an experiment."""
    runs, scenarios, first_cfg = [], [], None
    for args in _experiment_arg_lists(command or []):
        opts = _parse_experiment_args(args)
        cfg = read_config(ROOT / (opts.get('--config') or DEFAULT_CONFIG))
        first_cfg = cfg if first_cfg is None else first_cfg
        pick = lambda key, opt: opts.get(opt) or cfg.get(key) or []   # noqa: E731
        try:
            n_runs = int(opts.get('--runs') or cfg.get('n_runs') or 1)
        except (TypeError, ValueError):
            n_runs = 1
        for topology in pick('topologies', '--topologies'):
            for scenario in pick('scenarios', '--scenarios'):
                scenarios += [] if scenario in scenarios else [scenario]
                for run in range(n_runs):
                    for system in pick('systems', '--systems'):
                        runs.append(_run_entry(topology, system, scenario, run))
    if first_cfg is None:
        return None
    return _plan(runs, first_cfg, scenarios)


def plan_from_folder(out):
    """Watch only with no command: the runs already in the results folder, in
    experiment order (system order from the default config)."""
    out = Path(out)
    seen = set()
    for d in itertools.chain(out.glob('runs/*'), out.glob('*/runs/*')):
        info = parse_run_name(d.name) if d.is_dir() else None
        if info:
            seen.add((info['topology'], info['system'], info['scenario'], int(info['run'])))
    if not seen:
        return None
    cfg = read_config(ROOT / DEFAULT_CONFIG)
    order = {k: (cfg.get(k) or []) for k in ('topologies', 'systems', 'scenarios')}

    def rank(kind, value):
        listed = order[kind]
        return (listed.index(value), '') if value in listed else (len(listed), value)

    keys = sorted(seen, key=lambda k: (rank('topologies', k[0]), rank('scenarios', k[2]),
                                       k[3], rank('systems', k[1])))
    scenarios = sorted({k[2] for k in keys}, key=lambda s: rank('scenarios', s))
    return _plan([_run_entry(t, sy, sc, r) for t, sy, sc, r in keys], cfg, scenarios)


# --- all_results.csv (one row per finished run) ----------------------------------
RESULT_KEYS = ('system', 'scenario', 'topology', 'run')


def _number(text):
    if text in ('True', 'False'):
        return text == 'True'
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return int(value) if value.is_integer() and '.' not in text else value


def parse_result_row(header, line):
    """One all_results.csv line -> 'result' message fields, or None for a broken row."""
    try:
        row = next(csv.reader([line]))
    except (csv.Error, StopIteration):
        return None
    if len(row) != len(header):
        return None
    rec = dict(zip(header, row))
    if not all(rec.get(k) for k in RESULT_KEYS) or not rec['run'].isdigit():
        return None
    values = {}
    for key, text in rec.items():
        if key not in RESULT_KEYS:
            value = _number(text)
            if value is not None:
                values[key] = value
    return {'name': f'{rec["topology"]}_{rec["system"]}_{rec["scenario"]}_{rec["run"]}',
            **{k: rec[k] for k in RESULT_KEYS}, 'values': values}


class ResultsWatcher:
    """Tails <out>/all_results.csv (and <out>/*/all_results.csv): each new row
    becomes a 'result' message. Rows before a header line are ignored.
    out_dir may be a list of folders. Rows stamped before `since` (left by an
    earlier campaign, before demo.sh wipes the folder) are skipped."""

    def __init__(self, out_dir, log, since=0.0):
        self.outs, self.log, self.since = out_dirs(out_dir), log, since
        self.tails = {}     # path -> FileTail
        self.headers = {}   # path -> column names

    def poll(self):
        for path in _globbed(self.outs, 'all_results.csv', '*/all_results.csv'):
            self.tails.setdefault(path, FileTail(path))
        for path, tail in self.tails.items():
            for line in tail.poll():
                if not line.strip():
                    continue
                if line.startswith('timestamp,'):
                    self.headers[path] = next(csv.reader([line]))
                    continue
                header = self.headers.get(path)
                fields = parse_result_row(header, line) if header else None
                if fields and fields['values'].get('timestamp', self.since) >= self.since:
                    self.log.add('result', **fields)


class ReportStatus:
    """Thread-safe record of the current run's campaign folders (the same list
    published in 'outs'/'status' messages) and which of them have a ready
    report.pdf, so GET /report/<n> can validate against exactly what the page
    was told, without re-deriving it from the message log."""

    def __init__(self):
        self._lock = threading.Lock()
        self.outs = []
        self._ready = set()

    def set_outs(self, outs):
        """A run started (or this is the first run): fix the index->folder mapping
        and forget any earlier readiness (a reused index now means a different folder)."""
        with self._lock:
            self.outs = list(outs)
            self._ready = set()

    def mark_ready(self, index):
        with self._lock:
            if 0 <= index < len(self.outs):
                self._ready.add(index)

    def lookup(self, index):
        """(out Path, ready bool) for a valid index, or (None, False) when index
        is out of range for the current outs list."""
        with self._lock:
            if 0 <= index < len(self.outs):
                return self.outs[index], index in self._ready
            return None, False


# --- message log -----------------------------------------------------------------
class MessageLog:
    """Append-only, thread-safe page messages with sequence ids, so a browser
    that opens late or reconnects gets the whole history. Oldest messages are
    dropped past max_messages / max_bytes."""

    def __init__(self, max_messages=50_000, max_bytes=64 << 20):
        self.max_messages, self.max_bytes = max_messages, max_bytes
        self._msgs = collections.deque()   # (seq, json text); seqs are consecutive
        self._bytes = 0
        self._seq = 0
        self._cond = threading.Condition()

    def add(self, mtype, **fields):
        msg = {'type': mtype, 't': time.time(), **fields}
        try:
            data = json.dumps(msg, allow_nan=False, default=str)
        except ValueError:
            data = json.dumps(_finite(msg), default=str)
        with self._cond:
            self._seq += 1
            self._msgs.append((self._seq, data))
            self._bytes += len(data)
            while len(self._msgs) > self.max_messages or (
                    self._bytes > self.max_bytes and len(self._msgs) > 1):
                self._bytes -= len(self._msgs.popleft()[1])
            self._cond.notify_all()
            return self._seq

    def since(self, seq):
        """[(seq, json text)] of the retained messages after seq."""
        with self._cond:
            if not self._msgs or self._msgs[-1][0] <= seq:
                return []
            start = max(0, seq - self._msgs[0][0] + 1)
            return list(itertools.islice(self._msgs, start, None))

    def wait(self, seq, timeout):
        with self._cond:
            self._cond.wait_for(lambda: self._seq > seq, timeout)
        return self.since(seq)


# --- watching the results folder -----------------------------------------------
class FileTail:
    """New complete lines of a file that may appear late, be truncated,
    deleted or replaced. A partial last line is held until its newline."""
    MAX_READ = 1 << 20

    def __init__(self, path):
        self.path = Path(path)
        self._reset(None)

    def _reset(self, inode):
        self.inode, self.offset, self.partial = inode, 0, b''

    def poll(self):
        try:
            st = os.stat(self.path)
        except OSError:
            self._reset(None)
            return []
        if st.st_ino != self.inode or st.st_size < self.offset:
            self._reset(st.st_ino)
        if st.st_size == self.offset:
            return []
        try:
            with open(self.path, 'rb') as f:
                f.seek(self.offset)
                data = f.read(self.MAX_READ)
        except OSError:
            return []
        self.offset += len(data)
        *lines, self.partial = (self.partial + data).split(b'\n')
        if len(self.partial) > self.MAX_READ:   # no newline for 1 MB: show it anyway
            lines.append(self.partial)
            self.partial = b''
        return [line.decode('utf-8', 'replace').rstrip('\r') for line in lines]


class RunWatcher:
    """Follows the newest run folder under <out>/runs/ (or <out>/*/runs/):
    tails events.jsonl and ryu.log, announces files once their size is stable
    (meta.json and error.txt with their contents) and notices a wiped folder.
    out_dir may be a list of folders (the complete run's campaigns). Also
    watches <out>/report.pdf for each campaign folder (one level up from
    runs/, so it needs its own tracking, not the per-run file logic above)."""

    def __init__(self, out_dir, log, since=0.0, backlog=0, results_since=None, reports=None):
        self.outs, self.log, self.since = out_dirs(out_dir), log, since
        self.backlog = backlog   # watch only: earlier runs replayed once, oldest first
        self.run_dir = None
        self.results = ResultsWatcher(out_dir, log, since if results_since is None else results_since)
        self._lock = threading.Lock()   # watcher thread + the final poll in main()
        self.reports = reports   # ReportStatus the server's /report/<n> validates against
        self.report_sizes = {}      # out index -> size at the previous poll (None: missing)
        self.report_announced = set()   # out indices already announced ready
        if self.reports is not None:
            self.reports.set_outs(self.outs)

    def _run_dirs(self):
        """[(mtime, run dir)] of the run folders at or after self.since."""
        found = []
        for d in _globbed(self.outs, 'runs/*', '*/runs/*'):
            try:
                st = d.stat()
            except OSError:
                continue
            if d.is_dir() and st.st_mtime >= self.since:
                found.append((st.st_mtime, d))
        return found

    def newest_run(self):
        found = self._run_dirs()
        return max(found, key=lambda f: f[0])[1] if found else None

    def _replay_backlog(self):
        """The few runs before the newest, each read to its end (for the timeline)."""
        older = [d for _, d in sorted(self._run_dirs(), key=lambda f: f[0])][:-1]
        for d in older[-self.backlog:] if self.backlog else []:
            self._start(d)
            self._poll_run(final=True)
        self.backlog = 0

    def poll(self, final=False):
        """final=True once the command has ended: nothing writes any more, so
        files are announced without waiting for a stable size."""
        with self._lock:
            if self.backlog:
                self._replay_backlog()
            newest = self.newest_run()
            if newest is not None and newest != self.run_dir:
                if self.run_dir is not None:   # the old run's last files (meta.json is
                    self._poll_run(final=True)   # written just before the next run starts)
                self._start(newest)
            if self.run_dir is not None:
                self._poll_run(final)
            self.results.poll()   # after the run's files: its meta.json comes first
            self._poll_reports(final)

    def _start(self, run_dir, wiped=False):
        self.run_dir = run_dir
        self.events = FileTail(run_dir / 'events.jsonl')
        self.ryu = FileTail(run_dir / 'ryu.log')
        self.announced = {}   # name -> size when announced
        self.sizes = {}       # name -> size at the previous poll
        self.log.add('run', name=run_dir.name, dir=str(run_dir),
                     run=parse_run_name(run_dir.name), wiped=wiped)

    def _poll_run(self, final):
        try:
            sizes = {p.name: p.stat().st_size for p in self.run_dir.iterdir() if p.is_file()}
        except OSError:   # folder deleted (e.g. demo.sh's rm -rf)
            sizes = {}
        if any(name not in sizes for name in self.sizes):
            self._start(self.run_dir, wiped=True)   # a re-run deletes the old files
        for line in self.events.poll():
            self.log.add('event', **event_message(line))
        for line in self.ryu.poll():
            self.log.add('log', text=clean_line(line))
        for name, size in sorted(sizes.items()):
            stable = final or self.sizes.get(name) == size
            if stable and self.announced.get(name) != size and (
                    name not in self.announced or name in SHOWN_FILES):
                self._announce(name, size)
        self.sizes = sizes

    def _announce(self, name, size):
        self.announced[name] = size
        fields = {'name': name, 'size': size}
        if name in SHOWN_FILES:
            try:
                with open(self.run_dir / name, 'rb') as f:
                    fields['content'] = f.read(MAX_SHOWN_BYTES).decode('utf-8', 'replace')
            except OSError as e:
                fields['content'] = f'(could not read: {e})'
        self.log.add('file', **fields)

    def _poll_reports(self, final):
        """<out>/report.pdf for each campaign folder: same stable-size rule as
        _poll_run above (final, or unchanged since the previous poll), announced
        once per index (it only ever goes from absent/growing to done)."""
        for i, out in enumerate(self.outs):
            try:
                size = (out / REPORT_FILE).stat().st_size
            except OSError:
                size = None
            if i in self.report_announced:
                self.report_sizes[i] = size
                continue
            stable = size is not None and (final or self.report_sizes.get(i) == size)
            if stable:
                self.report_announced.add(i)
                if self.reports is not None:
                    self.reports.mark_ready(i)
                self.log.add('report', index=i, out=str(out), ready=True, size=size)
            self.report_sizes[i] = size


def watch_loop(watcher, stop):
    while not stop.is_set():
        try:
            watcher.poll()
        except Exception as e:  # noqa: BLE001 - the view must outlive odd files
            watcher.log.add('info', text=f'watcher error: {type(e).__name__}: {e}')
        stop.wait(POLL_SECONDS)


# --- HTTP server -----------------------------------------------------------------
MAX_CONTROL_BODY = 1024   # bytes; a control request is a tiny JSON object
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def render_page(mode, token=''):
    """The page with the view mode and (control mode only) the per-process token."""
    return PAGE.replace('__MODE__', mode).replace('__TOKEN__', token)


class Handler(http.server.BaseHTTPRequestHandler):
    def _own_names(self):
        port = self.server.server_address[1]
        return (f'127.0.0.1:{port}', f'localhost:{port}')

    def _host_ok(self):
        if self.headers.get('Host') in self._own_names():
            return True
        self.send_error(403)   # DNS rebinding: a foreign name pointed at 127.0.0.1
        return False

    def do_GET(self):
        if not self._host_ok():
            return
        path = self.path.split('?', 1)[0]
        if path in ('/', '/index.html'):
            body = render_page(self.server.mode, self.server.token).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', CSP)
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
        elif path == '/stream':
            self._stream()
        elif path == '/control' and self.server.control is not None:
            self._json(405, {'error': 'Use POST.'}, allow='POST')   # GET never changes state
        elif REPORT_PATH.match(path):
            self._report(REPORT_PATH.match(path)[1])
        else:
            self.send_error(404)

    # --- POST /control: start or stop a run (control mode only) ------------------------
    def do_POST(self):
        if not self._host_ok():
            return
        if self.path != '/control' or self.server.control is None:
            self._drain()
            self.send_error(404)
            return
        status, reply = self._control_request()
        self._json(status, reply)

    def _control_request(self):
        """(HTTP status, JSON reply). Checks, in order: same-origin page, JSON,
        size, token, then the action; nothing from the client is ever run."""
        if self.headers.get('Origin') not in tuple(f'http://{n}' for n in self._own_names()):
            self._drain()
            return 403, {'error': 'Requests must come from this page.'}
        ctype = (self.headers.get('Content-Type') or '').split(';', 1)[0].strip().lower()
        if ctype != 'application/json':
            self._drain()
            return 415, {'error': 'Send JSON (Content-Type: application/json).'}
        length = self.headers.get('Content-Length')
        if length is None or not length.isdigit():
            self.close_connection = True
            return 411, {'error': 'Content-Length is required.'}
        if int(length) > MAX_CONTROL_BODY:
            self._drain()
            return 413, {'error': 'Request too large.'}
        try:
            body = json.loads(self.rfile.read(int(length)))
        except (ValueError, UnicodeDecodeError):
            return 400, {'error': 'Body is not valid JSON.'}
        if not isinstance(body, dict):
            return 400, {'error': 'Body must be a JSON object.'}
        token = body.get('token')
        if not (isinstance(token, str) and hmac.compare_digest(token.encode(), self.server.token.encode())):
            return 403, {'error': 'Wrong or missing token: reload the page.'}
        action = body.get('action')
        if not isinstance(action, str) or action not in CONTROL_ACTIONS:
            return 400, {'error': 'Unknown action.'}
        return self.server.control.request(action, body)

    def _drain(self):
        """Read a small unread body so the reply is not lost to a connection reset."""
        length = self.headers.get('Content-Length') or ''
        if length.isdigit() and int(length) <= 64 * 1024:
            self.rfile.read(int(length))
        else:
            self.close_connection = True

    def _json(self, status, data, allow=None):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        if allow:
            self.send_header('Allow', allow)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # --- GET /report/<n>: the campaign's report.pdf, once it is ready -------------------
    def _report(self, raw):
        """<n> must be a plain non-negative integer indexing the current outs list
        (the same one published in 'outs'); never used to build a path beyond that."""
        if self.server.reports is None or not raw.isdigit():
            self.send_error(400, 'Bad report index.')
            return
        out, ready = self.server.reports.lookup(int(raw))
        if out is None:
            self.send_error(400, 'No such report index.')
            return
        if not ready:
            self.send_error(404, 'Report not written yet.')
            return
        try:
            f = open(out / REPORT_FILE, 'rb')
        except OSError:   # disappeared between the readiness check and now
            self.send_error(404, 'Report not found.')
            return
        with f:
            size = os.fstat(f.fileno()).st_size
            self.send_response(200)
            self.send_header('Content-Type', 'application/pdf')
            self.send_header('Content-Length', str(size))
            self.send_header('Content-Disposition', 'inline; filename="report.pdf"')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            shutil.copyfileobj(f, self.wfile)

    def _last_seq(self):
        """Last-Event-ID is '<server instance>:<seq>'; a different instance
        (tool restarted) means start from the beginning."""
        value = self.headers.get('Last-Event-ID') or '0'
        instance, _, seq = value.rpartition(':')
        if instance and instance != self.server.instance:
            return 0
        return int(seq) if seq.isdigit() else 0

    def _stream(self):
        last = self._last_seq()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(b'retry: 2000\n\n')
            while True:
                msgs = self.server.log.wait(last, timeout=10)
                if msgs:
                    last = msgs[-1][0]
                    chunk = ''.join(f'id: {self.server.instance}:{seq}\ndata: {data}\n\n'
                                    for seq, data in msgs)
                else:
                    chunk = ': keep-alive\n\n'
                self.wfile.write(chunk.encode())
                self.wfile.flush()
        except OSError:   # browser closed the page
            pass

    def log_message(self, format, *args):   # keep the terminal for the command
        pass


def make_server(log, port, mode='command', control=None, reports=None):
    """HTTP server on 127.0.0.1 only (the page shows lab logs; not for the network).
    mode: 'command', 'watch' or 'control'; control mode needs a Control and gets a
    random per-process token that the page must send back with every request.
    reports: the ReportStatus that GET /report/<n> validates against; a fresh one
    is made when not given (control mode passes the one its Control also uses)."""
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    srv.daemon_threads = True
    srv.log = log
    srv.instance = uuid.uuid4().hex[:8]
    srv.mode = mode
    srv.control = control if mode == 'control' else None
    srv.token = secrets.token_urlsafe(32) if srv.control is not None else ''
    srv.reports = reports if reports is not None else ReportStatus()
    return srv


def serve_in_background(srv):
    threading.Thread(target=srv.serve_forever, daemon=True).start()


# --- running the command ---------------------------------------------------------
def pump(proc, log, echo):
    """Copy the command's output to the terminal unchanged, its lines to the page."""
    fd, partial = proc.stdout.fileno(), b''
    while True:
        try:
            data = os.read(fd, 65536)
        except OSError:
            break
        if not data:
            break
        try:
            echo.write(data)
            echo.flush()
        except (OSError, ValueError):   # terminal gone: keep feeding the page
            pass
        *lines, partial = (partial + data).split(b'\n')
        while len(partial) >= MAX_PARTIAL:
            lines.append(partial[:MAX_PARTIAL])
            partial = partial[MAX_PARTIAL:]
        for line in lines:
            log.add('term', text=clean_line(line.decode('utf-8', 'replace')))
    if partial:
        log.add('term', text=clean_line(partial.decode('utf-8', 'replace')))


def exit_code(returncode):
    """Shell-style code: killed by signal N -> 128 + N."""
    return 128 - returncode if returncode < 0 else returncode


def terminal_signalled_child():
    """True when Ctrl+C in the terminal also reached the command (same
    foreground process group), so it must not get a second SIGINT."""
    try:
        return os.tcgetpgrp(sys.stdin.fileno()) == os.getpgrp()
    except (OSError, ValueError, AttributeError):
        return False


def _signal(proc, sig):
    try:
        proc.send_signal(sig)
    except (ProcessLookupError, PermissionError):   # gone, or root-owned (sudo)
        pass


def stop_child(proc, grace=(30, 5, 5)):
    """After Ctrl+C: let the command clean up (Mininet needs a few seconds);
    a second Ctrl+C or 30 s without exiting escalates to SIGTERM, then SIGKILL."""
    print('\n[live view] stopping the command (press Ctrl+C again to force)...',
          file=sys.stderr, flush=True)
    if not terminal_signalled_child():
        _signal(proc, signal.SIGINT)
    for sig, wait in zip((None, signal.SIGTERM, signal.SIGKILL), grace):
        if sig is not None:
            _signal(proc, sig)
        try:
            return proc.wait(timeout=wait)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            continue
    code = proc.poll()   # still running: root-owned (sudo) and ignoring us; give up
    return -signal.SIGINT if code is None else code


def run_command(command, log, plan=None, outs=None):
    """Run the command to the end; returns (exit code, interrupted by Ctrl+C)."""
    log.add('status', state='running', command=shlex.join(command), started=time.time(),
            plan=plan, outs=outs or [])
    try:   # stdin inherited: sudo can still ask for the password in the terminal
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as e:
        print(f'[live view] cannot start {command[0]}: {e}', file=sys.stderr, flush=True)
        log.add('term', text=f'cannot start {command[0]}: {e}')
        return 127, False
    reader = threading.Thread(target=pump, args=(proc, log, sys.stdout.buffer), daemon=True)
    reader.start()
    interrupted = False
    try:
        code = proc.wait()
    except KeyboardInterrupt:
        interrupted = True
        code = stop_child(proc)
    reader.join(timeout=5)   # the rest of the output
    return exit_code(code), interrupted


def open_browser(url):
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        print(f'[live view] running as root: not opening a browser. Open {url} '
              'yourself (as your normal user).', flush=True)
        return
    if sys.platform.startswith('linux') and not (
            os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        print(f'[live view] no desktop found: open {url} in a browser.', flush=True)
        return
    if shutil.which('xdg-open'):   # silenced: browser chatter must not bury prompts in the terminal
        subprocess.Popen(['xdg-open', url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    else:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()


SUDO_SCRIPTS = ('demo.sh', 'run.sh', 'run_full.sh')   # project scripts that call sudo


def needs_sudo(command):
    return bool(command) and (Path(command[0]).name in SUDO_SCRIPTS or command[0] == 'sudo')


def ask_sudo_first(command):
    """Ask for the sudo password up front, before the browser and the command's output
    start writing to the terminal; sudo then remembers it for the run."""
    if not (needs_sudo(command) and sys.stdin.isatty() and shutil.which('sudo')):
        return
    if subprocess.call(['sudo', '-n', 'true'], stderr=subprocess.DEVNULL) == 0:
        return   # already remembered
    print('[live view] this run needs root (Mininet): type your sudo password here.', flush=True)
    subprocess.call(['sudo', '-v'])


# --- control mode: the page's Start / Stop buttons --------------------------------
CONTROL_ACTIONS = ('start_demo', 'start_full', 'start_topology', 'add_topology', 'stop')
SUDO_REFRESH_SECONDS = 4 * 60   # sudo forgets the password after 15 min by default
# resume: the experiment skips runs already in the folder's all_results.csv, so
# those rows belong to this test too and are shown (not filtered by start time).
# fresh: wipe `outs` (sudo, since an earlier run may have left root-owned files)
# before starting, so a repeat test always shows a clean run instead of silently
# resuming/skipping finished rows from last time.
RunSpec = collections.namedtuple('RunSpec', 'label command outs resume fresh',
                                 defaults=(False, False))


def default_runs():
    """The allow-list: the only commands the page can start. Nothing from the
    client is ever run; an action name only picks one of these."""
    results = ROOT / 'results'
    return {
        'start_demo': RunSpec('Demo run', [str(ROOT / 'demo.sh')], [results / 'demo']),
        # run_full.sh: two campaigns, then evaluation/ablation.py A B C D (--out results)
        'start_full': RunSpec('Complete run', [str(ROOT / 'run_full.sh')],
                              [results / 'fattree_5runs', results / 'wan_5runs',
                               *(results / f'ablation_{x}' for x in 'ABCD')]),
    }


# --- topologies: the list on the page, a 6-run test of one, adding one from the Zoo --
TOPOLOGY_TITLES = {'fat_tree': 'Fat-tree', 'internet2': 'Internet2', 'geant': 'GÉANT', 'nsfnet': 'NSFNET'}
ZOO_FILES_OF_BUILTINS = {'geant2012', 'nsfnet'}   # graphml files behind geant / nsfnet
ZOO_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,39}')   # used with fullmatch only
ADD_TIMEOUT = 60          # seconds for python3 -m topology.add
RUN_SETUP_SECONDS = 15    # starting Mininet and the controller, per run (estimate)
STATE_SUFFIX = re.compile(r'\s+[A-Z]{2}$')


def _topology_package():
    """The project's topology package (needs ROOT on sys.path; no root, no Mininet)."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import topology
    return topology


def available_topologies():
    """(built-in names, downloaded Zoo names), read again on every call."""
    try:
        pkg = _topology_package()
        return list(pkg.BUILTIN), list(pkg.discovered())
    except Exception:  # noqa: BLE001 - the list must still show the built-ins
        stems = {p.stem.lower() for p in (ROOT / 'topology' / 'data').glob('*.graphml')}
        return list(BUILTIN_TOPOLOGIES), sorted(stems - ZOO_FILES_OF_BUILTINS)


def topology_list():
    builtin, zoo = available_topologies()
    return builtin + zoo


def _zoo_titles():
    return {p.stem.lower(): p.stem for p in (ROOT / 'topology' / 'data').glob('*.graphml')}


def topology_title(name, titles=None):
    if name in TOPOLOGY_TITLES:
        return TOPOLOGY_TITLES[name]
    return (titles if titles is not None else _zoo_titles()).get(name, name)


def short_label(label):
    """A short city name for the map: 'Kansas City, MO' -> 'Kansas City',
    'NCSA, University of Illinois, Champaign' -> 'Champaign'."""
    parts = [p.strip() for p in str(label).split(',') if p.strip()]
    while len(parts) > 1 and len(parts[-1]) <= 3 and parts[-1].isupper():
        parts.pop()
    text = parts[-1] if parts else str(label)
    if len(text) > 4:
        text = STATE_SUFFIX.sub('', text)
    words = text.split()
    while len(' '.join(words)) > 16 and len(words) > 1:
        words.pop()
    text = ' '.join(words)
    return text if len(text) <= 16 else text[:15] + '…'


_GRAPH_CACHE = {}


def _graph_info(name):
    """Sizes, nodes (with city names and lat/lon where known) and links of a topology."""
    data = ROOT / 'topology' / 'data'
    stamp = max((p.stat().st_mtime for p in data.glob('*') if p.stem.lower() == name), default=0)
    key = (name, stamp)
    if key not in _GRAPH_CACHE:
        topo = _topology_package().load_topology(name)
        graph = topo.get_switch_graph()
        labels = getattr(topo, 'labels', None) or {}
        coords = getattr(topo, 'coords', None) or {}
        nodes = []
        for n in graph.nodes():
            label = str(labels.get(n, n))
            node = {'id': str(n), 'label': label, 'short': short_label(label) if n in labels else str(n)}
            c = coords.get(n)
            if c and all(isinstance(v, (int, float)) and math.isfinite(v) for v in c):
                node['lat'], node['lon'] = float(c[0]), float(c[1])
            nodes.append(node)
        _GRAPH_CACHE[key] = {'switches': graph.number_of_nodes(), 'links': graph.number_of_edges(),
                             'nodes': nodes, 'edges': [[str(a), str(b)] for a, b in graph.edges()]}
    return _GRAPH_CACHE[key]


def topology_run(topo):
    """The 6-run test of one topology (3 systems x no attack / attack). topo must
    already be checked against topology_list(): it becomes part of the out path."""
    return RunSpec(f'Test of {topology_title(topo)}',
                   [str(ROOT / 'run.sh'), 'python3', '-m', 'evaluation.experiment',
                    '--config', 'config/smoke_config.yaml', '--out', f'results/{topo}_demo',
                    '--topologies', topo, '--scenarios', 'S1', 'S3', '--runs', '1'],
                   [ROOT / 'results' / f'{topo}_demo'], fresh=True)


def estimate_seconds(plan):
    """Rough length of a planned campaign: each run's phases plus setup."""
    if not plan or not plan.get('runs'):
        return None
    tm, total = plan['timing'], 0
    for run in plan['runs']:
        sc = plan['scenarios'].get(run['scenario'], {})
        total += ((tm.get('warmup_seconds') or 0) + (tm.get('attack_delay_seconds') or 0)
                  + (sc.get('duration') or tm.get('duration_seconds') or 60)
                  + (tm.get('cleanup_seconds') or 0) + RUN_SETUP_SECONDS)
    return total


def topology_catalog():
    """[{name, title, builtin, switches, links, seconds, nodes, edges}] for the page."""
    builtin, zoo = available_topologies()
    titles = _zoo_titles()
    seconds = estimate_seconds(plan_for_command(topology_run('fat_tree').command))
    items = []
    for name in builtin + zoo:
        item = {'name': name, 'title': topology_title(name, titles), 'builtin': name in builtin,
                'seconds': seconds, 'switches': None, 'links': None, 'nodes': [], 'edges': []}
        try:
            item.update(_graph_info(name))
        except Exception as e:  # noqa: BLE001 - a broken file must not hide the others
            item['error'] = f'{type(e).__name__}: {e}'
        items.append(item)
    return items


def safe_catalog():
    try:
        return topology_catalog()
    except Exception:  # noqa: BLE001
        return []


def wipe_results(out_dir):
    """Delete a results folder with sudo (an earlier run may have left root-owned
    files) before a fresh test; True on success. Non-interactive: relies on sudo
    already being remembered (Start is gated on sudo_ok), same as demo.sh's own
    `sudo rm -rf results/demo`."""
    if not out_dir.exists():
        return True
    return subprocess.run(['sudo', 'rm', '-rf', str(out_dir)], cwd=ROOT,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True).returncode == 0


def run_downloader(argv):
    """Run `python -m topology.add NAME` (argv list, no shell); (exit code, output)."""
    proc = subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, errors='replace', timeout=ADD_TIMEOUT)
    return proc.returncode, (proc.stdout or '') + (proc.stderr or '')


def add_error(name, output):
    """Plain-English reason why topology.add failed."""
    if 'HTTP Error 404' in output:
        return f"No network called '{name}' in the Topology Zoo. Check the spelling on topology-zoo.org."
    if any(s in output for s in ('URLError', 'urlopen error', 'timed out', 'Temporary failure')):
        return 'Could not reach the Topology Zoo. Check the internet connection and try again.'
    last = next((clean_line(line) for line in reversed(output.splitlines()) if line.strip()),
                'no output')
    return f"Could not add '{name}': {last[:200]}"


def descendants(pid):
    """PIDs of all processes below pid (from /proc; empty where there is no /proc)."""
    children = collections.defaultdict(list)
    for stat in Path('/proc').glob('[0-9]*/stat'):
        try:
            text = stat.read_text()
            ppid = int(text[text.rindex(')') + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children[ppid].append(int(stat.parent.name))
    found, todo = set(), [pid]
    while todo:
        for child in children.get(todo.pop(), ()):
            if child not in found:
                found.add(child)
                todo.append(child)
    return found


def signal_pids(pids, sig):
    """Send sig to the processes, like Ctrl+C does to the whole job. It is sent by a
    helper in its own session because sudo only passes on signals that come from
    outside its own process group; root-owned processes are reached through sudo."""
    pids = sorted(p for p in pids if p != os.getpid() and os.path.exists(f'/proc/{p}'))
    if not pids:
        return
    kill = shutil.which('kill')
    if kill:
        try:
            subprocess.run([kill, '-s', signal.Signals(sig).name[3:], '--', *map(str, pids)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, start_new_session=True, timeout=10)
            return
        except (OSError, subprocess.SubprocessError):
            pass
    for pid in pids:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


class Control:
    """Control mode: runs one allow-listed command at a time for the page, streams
    it like command mode and reports its state as 'control' messages."""

    def __init__(self, log, runs=None, echo=None, grace=(30, 5, 5), topology_spec=topology_run,
                 topologies=topology_list, catalog=safe_catalog, downloader=run_downloader,
                 wipe=wipe_results, reports=None):
        self.log = log
        self.runs = default_runs() if runs is None else runs
        self.echo = echo if echo is not None else sys.stdout.buffer
        self.grace = grace
        self.topology_spec, self.topologies = topology_spec, topologies
        self.catalog, self.downloader, self.wipe = catalog, downloader, wipe
        self.reports = reports   # same ReportStatus the server's /report/<n> uses
        self._lock = threading.RLock()
        self.proc = self.reader = self.stopper = None
        self.action = self.label = None
        self.state, self.code, self.stopped, self.sudo_ok = 'idle', None, False, True
        self.adding = None   # Zoo name being downloaded (one at a time)
        self._watch_stop = threading.Event()
        self.publish()
        self.publish_topologies()

    def publish_topologies(self):
        self.log.add('topologies', items=self.catalog())

    def publish(self):
        with self._lock:
            self.log.add('control', state=self.state, action=self.action, label=self.label,
                         code=self.code, stopped=self.stopped, sudo_ok=self.sudo_ok)

    def set_sudo(self, ok):
        with self._lock:
            if ok != self.sudo_ok:
                self.sudo_ok = ok
                self.publish()

    @property
    def active(self):
        return self.state in ('running', 'stopping')

    def request(self, action, body=None):
        """Page request -> (HTTP status, JSON reply). body: the request's JSON object;
        only 'topology' (checked against the current list) and 'name' (checked against
        ZOO_NAME) are read from it."""
        body = body if isinstance(body, dict) else {}
        if action == 'add_topology':
            return self._request_add(body.get('name'))
        spec = None
        if action == 'start_topology':
            topo = body.get('topology')
            if not isinstance(topo, str) or topo not in self.topologies():
                return 400, {'error': 'Unknown network: reload the page and pick one from the list.',
                             'state': self.state}
            spec = self.topology_spec(topo)
        with self._lock:
            if action == 'stop':
                if self.state != 'running':
                    return 409, {'error': 'Nothing is running.', 'state': self.state}
                self._begin_stop()
                return 200, {'state': self.state}
            if self.active:
                return 409, {'error': 'A run is already going: stop it first.', 'state': self.state}
            spec = spec or self.runs.get(action)
            if spec is None:
                return 400, {'error': 'Unknown action.', 'state': self.state}
            if not self.sudo_ok:
                return 409, {'error': 'Password needed: type it in the terminal first.',
                             'state': self.state}
            self._start(action, spec)
            return 200, {'state': self.state}

    def _request_add(self, name):
        if not isinstance(name, str) or not ZOO_NAME.fullmatch(name):
            return 400, {'error': 'Use the network name as on topology-zoo.org: letters, digits, '
                                  '- and _ only (at most 40), for example Abilene.'}
        with self._lock:
            if self.adding:
                return 409, {'error': f'Still adding {self.adding}: wait for it to finish.'}
            if name.lower() in self.topologies():
                self.log.add('add_topology', ok=True, already=True, name=topology_title(name.lower()),
                             topology=name.lower(), error=None,
                             **self._sizes(name.lower()))
                return 200, {'adding': None}
            self.adding = name
        threading.Thread(target=self._add, args=(name,), daemon=True).start()
        return 202, {'adding': name}

    @staticmethod
    def _sizes(name):
        try:
            info = _graph_info(name)
            return {'switches': info['switches'], 'links': info['links']}
        except Exception:  # noqa: BLE001
            return {'switches': None, 'links': None}

    def _add(self, name):
        """Download one Zoo network with topology.add (argv list, cwd ROOT, no shell)."""
        switches = links = error = None
        try:
            code, output = self.downloader([sys.executable, '-m', 'topology.add', name])
            found = re.search(r'^\S+: (\d+) switches, (\d+) links', output or '', re.M)
            if code == 0 and found:
                switches, links = int(found[1]), int(found[2])
            else:
                error = add_error(name, output or '')
        except subprocess.TimeoutExpired:
            error = (f'The download took longer than {ADD_TIMEOUT} s. '
                     'Check the internet connection and try again.')
        except Exception as e:  # noqa: BLE001 - the page must always get an answer
            error = f"Could not add '{name}': {type(e).__name__}: {e}"
        try:
            if error is None:
                self.publish_topologies()
            self.log.add('add_topology', ok=error is None, already=False,
                         name=topology_title(name.lower()) if error is None else name,
                         topology=name.lower(), switches=switches, links=links, error=error)
        finally:
            with self._lock:
                self.adding = None

    def _start(self, action, spec):
        self._watch_stop.set()   # the previous run's watcher
        self._watch_stop = threading.Event()
        self.action, self.label, self.code, self.stopped = action, spec.label, None, False
        started = time.time()
        if spec.fresh:
            self.log.add('term', text=f'Clearing earlier results in {", ".join(str(o) for o in spec.outs)}…')
            if not all(self.wipe(out) for out in spec.outs):
                self.log.add('term', text='Could not clear the old results folder (sudo rm -rf failed). '
                                          'Check the terminal, then try again.')
                self._finish(1, started)
                return
        self.log.add('status', state='running', command=shlex.join(spec.command), started=started,
                     plan=plan_for_command(spec.command), reset=True, label=spec.label,
                     outs=[str(o) for o in spec.outs])
        try:   # stdin inherited: sudo can still ask for the password in the terminal
            proc = subprocess.Popen(spec.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    cwd=ROOT)
        except OSError as e:
            self.log.add('term', text=f'cannot start {spec.command[0]}: {e}')
            self._finish(127, started)
            return
        self.proc, self.state = proc, 'running'
        print(f'[live view] {spec.label} started from the page: {shlex.join(spec.command)}', flush=True)
        self.reader = threading.Thread(target=pump, args=(proc, self.log, self.echo), daemon=True)
        self.reader.start()
        watcher = RunWatcher(spec.outs, self.log, since=started - 1,
                             results_since=0 if spec.resume else None, reports=self.reports)
        threading.Thread(target=watch_loop, args=(watcher, self._watch_stop), daemon=True).start()
        threading.Thread(target=self._wait, args=(proc, self.reader, watcher, started),
                         daemon=True).start()
        self.publish()

    def _wait(self, proc, reader, watcher, started):
        code = proc.wait()
        reader.join(timeout=5)   # the rest of the output
        stopper = self.stopper
        if stopper is not None:   # Stop pressed: its escalation decides when we give up
            stopper.join()
            reader.join(timeout=1)
        watcher.poll(final=True)   # files written just before the end
        with self._lock:
            if self.proc is proc:
                self._finish(exit_code(code), started)

    def _finish(self, code, started):
        self._watch_stop.set()
        self.proc = self.stopper = None
        self.state, self.code = 'finished', code
        self.log.add('status', state='finished', code=code, ended=time.time(),
                     interrupted=self.stopped, started=started)
        print(f'[live view] {self.label} {"stopped" if self.stopped else "finished"} '
              f'(exit code {code}).', flush=True)
        self.publish()

    def _begin_stop(self):
        self.state, self.stopped = 'stopping', True
        self.stopper = threading.Thread(target=self._stop, args=(self.proc, self.reader), daemon=True)
        self.stopper.start()
        self.publish()

    def _stop(self, proc, reader):
        """Graceful SIGINT to the whole run (as Ctrl+C), then SIGTERM, then SIGKILL."""
        print('\n[live view] Stop pressed in the page: stopping the run...', file=sys.stderr, flush=True)
        known = {proc.pid} | descendants(proc.pid)   # orphaned shells' children too
        for sig, wait in zip((signal.SIGINT, signal.SIGTERM, signal.SIGKILL), self.grace):
            signal_pids(known | descendants(proc.pid), sig)
            end = time.time() + wait
            try:
                proc.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                continue
            reader.join(timeout=max(0.0, end - time.time()))   # output ends when all have exited
            if not reader.is_alive():
                return

    def interrupt(self):
        """Ctrl+C in the terminal: the run got it too; wait for it as command mode does."""
        with self._lock:
            proc, self.stopped = self.proc, True
            if proc is not None:
                self.state = 'stopping'
                self.publish()
        return exit_code(stop_child(proc)) if proc is not None else 0

    def close(self):
        self._watch_stop.set()
        proc = self.proc
        if proc is not None and proc.poll() is None:
            signal_pids({proc.pid} | descendants(proc.pid), signal.SIGTERM)


def sudo_is_remembered():
    """True when sudo works without a password now; also extends sudo's memory."""
    if not shutil.which('sudo'):
        return True   # nothing to keep alive
    return subprocess.call(['sudo', '-n', '-v'], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


class SudoKeeper:
    """Refreshes sudo every few minutes so runs started from the page never stop
    to ask for the password; when that fails the page says 'Password needed'
    and ask() (by default: sudo -v in the terminal, only between runs) asks again."""

    def __init__(self, control, check=sudo_is_remembered, every=SUDO_REFRESH_SECONDS, ask=None):
        self.control, self.check, self.every = control, check, every
        self.ask = ask if ask is not None else self._ask_in_terminal
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            ok = bool(self.check())
            self.control.set_sudo(ok)
            if not ok and not self.control.active:
                self.ask()
                self.control.set_sudo(bool(self.check()))
            self._stop.wait(self.every)

    def _ask_in_terminal(self):
        if sys.stdin.isatty() and shutil.which('sudo'):
            print('\n[live view] sudo forgot the password: type it here to enable the Start '
                  'buttons again.', flush=True)
            subprocess.call(['sudo', '-v'])


def pick_mode(args):
    """'command' (run it), 'watch' (--watch, or --out with no command) or
    'control' (no command: the page gets Start / Stop buttons)."""
    if args.command and not args.watch:
        return 'command'
    if args.watch or args.out:
        return 'watch'
    return 'control'


def parse_args(argv):
    p = argparse.ArgumentParser(
        description='Run a command and show its output, controller events and logs live in the browser.',
        usage='%(prog)s [--port N] [--out DIR] [--no-browser] [--watch] [--] [command ...]',
        epilog='With no command (and no --watch / --out) the page gets Start and Stop buttons '
               'for ./demo.sh and ./run_full.sh.')
    p.add_argument('--port', type=int, default=8765, help='0 = any free port (default 8765)')
    p.add_argument('--out', help='results folder to watch (default: from the command)')
    p.add_argument('--no-browser', action='store_true', help='do not open a browser')
    p.add_argument('--watch', action='store_true',
                   help='do not run the command; only read its planned runs and results folder')
    p.add_argument('command', nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    if args.command[:1] == ['--']:
        args.command = args.command[1:]
    if not 0 <= args.port <= 65535:
        p.error(f'--port must be 0-65535, got {args.port}')
    return args


def run_control(args):
    """Control mode: ask for sudo once, keep it fresh, serve the page with buttons."""
    ask_sudo_first([str(ROOT / 'demo.sh')])
    log = MessageLog()
    reports = ReportStatus()
    control = Control(log, reports=reports)
    try:
        srv = make_server(log, args.port, mode='control', control=control, reports=reports)
    except OSError as e:
        print(f'[live view] cannot listen on 127.0.0.1:{args.port} ({e}); '
              'try another --port, or --port 0 for any free one.', file=sys.stderr)
        return 2
    url = f'http://127.0.0.1:{srv.server_address[1]}/'
    log.add('status', state='idle', command='', plan=None)
    serve_in_background(srv)
    keeper = SudoKeeper(control)
    keeper.start()
    print(f'[live view] {url}  (control mode: start a run with the buttons in the page; '
          'press Ctrl+C here to quit)', flush=True)
    if not args.no_browser:
        open_browser(url)
    code = 0
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        code = control.interrupt()
    finally:
        keeper.stop()
        control.close()
        srv.shutdown()
        srv.server_close()
    return code


def main(argv=None):
    args = parse_args(argv)
    mode = pick_mode(args)
    if mode == 'control':
        return run_control(args)
    command = args.command
    out = guess_out_dir(command, args.out)
    plan = plan_for_command(command) if command else plan_from_folder(out)
    if args.watch:
        command = []   # the command only told us the plan and the folder
    ask_sudo_first(command)
    log = MessageLog()
    try:
        srv = make_server(log, args.port, mode=mode)
    except OSError as e:
        print(f'[live view] cannot listen on 127.0.0.1:{args.port} ({e}); '
              'try another --port, or --port 0 for any free one.', file=sys.stderr)
        return 2
    url = f'http://127.0.0.1:{srv.server_address[1]}/'
    log.add('topologies', items=safe_catalog())   # names, sizes and maps for the page
    serve_in_background(srv)
    print(f'[live view] {url}  (watching {out})', flush=True)
    stop = threading.Event()
    # with a command, only runs it creates; watch-only also replays the last few runs
    watcher = RunWatcher(out, log, since=time.time() - 1 if command else 0,
                         backlog=0 if command else WATCH_BACKLOG, reports=srv.reports)
    threading.Thread(target=watch_loop, args=(watcher, stop), daemon=True).start()
    if not args.no_browser:
        open_browser(url)
    code = 0
    try:
        if command:
            code, interrupted = run_command(command, log, plan, outs=[str(out)])
            watcher.poll(final=True)   # files written just before the end
            log.add('status', state='finished', code=code, ended=time.time(),
                    interrupted=interrupted)
            if interrupted:
                return code
            print(f'[live view] command finished (exit code {code}); still serving {url} '
                  '- press Ctrl+C to stop.', flush=True)
        else:
            log.add('status', state='watching', command='', started=time.time(), plan=plan,
                    outs=[str(out)])
            print('[live view] watch only (no command); press Ctrl+C to stop.', flush=True)
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        srv.shutdown()
        srv.server_close()
    return code


PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ResilientSDN test bench</title>
<style>
:root {
  --page: #f7f7f5; --panel: #ffffff; --ink: #1d1d1f; --muted: #66666b; --grid: #e6e6e2;
  --line: #d6d6d1; --hl: #fff4d6; --warn: #8a5300; --warn-bg: #fdf0d8; --fs: 16px;
  --sys-resilient_sdn: #2a78d6; --sys-simple_reactive: #eb6834; --sys-static: #1baf7a;
  --sys-other: #8a8a90;
  --attack: #d03b3b; --attack-ink: #b42b2b; --attack-tint: rgba(208, 59, 59, .08); --attack-hatch: rgba(208, 59, 59, .25);
  --lk-low: #c9c9c4; --lk-mid: #6b6b70; --lk-none: #e2e2de;
  --shadow: 0 1px 2px rgba(0, 0, 0, .04);
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --page: #141416; --panel: #1d1d20; --ink: #ececec; --muted: #a2a2aa; --grid: #2c2c31;
    --line: #3a3a40; --hl: #3a3220; --warn: #e8ad55; --warn-bg: #3a2d16;
    --sys-resilient_sdn: #3987e5; --sys-simple_reactive: #d95926; --sys-static: #199e70;
    --sys-other: #8a8a92;
    --attack: #e66767; --attack-ink: #f08080; --attack-tint: rgba(230, 103, 103, .14); --attack-hatch: rgba(230, 103, 103, .28);
    --lk-low: #4a4a50; --lk-mid: #b5b5bb; --lk-none: #34343a;
    --shadow: none;
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
html, body { margin: 0; }
html { -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; scroll-padding-top: 72px; }
body { background: var(--page); color: var(--ink); font: 16px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
button, input, select { font: inherit; color: inherit; }
:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
.num { font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
[hidden] { display: none !important; }
code { font: 15px ui-monospace, monospace; background: var(--grid); padding: 1px 6px; border-radius: 4px; overflow-wrap: anywhere; }

/* ---- buttons ------------------------------------------------------------------------- */
.btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 40px; padding: 0 16px;
       border-radius: 8px; border: 1px solid var(--line); background: var(--panel); color: var(--ink);
       font-size: 16px; font-weight: 600; cursor: pointer; white-space: nowrap;
       transition-property: border-color, background-color, transform; transition-duration: 150ms;
       transition-timing-function: ease-out; }
.btn:hover:not(:disabled) { border-color: var(--muted); }
.btn:active:not(:disabled) { transform: scale(.97); }
.btn.primary { background: var(--ink); border-color: var(--ink); color: var(--panel); }
.btn.big { min-height: 48px; padding: 0 24px; font-size: 17px; }
.btn:disabled { cursor: not-allowed; opacity: .45; }
.btn .glyph { font-size: 13px; }
.linkbtn { border: none; background: none; padding: 0; font-weight: 600; text-decoration: underline;
           text-underline-offset: 3px; cursor: pointer; min-height: 24px; }

/* ---- pinned top bar ----------------------------------------------------------------- */
.topbar { position: fixed; top: 0; left: 0; right: 0; z-index: 20; height: 56px; display: flex; align-items: center;
          gap: 24px; padding: 0 24px; background: var(--panel); border-bottom: 1px solid var(--line); }
.brand { font-size: 17px; font-weight: 700; white-space: nowrap; letter-spacing: -.01em; }
.pagetabs { display: flex; align-self: stretch; gap: 4px; }
.pagetabs [role="tab"] { position: relative; display: inline-flex; align-items: center; gap: 6px; border: none; background: none;
  padding: 0 14px; font-size: 16px; font-weight: 600; color: var(--muted); cursor: pointer;
  border-bottom: 3px solid transparent; border-top: 3px solid transparent; }
.pagetabs [role="tab"]:hover { color: var(--ink); }
.pagetabs [role="tab"][aria-selected="true"] { color: var(--ink); border-bottom-color: var(--ink); }
.pagetabs [role="tab"]:focus-visible { outline-offset: -4px; }
.tab-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ink); }
.status { margin-left: auto; display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 4px 14px 4px 12px;
          border: 1px solid var(--line); border-radius: 22px; background: var(--page); min-width: 0; }
.st-icon { font-size: 16px; width: 18px; text-align: center; flex: none; }
.st-text { display: flex; flex-direction: column; line-height: 1.2; min-width: 0; }
.st-word { font-size: 15px; font-weight: 700; white-space: nowrap; }
.st-sub { font-size: 14px; color: var(--muted); white-space: nowrap; font-variant-numeric: tabular-nums;
          overflow: hidden; text-overflow: ellipsis; }
.status.password { background: var(--warn-bg); border-color: var(--warn); }
.status.password .st-word, .status.password .st-icon, .status.error .st-word, .status.error .st-icon { color: var(--warn); }
.status.password .st-word { white-space: normal; }

/* ---- content ------------------------------------------------------------------------ */
main { max-width: 1440px; margin: 0 auto; padding: 80px 24px 48px; }
.head { display: flex; align-items: center; gap: 16px; min-height: 40px; margin-bottom: 8px; }
.head h1 { flex: 1; min-width: 0; margin: 0; font-size: 20px; font-weight: 600; line-height: 1.3; text-wrap: balance; }
.head h1 .sysglyph { font-size: .9em; }
.why { margin: 0 0 24px; font-size: 15px; color: var(--muted); max-width: 80ch; text-wrap: pretty; }
.panel-why { margin: 2px 0 8px; font-size: 15px; color: var(--muted); text-wrap: pretty; }
h2 { font-size: 22px; font-weight: 600; line-height: 1.25; margin: 0; text-wrap: balance; }
.notice { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin: 0 0 24px; padding: 12px 16px;
          border-radius: 8px; background: var(--hl); font-size: 16px; }
.notice.bad { background: var(--warn-bg); color: var(--ink); border-left: 4px solid var(--warn); }

/* ---- tab 1: run a test --------------------------------------------------------------- */
.intro { margin: 0 0 24px; font-size: 17px; max-width: 70ch; text-wrap: pretty; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 24px; align-items: start; }
.choice { display: flex; flex-direction: column; gap: 16px; padding: 24px; background: var(--panel);
          border: 1px solid var(--line); border-radius: 12px; box-shadow: var(--shadow); }
.choice.selected { border: 2px solid var(--ink); padding: 23px; }
.choice p { margin: 0; text-wrap: pretty; }
.choice .facts { color: var(--muted); }
.choice-act { display: flex; flex-wrap: wrap; gap: 8px; }
.choice:not(.selected) .start, .choice:not(.selected) .choice-body { display: none; }
.choice.selected .choose { display: none; }
.choice .start { width: 100%; }
.choice-body { display: flex; flex-direction: column; gap: 12px; }
.field-label { font-weight: 600; font-size: 15px; }
select, input[type="text"] { width: 100%; min-height: 44px; padding: 0 12px; border-radius: 8px; border: 1px solid var(--line);
                             background: var(--page); }
.add-row { display: flex; gap: 8px; margin-top: 8px; }
.add-row input { flex: 1; min-width: 0; }
.add-msg { margin: 0; font-size: 15px; min-height: 1.45em; }
.add-msg.ok { color: var(--ink); }
.add-msg.bad { color: var(--warn); font-weight: 600; }
input[aria-invalid="true"] { border-color: var(--warn); }

/* ---- tab 2: watch live ---------------------------------------------------------------- */
.viewbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin: 0 0 16px; padding: 8px 16px;
           background: var(--hl); border-radius: 8px; }
.narration { margin: 0 0 16px; font-size: 24px; line-height: 1.3; font-weight: 500; letter-spacing: -.01em;
             max-width: 60ch; text-wrap: pretty; }
.narration.attack { color: var(--ink); }
.empty-act { margin: -4px 0 16px; }
.phases { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 0; margin: 0 0 24px; padding: 0; list-style: none; font-size: 15px; }
.phases li { display: flex; align-items: center; color: var(--muted); white-space: nowrap; }
.phases li + li::before { content: ""; width: 28px; height: 1px; margin: 0 10px; background: var(--line); }
.phases .ph { padding: 4px 10px; border-radius: 6px; }
.phases .done .ph { color: var(--ink); }
.phases .now .ph { color: var(--ink); font-weight: 700; text-decoration: underline; text-decoration-thickness: 2px; text-underline-offset: 5px; background: var(--grid); }
.phases .atk .ph { background-image: repeating-linear-gradient(45deg, var(--attack-hatch) 0 1.5px, transparent 1.5px 7px); color: var(--ink); }
.phases .atk.now .ph { background-color: var(--attack-tint); }
.numbers { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin: 0 0 24px; }
.number { padding: 12px 16px; background: var(--panel); border: 1px solid var(--grid); border-radius: 10px; box-shadow: var(--shadow); min-width: 0; }
.number dt { font-size: 15px; font-weight: 600; }
.number .big { margin: 2px 0 0; font-size: 40px; font-weight: 700; line-height: 1.1; letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
.number .big.none { color: var(--muted); }
.number .sub { margin: 0; font-size: 15px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.number .sub.alert { color: var(--attack-ink); font-weight: 600; }
.panels { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 24px; margin: 0 0 24px; }
.card { display: flex; flex-direction: column; min-width: 0; padding: 16px; background: var(--panel);
        border: 1px solid var(--grid); border-radius: 10px; box-shadow: var(--shadow); }
.legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 14px; color: var(--muted); align-items: center; margin-top: 8px; }
.legend span { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
.chart { position: relative; height: clamp(190px, calc(100vh - 560px), 360px); margin-top: 4px; }
.chart > svg { position: absolute; inset: 0; width: 100%; height: 100%; display: block; overflow: visible; }
.chart:focus-visible { outline-offset: 4px; border-radius: 4px; }
.caption { min-height: 22px; margin-top: 6px; font-size: 15px; display: flex; gap: 8px; align-items: center; white-space: nowrap; overflow: hidden; }
.caption .cap-text { overflow: hidden; text-overflow: ellipsis; }
.badge { flex: none; padding: 0 8px; border-radius: 10px; background: var(--grid); color: var(--ink); font-size: 14px; }
.tip { position: absolute; z-index: 3; pointer-events: none; min-width: 150px; max-width: 300px; padding: 6px 10px; border-radius: 8px;
       background: var(--panel); border: 1px solid var(--line); box-shadow: 0 4px 16px rgba(0, 0, 0, .14); font-size: 15px; line-height: 1.35; }
.tip .tip-head { font-weight: 700; margin-bottom: 2px; }
.tip .tip-row { display: flex; align-items: center; gap: 6px; font-variant-numeric: tabular-nums; }
.tip .tip-row b { margin-left: auto; padding-left: 12px; }
[data-tip] { position: relative; }
[data-tip]:focus-visible::after { content: attr(data-tip); position: absolute; left: 0; top: calc(100% + 6px); z-index: 30;
  width: max-content; max-width: 280px; padding: 6px 10px; border-radius: 8px; background: var(--ink); color: var(--panel);
  font: 400 14px/1.35 system-ui, sans-serif; white-space: normal; pointer-events: none; }
svg text { font-family: inherit; }
.ax { font-size: 14px; fill: var(--muted); font-variant-numeric: tabular-nums; }
.lbl { font-size: 14px; fill: var(--ink); font-variant-numeric: tabular-nums; }
.lbl-b { font-size: 14px; fill: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums; }
.lbl-hot { font-size: 14px; fill: var(--attack-ink); font-weight: 700; font-variant-numeric: tabular-nums; }
.halo { paint-order: stroke; stroke: var(--panel); stroke-width: 4px; stroke-linejoin: round; }
.empty { font-size: 16px; fill: var(--muted); }
.gridline { stroke: var(--grid); stroke-width: 1; }
.axisline { stroke: var(--line); stroke-width: 1; }
.thresh { stroke: var(--ink); stroke-width: 1.25; stroke-dasharray: 5 4; }
.att-fill { fill: var(--attack-tint); }
.att-hatch { stroke: var(--attack-hatch); stroke-width: 1.5; }
.other-line { fill: none; stroke: var(--muted); stroke-width: 1.5; stroke-opacity: .65; stroke-linejoin: round; }
.target-line { fill: none; stroke-width: 3; stroke-linejoin: round; stroke-linecap: round; }
.cong { fill: var(--attack); }
.cong-guide { stroke: var(--attack); stroke-opacity: .28; stroke-width: 1; }
.rr-guide { stroke-opacity: .35; stroke-width: 1; }
.cross { stroke: var(--ink); stroke-width: 1; stroke-opacity: .5; }
.lk { fill: none; stroke-linecap: round; }
.lk-none { stroke: var(--lk-none); stroke-width: 1.25; }
.lk-low { stroke: var(--lk-low); stroke-width: 1.75; }
.lk-mid { stroke: var(--lk-mid); stroke-width: 3.5; }
.lk-hot { stroke: var(--attack); stroke-width: 6; }
.node { fill: var(--panel); stroke: var(--muted); stroke-width: 1.25; }
.gnode { fill: var(--panel); stroke: var(--ink); stroke-width: 1.5; }
.host { fill: var(--muted); }
.pod { fill: none; stroke: var(--grid); stroke-width: 1.25; }
.old-path { fill: none; stroke: var(--ink); stroke-width: 3; stroke-dasharray: 7 5; stroke-linecap: round; stroke-linejoin: round; }
.new-path { fill: none; stroke-width: 5; stroke-linecap: round; stroke-linejoin: round; }
.row-base { stroke: var(--grid); stroke-width: 1; }
.run-span { stroke-width: 4; stroke-opacity: .28; stroke-linecap: round; }
.held-dot { fill: var(--muted); }
.drop-x { stroke: var(--ink); stroke-width: 1.75; stroke-linecap: round; }
.row-hit { fill: transparent; cursor: pointer; }
.row-view { fill: var(--hl); }
g[data-run]:focus { outline: none; }
g[data-run]:focus-visible .row-hit { stroke: var(--ink); stroke-width: 2; }
.glyph-cong { color: var(--attack-ink); }

/* ---- details (collapsed): timeline + logs --------------------------------------------- */
details.more { margin: 0 0 24px; border: 1px solid var(--grid); border-radius: 10px; background: var(--panel); }
details.more > summary { display: flex; align-items: center; gap: 8px; min-height: 48px; padding: 0 16px; cursor: pointer;
                         font-weight: 600; list-style: none; }
details.more > summary::-webkit-details-marker { display: none; }
details.more > summary::before { content: "▸"; display: inline-block; width: 1em; transition: transform 150ms ease-out; }
details.more[open] > summary::before { transform: rotate(90deg); }
.more-body { padding: 0 16px 16px; display: flex; flex-direction: column; gap: 24px; }
.timeline-chart { height: 220px; }
.logs { display: flex; flex-direction: column; height: 460px; border: 1px solid var(--grid); border-radius: 8px; min-width: 0; }
.logbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 6px 12px; border-bottom: 1px solid var(--grid); font-size: 15px; }
.tabs { display: flex; gap: 2px; }
.tabs button { border: none; background: none; min-height: 36px; padding: 0 12px; border-radius: 6px; cursor: pointer; color: var(--muted); }
.tabs button[aria-selected="true"] { color: var(--ink); background: var(--grid); font-weight: 600; }
.err-badge { margin-left: auto; color: var(--warn); font-weight: 600; white-space: nowrap; }
.logtools { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.logtools button, .logtools input[type="search"] { border: 1px solid var(--line); border-radius: 6px; background: var(--page); padding: 2px 10px; min-height: 36px; }
.logtools button { cursor: pointer; }
.logtools button[aria-pressed="true"] { background: var(--ink); color: var(--panel); }
.logtools input[type="search"] { width: 14em; }
.logtools label { white-space: nowrap; display: inline-flex; gap: 4px; align-items: center; }
.cmd { padding: 4px 12px; font: 14px/1.3 ui-monospace, monospace; color: var(--muted); overflow-wrap: anywhere; }
.panes { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.pane { flex: 1; min-height: 0; display: flex; flex-direction: column; position: relative; }
.pane:not(.active) { display: none; }
.pane h3 { font-size: 15px; font-weight: 600; margin: 0; padding: 4px 12px; color: var(--muted); }
.list { flex: 1; overflow: auto; padding: 4px 12px 12px; font-family: ui-monospace, monospace; font-size: var(--fs); line-height: 1.4; }
.list > * { white-space: pre-wrap; overflow-wrap: anywhere; }
.jump { position: absolute; right: 16px; bottom: 16px; display: none; background: var(--ink); color: var(--panel); border: none;
        border-radius: 6px; padding: 4px 12px; cursor: pointer; min-height: 36px; }
.jump.show { display: block; }
.sep { margin: .5em 0; padding: .2em .4em; border-radius: 4px; background: var(--hl); font-weight: 700; font-family: system-ui, sans-serif; }
.ev .head { display: flex; gap: .6em; width: 100%; text-align: left; border: none; padding: .05em 0; background: none;
            font-family: inherit; font-size: inherit; cursor: pointer; color: inherit; margin: 0; min-height: 0; }
.ev .time { color: var(--muted); flex: none; }
.ev .kind { flex: none; font-weight: 700; min-width: 7.5em; }
.ev pre { margin: .2em 0 .5em 1.5em; padding: .4em; background: var(--page); border-radius: 4px; white-space: pre-wrap; overflow-wrap: anywhere; }
.k-congestion .kind, .k-congestion .msg { color: var(--attack-ink); }
.k-held .kind, .k-held .msg, .k-new_flow .kind, .k-new_flow .msg, .k-stats .kind, .k-stats .msg { color: var(--muted); }
.k-dropped .kind, .k-dropped .msg, .k-unresolved .kind { color: var(--warn); }
.k-decision .kind, .k-no_candidate .kind, .k-reroute_skipped .kind { color: var(--muted); }
body:not(.show-stats) .k-stats { display: none; }
.file { margin: .4em 0; font-family: system-ui, sans-serif; }
.file pre { margin: .2em 0; padding: .4em; background: var(--page); border-radius: 4px; font-family: ui-monospace, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
.file.error, .err { color: var(--warn); }
.hidden-by-filter { display: none !important; }

/* ---- tab 3: results --------------------------------------------------------------------- */
.partial { margin: 0 0 8px; font-size: 15px; font-weight: 600; color: var(--warn); }
.conclusion { margin: 0 0 16px; font-size: 24px; line-height: 1.3; font-weight: 500; letter-spacing: -.01em; max-width: 60ch; text-wrap: pretty; }
.res-reports { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; margin: 0 0 20px; }
.res-reports .bar-val { font-size: 15px; }
.segmented { display: inline-flex; gap: 0; margin: 0 0 24px; padding: 3px; border-radius: 10px; background: var(--grid); flex-wrap: wrap; }
.segmented button { min-height: 40px; padding: 0 16px; border: none; border-radius: 8px; background: none; cursor: pointer;
                    font-weight: 600; color: var(--muted); }
.segmented button[aria-pressed="true"] { background: var(--panel); color: var(--ink); box-shadow: 0 1px 3px rgba(0, 0, 0, .15); }
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 24px; margin: 0 0 24px; }
.metric { padding: 16px; background: var(--panel); border: 1px solid var(--grid); border-radius: 10px; box-shadow: var(--shadow); display: flex; flex-direction: column; gap: 8px; }
.metric-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.metric h3 { margin: 0; font-size: 18px; font-weight: 600; }
.better { font-size: 14px; color: var(--muted); white-space: nowrap; }
.bars { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.bar-row { display: grid; grid-template-columns: 150px minmax(0, 1fr) 96px; align-items: center; gap: 10px; min-height: 28px; }
.bar-name { display: inline-flex; align-items: center; gap: 6px; font-size: 15px; white-space: nowrap; }
.bar-track { height: 18px; position: relative; }
.bar { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 0 4px 4px 0; min-width: 2px; }
.bar-val { font-size: 15px; font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.bar-val.none { font-weight: 400; color: var(--muted); }
.concl { margin: 4px 0 0; font-size: 15px; text-wrap: pretty; }
table.data { border-collapse: collapse; width: 100%; font-size: 15px; font-variant-numeric: tabular-nums; }
table.data th, table.data td { text-align: right; padding: 6px 10px; border-bottom: 1px solid var(--grid); white-space: nowrap; }
table.data th:first-child, table.data td:first-child { text-align: left; }
table.data thead th { font-weight: 600; color: var(--muted); }
.table-wrap { overflow-x: auto; }
.files { margin: 0; padding-left: 1.2em; display: flex; flex-direction: column; gap: 4px; }

/* ---- toast, dialogs ----------------------------------------------------------------------- */
.toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); z-index: 25; display: flex; align-items: center; gap: 12px;
         padding: 8px 8px 8px 16px; border-radius: 12px; background: var(--ink); color: var(--panel);
         box-shadow: 0 8px 28px rgba(0, 0, 0, .25); animation: toast-in 200ms ease-out; }
.toast .btn { background: var(--panel); color: var(--ink); border-color: var(--panel); min-height: 36px; }
@keyframes toast-in { from { opacity: 0; transform: translate(-50%, 8px); } to { opacity: 1; transform: translate(-50%, 0); } }
dialog { padding: 0; border: 1px solid var(--line); border-radius: 12px; background: var(--panel); color: var(--ink);
         box-shadow: 0 2px 6px rgba(0, 0, 0, .08), 0 16px 48px rgba(0, 0, 0, .22); max-width: min(560px, calc(100vw - 32px)); }
dialog::backdrop { background: rgba(20, 20, 22, .45); }
dialog[open] { animation: dlg-in 160ms ease-out; }
@keyframes dlg-in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
.dlg { padding: 24px; }
.dlg h2 { margin: 0 0 12px; }
.dlg p, .dlg li { line-height: 1.45; text-wrap: pretty; }
.dlg p { margin: 0 0 8px; }
.dlg ul { margin: 0; padding-left: 1.2em; }
.dlg li + li { margin-top: 6px; }
.dlg-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; margin-top: 24px; }
.bigline { font-size: 20px; font-weight: 700; }
dialog.sheet { margin: 0 0 0 auto; width: min(480px, 100vw); max-width: 100vw; height: 100vh; max-height: 100vh;
               border-radius: 0; border-width: 0 0 0 1px; }
dialog.sheet[open] { animation-name: sheet-in; }
@keyframes sheet-in { from { opacity: 0; transform: translateX(16px); } to { opacity: 1; transform: none; } }
.sheet-head { position: sticky; top: 0; display: flex; align-items: center; justify-content: space-between; gap: 12px;
              padding: 14px 24px; background: var(--panel); border-bottom: 1px solid var(--grid); }
.gloss { margin: 0; padding: 4px 24px 24px; }
.gloss dt { margin-top: 16px; font-weight: 700; }
.gloss dd { margin: 2px 0 0; line-height: 1.45; text-wrap: pretty; }
.gloss-group { margin: 24px 0 0; font-size: 15px; font-weight: 600; color: var(--muted); }

@media (max-width: 1100px) {
  .brand { display: none; }
  .panels { grid-template-columns: minmax(0, 1fr); }
  .numbers { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .chart { height: 280px; }
}
@media (max-width: 720px) {
  .topbar { height: auto; min-height: 56px; flex-wrap: wrap; gap: 4px 12px; padding: 4px 16px; }
  .pagetabs { order: 3; width: 100%; overflow-x: auto; min-height: 44px; }
  .pagetabs [role="tab"] { padding: 0 10px; }
  .status { margin-left: 0; flex: 1; }
  main { padding: 132px 16px 48px; }
  .narration, .conclusion { font-size: 21px; }
  .number .big { font-size: 32px; }
  .bar-row { grid-template-columns: 120px minmax(0, 1fr) 80px; }
  .metrics { grid-template-columns: minmax(0, 1fr); }
}
@media (prefers-reduced-motion: reduce) {
  dialog[open], .toast { animation: none; }
  .btn, details.more > summary::before { transition: none; }
}
</style>
</head>
<body data-mode="__MODE__" data-token="__TOKEN__">
<header class="topbar">
  <span class="brand">ResilientSDN test bench</span>
  <div class="pagetabs" role="tablist" aria-label="Sections" id="pagetabs">
    <button type="button" role="tab" id="tab-run" aria-controls="panel-run" aria-selected="true">Run a test<span class="tab-dot" hidden></span></button>
    <button type="button" role="tab" id="tab-live" aria-controls="panel-live" aria-selected="false" tabindex="-1">Watch live<span class="tab-dot" hidden></span></button>
    <button type="button" role="tab" id="tab-results" aria-controls="panel-results" aria-selected="false" tabindex="-1">Results<span class="tab-dot" hidden></span></button>
  </div>
  <div class="status" id="status">
    <span class="st-icon" id="st-icon" aria-hidden="true">○</span>
    <span class="st-text"><span class="st-word" id="st-word" role="status" aria-live="polite">Ready</span>
      <span class="st-sub" id="st-sub" aria-hidden="true"></span></span>
  </div>
  <button type="button" class="btn" id="btn-stop" disabled><span class="glyph" aria-hidden="true">■</span> Stop</button>
</header>

<main id="main">
<div class="head">
  <h1 id="page-title">Run a test</h1>
  <button type="button" class="btn" id="glossary-open" aria-haspopup="dialog">Glossary</button>
</div>

<!-- ① Run a test ---------------------------------------------------------------------- -->
<section id="panel-run" role="tabpanel" aria-labelledby="tab-run">
  <p class="why">The three ways to test, from a 7-minute demo to the full study. Same tests, same traffic for every system, so the comparison is fair.</p>
  <p class="intro">Choose how much to test. Each run starts a small virtual network, sends traffic, and (in half the runs) floods one link to see how each system copes.</p>
  <div class="notice" id="run-busy" hidden><span>A test is running – see Watch live.</span>
    <button type="button" class="btn" id="go-live">Watch live</button></div>
  <p class="notice" id="run-terminal" hidden>Started from the terminal – stop it there or restart with <code>python3 tools/live_view.py</code> to use these buttons.</p>
  <p class="notice bad" id="run-password" hidden>Password needed: type your computer password in the terminal window first, then the buttons work again.</p>
  <p class="notice bad" id="run-error" role="alert" hidden></p>
  <div class="cards">
    <section class="choice" id="card-demo" data-choice="demo" aria-labelledby="h-demo">
      <h2 id="h-demo">Quick demo</h2>
      <p class="facts">Fat-tree, 3 systems × with/without attack = 6 runs. About 6–8 min.</p>
      <div class="choice-act">
        <button type="button" class="btn primary big start" id="btn-demo"><span class="glyph" aria-hidden="true">▶</span> Start demo</button>
        <button type="button" class="btn choose" data-choose="demo" aria-label="Choose Quick demo">Choose</button>
      </div>
    </section>
    <section class="choice" id="card-topo" data-choice="topo" aria-labelledby="h-topo">
      <h2 id="h-topo">Test a topology</h2>
      <p class="facts">The same 6 runs on a network of your choice: a real backbone or one you add from the Topology Zoo.</p>
      <div class="choice-body">
        <label class="field-label" for="topo-select">Network</label>
        <select id="topo-select"></select>
        <form id="add-form" novalidate>
          <label class="field-label" for="add-name">Add a network from the Topology Zoo:</label>
          <div class="add-row">
            <input type="text" id="add-name" maxlength="40" autocomplete="off" spellcheck="false" placeholder="for example Abilene" aria-describedby="add-msg">
            <button type="submit" class="btn" id="add-btn">Add</button>
          </div>
          <p class="add-msg" id="add-msg" role="status" aria-live="polite"></p>
        </form>
      </div>
      <div class="choice-act">
        <button type="button" class="btn primary big start" id="btn-topo"><span class="glyph" aria-hidden="true">▶</span> Start test</button>
        <button type="button" class="btn choose" data-choose="topo" aria-label="Choose Test a topology">Choose</button>
      </div>
    </section>
    <section class="choice" id="card-full" data-choice="full" aria-labelledby="h-full">
      <h2 id="h-full">Complete evaluation</h2>
      <p class="facts">Every topology, all repeats. 17–19 h. Leave the computer on.</p>
      <div class="choice-act">
        <button type="button" class="btn primary big start" id="btn-full"><span class="glyph" aria-hidden="true">▶</span> Start full run…</button>
        <button type="button" class="btn choose" data-choose="full" aria-label="Choose Complete evaluation">Choose</button>
      </div>
    </section>
  </div>
</section>

<!-- ② Watch live ------------------------------------------------------------------------- -->
<section id="panel-live" role="tabpanel" aria-labelledby="tab-live" hidden>
  <div class="viewbar" id="viewbar" role="region" aria-label="Viewing a finished run" hidden>
    <span id="view-text"></span>
    <button type="button" class="btn" id="back-live">Back to live</button>
  </div>
  <p class="narration" id="narration" aria-live="polite" aria-atomic="true">Connecting…</p>
  <div class="empty-act" id="live-empty" hidden><button type="button" class="btn primary" id="go-run">Go to Run a test</button></div>
  <ol class="phases" id="phases" aria-label="Run phases"></ol>
  <dl class="numbers" id="numbers">
    <div class="number"><dt>Busiest link</dt><dd class="big none" id="n-busy">–</dd><dd class="sub" id="n-busy-sub">no data yet</dd></div>
    <div class="number"><dt>Seconds over the limit</dt><dd class="big none" id="n-above">–</dd><dd class="sub" id="n-above-sub">on the attacked link</dd></div>
    <div class="number"><dt>Reroutes</dt><dd class="big none" id="n-reroute">–</dd><dd class="sub" id="n-reroute-sub">traffic moved to another path</dd></div>
    <div class="number"><dt id="n-lost-label">Packets dropped</dt><dd class="big none" id="n-lost">–</dd><dd class="sub" id="n-lost-sub">by switches</dd></div>
  </dl>
  <div class="panels">
    <section class="card" aria-labelledby="h-link">
      <h2 id="h-link">Link use over time</h2>
      <p class="panel-why">Shows how full the attacked link (bold) and the busiest other links are, second by second. Above the dashed 0.8 line a link is congested and packets start to queue and drop.</p>
      <div class="chart" id="chart-link" tabindex="0">
        <svg id="svg-link" role="img" aria-label="Link use over time: waiting for link stats"></svg>
        <div class="tip" id="tip-link" hidden></div>
      </div>
      <div class="legend" id="legend-link"></div>
    </section>
    <section class="card" aria-labelledby="h-map">
      <h2 id="h-map">Network map</h2>
      <p class="panel-why">Shows where traffic flows now and which links are full. You can see the controller move traffic away from the flooded link.</p>
      <div class="chart" id="chart-map">
        <svg id="svg-map" role="img" aria-label="Network map"></svg>
      </div>
      <div class="caption" id="caption-map" aria-live="polite"></div>
      <div class="legend" id="legend-map"></div>
    </section>
  </div>
  <details class="more" id="live-details">
    <summary>Show details (controller actions, logs, run files)</summary>
    <div class="more-body">
      <section aria-labelledby="h-timeline">
        <h2 id="h-timeline">Controller actions per run</h2>
        <p class="panel-why">Each row is one run; ▲ congestion seen, │ traffic moved, ● move held back to avoid flip-flopping. Click a row to show that run above.</p>
        <div class="chart timeline-chart" id="chart-timeline">
          <svg id="svg-timeline" role="group" aria-label="Controller actions per run"></svg>
        </div>
        <div class="legend" id="legend-timeline"></div>
      </section>
      <section class="logs" id="logs" aria-label="Logs">
        <div class="logbar">
          <div class="tabs" role="tablist" aria-label="Log panes" id="logtabs">
            <button type="button" role="tab" data-pane="term" id="ltab-term" aria-selected="true" aria-controls="pane-term">Terminal</button>
            <button type="button" role="tab" data-pane="events" id="ltab-events" aria-selected="false" aria-controls="pane-events" tabindex="-1">Events</button>
            <button type="button" role="tab" data-pane="log" id="ltab-log" aria-selected="false" aria-controls="pane-log" tabindex="-1">Controller log</button>
          </div>
          <div class="logtools">
            <input type="search" id="filter" placeholder="Filter lines…" aria-label="Filter lines">
            <button type="button" id="pause" aria-pressed="false">Pause</button>
            <label><input type="checkbox" id="stats"> stats events</label>
            <button type="button" id="smaller" aria-label="Smaller log text">A−</button>
            <button type="button" id="bigger" aria-label="Bigger log text">A+</button>
          </div>
          <span class="err-badge" id="err-badge" hidden></span>
        </div>
        <div class="cmd" id="cmd"></div>
        <div class="panes">
          <section class="pane active" id="pane-term" role="tabpanel" aria-labelledby="ltab-term">
            <div class="list" id="list-term" tabindex="0"></div><button type="button" class="jump">Jump to latest ↓</button></section>
          <section class="pane" id="pane-events" role="tabpanel" aria-labelledby="ltab-events">
            <h3>Controller events (click a line for all details)</h3>
            <div class="list" id="list-events" tabindex="0"></div><button type="button" class="jump">Jump to latest ↓</button></section>
          <section class="pane" id="pane-log" role="tabpanel" aria-labelledby="ltab-log">
            <h3>Controller log (ryu.log) and run files</h3>
            <div class="list" id="list-log" tabindex="0"></div><button type="button" class="jump">Jump to latest ↓</button></section>
        </div>
      </section>
    </div>
  </details>
</section>

<!-- ③ Results ------------------------------------------------------------------------------ -->
<section id="panel-results" role="tabpanel" aria-labelledby="tab-results" hidden>
  <p class="why">Compares the three systems on the same tests. It shows whether ResilientSDN avoids congestion without constantly changing paths.</p>
  <div id="res-empty" hidden>
    <p class="narration">No results yet. Run the quick demo (about 7 min) to fill this page.</p>
    <button type="button" class="btn primary big" id="res-demo"><span class="glyph" aria-hidden="true">▶</span> Start demo</button>
  </div>
  <div id="res-body" hidden>
    <p class="partial" id="res-partial" hidden></p>
    <p class="conclusion" id="res-headline"></p>
    <div class="res-reports" id="res-reports" aria-label="Report downloads"></div>
    <div class="segmented" role="group" aria-label="Which test" id="res-scen"></div>
    <div class="metrics" id="res-metrics"></div>
    <details class="more"><summary>Show as table</summary>
      <div class="more-body"><div class="table-wrap"><table class="data" id="res-table"></table></div></div></details>
    <details class="more"><summary>Show details (per-run numbers, files)</summary>
      <div class="more-body"><ul class="files" id="res-files"></ul>
        <div class="table-wrap"><table class="data" id="res-runs"></table></div></div></details>
  </div>
</section>
</main>

<div class="toast" id="toast" role="status" hidden><span>Results ready</span>
  <button type="button" class="btn" id="toast-back">Back to Watch live</button></div>
<div id="tip-descs" hidden></div>

<dialog id="confirm-full" aria-labelledby="cf-title" aria-describedby="cf-body">
  <div class="dlg">
    <h2 id="cf-title">Start the complete evaluation?</h2>
    <div id="cf-body">
      <p class="bigline">It takes 17–19 hours.</p>
      <ul>
        <li>Keep the computer awake and plugged in the whole time (turn off sleep).</li>
        <li>You can stop it at any time and start it again later: finished runs are skipped.</li>
        <li>Results go to results/fattree_5runs, results/wan_5runs and results/ablation_A–D.</li>
      </ul>
    </div>
    <div class="dlg-actions">
      <button type="button" class="btn" id="cf-cancel">Cancel</button>
      <button type="button" class="btn primary" id="cf-go">Start the 17–19 h run</button>
    </div>
  </div>
</dialog>

<dialog id="glossary" class="sheet" aria-labelledby="gl-title">
  <div class="sheet-head">
    <h2 id="gl-title">Glossary</h2>
    <button type="button" class="btn" id="glossary-close">Close</button>
  </div>
  <dl class="gloss">
    <dt>S1</dt><dd>No attack: normal traffic only. Shows what each system costs on a quiet network.</dd>
    <dt>S2</dt><dd>Low single attacker: one host floods one link with 30 Mbit/s of junk traffic.</dd>
    <dt>S3</dt><dd>High single attacker: one host floods one link with 70 Mbit/s of junk traffic.</dd>
    <dt>S4</dt><dd>Distributed 3x: three hosts each flood the link with 20 Mbit/s.</dd>
    <dt>S5</dt><dd>Heavy 5x: five hosts each flood the link with 15 Mbit/s.</dd>
    <dt>S6</dt><dd>Sustained 10min: three attackers at 30 Mbit/s each, kept up for 10 minutes instead of the usual 60s.</dd>
    <dt>S11</dt><dd>Overload 2min: same attack as S6 (three attackers, 30 Mbit/s each) but only 2 minutes, for quicker runs.</dd>
    <dt>S7</dt><dd>Threshold evasion: attack load alternates high/low every 4s so the link is never over the limit for long enough to trigger a reroute.</dd>
    <dt>S8</dt><dd>Cooldown exploit: an 8s burst at 90% load every 31s, timed to land right after the controller's 30s cooldown ends.</dd>
    <dt>S9</dt><dd>Capacity presaturation: first loads the backup paths to 79% so they look almost full, then attacks at 90% — there is nowhere safe left to reroute to.</dd>
    <dt>S10</dt><dd>Randomised thresholds: a random mix of S7, S8 or S9 each run, so the controller cannot be tuned to one fixed pattern.</dd>
    <dt>Congestion</dt><dd>A link carrying so much traffic that packets queue up or get lost; here it means the link is above the 0.8 limit.</dd>
    <dt>The 0.8 limit</dt><dd>The dashed line in the link graph: a link using 80% or more of its capacity counts as congested.</dd>
    <dt>Reroute</dt><dd>The controller moves a flow (one host-to-host conversation) onto a different path to get it off a busy link.</dd>
    <dt>Held back</dt><dd>The controller waits instead of moving a flow when the new path is only slightly better, so traffic does not flip back and forth (also called hysteresis).</dd>
    <dt>Path flip-flop</dt><dd>A reroute that puts a flow back on a path it used before (an oscillation). Fewer is better.</dd>
    <dt>DPP score</dt><dd>Drift-plus-penalty: ResilientSDN’s score for a candidate path, higher when the path leaves links less loaded and needs fewer switch rule changes.</dd>
    <dt>Topology</dt><dd>The shape of the test network: which switches exist and how they are linked. Fat-tree is a data-centre layout; the others are real backbones with city names.</dd>
    <dt>Topology Zoo</dt><dd>A public collection of real network maps (topology-zoo.org). Networks added from it appear under “Downloaded”.</dd>
  </dl>
  <p class="gloss gloss-group">The three systems compared</p>
  <dl class="gloss">
    <dt>Static routing</dt><dd>■ Picks the shortest path for each flow once and never changes it: the “do nothing” baseline.</dd>
    <dt>Simple reactive</dt><dd>● As soon as a link goes over 0.8, moves every flow on it to the shortest other path, without checking that path or waiting.</dd>
    <dt>ResilientSDN</dt><dd>◆ The system being tested: confirms congestion from the trend, scores candidate paths (DPP) and holds back moves that would cause flip-flopping.</dd>
  </dl>
</dialog>
<script>
'use strict';
// All log text is untrusted: it is only ever inserted with textContent (HTML) or
// textContent / setAttribute on SVG nodes made with createElementNS.
const MAX_LINES = 20000;          // per log pane
const MAX_STATS = 1500;           // stats samples kept for the current run
const MAX_EVENTS = 3000;          // controller actions kept per run (timeline)
const DRAW_EVERY_MS = 250;        // graphs redraw at most ~4 times a second
const MAX_UTIL = 2;               // link readings above this are impossible and left out
const SETUP_SECONDS = 15;         // starting Mininet + controller, per run (estimate)
const MANUAL_HOLD_MS = 20000;     // no automatic tab switch this soon after the user chose one
const $ = (id) => document.getElementById(id);
const SVGNS = $('svg-link').namespaceURI;
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const SYSTEMS = ['static', 'simple_reactive', 'resilient_sdn'];   // fixed order everywhere
const SHAPE = {resilient_sdn: 'diamond', simple_reactive: 'circle', static: 'square'};
const SYS_NAME = {static: 'Static routing', simple_reactive: 'Simple reactive', resilient_sdn: 'ResilientSDN'};
const SYS_GLYPH = {static: '■', simple_reactive: '●', resilient_sdn: '◆'};
const ROLE = {c: 'core switch', a: 'aggregation switch', e: 'edge switch', h: 'host'};
const CORE_AGG = /^(c\d+-a\d+|a\d+-c\d+)$/;
const ZOO_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$/;
const MODE = document.body.dataset.mode || 'command';   // control | command | watch
const TOKEN = document.body.dataset.token || '';        // control mode: sent back with each request
const KEEP_STATS_RUNS = 40;       // finished runs whose link samples stay in memory (replay)
const KEEP_STATS_SAMPLES = 400;   // samples kept per finished run
const LOADED_AT = Date.now() / 1000;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
function svg(tag, attrs, parent) {
  const node = document.createElementNS(SVGNS, tag);
  for (const k in attrs) node.setAttribute(k, attrs[k]);
  if (parent) parent.append(node);
  return node;
}
function stext(parent, x, y, text, cls, anchor) {
  const t = svg('text', {x: r1(x), y: r1(y), class: cls || 'lbl'}, parent);
  if (anchor) t.setAttribute('text-anchor', anchor);
  t.textContent = text;
  return t;
}
function r1(v) { return Math.round(v * 10) / 10; }
function pad(n) { return String(n).padStart(2, '0'); }
function mmss(s) {
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
  return (h ? h + ':' + pad(m) : m) + ':' + pad(s % 60);
}
function clock(t) {
  if (typeof t !== 'number') return '--:--:--';
  const d = new Date(t * 1000);
  return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
}
function hhmm(t) { const d = new Date(t * 1000); return pad(d.getHours()) + ':' + pad(d.getMinutes()); }
function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }
function setText(node, text) { if (node.textContent !== text) node.textContent = text; }   // no repeat announcements
function sysColor(sys) { return SHAPE[sys] ? 'var(--sys-' + sys + ')' : 'var(--sys-other)'; }
function sysName(sys) { return SYS_NAME[sys] || sys || 'the controller'; }
function marker(parent, sys, cx, cy, r) {
  let node;
  const shape = SHAPE[sys] || 'triangle';
  if (shape === 'circle') node = svg('circle', {cx: r1(cx), cy: r1(cy), r: r1(r * 0.85)}, parent);
  else if (shape === 'square') node = svg('rect', {x: r1(cx - r * 0.75), y: r1(cy - r * 0.75), width: r1(r * 1.5), height: r1(r * 1.5), rx: 1.5}, parent);
  else if (shape === 'diamond') node = svg('path', {d: `M${r1(cx)} ${r1(cy - r)}L${r1(cx + r)} ${r1(cy)}L${r1(cx)} ${r1(cy + r)}L${r1(cx - r)} ${r1(cy)}Z`}, parent);
  else node = svg('path', {d: `M${r1(cx)} ${r1(cy - r)}L${r1(cx + r)} ${r1(cy + r * 0.8)}L${r1(cx - r)} ${r1(cy + r * 0.8)}Z`}, parent);
  node.style.fill = sysColor(sys);
  return node;
}
function markerIcon(sys, size) {
  size = size || 14;
  const icon = svg('svg', {width: size, height: size, viewBox: '0 0 16 16', 'aria-hidden': 'true'});
  marker(icon, sys, 8, 8, 6.5);
  return icon;
}
function swatchLine(cls, style) {
  const icon = svg('svg', {width: 22, height: 12, viewBox: '0 0 22 12', 'aria-hidden': 'true'});
  const line = svg('line', {x1: 1, y1: 6, x2: 21, y2: 6, class: cls}, icon);
  if (style) for (const k in style) line.style.setProperty(k, style[k]);
  return icon;
}
function hatchDefs(svgNode, id) {
  const defs = svg('defs', {}, svgNode);
  const pat = svg('pattern', {id, patternUnits: 'userSpaceOnUse', width: 8, height: 8, patternTransform: 'rotate(45)'}, defs);
  svg('rect', {width: 8, height: 8, class: 'att-fill'}, pat);
  svg('line', {x1: 0, y1: 0, x2: 0, y2: 8, class: 'att-hatch'}, pat);
  return 'url(#' + id + ')';
}
function niceStep(span, px, minPx) {
  const raw = span / Math.max(1, px / (minPx || 70));
  for (const s of [5, 10, 20, 30, 60, 120, 300, 600, 1200]) if (s >= raw) return s;
  return 1800;
}
function boxSize(node) { return {w: node.clientWidth, h: node.clientHeight}; }
// Tooltips: title for the mouse, data-tip for a keyboard-focus bubble (CSS), and
// aria-describedby pointing at a hidden description for screen readers.
const tipIds = new Map();
function tip(node, text) {
  node.title = text;
  node.dataset.tip = text;
  if (!/^(BUTTON|A|INPUT|SELECT|TEXTAREA)$/.test(node.tagName)) node.tabIndex = 0;
  let id = tipIds.get(text);
  if (!id) {
    id = 'tipd-' + tipIds.size;
    tipIds.set(text, id);
    const span = el('span', null, text);
    span.id = id;
    $('tip-descs').append(span);
  }
  node.setAttribute('aria-describedby', id);
  return node;
}
function legendItem(text, tipText, ...icons) {
  const s = el('span');
  s.append(...icons, text);
  return tip(s, tipText);
}

// --- topologies (names, sizes, maps) -------------------------------------------------------
const T = {items: [], by: new Map(), key: ''};
function onTopologies(m) {
  if (!Array.isArray(m.items)) return;
  T.items = m.items.filter((i) => i && typeof i.name === 'string');
  T.by = new Map();
  for (const it of T.items) {
    it.nodeById = new Map((Array.isArray(it.nodes) ? it.nodes : []).map((n) => [String(n.id), n]));
    T.by.set(it.name, it);
  }
}
function topoTitle(name) { const it = T.by.get(name); return it ? it.title : (name === 'fat_tree' ? 'Fat-tree' : name || ''); }
function nodeName(id, topo, long) {
  const it = T.by.get(topo);
  const n = it && it.nodeById.get(String(id));
  if (n && n.label !== n.id) return long ? n.label : n.short;
  return String(id);
}
function linkName(key, topo, long) {   // "c1-a7" -> "c1–a7"; "s3-s7" -> "Chicago–New York"
  if (!key) return '';
  const parts = String(key).split('-');
  if (parts.length !== 2) return String(key);
  const [a, b] = parts;
  if (topo === 'fat_tree' || (!T.by.has(topo) && /^[cae]\d+$/.test(a))) {
    return long && ROLE[a[0]] && ROLE[b[0]] ? ROLE[a[0]] + ' ' + a + ' ↔ ' + ROLE[b[0]] + ' ' + b : a + '–' + b;
  }
  return nodeName(a, topo, long) + (long ? ' ↔ ' : '–') + nodeName(b, topo, long);
}

// --- state built from the stream -----------------------------------------------------
const S = {
  plan: [], planIndex: new Map(), timing: {}, scen: {}, state: '', current: null,
  runs: new Map(), order: [], results: new Map(), errors: 0, peakAll: null,
  lastReroute: null, badReadings: 0, view: null, kept: [], finish: null, outs: [], startedAt: null,
  campaign: null,   // {done, skipped, failed} parsed from the terminal's own summary line
  reports: new Map(),   // outs index -> true once its report.pdf is ready (see 'report' messages)
};
function resetRuns() {   // a new run started from the page: fresh graphs, the logs stay
  Object.assign(S, {plan: [], planIndex: new Map(), timing: {}, scen: {}, current: null,
    runs: new Map(), order: [], results: new Map(), errors: 0, peakAll: null, lastReroute: null,
    badReadings: 0, view: null, kept: [], finish: null});
  linkView.hoverX = null;
}
function newRun(name, info) {
  return {name, info: info || null, t0: null, lastT: null, firstFlowT: null, firstCongT: null,
          attackStart: null, attackEnd: null, exact: false, target: null, threshold: 0.8,
          meta: null, failed: false, reason: '', stats: [], events: [], counts: {}, timeAbove: 0,
          statsDropped: false};
}
function runFor(name, info) {
  let r = S.runs.get(name);
  if (!r) { r = newRun(name, info); S.runs.set(name, r); S.order.push(name); }
  if (info && !r.info) r.info = info;
  return r;
}
function curRun() { return S.current ? S.runs.get(S.current) : null; }
function viewRun() { return (S.view && S.runs.get(S.view)) || curRun(); }   // what the graphs show
function viewing() { const v = S.view && S.runs.get(S.view); return !!v && v !== curRun(); }
function canView(r) { return !!r && r.t0 !== null; }
function setView(name) {
  const r = name ? S.runs.get(name) : null;
  S.view = r && canView(r) && name !== S.current ? name : null;
  linkView.hoverX = null;
  invalidate();
}
function keepStats(r) {   // thin a finished run's samples and keep the last few runs
  const n = r.stats.length;
  if (n > KEEP_STATS_SAMPLES) {
    const k = Math.ceil(n / KEEP_STATS_SAMPLES);
    r.stats = r.stats.filter((_, i) => i % k === 0 || i === n - 1);
  }
  S.kept = S.kept.filter((name) => name !== r.name);
  S.kept.push(r.name);
  while (S.kept.length > KEEP_STATS_RUNS) {
    const old = S.runs.get(S.kept.shift());
    if (old && old.name !== S.view) { old.stats = []; old.statsDropped = true; }
  }
}
function sibling(r) {   // same topology / scenario / run: same traffic and target link
  if (!r || !r.info) return null;
  for (const o of S.runs.values()) {
    if (o !== r && o.info && o.meta && o.info.topology === r.info.topology
        && o.info.scenario === r.info.scenario && o.info.run === r.info.run) return o;
  }
  return null;
}
function targetOf(r) {
  if (!r) return null;
  if (r.target) return r.target;
  const sib = sibling(r);
  return sib ? sib.target : null;
}
function topoOf(r) { return r && r.info ? r.info.topology : (S.plan[0] && S.plan[0].topology) || null; }
function scenFor(r) {
  const s = r && r.info ? r.info.scenario : null;
  return S.scen[s] || {attack: !!s && s !== 'S1', label: s === 'S1' ? 'no attack' : 'attack', duration: null};
}
function timingFor(r) {
  const tm = S.timing || {}, sc = scenFor(r);
  return {warm: num(tm.warmup_seconds) ?? 3, delay: num(tm.attack_delay_seconds) ?? 10,
          dur: num(sc.duration) ?? num(tm.duration_seconds) ?? 60, clean: num(tm.cleanup_seconds) ?? 3,
          attack: !!sc.attack};
}
function isDone(r) {
  return !!(S.results.has(r.name) || r.meta || r.failed || (S.current !== r.name && r.t0 !== null));
}
function isLive(r) {   // still being written (a stopped or crashed run goes quiet)
  if (!r || r.t0 === null || isDone(r)) return false;
  return S.state === 'running' || Date.now() / 1000 - r.lastT < 60;
}
function nowFor(r) {
  if (!r || r.t0 === null) return null;
  return isLive(r) ? Math.max(Date.now() / 1000, r.lastT) : r.lastT;
}
function windows(r) {
  const tm = timingFor(r);
  let aStart = r.attackStart, aEnd = r.attackEnd;
  const exact = r.exact && aStart !== null;
  if (!exact) {
    aStart = r.firstFlowT !== null ? r.firstFlowT + tm.delay : null;
    if (aStart !== null && tm.attack && r.firstCongT !== null && r.firstCongT > r.firstFlowT)
      aStart = Math.min(aStart, r.firstCongT);
    aEnd = aStart !== null ? aStart + tm.dur : null;
  }
  return {t0: r.t0, traffic: r.firstFlowT, aStart, aEnd, exact, attack: tm.attack, tm};
}
function phaseOf(r) {
  if (!r) return {key: 'waiting'};
  if (r.failed) return {key: 'failed'};
  if (isDone(r)) return {key: 'done', w: windows(r)};
  if (r.t0 !== null && !isLive(r)) return {key: 'stalled', w: windows(r)};
  const w = windows(r), now = nowFor(r);
  if (now === null || w.traffic === null || now < w.traffic) return {key: 'warm', w, now};
  if (w.aStart === null || now < w.aStart) return {key: 'normal', w, now};
  if (now < w.aEnd) return {key: w.attack ? 'attack' : 'normal', w, now};
  return {key: 'cleanup', w, now};
}
function linkUtil(u, link) { return u ? num(u[link]) : null; }
function busiest(u, fatTree) {
  let best = null, bestV = -1;
  for (const k in u) {
    if (fatTree && !CORE_AGG.test(k)) continue;
    const v = num(u[k]);
    if (v !== null && v > bestV) { best = k; bestV = v; }
  }
  return best === null ? null : {link: best, value: bestV};
}
function isFatTree(r) { return !!(r && r.info && r.info.topology === 'fat_tree'); }
function timeAbove(r) {   // seconds the target link spent above the threshold (sample gaps)
  const target = targetOf(r);
  if (!target || r.stats.length === 0) return r.timeAbove || 0;
  const st = r.stats;
  const gaps = [];
  for (let i = 1; i < st.length; i++) gaps.push(st[i].t - st[i - 1].t);
  const typical = gaps.length ? gaps.slice().sort((a, b) => a - b)[gaps.length >> 1] : 0;
  let total = 0;
  for (let i = 0; i < st.length; i++) {
    const v = linkUtil(st[i].u, target);
    if (v !== null && v > r.threshold) total += i < gaps.length ? gaps[i] : typical;
  }
  return total;
}
function aboveOf(r) {   // seconds over the threshold: the run's result once it has one
  const res = S.results.get(r.name);
  if (res && num(res.values.time_above_s) !== null) return res.values.time_above_s;
  return r === curRun() ? timeAbove(r) : (r.timeAbove || timeAbove(r));
}

function onRun(m) {
  const prev = curRun();
  if (prev && prev.name !== m.name) { prev.timeAbove = timeAbove(prev); keepStats(prev); }
  const r = runFor(m.name, m.run);
  if (m.wiped) Object.assign(r, newRun(r.name, r.info));
  S.current = m.name;
}
function onEvent(m) {
  const r = curRun();
  if (!r) return;
  const t = num(m.time) ?? num(m.t);
  if (t === null) return;
  if (r.t0 === null) r.t0 = t;
  r.lastT = r.lastT === null ? t : Math.max(r.lastT, t);
  const raw = m.raw && typeof m.raw === 'object' ? m.raw : {};
  const x = t - r.t0;
  const kind = m.kind;
  if (kind === 'stats') {
    const util = raw.util && typeof raw.util === 'object' ? raw.util : null;
    if (!util) return;
    const u = {};
    for (const k in util) {   // utilisation is a share of capacity: > 2 is a broken reading
      const v = num(util[k]);
      if (v !== null && v >= 0 && v <= MAX_UTIL) u[k] = v; else S.badReadings += 1;
    }
    r.stats.push({x, t, u});
    if (r.stats.length > MAX_STATS) r.stats = r.stats.filter((_, i) => i % 2 === 0);   // thin, keep shape
    return;
  }
  if (kind === 'new_flow' && r.firstFlowT === null) r.firstFlowT = t;
  if (!['congestion', 'reroute', 'held', 'dropped'].includes(kind)) return;
  r.counts[kind] = (r.counts[kind] || 0) + 1;
  const ev = {x, t, kind};
  if (kind === 'congestion') {
    const links = Array.isArray(raw.links) ? raw.links.map(String) : [];
    ev.links = links;
    if (r.firstCongT === null) r.firstCongT = t;
    if (!r.exact && !r.target && links.length) r.target = links[0];
  } else if (kind === 'reroute') {
    ev.flow = String(raw.flow ?? '');
    ev.old = Array.isArray(raw.old) ? raw.old.map(String) : [];
    ev.new = Array.isArray(raw.new) ? raw.new.map(String) : [];
    ev.n = r.counts.reroute;
    S.lastReroute = {run: r.name, ev, at: performance.now()};
  } else {
    ev.flow = String(raw.flow ?? '');
  }
  r.events.push(ev);
  if (r.events.length > MAX_EVENTS) r.events.shift();
}
function lastLine(text) {
  const lines = String(text || '').split('\n').map((l) => l.trim()).filter(Boolean);
  const line = lines.length ? lines[lines.length - 1] : '';
  return line.length > 140 ? line.slice(0, 139) + '…' : line;
}
function onFile(m) {
  const r = curRun();
  if (!r) return;
  if (m.name === 'error.txt') { r.failed = true; r.reason = lastLine(m.content) || 'see error.txt'; return; }
  if (m.name !== 'meta.json' || typeof m.content !== 'string') return;
  let meta;
  try { meta = JSON.parse(m.content); } catch (err) { return; }
  if (!meta || typeof meta !== 'object') return;
  r.meta = meta;
  if (typeof meta.target_link === 'string') r.target = meta.target_link;
  if (num(meta.threshold) !== null) r.threshold = meta.threshold;
  if (num(meta.attack_start) !== null) {
    r.attackStart = meta.attack_start;
    r.attackEnd = num(meta.end) ?? meta.attack_start + timingFor(r).dur;
    r.exact = true;
  }
}
function onResult(m) {
  if (typeof m.name !== 'string' || !m.values || typeof m.values !== 'object') return;
  S.results.set(m.name, m);
}
function onReport(m) {
  if (typeof m.index === 'number' && m.ready) S.reports.set(m.index, true);
}
function onStatus(m) {
  if (m.state) S.state = m.state;
  if (Array.isArray(m.outs)) S.outs = m.outs.map(String);
  // a fresh run reuses the same outs indices for (probably) different folders
  if (m.state === 'running') { S.finish = null; S.startedAt = num(m.started); S.reports = new Map(); }
  if (m.state === 'finished') S.finish = {code: m.code, interrupted: !!m.interrupted, ended: num(m.ended) ?? m.t};
  const plan = m.plan;
  if (plan && Array.isArray(plan.runs)) {
    S.plan = plan.runs.filter((p) => p && typeof p.name === 'string');
    S.planIndex = new Map(S.plan.map((p, i) => [p.name, i]));
    S.timing = plan.timing || {};
    S.scen = plan.scenarios || {};
    for (const p of S.plan) runFor(p.name, p);
    S.order.sort((a, b) => (S.planIndex.get(a) ?? 1e9) - (S.planIndex.get(b) ?? 1e9));
  }
}

// --- campaign status (top bar) -----------------------------------------------------------
const C = {state: 'idle', action: null, label: '', code: null, stopped: false, sudo_ok: true, pending: false, error: ''};
function running() {
  if (MODE === 'control') return C.state === 'running' || C.state === 'stopping';
  if (S.state === 'running') return true;
  return MODE === 'watch' && isLive(curRun());
}
function campaign() {
  if (MODE === 'control' && !C.sudo_ok) return 'password';
  if (running()) return MODE === 'control' && C.state === 'stopping' ? 'stopping' : 'running';
  if (S.finish) return S.finish.interrupted ? 'stopped' : S.finish.code === 0 ? 'finished' : 'error';
  return MODE === 'watch' ? 'watching' : 'ready';
}
const CHIP = {
  ready: ['○', 'Ready'], running: ['●', 'Running'], stopping: ['●', 'Stopping…'], watching: ['○', 'Watching'],
  password: ['◐', 'Password needed – type it in the terminal'], finished: ['✓', 'Finished'],
  stopped: ['✕', 'Stopped'], error: ['!', 'Error'],
};
function runSeconds(p) {
  const tm = S.timing || {}, sc = S.scen[p.scenario] || {};
  return (num(tm.warmup_seconds) ?? 3) + (num(tm.attack_delay_seconds) ?? 10) + (num(sc.duration) ?? num(tm.duration_seconds) ?? 60)
    + (num(tm.cleanup_seconds) ?? 3) + SETUP_SECONDS;
}
function progress() {   // {k, n, left}: run k of n and seconds left (null when unknown)
  const idx = S.planIndex.get(S.current);
  const n = S.plan.length;
  if (!n) return null;
  if (idx === undefined) return {k: 0, n, left: S.plan.reduce((a, p) => a + runSeconds(p), 0)};
  let left = 0;
  for (let i = idx + 1; i < n; i++) left += runSeconds(S.plan[i]);
  const r = curRun();
  const spent = r && r.t0 !== null ? (nowFor(r) - r.t0) + SETUP_SECONDS : 0;
  left += Math.max(0, runSeconds(S.plan[idx]) - spent);
  return {k: idx + 1, n, left};
}
function drawBar() {
  const key = campaign();
  const [icon, word] = CHIP[key];
  setText($('st-icon'), icon);
  setText($('st-word'), word);
  $('status').className = 'status ' + key;
  let sub = '';
  const pr = progress();
  if ((key === 'running' || key === 'stopping') && pr) sub = 'run ' + Math.max(1, pr.k) + ' of ' + pr.n + ' · ' + mmss(pr.left) + ' left';
  else if (key === 'running' || key === 'watching') sub = curRun() ? runLabel(curRun()).toLowerCase() : '';
  else if ((key === 'finished' || key === 'stopped' || key === 'error') && S.finish)
    sub = (pr ? doneCount() + ' of ' + pr.n + ' runs · ' : '') + hhmm(S.finish.ended);
  setText($('st-sub'), sub);
  $('st-sub').hidden = !sub;
  const stop = $('btn-stop');
  const canStop = MODE === 'control' && C.state === 'running';
  stop.disabled = !canStop;
  stop.title = MODE !== 'control' ? 'Started from the terminal: press Ctrl+C there to stop it.'
    : canStop ? 'Stop the test gracefully (like Ctrl+C). Finished runs are kept.' : 'Nothing to stop.';
}
function doneCount() {
  let n = 0;
  for (const p of S.plan) if (S.results.has(p.name)) n += 1;
  return n;
}

// --- tabs, automatic switching, toast --------------------------------------------------
const TABS = ['run', 'live', 'results'];
const UI = {tab: 'run', manualAt: -1e9, ready: false, choice: 'demo', resScen: null, narrKey: '', narrAt: 0};
function selectTab(name, how) {
  UI.tab = name;
  for (const t of TABS) {
    const b = $('tab-' + t), on = t === name;
    b.setAttribute('aria-selected', String(on));
    b.tabIndex = on ? 0 : -1;
    $('panel-' + t).hidden = !on;
    if (on) b.querySelector('.tab-dot').hidden = true;
  }
  if (how === 'manual') UI.manualAt = performance.now();
  if (name !== 'results') $('toast').hidden = true;
  drawAll();
}
function autoTab(name) {
  if (!UI.ready || UI.tab === name) return false;
  if (performance.now() - UI.manualAt < MANUAL_HOLD_MS) {   // the user just chose a tab: only hint
    $('tab-' + name).querySelector('.tab-dot').hidden = false;
    return false;
  }
  selectTab(name, 'auto');
  return true;
}
$('pagetabs').addEventListener('click', (e) => {
  const b = e.target.closest('[role="tab"]');
  if (b) selectTab(b.id.slice(4), 'manual');
});
$('pagetabs').addEventListener('keydown', (e) => {
  const i = TABS.indexOf(UI.tab);
  const to = {ArrowLeft: (i + 2) % 3, ArrowRight: (i + 1) % 3, Home: 0, End: 2}[e.key];
  if (to === undefined) return;
  e.preventDefault();
  selectTab(TABS[to], 'manual');
  $('tab-' + TABS[to]).focus();
});
let toastTimer = null;
function showToast() {
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 12000);
}
$('toast-back').addEventListener('click', () => { selectTab('live', 'manual'); $('tab-live').focus(); });

// --- page title (header line) --------------------------------------------------------------
function runLabel(r) {
  const i = S.planIndex.get(r.name);
  if (i !== undefined) return 'Run ' + (i + 1) + ' of ' + S.plan.length;
  return 'Run ' + (S.order.indexOf(r.name) + 1);
}
function scenWords(r) { return scenFor(r).attack ? 'with attack' : 'without attack'; }
function drawTitle() {
  let text = 'Run a test';
  if (UI.tab === 'live') {
    const r = viewRun();
    text = r && r.info ? runLabel(r) + ' · ' + sysName(r.info.system) + ' ' + (SYS_GLYPH[r.info.system] || '') + ' · ' + scenWords(r)
      : 'Watch live';
  } else if (UI.tab === 'results') {
    const topo = resultsTopology(), rows = resultRows(topo);
    text = 'Results';
    if (rows.length) {
      text += ' · ' + topoTitle(topo).replace(/^F/, 'f') + ', ' + rows.length + (rows.length === 1 ? ' run' : ' runs');
      text += running() ? ', in progress' : S.finish ? ', finished ' + hhmm(S.finish.ended) : '';
    }
  }
  setText($('page-title'), text);
}

// --- narration: one plain sentence about what is happening now ------------------------------
function narration() {
  const r = curRun();
  const S_ = r && r.info ? sysName(r.info.system) : 'the controller';
  const topo = topoOf(r);
  const camp = campaign();
  const pr = progress();
  const N = pr ? pr.n : S.order.length;
  const k = r ? (S.planIndex.has(r.name) ? S.planIndex.get(r.name) + 1 : S.order.indexOf(r.name) + 1) : 0;
  if (camp === 'password') return ['password', 'The test needs your computer password. Type it in the terminal window, then come back here.'];
  if (camp === 'stopped') return ['stopped', 'You stopped the test. Runs that finished are saved; the current run was discarded.'];
  if (r && r.failed) return ['error' + k, 'Run ' + k + ' failed: ' + r.reason + '. Check Show details → Terminal, then start again.'];
  if (camp === 'error') return ['error', 'Run ' + Math.max(1, k) + ' failed: the test ended with exit code ' + S.finish.code
                                          + '. Check Show details → Terminal, then start again.'];
  if (camp === 'finished') {
    const c = S.campaign;
    if (c && (c.failed || c.skipped)) {
      const bits = [c.done + ' done'];
      if (c.skipped) bits.push(c.skipped + ' already had results');
      if (c.failed) bits.push(c.failed + ' failed (see Show details → Terminal)');
      return ['finished', 'Finished: ' + bits.join(', ') + '. Opening Results.'];
    }
    return ['finished', 'All ' + N + ' runs finished. Opening Results.'];
  }
  if (!r || r.t0 === null) {
    if (camp === 'running' || camp === 'stopping')
      return ['warm', 'Starting the virtual network and the ' + (r && r.info ? S_ : 'first') + ' controller. This takes about 20 s.'];
    if (MODE === 'control') return ['idle', 'Nothing is running. Choose a test in Run a test and press Start.'];
    return ['wait', 'Waiting for the first run to start.'];
  }
  const ph = phaseOf(r);
  const last = r.stats.length ? r.stats[r.stats.length - 1] : null;
  const target = targetOf(r);
  if (ph.key === 'done') {
    return ['done' + k, 'Run ' + k + ' of ' + N + ' done: ' + S_ + ' spent ' + Math.round(aboveOf(r)) + ' s over the limit. Next run starts in a few seconds.'];
  }
  if (ph.key === 'stalled') return ['stalled', 'No new data from this run since ' + clock(r.lastT) + '. It may have been stopped.'];
  if (ph.key === 'warm') return ['warm', 'Starting the virtual network and the ' + S_ + ' controller. This takes about 20 s.'];
  if (ph.key === 'cleanup') return ['cleanup', 'The attack is over. Collecting the results of this run.'];
  if (ph.key === 'normal') {
    const b = last ? busiest(last.u, isFatTree(r)) : null;
    return ['normal', 'Normal traffic is flowing.' + (b ? ' The busiest link is ' + linkName(b.link, topo) + ' at '
                                                         + Math.round(b.value * 100) + '% of its capacity.' : '')];
  }
  // attack: the most recent controller action decides the sentence
  const now = nowFor(r);
  const recent = r.events.filter((e) => e.kind !== 'dropped' && now - e.t <= 10).pop();
  const L = target ? linkName(target, topo) : 'the target link';
  if (recent && recent.kind === 'reroute') {
    const moved = newLink(recent);
    return ['reroute' + recent.n, S_ + ' moved traffic from ' + L + ' to ' + (moved ? linkName(moved, topo) : 'another path')
            + ' at ' + mmss(recent.x) + '. This is reroute number ' + recent.n + ' in this run.'];
  }
  if (recent && recent.kind === 'held') return ['held', S_ + ' waited instead of moving traffic again, so paths do not keep flipping back and forth.'];
  const tv = last && target ? linkUtil(last.u, target) : null;
  if (r.info && r.info.system === 'static' && tv !== null && tv >= r.threshold)
    return ['static', 'Static routing never changes paths, so ' + L + ' stays congested.'];
  if (r.counts.reroute && tv !== null && tv < r.threshold && !(recent && recent.kind === 'congestion'))
    return ['cleared', S_ + ' moved traffic off ' + L + ': it is now at ' + Math.round(tv * 100) + '%, below the 0.8 limit.'];
  if (recent && recent.kind === 'congestion') {
    const cl = recent.links && recent.links[0] ? linkName(recent.links[0], topo) : L;
    return ['cong', cl + ' is over the 0.8 limit (congested). ' + S_ + ' has spotted it.'];
  }
  return ['attack', 'The attack has started: one host is flooding ' + (target ? linkName(target, topo, true) : 'the target link')
          + '. Watch whether ' + S_ + ' moves traffic off it.'];
}
function newLink(ev) {   // first link of the new path that the old path did not use
  const used = new Set();
  for (let i = 0; i + 1 < ev.old.length; i++) { used.add(ev.old[i] + '-' + ev.old[i + 1]); used.add(ev.old[i + 1] + '-' + ev.old[i]); }
  for (let i = 0; i + 1 < ev.new.length; i++) {
    const k = ev.new[i] + '-' + ev.new[i + 1];
    if (!used.has(k) && ev.new[i][0] !== 'h' && ev.new[i + 1][0] !== 'h') return k;
  }
  return null;
}
function drawNarration() {
  const [key, text] = narration();
  const node = $('narration');
  const t = performance.now();
  // a new situation is shown at once; changing numbers in the same sentence at most every 8 s
  if (key !== UI.narrKey || (node.textContent !== text && t - UI.narrAt > 8000)) {
    setText(node, text);
    UI.narrKey = key;
    UI.narrAt = t;
  }
  node.classList.toggle('attack', key === 'attack' || key === 'cong' || key === 'static');
  node.title = '';
  $('live-empty').hidden = key !== 'idle';
}

// --- phase strip ----------------------------------------------------------------------------
function drawPhases() {
  const r = viewRun();
  const list = $('phases');
  const tm = timingFor(r);
  const segs = [['warm', 'Warm-up'], ['normal', 'Normal traffic']];
  if (tm.attack) {
    let a0 = tm.warm + tm.delay, a1 = a0 + tm.dur;
    if (r && r.t0 !== null) {
      const w = windows(r);
      if (w.aStart !== null) { a0 = w.aStart - r.t0; a1 = w.aEnd - r.t0; }
    }
    segs.push(['attack', 'Attack ' + mmss(a0) + '–' + mmss(a1)]);
  }
  segs.push(['cleanup', 'Cleanup']);
  const ph = r ? phaseOf(r) : {key: 'waiting'};
  let idx = segs.findIndex((s) => s[0] === ph.key);
  if (ph.key === 'done') idx = segs.length;
  if (ph.key === 'normal' && !tm.attack) idx = 1;
  const items = segs.map(([key, name], i) => {
    const li = el('li', (key === 'attack' ? 'atk ' : '') + (i < idx ? 'done' : i === idx ? 'now' : ''));
    const span = el('span', 'ph', (i < idx ? '✓ ' : '') + name);
    if (i === idx) li.setAttribute('aria-current', 'step');
    li.append(span);
    return li;
  });
  const key = segs.map((s) => s[1]).join('|') + idx;
  if (list.dataset.key !== key) { list.dataset.key = key; list.replaceChildren(...items); }
}

// --- four headline numbers ----------------------------------------------------------------------
function setNum(id, value, sub, alert) {
  const big = $(id);
  setText(big, value);
  big.classList.toggle('none', value === '–');
  setText($(id + '-sub'), sub);
  $(id + '-sub').classList.toggle('alert', !!alert);
}
function drawNumbers() {
  const r = viewRun();
  const topo = topoOf(r);
  const last = r && r.stats.length ? r.stats[r.stats.length - 1] : null;
  const b = last ? busiest(last.u, isFatTree(r)) : null;
  const thr = r ? r.threshold : 0.8;
  if (b) {
    setNum('n-busy', Math.round(b.value * 100) + '%', linkName(b.link, topo) + (b.value >= thr ? ' · over the limit' : ''), b.value >= thr);
    $('n-busy-sub').title = linkName(b.link, topo, true);
  } else setNum('n-busy', '–', r && r.t0 !== null ? 'no link stats yet' : 'no data yet');
  const target = targetOf(r);
  setNum('n-above', r && r.t0 !== null ? String(Math.round(aboveOf(r))) : '–',
         target ? 'on ' + linkName(target, topo) + ' (attacked)' : 'on the attacked link');
  $('n-above-sub').title = target ? 'On the attacked link: ' + linkName(target, topo, true) : '';
  const held = r ? r.counts.held || 0 : 0;
  setNum('n-reroute', r && r.t0 !== null ? String(r.counts.reroute || 0) : '–',
         held ? held + ' more held back (to avoid flip-flops)' : 'traffic moved to another path');
  const res = r ? S.results.get(r.name) : null;
  const loss = res ? num(res.values.packet_loss_pct) : null;
  if (loss !== null) {
    setText($('n-lost-label'), 'Packets lost');
    setNum('n-lost', fmtNum(loss, 1) + '%', 'of the normal traffic, whole run');
  } else {
    setText($('n-lost-label'), 'Packets dropped');
    setNum('n-lost', r && r.t0 !== null ? String(r.counts.dropped || 0) : '–', 'by switches (unknown sender)');
  }
  const badge = $('err-badge');
  badge.hidden = S.errors === 0;
  badge.textContent = '✕ ' + S.errors + (S.errors === 1 ? ' error' : ' errors');
}
function drawView() {   // viewing a finished run: banner with the way back
  const r = viewing() ? viewRun() : null;
  $('viewbar').hidden = !r;
  if (!r) return;
  const info = r.info || {};
  const how = r.failed ? 'failed' : isDone(r) ? 'finished' : 'stopped early';
  setText($('view-text'), 'Showing ' + runLabel(r).toLowerCase() + ': ' + sysName(info.system) + ', ' + scenWords(r)
          + ' (' + how + '). The sentence above still follows the live run.');
}
$('back-live').addEventListener('click', () => { setView(null); requestAnimationFrame(() => $('tab-live').focus()); });

// --- drawing (batched: at most ~4 per second) -------------------------------------------
let drawPending = false, lastDraw = 0;
function invalidate() {
  if (drawPending) return;
  drawPending = true;
  const wait = Math.max(0, DRAW_EVERY_MS - (performance.now() - lastDraw));
  setTimeout(() => requestAnimationFrame(drawAll), wait);
}
function drawAll() {
  drawPending = false;
  lastDraw = performance.now();
  const steps = [drawBar, drawTitle, drawRunTab];
  if (UI.tab === 'live') {
    steps.push(drawNarration, drawPhases, drawNumbers, drawView, drawLink, drawMap);
    if ($('live-details').open) steps.push(drawTimeline);
  } else if (UI.tab === 'results') steps.push(drawResults);
  for (const f of steps) {
    try { f(); } catch (err) { console.error(err); }
  }
}
function domainX() {   // shared x axis of the link chart and the timeline: 0-80 s, grows
  let maxX = 80;
  for (const r of S.runs.values()) {
    if (r.t0 === null) continue;
    const end = nowFor(r);
    if (end !== null) maxX = Math.max(maxX, end - r.t0);
    const w = windows(r);
    if (w.aEnd !== null) maxX = Math.max(maxX, w.aEnd - r.t0 + w.tm.clean);
  }
  return Math.ceil(maxX / 20) * 20;
}

// --- link use over time ---------------------------------------------------------------------
const linkView = {hoverX: null, geom: null};
function linkSeries(r) {
  const target = targetOf(r);
  const fat = isFatTree(r);
  const peak = {};
  for (const s of r.stats) for (const k in s.u) {
    if (k === target || (fat && !CORE_AGG.test(k))) continue;
    if (!(k in peak) || s.u[k] > peak[k]) peak[k] = s.u[k];
  }
  const others = Object.keys(peak).sort((a, b) => peak[b] - peak[a]).slice(0, 5);
  return {target, others};
}
function clip(text, n) { return text.length > n ? text.slice(0, n - 1) + '…' : text; }
function drawLink() {
  const box = $('chart-link'), node = $('svg-link');
  const {w, h} = boxSize(box);
  node.replaceChildren();
  node.setAttribute('viewBox', `0 0 ${w} ${h}`);
  if (w < 120 || h < 80) return;
  const r = viewRun();
  const topo = topoOf(r);
  const hatch = hatchDefs(node, 'hatch-link');
  const m = {l: 36, r: 150, t: 24, b: 24};
  const pw = w - m.l - m.r, ph = h - m.t - m.b;
  const xmax = domainX();
  const X = (x) => m.l + Math.max(0, Math.min(xmax, x)) / xmax * pw;
  const Y = (v) => m.t + (1 - Math.max(0, Math.min(1, v))) * ph;
  linkView.geom = {m, pw, ph, X, xmax};
  const thr = r ? r.threshold : 0.8;
  for (let i = 0; i <= 5; i++) {
    const v = i / 5;
    svg('line', {x1: m.l, x2: m.l + pw, y1: r1(Y(v)), y2: r1(Y(v)), class: i === 0 ? 'axisline' : 'gridline'}, node);
    stext(node, m.l - 8, Y(v) + 5, v.toFixed(1), 'ax', 'end');
  }
  const step = niceStep(xmax, pw);
  for (let x = 0; x <= xmax + 0.001; x += step)
    stext(node, X(x), m.t + ph + 20, x === 0 ? '0 s' : String(x), 'ax', x === 0 ? 'start' : 'middle');
  if (r && r.t0 !== null) {   // attack window, hatched
    const win = windows(r);
    if (win.attack && win.aStart !== null) {
      const now = nowFor(r);
      const endT = Math.min(win.aEnd, Math.max(now ?? win.aStart, win.aStart));
      const x1 = X(win.aStart - r.t0), x2 = X((isDone(r) ? win.aEnd : endT) - r.t0);
      if (x2 > x1) {
        svg('rect', {x: r1(x1), y: m.t, width: r1(x2 - x1), height: ph, fill: hatch}, node);
        stext(node, x1 + 4, m.t + ph - 8, 'Attack', 'lbl-b halo');
      }
    }
  }
  svg('line', {x1: m.l, x2: m.l + pw, y1: r1(Y(thr)), y2: r1(Y(thr)), class: 'thresh'}, node);
  stext(node, m.l + pw + 6, Y(thr) + 5, thr.toFixed(1) + ' limit', 'ax');
  if (!r || r.stats.length === 0) {
    stext(node, m.l + pw / 2, m.t + ph / 2, r && r.statsDropped ? 'Link details are kept for the last '
          + KEEP_STATS_RUNS + ' runs only' : r && r.t0 !== null ? 'Waiting for link stats…' : 'No run yet', 'empty', 'middle');
    node.setAttribute('aria-label', 'Link use over time: no link stats yet');
    $('tip-link').hidden = true;
    drawLinkLegend(r, null);
    return;
  }
  const {target, others} = linkSeries(r);
  const path = (key) => {
    let d = '';
    for (const s of r.stats) {
      const v = linkUtil(s.u, key);
      if (v === null) continue;
      d += (d ? 'L' : 'M') + r1(X(s.x)) + ' ' + r1(Y(v));
    }
    return d;
  };
  for (const k of others.slice().reverse()) {
    const d = path(k);
    if (d) svg('path', {d, class: 'other-line'}, node);
  }
  if (target) {
    const d = path(target);
    if (d) svg('path', {d, class: 'target-line'}, node).style.stroke = sysColor(r.info && r.info.system);
  }
  const last = r.stats[r.stats.length - 1];
  const labels = [];
  if (target && linkUtil(last.u, target) !== null) labels.push({y: Y(linkUtil(last.u, target)), text: clip(linkName(target, topo), 18) + ' (attacked)', cls: 'lbl-b'});
  if (others[0] && linkUtil(last.u, others[0]) !== null) labels.push({y: Y(linkUtil(last.u, others[0])), text: clip(linkName(others[0], topo), 20), cls: 'ax'});
  if (labels.length === 2 && Math.abs(labels[0].y - labels[1].y) < 18) {
    const mid = (labels[0].y + labels[1].y) / 2, up = labels[0].y <= labels[1].y ? -9 : 9;
    labels[0].y = mid + up; labels[1].y = mid - up;
  }
  const lowest = Math.max(...labels.map((l) => l.y), 0), limit = m.t + ph - 8;
  if (lowest > limit) for (const lab of labels) lab.y -= lowest - limit;
  const lx = X(last.x) + 8;
  for (const lab of labels) stext(node, Math.min(lx, m.l + pw + 6), lab.y + 5, lab.text, lab.cls + ' halo');
  const laneY = m.t - 13;   // ▲ congestion, system marker = reroute
  for (const ev of r.events) {
    if (ev.kind !== 'congestion' && ev.kind !== 'reroute') continue;
    const x = X(ev.x);
    if (ev.kind === 'congestion') {
      svg('line', {x1: r1(x), x2: r1(x), y1: m.t, y2: m.t + ph, class: 'cong-guide'}, node);
      svg('path', {d: `M${r1(x)} ${laneY - 6}L${r1(x + 6)} ${laneY + 5}L${r1(x - 6)} ${laneY + 5}Z`, class: 'cong'}, node);
    } else {
      svg('line', {x1: r1(x), x2: r1(x), y1: m.t, y2: m.t + ph, class: 'rr-guide'}, node).style.stroke = sysColor(r.info && r.info.system);
      marker(node, r.info && r.info.system, x, laneY, 6);
    }
  }
  linkView.hoverG = svg('g', {}, node);
  drawLinkHover(r);
  const tv = target ? linkUtil(last.u, target) : null;
  const peakT = target ? Math.max(...r.stats.map((s) => linkUtil(s.u, target) ?? 0)) : null;
  node.setAttribute('aria-label', 'Link use over time. ' + (target ? 'Attacked link ' + linkName(target, topo) + ' at '
    + (tv === null ? 'no data' : Math.round(tv * 100) + '%') + ' now, peak ' + Math.round(peakT * 100) + '%. ' : 'Attacked link not known yet. ')
    + (r.counts.congestion || 0) + ' congestion alerts, ' + (r.counts.reroute || 0) + ' reroutes. Use left and right arrow keys to read values.');
  drawLinkLegend(r, target);
}
let linkLegendKey = '';
function drawLinkLegend(r, target) {
  const sys = r && r.info ? r.info.system : null;
  const key = [sys, !!target, S.badReadings].join('|');
  if (key === linkLegendKey) return;   // rebuilt only on change: keeps keyboard focus
  linkLegendKey = key;
  const items = [];
  if (target) items.push(legendItem('attacked link', 'The link the attacker floods, drawn in the colour of the system being tested.',
                                    swatchLine('target-line', {stroke: sysColor(sys)})));
  items.push(legendItem('busiest other links', 'The five other links that carried the most traffic in this run.', swatchLine('other-line')));
  items.push(legendItem('congestion seen', 'The moment the controller reported a link over 0.8.', el('span', 'glyph-cong', '▲')));
  if (sys) items.push(legendItem('traffic moved', 'The moment the controller moved a flow to another path.', markerIcon(sys, 12)));
  if (S.badReadings) items.push(legendItem('ⓘ ' + S.badReadings + ' impossible readings left out',
                                           'Readings above 2.0 (twice the capacity) cannot be real and are not drawn.'));
  $('legend-link').replaceChildren(...items);
}
function nearestSample(r, x) {
  const st = r.stats;
  let lo = 0, hi = st.length - 1;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (st[mid].x < x) lo = mid + 1; else hi = mid; }
  if (lo > 0 && Math.abs(st[lo - 1].x - x) < Math.abs(st[lo].x - x)) lo -= 1;
  return lo;
}
function drawLinkHover(r) {
  const g = linkView.hoverG, tipBox = $('tip-link'), geom = linkView.geom;
  if (!g || !geom) return;
  g.replaceChildren();
  if (linkView.hoverX === null || !r || !r.stats.length) { tipBox.hidden = true; return; }
  const topo = topoOf(r);
  const s = r.stats[nearestSample(r, linkView.hoverX)];
  const x = geom.X(s.x);
  svg('line', {x1: r1(x), x2: r1(x), y1: geom.m.t, y2: geom.m.t + geom.ph, class: 'cross'}, g);
  const {target, others} = linkSeries(r);
  const rows = [];
  if (target && linkUtil(s.u, target) !== null) rows.push([linkName(target, topo) + ' (attacked)', linkUtil(s.u, target), true]);
  for (const k of others) if (linkUtil(s.u, k) !== null) rows.push([linkName(k, topo), linkUtil(s.u, k), false]);
  rows.sort((a, b) => (b[2] - a[2]) || (b[1] - a[1]));
  const lines = rows.map(([name, v, isT]) => {
    const row = el('div', 'tip-row');
    row.append(isT ? markerIcon(r.info && r.info.system, 10) : swatchLine('other-line'), name, el('b', '', Math.round(v * 100) + '%'));
    return row;
  });
  tipBox.replaceChildren(el('div', 'tip-head', mmss(s.x) + ' into the run'), ...lines);
  tipBox.hidden = false;
  const box = $('chart-link');
  const left = x + 14 + 280 > box.clientWidth ? x - 14 - Math.min(280, tipBox.offsetWidth || 220) : x + 14;
  tipBox.style.left = Math.max(0, left) + 'px';
  tipBox.style.top = geom.m.t + 'px';
}
$('chart-link').addEventListener('pointermove', (e) => {
  const geom = linkView.geom, r = viewRun();
  if (!geom || !r) return;
  const px = e.clientX - e.currentTarget.getBoundingClientRect().left;
  linkView.hoverX = px < geom.m.l || px > geom.m.l + geom.pw ? null : (px - geom.m.l) / geom.pw * geom.xmax;
  drawLinkHover(r);
});
$('chart-link').addEventListener('pointerleave', () => { linkView.hoverX = null; drawLinkHover(viewRun()); });
$('chart-link').addEventListener('keydown', (e) => {
  const r = viewRun();
  if (!r || !r.stats.length) return;
  if (e.key === 'Escape') { linkView.hoverX = null; drawLinkHover(r); return; }
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
  e.preventDefault();
  let i = linkView.hoverX === null ? r.stats.length - 1 : nearestSample(r, linkView.hoverX);
  i = Math.max(0, Math.min(r.stats.length - 1, i + (e.key === 'ArrowLeft' ? -1 : 1)));
  linkView.hoverX = r.stats[i].x;
  drawLinkHover(r);
});

// --- network map: fat-tree layers, real backbones from lat/lon, else a circle --------------
function fatTreeLayout(w, h) {
  const m = {l: 56, r: 8, t: 14, b: 26};
  const pw = w - m.l - m.r, ph = h - m.t - m.b;
  const rows = {c: m.t + ph * 0.06, a: m.t + ph * 0.42, e: m.t + ph * 0.72, h: m.t + ph * 0.94};
  const pos = {};
  const podW = pw / 4;
  for (let i = 0; i < 4; i++) pos['c' + (i + 1)] = [m.l + pw * (i + 0.5) / 4, rows.c];
  for (let i = 0; i < 8; i++) {
    const pod = Math.floor(i / 2), slot = i % 2;
    const x = m.l + pod * podW + podW * (slot ? 0.72 : 0.28);
    pos['a' + (i + 1)] = [x, rows.a];
    pos['e' + (i + 1)] = [x, rows.e];
  }
  for (let j = 0; j < 16; j++) {
    const e = pos['e' + (Math.floor(j / 2) + 1)];
    pos['h' + (j + 1)] = [e[0] + (j % 2 ? 1 : -1) * Math.min(14, podW * 0.1), rows.h];
  }
  return {m, pw, ph, rows, pos, podW};
}
function fatTreeLinks() {
  const out = [];
  for (let pod = 0; pod < 4; pod++) {
    for (let s = 0; s < 2; s++) {
      const a = 'a' + (pod * 2 + s + 1);
      for (let c = s * 2; c < s * 2 + 2; c++) out.push(['c' + (c + 1), a]);
      for (let t = 0; t < 2; t++) out.push([a, 'e' + (pod * 2 + t + 1)]);
    }
    for (let t = 0; t < 2; t++) {
      const e = 'e' + (pod * 2 + t + 1), first = (pod * 2 + t) * 2;
      out.push([e, 'h' + (first + 1)], [e, 'h' + (first + 2)]);
    }
  }
  return out;
}
const FAT_LINKS = fatTreeLinks();
function graphLayout(info, extraNodes, w, h) {   // {pos, nodes}: equirectangular when every node has lat/lon
  const nodes = info && info.nodes && info.nodes.length ? info.nodes.slice() : [];
  const ids = new Set(nodes.map((n) => n.id));
  for (const id of extraNodes) if (!ids.has(id)) { ids.add(id); nodes.push({id, label: id, short: id}); }
  const m = {l: 14, r: 70, t: 14, b: 14};
  const pos = {};
  const geo = nodes.length && nodes.every((n) => num(n.lat) !== null && num(n.lon) !== null);
  if (geo) {
    const lat0 = nodes.reduce((a, n) => a + n.lat, 0) / nodes.length * Math.PI / 180;
    const xs = nodes.map((n) => n.lon * Math.cos(lat0)), ys = nodes.map((n) => -n.lat);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const k = Math.min((w - m.l - m.r) / Math.max(1e-6, x1 - x0), (h - m.t - m.b) / Math.max(1e-6, y1 - y0));
    const ox = m.l + ((w - m.l - m.r) - (x1 - x0) * k) / 2, oy = m.t + ((h - m.t - m.b) - (y1 - y0) * k) / 2;
    nodes.forEach((n, i) => { pos[n.id] = [ox + (xs[i] - x0) * k, oy + (ys[i] - y0) * k]; });
  } else {
    const cx = w / 2, cy = h / 2, rad = Math.max(10, Math.min(w, h) / 2 - 40);
    nodes.forEach((n, i) => {
      const a = -Math.PI / 2 + 2 * Math.PI * i / Math.max(1, nodes.length);
      pos[n.id] = [cx + rad * Math.cos(a), cy + rad * Math.sin(a)];
    });
  }
  return {pos, nodes, geo};
}
function rerouteToShow(r) {
  let rr = S.lastReroute;
  let age = rr ? performance.now() - rr.at : Infinity;
  const linger = 6000, fade = reducedMotion.matches ? 0 : 2000;
  if (!rr || rr.run !== (r && r.name) || age > linger + fade) rr = null;
  if (viewing()) {   // a finished run: its last reroute, without fading
    const ev = r.events.filter((e) => e.kind === 'reroute').pop();
    rr = ev ? {run: r.name, ev, at: performance.now()} : null;
    age = 0;
  }
  if (rr && !viewing()) {
    if (age > linger) invalidate();   // keep fading
    else setTimeout(invalidate, linger - age + 20);
  }
  return rr ? {rr, opacity: age <= linger ? 1 : Math.max(0, 1 - (age - linger) / fade)} : null;
}
function drawMap() {
  const box = $('chart-map'), node = $('svg-map');
  const {w, h} = boxSize(box);
  node.replaceChildren();
  node.setAttribute('viewBox', `0 0 ${w} ${h}`);
  if (w < 120 || h < 80) return;
  const r = viewRun();
  const topo = topoOf(r) || 'fat_tree';
  const last = r && r.stats.length ? r.stats[r.stats.length - 1].u : null;
  const thr = r ? r.threshold : 0.8;
  const target = targetOf(r);
  const shown = rerouteToShow(r);
  const util = (p, q) => last ? Math.max(linkUtil(last, p + '-' + q) ?? -1, linkUtil(last, q + '-' + p) ?? -1) : -1;
  const cls = (v) => v < 0 ? 'lk lk-none' : 'lk ' + (v >= thr ? 'lk-hot' : v >= 0.5 ? 'lk-mid' : 'lk-low');
  let P, hot = [];
  const labelled = new Set();
  if (topo === 'fat_tree') {
    const L = fatTreeLayout(w, h);
    P = L.pos;
    for (let pod = 0; pod < 4; pod++) {
      const x = L.m.l + pod * L.podW + 4, y = L.rows.a - 20;
      svg('rect', {x: r1(x), y: r1(y), width: r1(L.podW - 8), height: r1(L.rows.h - y + 12), rx: 8, class: 'pod'}, node);
    }
    for (const [label, key] of [['Core', 'c'], ['Agg.', 'a'], ['Edge', 'e'], ['Hosts', 'h']]) stext(node, 2, L.rows[key] + 5, label, 'ax');
    for (const [p, q] of FAT_LINKS) {
      const v = p[0] === 'c' ? util(p, q) : -1;
      if (v >= thr) hot.push([p + '-' + q, v, (P[p][0] + P[q][0]) / 2, (P[p][1] + P[q][1]) / 2]);
      svg('line', {x1: r1(P[p][0]), y1: r1(P[p][1]), x2: r1(P[q][0]), y2: r1(P[q][1]), class: cls(v)}, node);
    }
  } else {
    const info = T.by.get(topo);
    const edges = info && info.edges && info.edges.length ? info.edges.slice() : [];
    const extra = new Set();
    if (!edges.length && last) for (const k in last) { const [a, b] = k.split('-'); if (a && b) { extra.add(a); extra.add(b); edges.push([a, b]); } }
    const G = graphLayout(info, extra, w, h);
    P = G.pos;
    for (const [a, b] of edges) {
      if (!P[a] || !P[b]) continue;
      const v = util(a, b);
      if (v >= thr) { hot.push([a + '-' + b, v, (P[a][0] + P[b][0]) / 2, (P[a][1] + P[b][1]) / 2]); labelled.add(a); labelled.add(b); }
      const line = svg('line', {x1: r1(P[a][0]), y1: r1(P[a][1]), x2: r1(P[b][0]), y2: r1(P[b][1]), class: cls(v)}, node);
      svg('title', {}, line).textContent = linkName(a + '-' + b, topo, true) + (v >= 0 ? ': ' + Math.round(v * 100) + '% full' : '');
    }
    if (target) for (const id of target.split('-')) labelled.add(id);
    if (shown) for (const id of [...shown.rr.ev.old, ...shown.rr.ev.new]) labelled.add(id);
    const crowded = G.nodes.length > 14;
    const drawLater = [];
    for (const n of G.nodes) {
      const [x, y] = P[n.id];
      const c = svg('circle', {cx: r1(x), cy: r1(y), r: 5, class: 'gnode'}, node);
      svg('title', {}, c).textContent = n.label;
      if (!crowded || labelled.has(n.id)) drawLater.push([n, x, y]);
    }
    P.__labels = drawLater;
  }
  if (shown) {   // reroute: new path in the system colour, old path dashed
    const g = svg('g', {}, node);
    g.style.opacity = String(shown.opacity);
    const pts = (path) => path.filter((n) => P[n]).map((n) => r1(P[n][0]) + ',' + r1(P[n][1])).join(' ');
    if (shown.rr.ev.old.length) svg('polyline', {points: pts(shown.rr.ev.old), class: 'old-path'}, g);
    svg('polyline', {points: pts(shown.rr.ev.new), class: 'new-path'}, g).style.stroke = sysColor(r && r.info && r.info.system);
  }
  if (topo === 'fat_tree') {
    for (const n in P) {
      const [x, y] = P[n];
      if (n[0] === 'h') { svg('circle', {cx: r1(x), cy: r1(y), r: 3.5, class: 'host'}, node); continue; }
      svg('rect', {x: r1(x - 15), y: r1(y - 10), width: 30, height: 20, rx: 4, class: 'node'}, node);
      stext(node, x, y + 5, n, 'lbl', 'middle');
    }
  } else {
    placeLabels(node, P.__labels || [], Object.values(P).filter(Array.isArray), w, h);
  }
  for (const [, v, x, y] of hot) stext(node, x + 8, y - 6, '⚠ ' + Math.round(v * 100) + '%', 'lbl-hot halo');
  const caption = $('caption-map');
  if (shown) {
    const ev = shown.rr.ev;
    const names = (p) => p.filter((n) => n[0] !== 'h' || topo === 'fat_tree').map((n) => nodeName(n, topo)).join(' › ');
    caption.replaceChildren(markerIcon(r.info && r.info.system, 14),
      el('span', 'cap-text', (viewing() ? 'Last move of this run: ' : 'Traffic moved: ') + names(ev.old) + '  →  ' + names(ev.new)));
  } else if (!last) {
    caption.replaceChildren(el('span', 'muted', r && r.t0 !== null ? 'Waiting for link stats…' : 'Grey: no traffic measured yet.'));
  } else {
    caption.replaceChildren(el('span', 'muted', viewing() ? 'Link load at the end of this run.' : 'Moves of traffic show here as they happen.'));
  }
  node.setAttribute('aria-label', 'Network map of ' + topoTitle(topo) + '. ' + (last ? hot.length + ' links over the limit'
    + (hot.length ? ': ' + hot.map((x) => linkName(x[0], topo) + ' ' + Math.round(x[1] * 100) + '%').join(', ') : '') + '.' : 'No link stats yet.'));
}
function placeLabels(node, labels, points, w, h) {   // city names that do not cover each other
  const taken = points.map(([x, y]) => [x - 6, y - 6, 12, 12]);
  const hit = (b) => b[0] < 0 || b[1] < 0 || b[0] + b[2] > w || b[1] + b[3] > h
    || taken.some((t) => b[0] < t[0] + t[2] && t[0] < b[0] + b[2] && b[1] < t[1] + t[3] && t[1] < b[1] + b[3]);
  for (const [n, x, y] of labels) {
    const tw = n.short.length * 7.4 + 4, th = 16;
    const options = [[x + 8, y + 4, 'start', x + 7], [x - 8, y + 4, 'end', x - 7 - tw], [x, y - 10, 'middle', x - tw / 2],
                     [x, y + 20, 'middle', x - tw / 2]].map(([tx, ty, anchor, left]) => ({tx, ty, anchor, box: [left, ty - 12, tw, th]}));
    const pick = options.find((o) => !hit(o.box));
    if (!pick) continue;   // no free spot: the name stays in the dot's tooltip
    taken.push(pick.box);
    stext(node, pick.tx, pick.ty, n.short, 'lbl halo', pick.anchor);
  }
}
function buildMapLegend() {
  const items = [];
  for (const [c, text, help] of [
    ['lk lk-low', 'under 50%', 'Under half full.'], ['lk lk-mid', '50–80%', 'Busy, but below the congestion limit.'],
    ['lk lk-hot', '⚠ over 0.8', 'Congested: at or over the 0.8 limit; the number shows how full it is.'],
    ['lk lk-none', 'not measured', 'No data for this link (yet).']])
    items.push(legendItem(text, 'Link ' + text + ': ' + help, swatchLine(c)));
  items.push(legendItem('old path', 'Dashed: where the traffic went before the latest move.', swatchLine('old-path')));
  items.push(legendItem('new path', 'Where the controller moved the traffic, in the colour of the system.', swatchLine('new-path', {stroke: 'var(--ink)'})));
  $('legend-map').replaceChildren(...items);
}

// --- controller actions per run (timeline, in Show details) ----------------------------------
function timelineRows(maxRows) {
  const names = S.plan.length ? S.plan.map((p) => p.name) : S.order.slice();
  for (const n of S.order) if (!names.includes(n)) names.push(n);
  if (names.length <= maxRows) return names;
  const cur = Math.max(0, names.indexOf(S.current));
  const start = Math.max(0, Math.min(cur - Math.floor(maxRows / 2), names.length - maxRows));
  return names.slice(start, start + maxRows);
}
function drawTimeline() {
  const box = $('chart-timeline'), node = $('svg-timeline');
  const {w, h} = boxSize(box);
  const focused = node.contains(document.activeElement) ? document.activeElement.dataset.run : null;
  node.replaceChildren();
  node.setAttribute('viewBox', `0 0 ${w} ${h}`);
  if (w < 160 || h < 60) return;
  const hatch = hatchDefs(node, 'hatch-timeline');
  const labelW = Math.min(260, Math.max(170, w * 0.3));
  const m = {l: labelW, r: 12, t: 4, b: 26};
  const pw = w - m.l - m.r, ph = h - m.t - m.b;
  const rows = timelineRows(Math.max(1, Math.floor(ph / 20)));
  const xmax = domainX();
  const X = (x) => m.l + Math.max(0, Math.min(xmax, x)) / xmax * pw;
  const step = niceStep(xmax, pw);
  for (let x = 0; x <= xmax + 0.001; x += step) {
    svg('line', {x1: r1(X(x)), x2: r1(X(x)), y1: m.t, y2: m.t + ph, class: 'gridline'}, node);
    stext(node, X(x), m.t + ph + 20, x === 0 ? '0 s' : String(x), 'ax', x === 0 ? 'start' : 'middle');
  }
  if (!rows.length) {
    stext(node, m.l + pw / 2, m.t + ph / 2, 'No run yet', 'empty', 'middle');
    node.setAttribute('aria-label', 'Controller actions per run: no run yet');
    return;
  }
  const rowH = Math.min(36, ph / rows.length);
  const summary = [];
  const showRun = S.plan.some((p) => p.run !== '0');
  rows.forEach((name, i) => {
    const r = S.runs.get(name) || newRun(name, null);
    const info = r.info || {};
    const y = m.t + rowH * i + rowH / 2;
    const g = svg('g', {}, node);
    const isCur = name === S.current, isView = name === S.view;
    g.style.opacity = isCur || isView || r.t0 === null ? '1' : '0.7';
    const rowName = sysName(info.system) + ', ' + scenWords(r) + (showRun ? ' #' + info.run : '');
    if (isView) svg('rect', {x: 0, y: r1(y - rowH / 2), width: r1(w), height: r1(rowH), rx: 4, class: 'row-view'}, g);
    if (canView(r)) {   // a row is a button: show this run in the graphs
      svg('rect', {x: 1, y: r1(y - rowH / 2 + 1), width: r1(w - 2), height: r1(Math.max(1, rowH - 2)), rx: 4, class: 'row-hit'}, g);
      g.dataset.run = name;
      g.setAttribute('tabindex', '0');
      g.setAttribute('role', 'button');
      g.setAttribute('aria-label', rowName + (isCur ? ': running now, shown live' : isView ? ': shown above now'
        : ': show this run above') + '. ' + (r.counts.reroute || 0) + ' reroutes, ' + (r.counts.congestion || 0) + ' congestion alerts.');
    }
    marker(g, info.system, 9, y, 6);
    stext(g, 22, y + 5, clip(rowName, 34) + (isView ? ' (shown)' : ''), isCur || isView ? 'lbl-b' : 'lbl');
    svg('line', {x1: m.l, x2: m.l + pw, y1: r1(y), y2: r1(y), class: 'row-base'}, g);
    if (r.t0 === null) {
      stext(g, m.l + 6, y + 5, S.results.has(name) ? 'finished before this page opened' : 'not run yet', 'ax');
      return;
    }
    svg('line', {x1: m.l, x2: r1(X((nowFor(r) ?? r.t0) - r.t0)), y1: r1(y), y2: r1(y), class: 'run-span'}, g).style.stroke = sysColor(info.system);
    const win = windows(r);
    if (win.attack && win.aStart !== null) {
      const now = nowFor(r);
      const end = isDone(r) ? win.aEnd : Math.min(win.aEnd, Math.max(now, win.aStart));
      const x1 = X(win.aStart - r.t0), x2 = X(end - r.t0);
      if (x2 > x1) svg('rect', {x: r1(x1), y: r1(y - rowH * 0.38), width: r1(x2 - x1), height: r1(rowH * 0.76), fill: hatch}, g);
    }
    const tick = Math.min(9, rowH * 0.36);
    for (const ev of r.events) {
      const x = X(ev.x);
      if (ev.kind === 'reroute') {
        const l = svg('line', {x1: r1(x), x2: r1(x), y1: r1(y - tick), y2: r1(y + tick)}, g);
        l.style.stroke = sysColor(info.system); l.style.strokeWidth = '2';
      } else if (ev.kind === 'congestion') {
        svg('path', {d: `M${r1(x)} ${r1(y - 6)}L${r1(x + 5.5)} ${r1(y + 4)}L${r1(x - 5.5)} ${r1(y + 4)}Z`, class: 'cong'}, g);
      } else if (ev.kind === 'held') {
        svg('circle', {cx: r1(x), cy: r1(y), r: 3.5, class: 'held-dot'}, g);
      } else if (ev.kind === 'dropped') {
        svg('path', {d: `M${r1(x - 4)} ${r1(y - 4)}L${r1(x + 4)} ${r1(y + 4)}M${r1(x + 4)} ${r1(y - 4)}L${r1(x - 4)} ${r1(y + 4)}`, class: 'drop-x'}, g);
      }
    }
    summary.push(rowName + ': ' + (r.counts.reroute || 0) + ' reroutes, ' + (r.counts.congestion || 0) + ' congestion alerts');
  });
  node.setAttribute('aria-label', 'Controller actions per run. ' + (summary.join('; ') || 'No run started yet.'));
  if (focused) { const again = node.querySelector('[data-run="' + CSS.escape(focused) + '"]'); if (again) again.focus(); }
}
function timelinePick(e) {
  const g = e.target.closest && e.target.closest('[data-run]');
  if (!g) return;
  if (e.type === 'keydown') {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
  }
  setView(g.dataset.run === S.view ? null : g.dataset.run);
}
$('svg-timeline').addEventListener('click', timelinePick);
$('svg-timeline').addEventListener('keydown', timelinePick);
function buildTimelineLegend() {
  const sw = el('span');
  sw.style.cssText = 'display:inline-block;width:18px;height:12px;border-radius:2px;background:repeating-linear-gradient(45deg,var(--attack-hatch) 0 1.5px,var(--attack-tint) 1.5px 6px)';
  $('legend-timeline').replaceChildren(
    legendItem('traffic moved', 'A short line: the controller moved a flow to another path.', swatchLine('', {stroke: 'var(--ink)', 'stroke-width': '2'})),
    legendItem('congestion seen', 'The controller saw a link over 0.8.', el('span', 'glyph-cong', '▲')),
    legendItem('held back', 'A move postponed on purpose so traffic does not flip-flop.', el('span', 'muted', '●')),
    legendItem('dropped', 'Traffic thrown away at a switch (unknown or faked address).', el('span', '', '×')),
    legendItem('attack', 'Hatched: when the attack traffic was on.', sw));
}
$('live-details').addEventListener('toggle', invalidate);

// --- results -----------------------------------------------------------------------------------
const METRICS = [
  {field: 'time_above_s', title: 'Time over limit', better: 'Lower is better', unit: ' s', dec: 0, tol: 0.5},
  {field: 'reroutes', title: 'Reroutes', better: 'Lower is better, if the link is cleared', unit: '', dec: 0, tol: 0.5},
  {field: 'oscillations', title: 'Path flip-flops', better: 'Lower is better', unit: '', dec: 0, tol: 0.5},
  {field: 'packet_loss_pct', title: 'Packet loss', better: 'Lower is better', unit: '%', dec: 1, tol: 0.1},
  {field: 'throughput_mbps', title: 'Throughput', better: 'Higher is better', unit: ' Mbit/s', dec: 1, tol: 0.1},
];
function fmtNum(v, dec) { return v.toFixed(dec).replace(/\.0+$/, ''); }
function resultsTopology() {
  const r = curRun();
  if (r && r.info) return r.info.topology;
  let lastRes = null;
  for (const res of S.results.values()) lastRes = res;
  return lastRes ? lastRes.topology : (S.plan[0] && S.plan[0].topology) || null;
}
function resultRows(topo) { return [...S.results.values()].filter((x) => !topo || x.topology === topo); }
function resultScenarios(topo) {
  const list = [];
  for (const p of S.plan) if ((!topo || p.topology === topo) && !list.includes(p.scenario)) list.push(p.scenario);
  for (const res of resultRows(topo)) if (!list.includes(res.scenario)) list.push(res.scenario);
  return list;
}
function resultSystems() {
  const list = SYSTEMS.slice();
  for (const p of S.plan) if (!list.includes(p.system)) list.push(p.system);
  for (const res of S.results.values()) if (!list.includes(res.system)) list.push(res.system);
  const used = new Set([...S.plan.map((p) => p.system), ...[...S.results.values()].map((x) => x.system)]);
  return used.size ? list.filter((s) => used.has(s)) : SYSTEMS.slice();
}
function aggregate(topo, scen, field) {   // {system: {v: mean, n}} over the finished runs
  const sums = {};
  for (const res of resultRows(topo)) {
    if (res.scenario !== scen) continue;
    const v = num(res.values[field]);
    if (v === null) continue;
    const a = sums[res.system] = sums[res.system] || {s: 0, n: 0};
    a.s += v; a.n += 1;
  }
  const out = {};
  for (const k in sums) out[k] = {v: sums[k].s / sums[k].n, n: sums[k].n};
  return out;
}
function same(a, b, tol) { return Math.abs(a - b) <= Math.max(0.05 * Math.max(Math.abs(a), Math.abs(b)), tol); }
function fmtMetric(mt, v, n) { return fmtNum(v, n > 1 && mt.dec === 0 ? 1 : mt.dec) + mt.unit; }
function metricConclusion(mt, agg) {
  const vals = Object.entries(agg).map(([sys, a]) => ({sys, v: a.v, n: a.n}));
  if (vals.length < 2) return vals.length ? 'Waiting for the other systems to finish.' : '';
  const lo = vals.reduce((a, b) => (b.v < a.v ? b : a)), hi = vals.reduce((a, b) => (b.v > a.v ? b : a));
  const f = (x) => fmtNum(x.v, x.n > 1 && mt.dec === 0 ? 1 : mt.dec);
  if (same(lo.v, hi.v, mt.tol)) {
    const mean = vals.reduce((a, b) => a + b.v, 0) / vals.length;
    return (vals.length === 3 ? 'All three' : 'All ' + vals.length) + ' were about the same (' + fmtMetric(mt, mean, 2) + ').';
  }
  const rs = vals.find((x) => x.sys === 'resilient_sdn');
  switch (mt.field) {
    case 'time_above_s': return sysName(lo.sys) + ' spent ' + f(lo) + ' s over the limit vs ' + f(hi) + ' s for ' + sysName(hi.sys) + '.';
    case 'reroutes': {
      const other = hi.sys !== 'resilient_sdn' && rs ? rs : lo;
      return sysName(hi.sys) + ' moved traffic ' + f(hi) + ' times; ' + sysName(other.sys) + ' needed ' + f(other) + '.';
    }
    case 'oscillations': {
      if (!rs) return sysName(hi.sys) + ' switched paths back and forth ' + f(hi) + ' times.';
      const worst = vals.filter((x) => x.sys !== 'resilient_sdn').reduce((a, b) => (b.v > a.v ? b : a));
      return sysName(worst.sys) + ' switched paths back and forth ' + f(worst) + ' times; ResilientSDN ' + f(rs) + '.';
    }
    case 'packet_loss_pct': return sysName(lo.sys) + ' lost ' + f(lo) + '% of packets vs ' + f(hi) + '% for ' + sysName(hi.sys) + '.';
    default: return sysName(hi.sys) + ' delivered ' + f(hi) + ' Mbit/s vs ' + f(lo) + ' for ' + sysName(lo.sys) + '.';
  }
}
function headline(topo, scen) {
  const attack = (S.scen[scen] || {attack: scen !== 'S1'}).attack;
  if (!attack) {
    const ok = ['throughput_mbps', 'packet_loss_pct'].every((field) => {
      const mt = METRICS.find((x) => x.field === field), agg = aggregate(topo, scen, field);
      const rs = agg.resilient_sdn, others = Object.entries(agg).filter(([s]) => s !== 'resilient_sdn');
      return rs && others.length && others.every(([, a]) => same(rs.v, a.v, mt.tol));
    });
    if (ok) return 'Without an attack, ResilientSDN costs nothing: results match the others.';
    return 'Without an attack: ' + lowerFirst(metricConclusion(METRICS[4], aggregate(topo, scen, 'throughput_mbps')));
  }
  const agg = aggregate(topo, scen, 'time_above_s');
  const vals = Object.entries(agg).map(([sys, a]) => ({sys, v: a.v, n: a.n}));
  if (vals.length < 2) return 'With an attack: waiting for the other systems to finish.';
  const lo = vals.reduce((a, b) => (b.v < a.v ? b : a)), hi = vals.reduce((a, b) => (b.v > a.v ? b : a));
  if (same(lo.v, hi.v, METRICS[0].tol)) return 'With an attack, all ' + (vals.length === 3 ? 'three' : vals.length)
    + ' systems kept the flooded link congested for about the same time (' + fmtMetric(METRICS[0], lo.v, 2) + ').';
  return 'With an attack, ' + sysName(lo.sys) + ' kept the flooded link congested for the shortest time: '
    + fmtMetric(METRICS[0], lo.v, lo.n) + ', against ' + fmtMetric(METRICS[0], hi.v, hi.n) + ' for ' + sysName(hi.sys) + '.';
}
function lowerFirst(t) { return /^(All|Waiting)/.test(t) ? t.charAt(0).toLowerCase() + t.slice(1) : t; }
function scenLabel(sc) {
  if (sc === 'S1') return 'Without attack (S1)';
  if (sc === 'S3') return 'With attack (S3)';
  const info = S.scen[sc];
  return (info ? info.label.charAt(0).toUpperCase() + info.label.slice(1) : sc) + ' (' + sc + ')';
}
let resKey = '';
function drawResults() {
  const topo = resultsTopology();
  const rows = resultRows(topo);
  const empty = rows.length === 0;
  $('res-empty').hidden = !empty;
  $('res-body').hidden = empty;
  $('res-demo').disabled = MODE !== 'control' || running() || !C.sudo_ok;
  $('res-demo').title = MODE !== 'control' ? 'Started from the terminal: restart with python3 tools/live_view.py to use this button.' : '';
  if (empty) return;
  const scens = resultScenarios(topo);
  if (!scens.includes(UI.resScen)) UI.resScen = scens.includes('S3') ? 'S3' : scens[0];
  const planned = S.plan.filter((p) => !topo || p.topology === topo).length;
  const key = JSON.stringify([topo, UI.resScen, rows.length, planned, scens, running()]);
  if (key === resKey) return;
  resKey = key;
  drawReports();
  const partial = planned && rows.length < planned;
  $('res-partial').hidden = !partial;
  setText($('res-partial'), partial ? rows.length + ' of ' + planned + ' runs done – numbers will change.' : '');
  setText($('res-headline'), headline(topo, UI.resScen));
  const focusedScen = $('res-scen').contains(document.activeElement) ? document.activeElement.dataset.scen : null;
  $('res-scen').replaceChildren(...scens.map((sc) => {
    const b = el('button', '', scenLabel(sc));
    b.type = 'button';
    b.dataset.scen = sc;
    b.setAttribute('aria-pressed', String(sc === UI.resScen));
    return b;
  }));
  if (focusedScen) { const b = $('res-scen').querySelector('[data-scen="' + CSS.escape(focusedScen) + '"]'); if (b) b.focus(); }
  const systems = resultSystems();
  const table = [];
  $('res-metrics').replaceChildren(...METRICS.map((mt) => {
    const agg = aggregate(topo, UI.resScen, mt.field);
    const max = Math.max(0, ...Object.values(agg).map((a) => a.v)) || 1;
    const box = el('section', 'metric');
    const head = el('div', 'metric-head');
    head.append(el('h3', '', mt.title), el('span', 'better', mt.better));
    const list = el('ul', 'bars');
    const cells = [mt.title + (mt.unit ? ' (' + mt.unit.trim() + ')' : '')];
    for (const sys of systems) {
      const a = agg[sys];
      const li = el('li', 'bar-row');
      const name = el('span', 'bar-name');
      name.append(markerIcon(sys, 13), sysName(sys));
      const track = el('span', 'bar-track');
      let val;
      if (a) {
        const bar = el('span', 'bar');
        bar.style.width = Math.max(0.5, a.v / max * 100) + '%';
        bar.style.background = sysColor(sys);
        track.append(bar);
        val = el('span', 'bar-val', fmtMetric(mt, a.v, a.n));
        li.title = sysName(sys) + ': ' + fmtMetric(mt, a.v, a.n) + (a.n > 1 ? ' (mean of ' + a.n + ' runs)' : ' (1 run)');
        cells.push(fmtMetric(mt, a.v, a.n));
      } else {
        val = el('span', 'bar-val none', 'not run yet');
        cells.push('not run yet');
      }
      li.append(name, track, val);
      list.append(li);
    }
    table.push(cells);
    box.append(head, list, el('p', 'concl', metricConclusion(mt, agg)));
    return box;
  }));
  const thead = el('thead'), hr = el('tr');
  hr.append(el('th', '', 'Measure'), ...systems.map((s) => el('th', '', sysName(s))));
  thead.append(hr);
  const tbody = el('tbody');
  for (const cells of table) { const tr = el('tr'); tr.append(...cells.map((c, i) => el(i ? 'td' : 'th', '', c))); tbody.append(tr); }
  $('res-table').replaceChildren(el('caption', 'sr-only', 'Mean per system, ' + scenLabel(UI.resScen)), thead, tbody);
  const rh = el('tr');
  rh.append(el('th', '', 'Run'), ...METRICS.map((mt) => el('th', '', mt.title)));
  const rb = el('tbody');
  for (const res of rows) {
    const tr = el('tr');
    tr.append(el('th', '', sysName(res.system) + ', ' + (S.scen[res.scenario] && !S.scen[res.scenario].attack || res.scenario === 'S1' ? 'without' : 'with')
                 + ' attack (' + res.scenario + ' #' + res.run + ')'),
              ...METRICS.map((mt) => el('td', '', num(res.values[mt.field]) === null ? '–' : fmtMetric(mt, res.values[mt.field], 1))));
    rb.append(tr);
  }
  const rhead = el('thead'); rhead.append(rh);
  $('res-runs').replaceChildren(el('caption', 'sr-only', 'Numbers of every finished run'), rhead, rb);
  $('res-files').replaceChildren(...(S.outs.length ? S.outs : ['(results folder unknown)']).flatMap((out) => [
    fileItem(out + '/all_results.csv', 'one line per finished run'), fileItem(out + '/runs/', 'logs and events of every run'),
    fileItem(out + '/report.pdf', 'written at the end of a complete test')]));
}
function fileItem(path, what) { const li = el('li'); li.append(el('code', '', path), ' – ' + what); return li; }
function reportLink(index) {
  const a = el('a', 'btn report-link');
  const glyph = el('span', 'glyph', '📄');
  glyph.setAttribute('aria-hidden', 'true');
  a.append(glyph, ' View report (PDF)');
  a.href = '/report/' + index;
  a.target = '_blank';
  a.rel = 'noopener';
  return a;
}
function drawReports() {   // one button (ready) or muted text (not yet) per campaign folder
  $('res-reports').replaceChildren(...S.outs.map((out, i) =>
    S.reports.get(i) ? reportLink(i) : el('span', 'bar-val none', 'Report not written yet')));
}
$('res-scen').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-scen]');
  if (!b) return;
  UI.resScen = b.dataset.scen;
  resKey = '';
  invalidate();
});

// --- tab 1: run a test --------------------------------------------------------------------------
let topoKey = '';
function drawTopoSelect() {
  const sel = $('topo-select');
  const key = JSON.stringify(T.items.map((i) => [i.name, i.switches, i.links, i.seconds, !!i.error]));
  if (key === topoKey) return;
  topoKey = key;
  const keep = sel.value;
  const opt = (it) => {
    const o = el('option', '', it.title + (it.error ? ' – could not be read'
      : ' – ' + it.switches + ' switches, ' + it.links + ' links' + (it.seconds ? ', ' + Math.round(it.seconds / 60) + ' min' : '')));
    o.title = it.title + (it.seconds ? ': about ' + Math.round(it.seconds / 60) + ' minutes for 6 runs' : '');
    o.value = it.name;
    o.disabled = !!it.error;
    return o;
  };
  const built = T.items.filter((i) => i.builtin).map(opt);
  const zoo = T.items.filter((i) => !i.builtin);
  const nodes = [...built];
  if (zoo.length) { const g = el('optgroup'); g.label = 'Downloaded from the Internet Topology Zoo'; g.append(...zoo.map(opt)); nodes.push(g); }
  if (!nodes.length) { const o = el('option', '', 'Fat-tree'); o.value = 'fat_tree'; nodes.push(o); }
  sel.replaceChildren(...nodes);
  if (keep && T.by.has(keep)) sel.value = keep;
}
function drawRunTab() {
  drawTopoSelect();
  const busy = running();
  const control = MODE === 'control';
  const off = !control || busy || !C.sudo_ok || C.pending;
  $('run-busy').hidden = !(control && busy);
  $('run-terminal').hidden = control;
  $('run-password').hidden = !(control && !C.sudo_ok);
  $('run-error').hidden = !C.error;
  setText($('run-error'), C.error);
  const reason = !control ? 'Started from the terminal: restart with python3 tools/live_view.py to use these buttons.'
    : busy ? 'A test is running: stop it first.' : !C.sudo_ok ? 'Password needed: type it in the terminal first.' : '';
  for (const id of ['btn-demo', 'btn-topo', 'btn-full', 'topo-select', 'add-name', 'add-btn']) {
    $(id).disabled = off || (id === 'add-btn' && !!T.adding);
    $(id).title = off ? reason : '';
  }
  for (const b of document.querySelectorAll('.choose')) b.disabled = !control;
  for (const card of document.querySelectorAll('.choice')) {
    card.classList.toggle('selected', card.dataset.choice === UI.choice);
    card.setAttribute('aria-disabled', String(off));
  }
}
function choose(name, focus) {
  UI.choice = name;
  try { localStorage.setItem('rsdnChoice', name); } catch (err) { /* no storage */ }
  drawRunTab();
  if (focus) { const card = $('card-' + name); const f = card.querySelector('select, .start'); if (f && !f.disabled) f.focus(); }
}
try { const v = localStorage.getItem('rsdnChoice'); if (['demo', 'topo', 'full'].includes(v)) UI.choice = v; } catch (err) { /* no storage */ }
for (const b of document.querySelectorAll('.choose')) b.addEventListener('click', () => choose(b.dataset.choose, true));
$('go-live').addEventListener('click', () => { selectTab('live', 'manual'); $('tab-live').focus(); });
$('go-run').addEventListener('click', () => { selectTab('run', 'manual'); $('tab-run').focus(); });

async function sendControl(action, extra) {   // -> {ok, status, data}
  if (C.pending) return {ok: false};
  C.pending = true;
  if (action !== 'add_topology') C.error = '';
  invalidate();
  try {
    const res = await fetch('control', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                        body: JSON.stringify(Object.assign({action, token: TOKEN}, extra || {})), cache: 'no-store'});
    let data = {};
    try { data = await res.json(); } catch (err) { /* not JSON */ }
    if (!res.ok && action !== 'add_topology') C.error = (action === 'stop' ? 'Could not stop: ' : 'Could not start: ')
      + (data.error || 'the live view answered ' + res.status + '.');
    return {ok: res.ok, status: res.status, data};
  } catch (err) {
    const text = 'Could not reach the live view: is it still running in the terminal?';
    if (action !== 'add_topology') C.error = text;
    return {ok: false, status: 0, data: {error: text}};
  } finally {
    C.pending = false;
    invalidate();
  }
}
async function start(action, extra) {
  const res = await sendControl(action, extra);
  if (res.ok) { selectTab('live', 'auto'); $('tab-live').focus(); }
}
$('btn-demo').addEventListener('click', () => start('start_demo'));
$('res-demo').addEventListener('click', () => start('start_demo'));
$('btn-topo').addEventListener('click', () => start('start_topology', {topology: $('topo-select').value}));
$('btn-full').addEventListener('click', () => openDialog($('confirm-full'), $('cf-cancel')));
$('cf-cancel').addEventListener('click', () => $('confirm-full').close());
$('cf-go').addEventListener('click', () => { $('confirm-full').close(); start('start_full'); });
$('btn-stop').addEventListener('click', () => sendControl('stop'));

// adding a network from the Topology Zoo
T.adding = null;
function addMessage(text, cls) {
  const msg = $('add-msg');
  setText(msg, text);
  msg.className = 'add-msg ' + (cls || '');
  $('add-name').setAttribute('aria-invalid', String(cls === 'bad'));
}
$('add-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = $('add-name').value.trim();
  if (!ZOO_NAME.test(name)) {
    addMessage(name ? 'Use the name as on topology-zoo.org: letters, digits, - and _ only, for example Abilene.'
                    : 'Type a network name first, for example Abilene.', 'bad');
    $('add-name').focus();
    return;
  }
  T.adding = name;
  addMessage('Adding ' + name + '… (downloading from the Topology Zoo)', '');
  const res = await sendControl('add_topology', {name});
  if (!res.ok) { T.adding = null; addMessage(res.data.error || 'Could not add ' + name + '.', 'bad'); }
  else if (res.status === 200) T.adding = null;
  invalidate();
});
function onAdded(m) {
  if (num(m.t) !== null && m.t < LOADED_AT - 2) return;   // an old answer from before this page opened
  T.adding = null;
  if (m.ok) {
    addMessage((m.already ? m.name + ' is already in the list' : 'Added ' + m.name)
               + (m.switches !== null && m.switches !== undefined ? ' (' + m.switches + ' switches, ' + m.links + ' links).' : '.'), 'ok');
    $('add-name').value = '';
    topoKey = '';
    drawTopoSelect();
    if (T.by.has(m.topology)) $('topo-select').value = m.topology;
  } else addMessage(m.error || 'Could not add ' + m.name + '.', 'bad');
}

// dialogs: native modal <dialog> (inert background, Esc cancels) plus a Tab loop inside
let dialogOpener = null;
function openDialog(dlg, first) {
  dialogOpener = document.activeElement;
  dlg.showModal();
  first.focus();
}
for (const dlg of document.querySelectorAll('dialog')) {
  dlg.addEventListener('close', () => { if (dialogOpener && dialogOpener.isConnected) dialogOpener.focus(); });
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });   // backdrop
  dlg.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const items = [...dlg.querySelectorAll('button:not(:disabled), [tabindex="0"]')];
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
}
$('glossary-open').addEventListener('click', () => openDialog($('glossary'), $('glossary-close')));
$('glossary-close').addEventListener('click', () => $('glossary').close());

// --- log panes (batched, pausable, filterable) ---------------------------------------------
const panes = {};
for (const name of ['term', 'events', 'log']) {
  const list = $('list-' + name);
  const pane = {list, jump: list.parentNode.querySelector('.jump'), stick: true, pending: []};
  list.addEventListener('scroll', () => {
    pane.stick = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
    pane.jump.classList.toggle('show', !pane.stick);
  });
  pane.jump.addEventListener('click', () => {
    pane.stick = true; list.scrollTop = list.scrollHeight; pane.jump.classList.remove('show');
  });
  panes[name] = pane;
}
let paused = false, query = '', scheduled = false;
function matches(node) { return !query || node.dataset.search.includes(query); }
function add(paneName, node, searchText) {
  node.dataset.search = searchText.toLowerCase();
  if (!matches(node)) node.classList.add('hidden-by-filter');
  panes[paneName].pending.push(node);
  if (!scheduled && !paused) { scheduled = true; requestAnimationFrame(flush); }
}
function flush() {
  scheduled = false;
  if (paused) return;
  for (const pane of Object.values(panes)) {
    if (!pane.pending.length) continue;
    const frag = document.createDocumentFragment();
    frag.append(...pane.pending);
    pane.pending = [];
    pane.list.append(frag);
    while (pane.list.childElementCount > MAX_LINES) pane.list.firstElementChild.remove();
    if (pane.stick) pane.list.scrollTop = pane.list.scrollHeight;
  }
}
function eventRow(m) {
  const kind = String(m.kind || 'unknown');
  const row = el('div', 'ev k-' + kind.replace(/[^a-z_]/gi, ''));
  const head = el('button', 'head');
  head.type = 'button';
  head.setAttribute('aria-expanded', 'false');
  head.append(el('span', 'time', clock(m.time ?? m.t)), el('span', 'kind', kind), el('span', 'msg', String(m.text)));
  const pre = el('pre');
  pre.hidden = true;
  head.addEventListener('click', () => {
    if (!pre.textContent) pre.textContent = typeof m.raw === 'string' ? m.raw : JSON.stringify(m.raw, null, 2);
    pre.hidden = !pre.hidden;
    head.setAttribute('aria-expanded', String(!pre.hidden));
  });
  row.append(head, pre);
  return row;
}
function fileCard(m) {
  const isError = m.name === 'error.txt';
  const card = el('div', 'file' + (isError ? ' error' : ''));
  card.append(el('div', '', clock(m.t) + '  File ' + (isError ? '✕ ERROR ' : '') + m.name + ' (' + m.size + ' bytes)'));
  if (typeof m.content === 'string') card.append(el('pre', '', m.content));
  return card;
}
function runText(m) {
  const r = m.run;
  return r ? topoTitle(r.topology) + ' / ' + sysName(r.system) + ' / ' + r.scenario + ' / run ' + r.run : m.name;
}

function handle(m) {
  switch (m.type) {
    case 'term': {
      const node = el('div', /FAILED|Traceback|Error/.test(m.text) ? 'err' : '', m.text);
      // error.txt (case 'file' below) already counts this same failure once; don't double it here.
      const summary = /^done (\d+), skipped (\d+), failed (\d+)$/.exec(m.text);
      if (summary) S.campaign = {done: +summary[1], skipped: +summary[2], failed: +summary[3]};
      add('term', node, m.text);
      break;
    }
    case 'event':
      onEvent(m);
      add('events', eventRow(m), m.kind + ' ' + m.text + ' ' + (typeof m.raw === 'string' ? m.raw : JSON.stringify(m.raw)));
      break;
    case 'log':
      add('log', el('div', '', m.text), m.text);
      break;
    case 'file':
      if (m.name === 'error.txt') S.errors += 1;
      onFile(m);
      add('log', fileCard(m), m.name + ' ' + (m.content || ''));
      break;
    case 'result':
      onResult(m);
      resKey = '';
      break;
    case 'report':
      onReport(m);
      resKey = '';
      break;
    case 'run': {
      onRun(m);
      const text = clock(m.t) + (m.wiped ? '  Run restarted (folder wiped): ' : '  Run started: ') + runText(m);
      for (const name of ['events', 'log']) add(name, el('div', 'sep', text), text);
      break;
    }
    case 'status': {
      const wasRunning = running();
      if (m.reset) {   // a test started from the page: fresh graphs, the logs keep their history
        resetRuns();
        linkLegendKey = resKey = '';
        const text = clock(m.t) + '  ' + (m.label || 'Test') + ' started';
        for (const name of ['term', 'events', 'log']) add(name, el('div', 'sep', text), text);
      }
      onStatus(m);
      if (m.command !== undefined) $('cmd').textContent = m.command ? '$ ' + m.command
        : MODE === 'control' ? '(nothing started yet: use Run a test)' : '(watch only)';
      if (m.state === 'running' && m.reset) autoTab('live');
      if (m.state === 'finished' && UI.ready) {
        if (!m.interrupted && m.code === 0 && S.results.size) { if (autoTab('results') || UI.tab === 'results') showToast(); }
      }
      if (wasRunning && m.state === 'finished') resKey = '';
      break;
    }
    case 'info':
      add('log', el('div', 'err', m.text), m.text);
      break;
    case 'control':
      Object.assign(C, {state: m.state, action: m.action, label: m.label || '', code: m.code,
                        stopped: !!m.stopped, sudo_ok: m.sudo_ok !== false});
      break;
    case 'topologies':
      onTopologies(m);
      topoKey = '';
      break;
    case 'add_topology':
      onAdded(m);
      break;
  }
  invalidate();
}

// --- stream: the first burst is the history; tabs only switch by themselves after it ------------
let instance = null, settleTimer = null;
function settle() {   // pick the starting tab from the state the history left
  if (UI.ready) return;
  UI.ready = true;
  const results = S.results.size > 0;
  selectTab(running() ? 'live' : MODE === 'control' ? (S.finish && results && S.finish.code === 0 ? 'results' : 'run') : 'live', 'init');
}
const source = new EventSource('stream');
source.onmessage = (e) => {
  const inst = e.lastEventId.split(':')[0];
  if (instance && inst !== instance) { location.reload(); return; }   // tool restarted
  instance = inst;
  try { handle(JSON.parse(e.data)); } catch (err) { console.error(err); }
  if (!UI.ready) { clearTimeout(settleTimer); settleTimer = setTimeout(settle, 300); }
};
source.onerror = () => {
  if (!UI.ready) settle();
  if (source.readyState !== EventSource.OPEN) setText($('narration'), 'Lost the connection to the live view. Retrying… (is it still running in the terminal?)');
};

// --- log controls ------------------------------------------------------------------------------
$('filter').addEventListener('input', (e) => {
  query = e.target.value.trim().toLowerCase();
  for (const pane of Object.values(panes))
    for (const node of pane.list.children) node.classList.toggle('hidden-by-filter', !matches(node));
});
$('pause').addEventListener('click', (e) => {
  paused = !paused;
  e.target.setAttribute('aria-pressed', String(paused));
  e.target.textContent = paused ? 'Resume' : 'Pause';
  if (!paused) flush();
});
$('stats').addEventListener('change', (e) => document.body.classList.toggle('show-stats', e.target.checked));
let fontSize = 16;
try { fontSize = Number(localStorage.getItem('liveViewFont')) || 16; } catch (err) { /* no storage */ }
function setFont(px) {
  fontSize = Math.min(32, Math.max(11, px));
  document.documentElement.style.setProperty('--fs', fontSize + 'px');
  try { localStorage.setItem('liveViewFont', String(fontSize)); } catch (err) { /* no storage */ }
}
setFont(fontSize);
$('smaller').addEventListener('click', () => setFont(fontSize - 2));
$('bigger').addEventListener('click', () => setFont(fontSize + 2));
const LOG_TABS = ['term', 'events', 'log'];
function showPane(name, focus) {
  for (const t of LOG_TABS) {
    const b = $('ltab-' + t);
    b.setAttribute('aria-selected', String(t === name));
    b.tabIndex = t === name ? 0 : -1;
    $('pane-' + t).classList.toggle('active', t === name);
  }
  if (focus) $('ltab-' + name).focus();
  const pane = panes[name];
  if (pane.stick) pane.list.scrollTop = pane.list.scrollHeight;
}
$('logtabs').addEventListener('click', (e) => { const b = e.target.closest('[data-pane]'); if (b) showPane(b.dataset.pane); });
$('logtabs').addEventListener('keydown', (e) => {
  const cur = LOG_TABS.findIndex((t) => $('ltab-' + t).getAttribute('aria-selected') === 'true');
  const to = {ArrowLeft: (cur + 2) % 3, ArrowRight: (cur + 1) % 3, Home: 0, End: 2}[e.key];
  if (to === undefined) return;
  e.preventDefault();
  showPane(LOG_TABS[to], true);
});
document.addEventListener('keydown', (e) => {
  if (document.querySelector('dialog[open]')) return;   // the dialog handles its own keys
  if (e.key === 'Escape' && viewing()) $('back-live').click();
});

buildMapLegend();
buildTimelineLegend();
if ('ResizeObserver' in window) new ResizeObserver(invalidate).observe($('main'));
else window.addEventListener('resize', invalidate);
setInterval(() => { if (!document.hidden) invalidate(); }, 1000);   // clock, phase, time left
drawAll();
</script>
</body>
</html>
'''


if __name__ == '__main__':
    sys.exit(main())
