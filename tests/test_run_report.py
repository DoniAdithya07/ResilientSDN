import json

from analysis.run_report import build
from evaluation.metrics import FIELDS


def test_report_pdf_is_built_from_a_run_folder(tmp_path):
    rows = ['{}'.format(','.join(FIELDS))]
    for system in ('static', 'resilient_sdn'):
        d = tmp_path / 'runs' / f'nsfnet_{system}_S3_0'
        d.mkdir(parents=True)
        (d / 'meta.json').write_text(json.dumps({
            'target_link': 's1-s2', 'threshold': 0.8, 'attack_start': 10.0, 'end': 40.0,
            'legit': [], 'attack': [['h1', 'h2']]}))
        util = 0.9 if system == 'static' else 0.5
        (d / 'events.jsonl').write_text('\n'.join(
            json.dumps({'t': t, 'kind': 'stats', 'util': {'s1-s2': util}}) for t in (12, 20, 30)))
        row = dict.fromkeys(FIELDS, 0)
        row.update(system=system, scenario='S3', topology='nsfnet', reroutes=int(util < 0.8))
        rows.append(','.join(str(row[f]) for f in FIELDS))
    (tmp_path / 'all_results.csv').write_text('\n'.join(rows) + '\n')
    pdf = build(tmp_path)
    assert pdf.read_bytes().startswith(b'%PDF') and pdf.stat().st_size > 5000


def make_folder(tmp_path, meta=True, events='ok', threshold=0.8, loss=0.0, reroutes=0, scenario='S3'):
    rows = [','.join(FIELDS)]
    for system in ('static', 'resilient_sdn'):
        d = tmp_path / 'runs' / f'nsfnet_{system}_{scenario}_0'
        d.mkdir(parents=True)
        if meta:
            (d / 'meta.json').write_text(json.dumps({
                'target_link': 's1-s2', 'threshold': threshold, 'attack_start': 10.0, 'end': 40.0,
                'legit': [], 'attack': []}))
        if events == 'ok':
            (d / 'events.jsonl').write_text('\n'.join(
                json.dumps({'t': t, 'kind': 'stats', 'util': {'s1-s2': 0.5}}) for t in (12, 20, 30)))
        elif events == 'truncated':
            (d / 'events.jsonl').write_text('{"t": 12, "kind": "sta')
        row = dict.fromkeys(FIELDS, 0)
        row.update(system=system, scenario=scenario, topology='nsfnet', packet_loss_pct=loss,
                   reroutes=reroutes)
        rows.append(','.join(str(row[f]) for f in FIELDS))
    (tmp_path / 'all_results.csv').write_text('\n'.join(rows) + '\n')
    return tmp_path


def text_of(pdf):
    from pypdf import PdfReader
    return ' '.join(p.extract_text() for p in PdfReader(pdf).pages).replace('\n', ' ')


def test_bad_run_files_do_not_crash_and_are_not_explained_as_calm(tmp_path):
    for kind in ({'meta': False}, {'events': 'truncated'}, {'events': None}, {'threshold': None},
                 {'scenario': 'S11'}):
        folder = tmp_path / str(len(list(tmp_path.iterdir())))
        folder.mkdir()
        pdf = build(make_folder(folder, **{'meta' if k == 'meta' else k: v for k, v in kind.items()}))
        if 'meta' in kind or kind.get('events', 'ok') != 'ok':
            assert 'No explanation possible' in text_of(pdf)


def test_explanation_follows_the_numbers(tmp_path):
    calm = tmp_path / 'a'
    calm.mkdir()
    # static and resilient both peak at 50% (below 80%): 5 reroutes are false alarms, not "correct"
    assert 'false alarms' in text_of(build(make_folder(calm, reroutes=5))) \
        and 'Unexpected reroutes' in text_of(build(calm))
    lossy = tmp_path / 'b'
    lossy.mkdir()
    assert 'this loss is not explained by congestion' in text_of(build(make_folder(lossy, loss=12)))


def test_empty_results_give_a_clear_error(tmp_path):
    import pytest
    (tmp_path / 'all_results.csv').write_text(','.join(FIELDS) + '\n')
    with pytest.raises(ValueError, match='no runs'):
        build(tmp_path)


def test_correctness_checks_flag_a_static_run_that_reroutes(tmp_path):
    folder = make_folder(tmp_path, reroutes=3)   # static "rerouted" 3 times: impossible
    text = text_of(build(folder))
    assert 'Static routing never reroutes' in text and 'FAIL' in text
