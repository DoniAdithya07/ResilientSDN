"""tools/live_view.py: watcher, message log, server and an end-to-end run
against a fake experiment command (no sudo, no Mininet, localhost only)."""
import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import live_view as lv

TOOL = Path(lv.__file__)


# --- pure helpers -------------------------------------------------------------
def test_parse_run_name_handles_underscores_in_topology_and_system():
    assert lv.parse_run_name('fat_tree_resilient_sdn_S3_1') == {
        'topology': 'fat_tree', 'system': 'resilient_sdn', 'scenario': 'S3', 'run': '1'}
    assert lv.parse_run_name('geant_rsdn_relief_bypass_S11_0')['system'] == 'rsdn_relief_bypass'
    assert lv.parse_run_name('not-a-run-dir') is None


def test_describe_event_is_plain_english():
    reroute = lv.describe_event({'kind': 'reroute', 'flow': '10.0.0.1->10.0.0.9',
                                 'old': ['e1', 'a1', 'e2'], 'new': ['e1', 'a2', 'e2'],
                                 'reason': 'congestion on a1-e2', 'rule_changes': 3})
    assert '10.0.0.1->10.0.0.9' in reroute and 'e1 > a2 > e2' in reroute and '3 rule' in reroute
    congestion = lv.describe_event({'kind': 'congestion', 'links': ['a1-e2'],
                                    'confidence': {'a1-e2': 0.5}})
    assert 'a1-e2' in congestion and 'Congestion' in congestion
    assert 'mystery' in lv.describe_event({'kind': 'mystery', 'x': 1})
    assert lv.describe_event({}) != ''   # malformed event still renders


def test_clean_line_strips_ansi_and_carriage_returns():
    assert lv.clean_line('\x1b[31mred\x1b[0m text\r\n') == 'red text'
    assert lv.clean_line('10%\r50%\r100%') == '100%'


def test_needs_sudo_only_for_the_project_root_scripts():
    assert lv.needs_sudo(['./demo.sh']) and lv.needs_sudo(['./run.sh', 'python3', '-m', 'x'])
    assert lv.needs_sudo(['/home/u/ResilientSDN/run_full.sh']) and lv.needs_sudo(['sudo', 'mn'])
    assert not lv.needs_sudo(['python3', '-c', 'print(1)']) and not lv.needs_sudo([])


def test_guess_out_dir(tmp_path):
    root = lv.ROOT
    assert lv.guess_out_dir(['./run.sh', 'python3', '-m', 'evaluation.experiment',
                             '--out', 'results/x']) == root / 'results/x'
    assert lv.guess_out_dir(['python3', '--out=results/y']) == root / 'results/y'
    assert lv.guess_out_dir(['./demo.sh']) == root / 'results/demo'
    assert lv.guess_out_dir([]) == root / 'results'
    assert lv.guess_out_dir(['./demo.sh'], out=str(tmp_path)) == tmp_path


# --- file tailing and run watching -------------------------------------------
def test_file_tail_partial_lines_truncation_and_deletion(tmp_path):
    path = tmp_path / 'events.jsonl'
    tail = lv.FileTail(path)
    assert tail.poll() == []                         # file not there yet
    path.write_text('one\ntw')
    assert tail.poll() == ['one']                    # partial last line held back
    with open(path, 'a') as f:
        f.write('o\n')
    assert tail.poll() == ['two']
    path.write_text('new\n')                         # truncated and rewritten
    assert tail.poll() == ['new']
    path.unlink()
    assert tail.poll() == []                         # deleted: no crash
    path.write_text('again\n')
    assert tail.poll() == ['again']


def test_message_log_since_and_memory_cap():
    log = lv.MessageLog(max_messages=3)
    for i in range(5):
        log.add('term', text=str(i))
    msgs = log.since(0)
    assert [json.loads(m)['text'] for _, m in msgs] == ['2', '3', '4']   # oldest dropped
    assert [seq for seq, _ in log.since(4)] == [5]
    assert log.since(5) == []


def _texts(log, kind):
    return [json.loads(m) for _, m in log.since(0) if json.loads(m)['type'] == kind]


def test_run_watcher_follows_newest_run_and_survives_wipe(tmp_path):
    log = lv.MessageLog()
    watcher = lv.RunWatcher(tmp_path, log)
    watcher.poll()                                   # no runs/ folder yet: no crash
    run = tmp_path / 'runs' / 'fat_tree_static_S1_0'
    run.mkdir(parents=True)
    (run / 'events.jsonl').write_text(json.dumps({'t': 1.0, 'kind': 'new_flow', 'flow': 'f',
                                                  'path': ['a', 'b']}) + '\n{"t": 2, "ki')
    (run / 'ryu.log').write_text('loading app\n')
    watcher.poll()
    assert _texts(log, 'run')[-1]['run']['system'] == 'static'
    events = _texts(log, 'event')
    assert len(events) == 1 and events[0]['kind'] == 'new_flow' and 'a > b' in events[0]['text']
    assert _texts(log, 'log')[-1]['text'] == 'loading app'

    for f in run.iterdir():                          # a re-run wipes the folder
        f.unlink()
    watcher.poll()
    (run / 'events.jsonl').write_text('not json\n')
    (run / 'error.txt').write_text('Traceback: boom\n')
    watcher.poll()
    watcher.poll()                                   # content shown once the size is stable
    assert any(m.get('wiped') for m in _texts(log, 'run'))
    assert _texts(log, 'event')[-1]['kind'] == 'unparsed'
    files = {m['name']: m for m in _texts(log, 'file')}
    assert files['error.txt']['content'] == 'Traceback: boom\n'

    newer = tmp_path / 'runs' / 'fat_tree_resilient_sdn_S3_0'
    newer.mkdir()
    os.utime(newer, (time.time() + 5, time.time() + 5))
    watcher.poll()
    assert _texts(log, 'run')[-1]['run']['system'] == 'resilient_sdn'


def test_final_poll_shows_files_written_just_before_the_command_ended(tmp_path):
    log = lv.MessageLog()
    watcher = lv.RunWatcher(tmp_path, log)
    run = tmp_path / 'runs' / 'fat_tree_static_S1_0'
    run.mkdir(parents=True)
    (run / 'meta.json').write_text('{"run": 0}')
    watcher.poll(final=True)                         # no second poll for a stable size
    assert _texts(log, 'file')[0]['content'] == '{"run": 0}'


def test_meta_json_of_a_run_is_not_lost_when_the_next_run_starts_at_once(tmp_path):
    # evaluation/experiment.py writes meta.json and starts the next run milliseconds later
    log = lv.MessageLog()
    watcher = lv.RunWatcher(tmp_path, log)
    first = tmp_path / 'runs' / 'fat_tree_static_S3_0'
    first.mkdir(parents=True)
    watcher.poll()
    (first / 'meta.json').write_text('{"attack_start": 1}')
    second = tmp_path / 'runs' / 'fat_tree_resilient_sdn_S3_0'
    second.mkdir()
    os.utime(second, (time.time() + 5, time.time() + 5))
    watcher.poll()
    names = [(m['type'], m.get('name')) for m in map(json.loads, (d for _, d in log.since(0)))]
    assert names.index(('file', 'meta.json')) < names.index(('run', 'fat_tree_resilient_sdn_S3_0'))


def test_watch_only_replays_a_few_earlier_runs_oldest_first(tmp_path):
    log = lv.MessageLog()
    now = time.time()
    for i, system in enumerate(('static', 'simple_reactive', 'resilient_sdn', 'qosentry')):
        run = tmp_path / 'runs' / f'fat_tree_{system}_S1_0'
        run.mkdir(parents=True)
        (run / 'events.jsonl').write_text(json.dumps({'t': i, 'kind': 'start', 'system': system}) + '\n')
        os.utime(run, (now - 100 + i, now - 100 + i))
    watcher = lv.RunWatcher(tmp_path, log, backlog=2)
    watcher.poll()
    watcher.poll()
    assert [m['run']['system'] for m in _texts(log, 'run')] == ['simple_reactive', 'resilient_sdn',
                                                                'qosentry']
    assert [m['raw']['system'] for m in _texts(log, 'event')] == ['simple_reactive', 'resilient_sdn',
                                                                  'qosentry']


# --- report.pdf watching (one level up from runs/, sibling of the run folders) --
def test_report_pdf_is_announced_only_once_its_size_is_stable(tmp_path):
    log = lv.MessageLog()
    reports = lv.ReportStatus()
    watcher = lv.RunWatcher(tmp_path, log, reports=reports)
    pdf = tmp_path / 'report.pdf'
    pdf.write_bytes(b'x' * 10)
    watcher.poll()
    assert _texts(log, 'report') == []            # first sighting: not stable yet
    assert reports.lookup(0) == (tmp_path, False)
    pdf.write_bytes(b'x' * 20)                    # still growing between polls
    watcher.poll()
    assert _texts(log, 'report') == []
    watcher.poll()                                # unchanged since the previous poll: stable
    msgs = _texts(log, 'report')
    assert len(msgs) == 1
    assert msgs[0]['index'] == 0 and msgs[0]['ready'] is True and msgs[0]['size'] == 20
    assert msgs[0]['out'] == str(tmp_path)
    assert reports.lookup(0) == (tmp_path, True)
    watcher.poll()                                # already announced: not sent again
    assert len(_texts(log, 'report')) == 1


def test_final_poll_announces_report_pdf_without_waiting_for_a_stable_size(tmp_path):
    log = lv.MessageLog()
    watcher = lv.RunWatcher(tmp_path, log)
    (tmp_path / 'report.pdf').write_bytes(b'done')
    watcher.poll(final=True)                      # command ended: nothing writes any more
    assert _texts(log, 'report')[0]['size'] == 4


def test_report_pdf_watcher_covers_every_campaign_folder_of_a_complete_run(tmp_path):
    log = lv.MessageLog()
    reports = lv.ReportStatus()
    a, b = tmp_path / 'fattree_5runs', tmp_path / 'wan_5runs'
    a.mkdir()
    b.mkdir()
    watcher = lv.RunWatcher([a, b], log, reports=reports)
    (a / 'report.pdf').write_bytes(b'A')
    watcher.poll()
    watcher.poll()
    assert reports.lookup(0) == (a, True) and reports.lookup(1) == (b, False)
    (b / 'report.pdf').write_bytes(b'B')
    watcher.poll()
    watcher.poll()
    assert reports.lookup(1) == (b, True)


# --- HTTP server ----------------------------------------------------------------
@pytest.fixture
def server():
    log = lv.MessageLog()
    srv = lv.make_server(log, 0)
    lv.serve_in_background(srv)
    yield srv, log
    srv.shutdown()
    srv.server_close()


def test_server_binds_localhost_only(server):
    srv, _ = server
    assert srv.server_address[0] == '127.0.0.1'


def test_foreign_host_header_is_refused(server):
    # DNS rebinding: a hostile page whose name resolves to 127.0.0.1 must not read the stream
    srv, _ = server
    port = srv.server_address[1]
    for path in ('/', '/stream'):
        req = urllib.request.Request(f'http://127.0.0.1:{port}{path}',
                                     headers={'Host': f'attacker.example:{port}'})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        assert e.value.code == 403


# --- GET /report/<n> -------------------------------------------------------------
def test_report_endpoint_checks_the_host_header(server):
    srv, _ = server
    srv.reports.set_outs([Path('/tmp')])
    srv.reports.mark_ready(0)
    req = urllib.request.Request(f'http://127.0.0.1:{srv.server_address[1]}/report/0',
                                 headers={'Host': f'attacker.example:{srv.server_address[1]}'})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 403


def test_report_non_numeric_or_negative_index_is_400_and_never_touches_disk(server):
    srv, _ = server
    # points at a folder that does not exist: if the index were ever used to read a
    # path, this would surface as something other than a clean 400 (e.g. a 500)
    srv.reports.set_outs([Path('/does/not/exist')])
    srv.reports.mark_ready(0)
    port = srv.server_address[1]
    for path in ('/report/-1', '/report/abc', '/report/1.5', '/report/',
                 '/report/0x1', '/report/1e2'):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=5)
        assert e.value.code == 400, path


def test_report_index_out_of_range_is_400(server):
    srv, _ = server
    srv.reports.set_outs([Path('/tmp/a'), Path('/tmp/b')])
    port = srv.server_address[1]
    for path in ('/report/2', '/report/99'):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=5)
        assert e.value.code == 400, path
    # also 400 when nothing has ever been published (e.g. before any run started)
    plain_srv = lv.make_server(lv.MessageLog(), 0)
    lv.serve_in_background(plain_srv)
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f'http://127.0.0.1:{plain_srv.server_address[1]}/report/0',
                                   timeout=5)
        assert e.value.code == 400
    finally:
        plain_srv.shutdown()
        plain_srv.server_close()


def test_report_not_yet_announced_is_404(server, tmp_path):
    srv, _ = server
    (tmp_path / 'report.pdf').write_bytes(b'not ready')
    srv.reports.set_outs([tmp_path])   # in range, but never marked ready
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f'http://127.0.0.1:{srv.server_address[1]}/report/0', timeout=5)
    assert e.value.code == 404


def test_report_file_missing_despite_being_marked_ready_is_404_not_a_crash(server, tmp_path):
    srv, _ = server
    srv.reports.set_outs([tmp_path])
    srv.reports.mark_ready(0)   # ready claimed, but report.pdf was never written (or got deleted)
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f'http://127.0.0.1:{srv.server_address[1]}/report/0', timeout=5)
    assert e.value.code == 404


def test_report_pdf_is_served_with_the_right_bytes_and_headers_once_ready(server, tmp_path):
    srv, _ = server
    pdf_bytes = b'%PDF-1.4 not a real pdf but exact bytes matter here' * 50
    (tmp_path / 'report.pdf').write_bytes(pdf_bytes)
    srv.reports.set_outs([tmp_path])
    srv.reports.mark_ready(0)
    with urllib.request.urlopen(f'http://127.0.0.1:{srv.server_address[1]}/report/0', timeout=5) as r:
        assert r.status == 200
        assert r.headers['Content-Type'] == 'application/pdf'
        assert r.headers['Content-Length'] == str(len(pdf_bytes))
        assert r.headers['Content-Disposition'] == 'inline; filename="report.pdf"'
        assert r.read() == pdf_bytes


def test_page_sends_safety_headers(server):
    srv, _ = server
    with urllib.request.urlopen(f'http://localhost:{srv.server_address[1]}/', timeout=5) as r:
        assert r.headers['X-Content-Type-Options'] == 'nosniff'
        assert "default-src 'none'" in r.headers['Content-Security-Policy']


def test_pump_flushes_a_huge_line_without_newline(tmp_path):
    log = lv.MessageLog()
    src = tmp_path / 'out.bin'   # a file, not a pipe: 1 MB would block a pipe's 64 KB buffer
    src.write_bytes(b'x' * (lv.MAX_PARTIAL + 10))
    with open(os.devnull, 'wb') as echo, open(src, 'rb') as out:
        lv.pump(SimpleNamespace(stdout=out), log, echo)
    sizes = [len(m['text']) for m in _texts(log, 'term')]
    assert max(sizes) <= lv.MAX_PARTIAL and sum(sizes) == lv.MAX_PARTIAL + 10


def test_page_uses_text_content_never_inner_html(server):
    srv, _ = server
    with urllib.request.urlopen(f'http://127.0.0.1:{srv.server_address[1]}/', timeout=5) as r:
        page = r.read().decode()
        assert r.headers['Content-Type'].startswith('text/html')
    assert '<title>ResilientSDN test bench</title>' in page
    assert 'textContent' in page
    assert 'innerHTML' not in page and 'insertAdjacentHTML' not in page
    assert 'http://' not in page and 'https://' not in page   # no external resources


def test_stream_resumes_after_last_event_id(server):
    srv, log = server
    for i in range(3):
        log.add('term', text=f'line {i}')
    req = urllib.request.Request(f'http://127.0.0.1:{srv.server_address[1]}/stream',
                                 headers={'Last-Event-ID': '2'})
    msgs = read_stream(req, lambda got: len(got) >= 1, timeout=5)
    assert [m['text'] for m in msgs] == ['line 2']


# --- end to end ----------------------------------------------------------------------
FAKE = textwrap.dedent('''
    import json, sys, time
    from pathlib import Path
    run = Path(sys.argv[1]) / 'runs' / 'fat_tree_resilient_sdn_S3_1'
    run.mkdir(parents=True)
    print('\\x1b[32m[1/1] fat_tree resilient_sdn S3 run 1\\x1b[0m', flush=True)
    (run / 'ryu.log').write_text('loading app controller/resilient_sdn.py\\n')
    with open(run / 'events.jsonl', 'w') as f:
        for ev in ({'kind': 'start', 'system': 'ResilientSDN'},
                   {'kind': 'congestion', 'links': ['a1-e2'], 'confidence': {'a1-e2': 0.9}},
                   {'kind': 'reroute', 'flow': 'h1->h2', 'old': ['e1', 'a1'],
                    'new': ['e1', 'a2'], 'reason': 'congestion on a1-e2', 'rule_changes': 2}):
            time.sleep(0.3)
            f.write(json.dumps({'t': time.time(), **ev}) + '\\n')
            f.flush()
    (run / 'meta.json').write_text(json.dumps({'topology': 'fat_tree', 'run': 1}))
    print('done 1, skipped 0, failed 0', flush=True)
    time.sleep(0.8)
    sys.exit(3)
''')


def read_stream(url, done, timeout):
    """SSE messages from /stream until done(messages) or timeout."""
    got = []
    end = time.time() + timeout
    with urllib.request.urlopen(url, timeout=timeout) as r:
        while time.time() < end and not done(got):
            line = r.readline().decode()
            if line.startswith('data: '):
                got.append(json.loads(line[6:]))
    return got


def start_tool(tmp_path, *command):
    out = open(tmp_path / 'tool.out', 'w+')
    proc = subprocess.Popen([sys.executable, str(TOOL), '--no-browser', '--port', '0',
                             '--out', str(tmp_path / 'res'), '--', *command],
                            stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    end = time.time() + 5
    while time.time() < end:
        text = (tmp_path / 'tool.out').read_text()
        if 'http://127.0.0.1:' in text:
            port = int(text.split('http://127.0.0.1:')[1].split('/')[0])
            return proc, f'http://127.0.0.1:{port}'
        time.sleep(0.05)
    proc.kill()
    raise AssertionError('no URL printed: ' + (tmp_path / 'tool.out').read_text())


def test_end_to_end_streams_everything_and_passes_exit_code(tmp_path):
    script = tmp_path / 'fake.py'
    script.write_text(FAKE)
    proc, url = start_tool(tmp_path, sys.executable, str(script), str(tmp_path / 'res'))
    try:
        msgs = read_stream(url + '/stream',
                           lambda got: any(m['type'] == 'status' and m['state'] == 'finished'
                                           for m in got), timeout=8)
    finally:
        proc.send_signal(signal.SIGINT)
        code = proc.wait(timeout=5)
    terminal = [m['text'] for m in msgs if m['type'] == 'term']
    assert '[1/1] fat_tree resilient_sdn S3 run 1' in terminal          # ANSI stripped
    assert 'done 1, skipped 0, failed 0' in terminal
    runs = [m['run'] for m in msgs if m['type'] == 'run']
    assert runs[0] == {'topology': 'fat_tree', 'system': 'resilient_sdn',
                       'scenario': 'S3', 'run': '1'}
    assert [m['kind'] for m in msgs if m['type'] == 'event'] == ['start', 'congestion', 'reroute']
    assert 'loading app controller/resilient_sdn.py' in [m['text'] for m in msgs
                                                         if m['type'] == 'log']
    meta = [m for m in msgs if m['type'] == 'file' and m['name'] == 'meta.json']
    assert json.loads(meta[0]['content'])['topology'] == 'fat_tree'
    assert msgs[-1]['code'] == 3
    assert code == 3                                                    # exit code passthrough
    echoed = (tmp_path / 'tool.out').read_text()
    assert '\x1b[32m[1/1] fat_tree resilient_sdn S3 run 1\x1b[0m' in echoed   # terminal unchanged


def test_ctrl_c_stops_a_running_command(tmp_path):
    proc, _ = start_tool(tmp_path, sys.executable, '-c',
                         'import time\nprint("busy", flush=True)\ntime.sleep(60)')
    time.sleep(0.5)
    proc.send_signal(signal.SIGINT)
    code = proc.wait(timeout=5)
    assert code != 0


# --- planned runs, timings and results (graphs) ----------------------------------
def test_tiny_yaml_reads_the_keys_the_view_needs():
    text = textwrap.dedent('''
        # comment
        systems: [static, simple_reactive, resilient_sdn]
        n_runs: 1
        warmup_seconds: 3              # after switches connect
        duration_seconds: 30
        results_dir: results/smoke
        legit:
          rate_mbps: 10
        trace:
          file: null
    ''')
    cfg = lv.tiny_yaml(text)
    assert cfg['systems'] == ['static', 'simple_reactive', 'resilient_sdn']
    assert cfg['n_runs'] == 1 and cfg['warmup_seconds'] == 3 and cfg['duration_seconds'] == 30
    assert cfg['results_dir'] == 'results/smoke'
    assert 'rate_mbps' not in cfg                          # nested keys are not needed


def test_timing_from_smoke_config():
    cfg = lv.read_config(lv.ROOT / 'config' / 'smoke_config.yaml')
    assert (cfg['warmup_seconds'], cfg['attack_delay_seconds'], cfg['duration_seconds']) == (3, 8, 30)
    assert lv.read_config(lv.ROOT / 'config' / 'missing.yaml') == {}


def test_scenario_info_has_durations_and_attack_flag():
    info = lv.scenario_info(['S1', 'S3', 'S6'], default_duration=30)
    assert info['S1']['attack'] is False and info['S3']['attack'] is True
    assert info['S3']['duration'] == 30 and info['S6']['duration'] == 600
    assert info['S1']['label'] == 'no attack' and 'attack' in info['S3']['label']


def test_plan_for_demo_sh_follows_the_experiment_order():
    plan = lv.plan_for_command(['./demo.sh'])
    names = [r['name'] for r in plan['runs']]
    assert names == ['fat_tree_static_S1_0', 'fat_tree_simple_reactive_S1_0',
                     'fat_tree_resilient_sdn_S1_0', 'fat_tree_static_S3_0',
                     'fat_tree_simple_reactive_S3_0', 'fat_tree_resilient_sdn_S3_0']
    assert plan['timing'] == {'warmup_seconds': 3, 'attack_delay_seconds': 8,
                              'duration_seconds': 30, 'cleanup_seconds': lv.CLEANUP_SECONDS}
    assert set(plan['scenarios']) == {'S1', 'S3'}


def test_plan_for_run_full_sh_adds_up_both_campaigns():
    plan = lv.plan_for_command(['./run_full.sh'])
    # fat_tree: 3 systems x 11 scenarios x 5 runs; WAN: 3 topologies x 3 x 5 x 5
    assert len(plan['runs']) == 3 * 11 * 5 + 3 * 3 * 5 * 5
    assert plan['runs'][0]['name'] == 'fat_tree_static_S1_0'
    assert plan['runs'][1]['name'] == 'fat_tree_simple_reactive_S1_0'
    assert plan['runs'][3]['name'] == 'fat_tree_static_S1_1'    # run before next scenario


def test_plan_for_a_direct_experiment_command(tmp_path):
    cfg = tmp_path / 'c.yaml'
    cfg.write_text('systems: [static, resilient_sdn]\nscenarios: [S1]\ntopologies: [fat_tree]\n'
                   'n_runs: 3\nwarmup_seconds: 1\nattack_delay_seconds: 2\nduration_seconds: 4\n')
    plan = lv.plan_for_command(['./run.sh', 'python3', '-m', 'evaluation.experiment',
                                f'--config={cfg}', '--scenarios', 'S3', '--runs', '1'])
    assert [r['name'] for r in plan['runs']] == ['fat_tree_static_S3_0', 'fat_tree_resilient_sdn_S3_0']
    assert plan['timing']['duration_seconds'] == 4
    assert lv.plan_for_command(['python3', '-c', 'print(1)']) is None
    assert lv.plan_for_command([]) is None


def test_plan_inferred_from_an_existing_results_folder(tmp_path):
    for name in ('fat_tree_static_S1_0', 'fat_tree_resilient_sdn_S1_0', 'junk'):
        (tmp_path / 'runs' / name).mkdir(parents=True)
    plan = lv.plan_from_folder(tmp_path)
    assert [r['name'] for r in plan['runs']] == ['fat_tree_static_S1_0', 'fat_tree_resilient_sdn_S1_0']
    assert lv.plan_from_folder(tmp_path / 'nothing') is None


HEADER = ('timestamp,system,scenario,topology,run,throughput_mbps,packet_loss_pct,latency_ms,'
          'recovery_time_s,recovered,time_above_s,stable_recovery_s,stable_recovered,'
          'secondary_congestion,rule_changes,oscillations,reroutes,unresolved,controller_cpu_pct\n')
ROW_STATIC = '1793184074,static,S1,fat_tree,0,3.17,59.9,4281.9,0.0,True,0.0,0.0,True,0,0,0,0,0,15.68\n'
ROW_RSDN = '1793184400,resilient_sdn,S3,fat_tree,0,9.5,1.25,12.5,4.0,True,7.5,6.0,True,0,6,1,3,0,20.1\n'


def test_results_tail_turns_csv_rows_into_result_messages(tmp_path):
    log = lv.MessageLog()
    results = lv.ResultsWatcher(tmp_path, log)
    results.poll()                                         # file not there yet: no crash
    csv_path = tmp_path / 'all_results.csv'
    csv_path.write_text(HEADER + ROW_STATIC + 'garbage,row\n' + ROW_RSDN[:30])   # partial last line
    results.poll()
    got = _texts(log, 'result')
    assert [m['name'] for m in got] == ['fat_tree_static_S1_0']
    assert got[0]['values']['packet_loss_pct'] == 59.9 and got[0]['values']['recovered'] is True
    with open(csv_path, 'a') as f:
        f.write(ROW_RSDN[30:])
    results.poll()
    got = _texts(log, 'result')
    assert got[-1]['name'] == 'fat_tree_resilient_sdn_S3_0'
    assert got[-1]['values']['reroutes'] == 3 and got[-1]['values']['time_above_s'] == 7.5
    assert got[-1]['system'] == 'resilient_sdn' and got[-1]['scenario'] == 'S3'


def test_results_tail_finds_csv_of_a_sub_campaign_and_ignores_rows_before_a_header(tmp_path):
    log = lv.MessageLog()
    results = lv.ResultsWatcher(tmp_path, log)
    sub = tmp_path / 'fattree_5runs'
    sub.mkdir()
    (sub / 'all_results.csv').write_text(ROW_STATIC + HEADER + ROW_RSDN)
    results.poll()
    assert [m['name'] for m in _texts(log, 'result')] == ['fat_tree_resilient_sdn_S3_0']


def test_results_from_before_the_run_started_are_skipped(tmp_path):
    # a Start press sees the previous demo's csv until demo.sh wipes it; same run names
    log = lv.MessageLog()
    (tmp_path / 'all_results.csv').write_text(HEADER + ROW_STATIC + ROW_RSDN)
    lv.ResultsWatcher(tmp_path, log, since=1793184200).poll()
    assert [m['name'] for m in _texts(log, 'result')] == ['fat_tree_resilient_sdn_S3_0']


def test_parse_result_row_rejects_bad_rows():
    header = HEADER.strip().split(',')
    assert lv.parse_result_row(header, 'a,b,c') is None
    assert lv.parse_result_row(header, ROW_STATIC.replace(',0,3.17', ',x,3.17')) is None   # run
    bad_number = lv.parse_result_row(header, ROW_STATIC.replace('3.17', 'nan'))
    assert 'throughput_mbps' not in bad_number['values']    # non-finite numbers are left out


FAKE_CAMPAIGN = textwrap.dedent('''
    import json, sys, time
    from pathlib import Path
    out = Path(sys.argv[1])
    systems = ['static', 'resilient_sdn']
    for i, system in enumerate(systems):
        run = out / 'runs' / f'fat_tree_{system}_S3_0'
        run.mkdir(parents=True)
        print(f'[{i + 1}/2] fat_tree {system} S3 run 0', flush=True)
        t0 = time.time()
        evs = [{'kind': 'start', 'system': system},
               {'kind': 'new_flow', 'flow': 'h1->h16', 'path': ['e1', 'a1', 'c1', 'a7', 'e8']}]
        for k, u in enumerate((0.2, 0.5, 0.85, 0.95, 0.9)):
            evs.append({'kind': 'stats', 'util': {'c1-a7': u, 'c2-a7': 0.1, 'a1-e1': 0.3},
                        'flows': {}, 'latency': {}, 'rules': k})
        evs += [{'kind': 'congestion', 'links': ['c1-a7'], 'confidence': {'c1-a7': 0.9}},
                {'kind': 'held', 'flow': 'h1->h16', 'new_dpp': 0.5, 'current_dpp': 0.4},
                {'kind': 'reroute', 'flow': 'h1->h16', 'old': ['e1', 'a1', 'c1', 'a7', 'e8'],
                 'new': ['e1', 'a2', 'c3', 'a8', 'e8'], 'reason': 'congestion on c1-a7',
                 'rule_changes': 4},
                {'kind': 'stats', 'util': {'c1-a7': 0.4, 'c3-a8': 0.6}, 'flows': {}, 'latency': {},
                 'rules': 9}]
        with open(run / 'events.jsonl', 'w') as f:
            for k, ev in enumerate(evs):
                f.write(json.dumps({'t': t0 + k, **ev}) + '\\n')
                f.flush()
                time.sleep(0.05)
        (run / 'meta.json').write_text(json.dumps({'target_link': 'c1-a7', 'attack_start': t0 + 2,
                                                   'end': t0 + 8, 'threshold': 0.8}))
        new = not (out / 'all_results.csv').exists()
        with open(out / 'all_results.csv', 'a') as f:
            if new:
                f.write(HEADER)
            f.write(f'{round(t0)},{system},S3,fat_tree,0,9.5,1.5,12.5,4.0,True,'
                    f'{3 if system == "static" else 1},6.0,True,0,6,1,{i * 2},0,20.1\\n')
        time.sleep(0.6)
    print('done 2, skipped 0, failed 0', flush=True)
''')


def test_end_to_end_campaign_streams_plan_stats_results_and_runs(tmp_path):
    cfg = tmp_path / 'c.yaml'
    cfg.write_text('systems: [static, resilient_sdn]\nscenarios: [S3]\ntopologies: [fat_tree]\n'
                   'n_runs: 1\nwarmup_seconds: 1\nattack_delay_seconds: 2\nduration_seconds: 6\n')
    script = tmp_path / 'fake_campaign.py'
    script.write_text(f'HEADER = {HEADER!r}\n' + FAKE_CAMPAIGN)
    # the fake ignores the experiment arguments; the view reads them to know the plan
    proc, url = start_tool(tmp_path, sys.executable, str(script), str(tmp_path / 'res'),
                           '-m', 'evaluation.experiment', '--config', str(cfg))
    try:
        msgs = read_stream(url + '/stream',
                           lambda got: any(m['type'] == 'status' and m['state'] == 'finished'
                                           for m in got), timeout=15)
    finally:
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=5)
    status = next(m for m in msgs if m['type'] == 'status')
    assert [r['name'] for r in status['plan']['runs']] == ['fat_tree_static_S3_0',
                                                           'fat_tree_resilient_sdn_S3_0']
    assert status['plan']['timing']['attack_delay_seconds'] == 2
    assert status['plan']['scenarios']['S3']['duration'] == 6
    assert [m['name'] for m in msgs if m['type'] == 'run'] == ['fat_tree_static_S3_0',
                                                               'fat_tree_resilient_sdn_S3_0']
    stats = [m for m in msgs if m['type'] == 'event' and m['kind'] == 'stats']
    assert len(stats) == 12 and max(m['raw']['util']['c1-a7'] for m in stats) == 0.95
    kinds = {m['kind'] for m in msgs if m['type'] == 'event'}
    assert {'congestion', 'reroute', 'held'} <= kinds
    results = [m for m in msgs if m['type'] == 'result']
    assert [(m['system'], m['values']['reroutes']) for m in results] == [('static', 0),
                                                                         ('resilient_sdn', 2)]
    metas = [m for m in msgs if m['type'] == 'file' and m['name'] == 'meta.json']
    assert len(metas) == 2


def test_watch_flag_plans_from_the_command_without_running_it(tmp_path):
    args = lv.parse_args(['--watch', '--out', str(tmp_path), './demo.sh'])
    assert args.watch and args.command == ['./demo.sh']
    marker = tmp_path / 'ran'
    out = open(tmp_path / 'tool.out', 'w+')
    proc = subprocess.Popen([sys.executable, str(TOOL), '--no-browser', '--port', '0', '--watch',
                             '--out', str(tmp_path / 'res'), '--', 'sh', '-c', f'touch {marker}',
                             '-m', 'evaluation.experiment', '--config', 'config/smoke_config.yaml'],
                            stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    try:
        url = None
        end = time.time() + 5
        while url is None and time.time() < end:
            text = (tmp_path / 'tool.out').read_text()
            if 'http://127.0.0.1:' in text:
                url = 'http://127.0.0.1:' + text.split('http://127.0.0.1:')[1].split('/')[0]
            time.sleep(0.05)
        msgs = read_stream(url + '/stream', lambda got: any(m['type'] == 'status' for m in got),
                           timeout=5)
    finally:
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=5)
    status = next(m for m in msgs if m['type'] == 'status')
    assert status['state'] == 'watching' and len(status['plan']['runs']) == 9 * 3
    assert not marker.exists()                                # the command was not run


def test_page_has_the_live_graphs_results_and_collapsed_logs(server):
    srv, _ = server
    with urllib.request.urlopen(f'http://127.0.0.1:{srv.server_address[1]}/', timeout=5) as r:
        page = r.read().decode()
    for node_id in ('chart-link', 'chart-map', 'chart-timeline', 'res-metrics', 'list-term', 'list-events',
                    'list-log', 'phases', 'numbers'):
        assert f'id="{node_id}"' in page, node_id
    assert '<details class="more" id="live-details">' in page   # collapsed by default (no open)
    assert 'prefers-reduced-motion' in page and 'prefers-color-scheme: dark' in page
    assert 'createElementNS' in page and 'requestAnimationFrame' in page
    assert 'aria-label' in page and 'role="tablist"' in page


# --- control mode: Start / Stop buttons in the page ------------------------------
import http.client   # noqa: E402

FAKE_RUN = textwrap.dedent('''
    import json, signal, sys, time
    from pathlib import Path
    out, seconds = Path(sys.argv[1]), float(sys.argv[2])
    def stop(*_):
        (out / 'got-sigint').write_text('yes')
        print('cleaning up after Ctrl+C', flush=True)
        time.sleep(0.3)
        sys.exit(130)
    signal.signal(signal.SIGINT, stop)
    run = out / 'runs' / 'fat_tree_static_S3_0'
    run.mkdir(parents=True)
    print('[1/1] fat_tree static S3 run 0', flush=True)
    with open(run / 'events.jsonl', 'w') as f:
        f.write(json.dumps({'t': time.time(), 'kind': 'start', 'system': 'static'}) + '\\n')
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(0.05)
    print('done 1, skipped 0, failed 0', flush=True)
''')


def _messages(log, kind):
    return [json.loads(d) for _, d in log.since(0) if json.loads(d)['type'] == kind]


def _wait_for(check, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


FAKE_CATALOG = [{'name': 'fat_tree', 'title': 'Fat-tree', 'builtin': True, 'switches': 20, 'links': 32},
                {'name': 'abilene', 'title': 'Abilene', 'builtin': False, 'switches': 11, 'links': 14}]


@pytest.fixture
def control(tmp_path):
    """Control-mode server whose allow-list runs small fake scripts (no sudo, no Mininet)."""
    script = tmp_path / 'fake_run.py'
    script.write_text(FAKE_RUN)
    demo_out, full_out = tmp_path / 'demo', tmp_path / 'full'
    demo_out.mkdir()
    full_out.mkdir()
    runs = {'start_demo': lv.RunSpec('Demo run', [sys.executable, str(script), str(demo_out), '30'],
                                     [demo_out]),
            'start_full': lv.RunSpec('Complete run', [sys.executable, str(script), str(full_out), '0.3'],
                                     [full_out])}
    log = lv.MessageLog()
    devnull = open(os.devnull, 'wb')
    ctl = lv.Control(log, runs=runs, echo=devnull, grace=(3, 1, 1), catalog=lambda: FAKE_CATALOG,
                     topologies=lambda: ['fat_tree', 'internet2', 'abilene'],
                     downloader=lambda argv: pytest.fail(f'unexpected download {argv}'),
                     wipe=lambda out: pytest.fail(f'unexpected wipe {out}'))
    srv = lv.make_server(log, 0, mode='control', control=ctl)
    lv.serve_in_background(srv)
    yield SimpleNamespace(srv=srv, log=log, ctl=ctl, demo_out=demo_out, full_out=full_out,
                          port=srv.server_address[1], tmp=tmp_path)
    ctl.close()
    srv.shutdown()
    srv.server_close()
    devnull.close()


def post(c, body=None, headers=None, raw=None, path='/control'):
    """POST to the control server; returns (status, parsed JSON body or {})."""
    data = raw if raw is not None else json.dumps(body).encode()
    hdrs = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{c.port}'}
    hdrs.update(headers or {})
    req = urllib.request.Request(f'http://127.0.0.1:{c.port}{path}', data=data, method='POST',
                                 headers={k: v for k, v in hdrs.items() if v is not None})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        text = e.read()
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, {}


def ok_body(c, action):
    return {'action': action, 'token': c.srv.token}


def test_mode_is_control_only_without_a_command_watch_or_out():
    assert lv.pick_mode(lv.parse_args([])) == 'control'
    assert lv.pick_mode(lv.parse_args(['--port', '0', '--no-browser'])) == 'control'
    assert lv.pick_mode(lv.parse_args(['./demo.sh'])) == 'command'
    assert lv.pick_mode(lv.parse_args(['--watch', './demo.sh'])) == 'watch'
    assert lv.pick_mode(lv.parse_args(['--out', 'results/demo'])) == 'watch'   # README form


def test_default_allow_list_is_only_the_two_project_scripts():
    runs = lv.default_runs()
    assert set(runs) == {'start_demo', 'start_full'}
    assert runs['start_demo'].command == [str(lv.ROOT / 'demo.sh')]
    assert runs['start_full'].command == [str(lv.ROOT / 'run_full.sh')]
    assert runs['start_demo'].outs == [lv.ROOT / 'results' / 'demo']
    full = runs['start_full'].outs
    assert lv.ROOT / 'results' / 'fattree_5runs' in full and lv.ROOT / 'results' / 'wan_5runs' in full
    assert lv.ROOT / 'results' / 'ablation_A' in full
    assert lv.ROOT / 'results' / 'demo' not in full


def test_control_token_is_random_per_server_and_only_in_the_control_page(control):
    other = lv.make_server(lv.MessageLog(), 0, mode='control', control=control.ctl)
    try:
        assert len(control.srv.token) >= 32 and other.token != control.srv.token
    finally:
        other.server_close()
    with urllib.request.urlopen(f'http://127.0.0.1:{control.port}/', timeout=5) as r:
        page = r.read().decode()
    assert control.srv.token in page and 'data-mode="control"' in page
    plain = lv.make_server(lv.MessageLog(), 0)
    lv.serve_in_background(plain)
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{plain.server_address[1]}/', timeout=5) as r:
            page = r.read().decode()
        assert 'data-mode="command"' in page and control.srv.token not in page
        assert '__TOKEN__' not in page and '__MODE__' not in page
        c = SimpleNamespace(port=plain.server_address[1])
        assert post(c, {'action': 'start_demo', 'token': ''})[0] == 404   # no control here
    finally:
        plain.shutdown()
        plain.server_close()


def test_control_needs_the_exact_token(control):
    for body in ({'action': 'start_demo'}, {'action': 'start_demo', 'token': 'wrong'},
                 {'action': 'start_demo', 'token': control.srv.token + 'x'},
                 {'action': 'start_demo', 'token': 12345}):
        assert post(control, body)[0] == 403
    assert control.ctl.proc is None


def test_control_needs_our_own_origin(control):
    for origin in ('http://attacker.example', 'null', f'http://127.0.0.1:{control.port + 1}',
                   f'https://127.0.0.1:{control.port}', None):
        assert post(control, ok_body(control, 'start_demo'), headers={'Origin': origin})[0] == 403
    assert control.ctl.proc is None


def test_control_checks_the_host_header(control):
    status, _ = post(control, ok_body(control, 'start_demo'),
                     headers={'Host': f'attacker.example:{control.port}'})
    assert status == 403 and control.ctl.proc is None


def test_control_needs_a_json_content_type(control):
    for ctype in ('text/plain', 'application/x-www-form-urlencoded', None):
        status, _ = post(control, ok_body(control, 'start_demo'), headers={'Content-Type': ctype})
        assert status == 415
    assert control.ctl.proc is None


def test_control_rejects_big_or_unsized_bodies(control):
    big = json.dumps({**ok_body(control, 'start_demo'), 'pad': 'x' * 2000}).encode()
    assert post(control, raw=big)[0] == 413
    conn = http.client.HTTPConnection('127.0.0.1', control.port, timeout=5)
    conn.putrequest('POST', '/control')
    conn.putheader('Content-Type', 'application/json')
    conn.putheader('Origin', f'http://127.0.0.1:{control.port}')
    conn.endheaders()
    assert conn.getresponse().status == 411
    conn.close()
    assert post(control, raw=b'{not json')[0] == 400
    assert post(control, raw=b'["start_demo"]')[0] == 400
    assert control.ctl.proc is None


def test_get_never_changes_state(control):
    for path in ('/control', '/control?action=start_demo&token=' + control.srv.token):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f'http://127.0.0.1:{control.port}{path}', timeout=5)
        assert e.value.code in (404, 405)
    assert post(control, ok_body(control, 'start_demo'), path='/stream')[0] == 404
    assert control.ctl.proc is None


def test_client_cannot_run_anything_but_the_allow_list(control):
    marker = control.tmp / 'hacked'
    for action in ('rm -rf /', 'start_demo; touch x', 'START_DEMO', '', None, ['start_demo']):
        assert post(control, {'action': action, 'token': control.srv.token})[0] == 400
    # extra fields are ignored: only the allow-listed command for the action runs
    status, _ = post(control, {**ok_body(control, 'start_full'),
                               'command': ['touch', str(marker)], 'out': str(marker)})
    assert status == 200
    assert _wait_for(lambda: control.ctl.state == 'finished')
    assert not marker.exists()
    assert control.ctl.code == 0


def test_start_then_second_start_is_409_and_stop_sends_sigint(control):
    status, data = post(control, ok_body(control, 'start_demo'))
    assert status == 200 and data['state'] == 'running'
    assert post(control, ok_body(control, 'start_demo'))[0] == 409
    assert post(control, ok_body(control, 'start_full'))[0] == 409      # one run at a time
    assert _wait_for(lambda: any(m['run']['system'] == 'static' for m in _messages(control.log, 'run')))
    status, data = post(control, ok_body(control, 'stop'))
    assert status == 200 and data['state'] == 'stopping'
    assert _wait_for(lambda: control.ctl.state == 'finished')
    assert (control.demo_out / 'got-sigint').read_text() == 'yes'       # graceful SIGINT first
    states = [m['state'] for m in _messages(control.log, 'control')]
    assert states[:1] == ['idle'] and 'running' in states and 'stopping' in states
    assert states[-1] == 'finished'
    last = _messages(control.log, 'control')[-1]
    assert last['stopped'] is True and last['code'] == 130 and last['label'] == 'Demo run'
    terminal = [m['text'] for m in _messages(control.log, 'term')]
    assert 'cleaning up after Ctrl+C' in terminal                       # output still streamed
    assert post(control, ok_body(control, 'stop'))[0] == 409            # nothing left to stop


def test_a_new_run_resets_the_page_and_watches_its_own_folder(control):
    assert post(control, ok_body(control, 'start_full'))[0] == 200
    assert _wait_for(lambda: control.ctl.state == 'finished')
    status = [m for m in _messages(control.log, 'status') if m['state'] == 'running']
    assert status[-1]['reset'] is True and status[-1]['label'] == 'Complete run'
    assert [m['dir'] for m in _messages(control.log, 'run')] == [
        str(control.full_out / 'runs' / 'fat_tree_static_S3_0')]
    finished = [m for m in _messages(control.log, 'status') if m['state'] == 'finished']
    assert finished[-1]['code'] == 0 and finished[-1]['interrupted'] is False
    # the next run may start once the first one has finished
    assert post(control, ok_body(control, 'start_demo'))[0] == 200
    assert _wait_for(lambda: any(m['dir'].startswith(str(control.demo_out))
                                 for m in _messages(control.log, 'run')))


def test_control_mode_wires_the_report_watcher_into_the_server_endpoint(tmp_path):
    """End to end with the same wiring run_control() uses for the real page: one
    ReportStatus shared by Control's RunWatcher and the server's /report/<n>."""
    script = tmp_path / 'fake_run.py'
    script.write_text(FAKE_RUN)
    out = tmp_path / 'demo'
    out.mkdir()
    runs = {'start_demo': lv.RunSpec('Demo run', [sys.executable, str(script), str(out), '30'], [out])}
    log = lv.MessageLog()
    reports = lv.ReportStatus()
    devnull = open(os.devnull, 'wb')
    ctl = lv.Control(log, runs=runs, echo=devnull, grace=(3, 1, 1), reports=reports,
                     catalog=lambda: FAKE_CATALOG, topologies=lambda: ['fat_tree'],
                     downloader=lambda argv: pytest.fail(f'unexpected download {argv}'),
                     wipe=lambda out: pytest.fail(f'unexpected wipe {out}'))
    srv = lv.make_server(log, 0, mode='control', control=ctl, reports=reports)
    lv.serve_in_background(srv)
    c = SimpleNamespace(port=srv.server_address[1], srv=srv)
    try:
        assert post(c, {'action': 'start_demo', 'token': srv.token})[0] == 200
        assert _wait_for(lambda: (out / 'runs').exists())
        (out / 'report.pdf').write_bytes(b'%PDF fake report')
        assert _wait_for(lambda: reports.lookup(0) == (out, True), timeout=5)
        assert _messages(log, 'report')[-1] == {
            'type': 'report', 't': _messages(log, 'report')[-1]['t'], 'index': 0, 'out': str(out),
            'ready': True, 'size': len(b'%PDF fake report')}
        with urllib.request.urlopen(f'http://127.0.0.1:{c.port}/report/0', timeout=5) as r:
            assert r.read() == b'%PDF fake report'
        assert post(c, {'action': 'stop', 'token': srv.token})[0] == 200
        assert _wait_for(lambda: ctl.state == 'finished')
    finally:
        ctl.close()
        srv.shutdown()
        srv.server_close()
        devnull.close()


def test_sudo_refresh_failure_shows_password_needed_and_blocks_start(control):
    answers = iter([False, False, True])
    keeper = lv.SudoKeeper(control.ctl, check=lambda: next(answers, True), every=0.05,
                           ask=lambda: None)
    keeper.start()
    try:
        assert _wait_for(lambda: control.ctl.sudo_ok is False)
        assert any(m['sudo_ok'] is False for m in _messages(control.log, 'control'))
        assert _wait_for(lambda: control.ctl.sudo_ok is True)          # flips back
    finally:
        keeper.stop()
    control.ctl.set_sudo(False)
    status, data = post(control, ok_body(control, 'start_demo'))
    assert status == 409 and 'password' in data['error'].lower()
    assert control.ctl.proc is None


def test_descendants_and_signal_pids_reach_grandchildren(tmp_path):
    flag = tmp_path / 'grandchild-got-int'
    child = subprocess.Popen(
        ['sh', '-c', f'{sys.executable} -c "import signal,sys,time,pathlib\n'
                     f'signal.signal(signal.SIGINT, lambda *a: (pathlib.Path(\'{flag}\').write_text(\'1\'), sys.exit(0)))\n'
                     f'time.sleep(30)" & wait'])
    try:
        assert _wait_for(lambda: len(lv.descendants(child.pid)) >= 1, timeout=5)
        time.sleep(0.3)   # the grandchild installs its handler
        lv.signal_pids({child.pid} | lv.descendants(child.pid), signal.SIGINT)
        assert _wait_for(flag.exists, timeout=5)
    finally:
        child.kill()
        child.wait(timeout=5)


def test_control_page_has_buttons_dialogs_glossary_and_help(control):
    with urllib.request.urlopen(f'http://127.0.0.1:{control.port}/', timeout=5) as r:
        page = r.read().decode()
    for text in ('Start demo', 'Start test', 'Start full run…', 'Stop', 'Glossary',
                 'Start the 17–19 h run', 'Cancel', 'finished runs are skipped', 'Back to live',
                 'Password needed'):
        assert text in page, text
    assert page.count('<dialog id=') == 2
    for term in ('S1', 'S3', 'Congestion', 'The 0.8 limit', 'Reroute', 'Held back', 'Path flip-flop',
                 'DPP score', 'Topology Zoo', 'Static routing', 'Simple reactive', 'ResilientSDN'):
        assert f'<dt>{term}' in page, term
    assert 'aria-describedby' in page and 'aria-pressed' in page
    assert 'innerHTML' not in page and 'insertAdjacentHTML' not in page
    assert "fetch('control'" in page


# --- topologies: list, 6-run test of one, adding one from the Topology Zoo ----------
def test_topology_catalog_lists_builtins_first_then_zoo_networks_with_sizes():
    items = lv.topology_catalog()
    names = [i['name'] for i in items]
    assert names[:4] == ['fat_tree', 'internet2', 'geant', 'nsfnet']
    by = {i['name']: i for i in items}
    assert (by['fat_tree']['switches'], by['fat_tree']['links'], by['fat_tree']['builtin']) == (20, 32, True)
    assert (by['nsfnet']['switches'], by['nsfnet']['links']) == (13, 15)
    assert 'geant2012' not in by                     # the file behind the built-in geant
    if 'abilene' in by:                              # shipped in topology/data
        ab = by['abilene']
        assert (ab['switches'], ab['links'], ab['builtin'], ab['title']) == (11, 14, False, 'Abilene')
        assert all('lat' in n and 'lon' in n for n in ab['nodes']) and len(ab['edges']) == 14
        assert 'New York' in [n['short'] for n in ab['nodes']]
    assert by['fat_tree']['seconds'] > 0 and not any('lat' in n for n in by['fat_tree']['nodes'])


def test_short_city_labels_for_the_map():
    assert lv.short_label('Kansas City, MO') == 'Kansas City'
    assert lv.short_label('NCSA, University of Illinois, Champaign') == 'Champaign'
    assert lv.short_label('Cornell Theory Center, Ithaca NY') == 'Ithaca'
    assert lv.short_label('San Diego Supercomputer Center') == 'San Diego'
    assert lv.short_label('NL') == 'NL' and len(lv.short_label('x' * 40)) <= 16


def test_control_publishes_the_topology_list(control):
    msgs = _messages(control.log, 'topologies')
    assert msgs and msgs[-1]['items'] == FAKE_CATALOG


def test_topology_test_command_is_exact():
    spec = lv.topology_run('abilene')
    assert spec.command == [str(lv.ROOT / 'run.sh'), 'python3', '-m', 'evaluation.experiment',
                            '--config', 'config/smoke_config.yaml', '--out', 'results/abilene_demo',
                            '--topologies', 'abilene', '--scenarios', 'S1', 'S3', '--runs', '1']
    assert spec.outs == [lv.ROOT / 'results' / 'abilene_demo'] and spec.fresh is True
    plan = lv.plan_for_command(spec.command)
    assert len(plan['runs']) == 6 and {r['topology'] for r in plan['runs']} == {'abilene'}


def test_start_topology_runs_only_a_name_from_the_current_list(control):
    calls = []
    script = control.tmp / 'fake_run.py'

    def spec(topo):
        calls.append(lv.topology_run(topo).command)    # the real argv, recorded, not run
        return lv.RunSpec('Test', [sys.executable, str(script), str(control.full_out), '0.2'],
                          [control.full_out], resume=True)
    control.ctl.topology_spec = spec
    for bad in ('nope', '../etc', 'ABILENE', 'abilene ', 'fat_tree; touch x', '', None, 5, ['abilene']):
        status, data = post(control, {**ok_body(control, 'start_topology'), 'topology': bad})
        assert status == 400 and 'Unknown network' in data['error'], bad
    assert post(control, ok_body(control, 'start_topology'))[0] == 400      # topology missing
    assert calls == [] and control.ctl.proc is None
    status, _ = post(control, {**ok_body(control, 'start_topology'), 'topology': 'abilene'})
    assert status == 200
    assert calls == [[str(lv.ROOT / 'run.sh'), 'python3', '-m', 'evaluation.experiment',
                      '--config', 'config/smoke_config.yaml', '--out', 'results/abilene_demo',
                      '--topologies', 'abilene', '--scenarios', 'S1', 'S3', '--runs', '1']]
    assert _wait_for(lambda: control.ctl.state == 'finished')
    running = [m for m in _messages(control.log, 'status') if m['state'] == 'running']
    assert running[-1]['outs'] == [str(control.full_out)]


def test_default_runs_never_mark_demo_or_full_run_as_fresh():
    # demo.sh does its own `sudo rm -rf` first line; run_full.sh is meant to resume.
    # Only a topology test (no wrapping script of its own) gets the server-side wipe.
    runs = lv.default_runs()
    assert runs['start_demo'].fresh is False and runs['start_full'].fresh is False


def test_wipe_results_skips_sudo_when_the_folder_is_already_gone(tmp_path):
    assert lv.wipe_results(tmp_path / 'never-existed') is True   # no subprocess needed


def test_fresh_topology_test_wipes_old_results_before_starting(control):
    wiped, old = [], control.tmp / 'topo_out'
    (old / 'runs').mkdir(parents=True)   # looks like a finished earlier test
    (old / 'all_results.csv').write_text('timestamp,topology\n100,abilene\n')

    def fake_wipe(out):
        wiped.append(out)
        shutil.rmtree(out, ignore_errors=True)
        return True
    control.ctl.wipe = fake_wipe
    script = control.tmp / 'fake_run.py'
    control.ctl.topology_spec = lambda topo: lv.RunSpec(
        'Test', [sys.executable, str(script), str(old), '0.2'], [old], fresh=True)
    status, _ = post(control, {**ok_body(control, 'start_topology'), 'topology': 'abilene'})
    assert status == 200 and wiped == [old]
    assert _wait_for(lambda: control.ctl.state == 'finished')
    # the wipe ran, and before the run started (not after/never): the fake script
    # recreates `old` once it runs, so this is the real ordering proof, not a file check
    kinds = [json.loads(d)['type'] for _, d in control.log.since(0)]
    assert kinds.index('term') < [i for i, k in enumerate(kinds) if k == 'status'][0]


def test_wipe_failure_is_reported_and_the_command_never_starts(control):
    calls = []

    def failing_wipe(out):
        calls.append(out)
        return False
    control.ctl.wipe = failing_wipe
    script = control.tmp / 'fake_run.py'
    control.ctl.topology_spec = lambda topo: lv.RunSpec(
        'Test', [sys.executable, str(script), str(control.tmp / 'x'), '0.2'],
        [control.tmp / 'x'], fresh=True)
    status, data = post(control, {**ok_body(control, 'start_topology'), 'topology': 'abilene'})
    assert status == 200 and calls == [control.tmp / 'x']
    assert control.ctl.proc is None   # never launched
    assert control.ctl.state == 'finished' and control.ctl.code != 0
    assert any('clear' in m['text'].lower() for m in _messages(control.log, 'term'))


def test_resumed_test_shows_result_rows_from_before_its_start(tmp_path):
    (tmp_path / 'all_results.csv').write_text(
        'timestamp,topology,system,scenario,run,time_above_s\n100,abilene,static,S3,0,12\n')
    log = lv.MessageLog()
    lv.RunWatcher(tmp_path, log, since=time.time() - 1, results_since=0).poll()
    assert [m['values']['time_above_s'] for m in _texts(log, 'result')] == [12]


def test_add_topology_rejects_bad_names_without_downloading(control):
    for bad in ('../x', '..', 'a/b', 'a b', 'x;rm -rf /', '$(id)', '-x', '_x', 'A' * 41, '',
                'abc\n', 'Äbilene', None, 5, ['Abilene']):
        status, data = post(control, {**ok_body(control, 'add_topology'), 'name': bad})
        assert status == 400 and 'letters, digits' in data['error'], bad
    assert not _messages(control.log, 'add_topology')


def test_add_topology_downloads_with_a_fixed_argv_and_refreshes_the_list(control):
    calls = []

    def fake(argv):
        calls.append(argv)
        return 0, 'bics: 33 switches, 48 links, saved to topology/data/Bics.graphml\nuse it with: ...\n'
    control.ctl.downloader = fake
    before = len(_messages(control.log, 'topologies'))
    status, data = post(control, {**ok_body(control, 'add_topology'), 'name': 'Bics'})
    assert status == 202 and data['adding'] == 'Bics'
    assert _wait_for(lambda: _messages(control.log, 'add_topology'))
    msg = _messages(control.log, 'add_topology')[-1]
    assert calls == [[sys.executable, '-m', 'topology.add', 'Bics']]
    assert (msg['ok'], msg['switches'], msg['links'], msg['error']) == (True, 33, 48, None)
    assert len(_messages(control.log, 'topologies')) == before + 1
    assert control.ctl.adding is None


def test_add_topology_failures_are_plain_english(control):
    outputs = iter([(1, 'Traceback (most recent call last):\nurllib.error.HTTPError: HTTP Error 404: Not Found\n'),
                    (1, 'urllib.error.URLError: <urlopen error [Errno -3] Temporary failure>\n')])
    control.ctl.downloader = lambda argv: next(outputs)
    expected = ["No network called 'Abilen' in the Topology Zoo. Check the spelling on topology-zoo.org.",
                'Could not reach the Topology Zoo. Check the internet connection and try again.']
    for i, text in enumerate(expected):
        assert post(control, {**ok_body(control, 'add_topology'), 'name': 'Abilen'})[0] == 202
        assert _wait_for(lambda: len(_messages(control.log, 'add_topology')) == i + 1)
        msg = _messages(control.log, 'add_topology')[-1]
        assert msg['ok'] is False and msg['error'] == text and msg['switches'] is None
        assert _wait_for(lambda: control.ctl.adding is None)

    def slow(argv):
        raise subprocess.TimeoutExpired(argv, lv.ADD_TIMEOUT)
    control.ctl.downloader = slow
    assert post(control, {**ok_body(control, 'add_topology'), 'name': 'Abilen'})[0] == 202
    assert _wait_for(lambda: len(_messages(control.log, 'add_topology')) == 3)
    assert 'longer than 60 s' in _messages(control.log, 'add_topology')[-1]['error']


def test_add_topology_one_at_a_time_and_known_names_are_not_downloaded(control):
    import threading
    release = threading.Event()
    control.ctl.downloader = lambda argv: (release.wait(5), (0, 'x: 1 switches, 0 links'))[1]
    assert post(control, {**ok_body(control, 'add_topology'), 'name': 'Bics'})[0] == 202
    status, data = post(control, {**ok_body(control, 'add_topology'), 'name': 'Cesnet'})
    assert status == 409 and 'Bics' in data['error']
    release.set()
    assert _wait_for(lambda: control.ctl.adding is None)
    control.ctl.downloader = lambda argv: pytest.fail('already in the list: no download')
    status, _ = post(control, {**ok_body(control, 'add_topology'), 'name': 'Abilene'})
    assert status == 200
    assert _messages(control.log, 'add_topology')[-1]['already'] is True


def _page(port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=5) as r:
        return r.read().decode()


def test_every_mode_has_the_tabs_status_and_stop(control, server):
    for page in (_page(control.port), _page(server[0].server_address[1])):
        assert page.count('role="tab"') >= 6          # 3 page tabs + 3 log tabs
        for tab in ('Run a test', 'Watch live', 'Results'):
            assert f'>{tab}<' in page, tab
        assert 'id="btn-stop"' in page and 'ResilientSDN test bench' in page
        assert 'role="status"' in page and 'id="narration"' in page
        assert 'Show details (controller actions, logs, run files)' in page
        for card in ('Quick demo', 'Test a topology', 'Complete evaluation'):
            assert card in page, card


def test_command_and_watch_pages_show_disabled_cards_with_the_reason(tmp_path):
    for mode in ('command', 'watch'):
        page = lv.render_page(mode)
        assert f'data-mode="{mode}"' in page
        assert 'Started from the terminal – stop it there or restart with' in page
        assert 'python3 tools/live_view.py' in page


def test_page_has_the_narration_and_results_templates():
    page = lv.render_page('control')
    for text in ('This takes about 20 s.', 'Watch whether ', 'is over the 0.8 limit (congested).',
                 'waited instead of moving traffic again', 'Static routing never changes paths',
                 'Next run starts in a few seconds.', 'Opening Results.',
                 'Runs that finished are saved; the current run was discarded.',
                 'Type it in the terminal window, then come back here.',
                 'Check Show details → Terminal, then start again.',
                 ' s over the limit vs ', ' moved traffic ', 'switched paths back and forth ',
                 '% of packets vs ', ' Mbit/s vs ', 'were about the same (',
                 'Without an attack, ResilientSDN costs nothing: results match the others.',
                 'runs done – numbers will change.', 'not run yet',
                 'No results yet. Run the quick demo (about 7 min) to fill this page.',
                 'Results ready', 'Downloaded from the Internet Topology Zoo',
                 'Add a network from the Topology Zoo:', 'A test is running – see Watch live.'):
        assert text in page, text
    for removed in ('What am I looking at?', 'Attack (estimated)', 'L to open', 'class="kpi'):
        assert removed not in page, removed


def test_page_has_the_report_button_and_not_written_yet_state():
    page = lv.render_page('control')
    assert 'id="res-reports"' in page                       # in the Results tab header…
    assert 'id="res-files"' in page                          # …separate from the informational list
    assert "el('span', 'glyph', '📄')" in page                # icon span, like the other buttons
    assert 'View report (PDF)' in page
    assert 'Report not written yet' in page
    assert "'/report/' + " in page                           # built only from the validated index
    assert "case 'report':" in page and 'onReport' in page   # reacts to the SSE message, no polling


def test_a_failed_run_counts_as_one_error_not_two():
    # experiment.py always emits a "...: FAILED ..." terminal line AND writes error.txt for the
    # SAME failure; only error.txt (the 'file' case) may increment S.errors, not the term-text match too.
    page = lv.render_page('control')
    assert "if (/FAILED/.test(m.text)) S.errors" not in page   # the old double-counting line is gone
    assert page.count('S.errors += 1') == 1                    # counted exactly once, from error.txt
    assert "if (m.name === 'error.txt') S.errors += 1" in page


def test_campaign_finish_reports_skipped_and_failed_runs_not_just_all_done():
    # evaluation/experiment.py's own final line is "done N, skipped M, failed K" — the window must
    # never claim "All N runs finished" when some failed or were skipped (resumed from a prior run).
    page = lv.render_page('control')
    assert r"/^done (\d+), skipped (\d+), failed (\d+)$/" in page
    assert 'S.campaign = {done:' in page
    assert "'Finished: ' + bits.join(', ')" in page
    assert "' already had results'" in page and "' failed (see Show details " in page
