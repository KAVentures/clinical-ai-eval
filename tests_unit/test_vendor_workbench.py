import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from caeval import vendor_workbench as w, vendor_submission as s


@pytest.fixture
def workspace(tmp_path):
    p = tmp_path / 'workbench.db'
    request = s.freeze(dict(request_id='r', buyer='hospital', vendor='company', product='ai', version='v1',
                            requirements=[dict(id='safety', mandatory=True, description='Report')]))
    root = w.init(p, request, request['request_sha256'], 'operator', 'reviewer', 'representative')
    return p, request, root


def package(request, supplied):
    return s.build(request, request['request_sha256'], {'safety': {'status': 'supplied' if supplied else 'unavailable',
                   'explanation': '' if supplied else 'pending'}}, {'safety': b'report'} if supplied else {})


def note(action, actor, data, response=None):
    return dict(id='q', requirement='safety', actor=actor, action=action, date='2026-01-01',
                due='2026-01-02' if action == 'open' else None, evidence_sha256=w.sha(data), response_sha256=response)


def test_full_procurement_flow(workspace):
    p, request, root = workspace
    assert w.status(p, root, root, '2026-01-03')['mandatory_missing'] == ['safety']
    head = w.import_submission(p, root, root, package(request, False))
    head = w.record_clarification(p, root, head, note('open', 'operator', b'question'), b'question')
    assert w.status(p, root, head, '2026-01-03')['clarifications']['overdue'] == 1
    head = w.record_clarification(p, root, head, note('respond', 'representative', b'answer'), b'answer')
    head = w.record_clarification(p, root, head, note('accept', 'reviewer', b'review', w.sha(b'answer')), b'review')
    assert w.status(p, root, head, '2026-01-03')['mandatory_missing'] == ['safety']
    head = w.import_submission(p, root, head, package(request, True))
    report = w.status(p, root, head, '2026-01-03')
    assert report['mandatory_missing'] == [] and report['clarifications']['unresolved'] == 0
    assert len(report['submission_digests']) == 2 and report['history_events'] == 5
    assert not report['decision_grade'] and not report['deployment_authorized']


def test_stale_head_and_concurrency(workspace):
    p, request, root = workspace
    def attempt(supplied):
        try:
            return w.import_submission(p, root, root, package(request, supplied))
        except w.WorkbenchError:
            return None
    with ThreadPoolExecutor(2) as executor:
        heads = list(executor.map(attempt, (True, False)))
    assert sum(h is not None for h in heads) == 1
    head = next(h for h in heads if h)
    assert w.status(p, root, head, '2026-01-03')['history_events'] == 1
    with pytest.raises(w.WorkbenchError):
        w.status(p, root, root, '2026-01-03')


@pytest.mark.parametrize('sql', ["UPDATE history SET artifact=x'41'", "DELETE FROM history", "UPDATE history SET payload=x'7b7d'", "UPDATE history SET digest='bad'"])
def test_tampering(workspace, sql):
    p, request, root = workspace
    head = w.import_submission(p, root, root, package(request, True))
    with sqlite3.connect(p) as c:
        c.execute(sql)
    with pytest.raises(w.WorkbenchError):
        w.status(p, root, head, '2026-01-03')


def test_rejections_do_not_advance(workspace):
    p, request, root = workspace
    raw = package(request, True)
    head = w.import_submission(p, root, root, raw)
    with pytest.raises(w.WorkbenchError):
        w.import_submission(p, root, head, raw)
    with pytest.raises(w.WorkbenchError):
        w.record_clarification(p, root, head, note('open','operator',b'wrong'), b'actual')
    with pytest.raises(ValueError):
        w.record_clarification(p, root, head, note('open','representative',b'question'), b'question')
    other = s.freeze({**request['request'], 'version': 'v2'})
    with pytest.raises(ValueError):
        w.import_submission(p, root, head, package(other, True))
    assert w.status(p, root, head, '2026-01-03')['history_events'] == 1


def test_private_file_and_cli(workspace, tmp_path, capsys):
    p, request, root = workspace
    import os
    if os.name == 'posix':
        assert p.stat().st_mode & 0o777 == 0o600
    archive = tmp_path/'submission.zip'
    archive.write_bytes(package(request, True))
    args = ['--db', str(p), '--root', root, '--head', root]
    assert w.main(['import', *args, '--archive', str(archive)]) == 0
    head = capsys.readouterr().out.strip()
    output = tmp_path/'report.json'
    args = ['status','--db',str(p),'--root',root,'--head',head,'--as-of','2026-01-03','--output',str(output)]
    assert w.main(args) == 0
    assert json.loads(output.read_text())['mandatory_missing'] == []
    assert w.main(args) == 2
    if os.name == 'posix':
        p.chmod(0o644)
        with pytest.raises(w.WorkbenchError):
            w.status(p, root, head, '2026-01-03')
