"""Local single-operator procurement evidence workbench; not an approval engine."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

from . import vendor_submission as submission, vendor_clarification as clarification

SCHEMA = 'vendor-workbench/1'
MAX_EVENTS = 300
MAX_TOTAL = 96 * 1024 * 1024
MAX_NOTE = 256 * 1024


class WorkbenchError(ValueError):
    pass


def require(ok):
    if not ok:
        raise WorkbenchError('invalid workbench scope, history, file or checkpoint')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    value = json.loads(raw, object_pairs_hook=submission._unique)
    require(canonical(value) == raw)
    return value


def _scope(request, expected_request, requester, reviewer, representative):
    req = submission._request(request, expected_request)
    return clarification.freeze({'request_sha256': expected_request, 'version': req['version'],
        'requester': requester, 'reviewer': reviewer, 'vendor': representative,
        'requirements': [r['id'] for r in req['requirements']]})


def _root(request, plan):
    return sha(canonical([SCHEMA, request, plan]))


def _path(path):
    p = Path(path).absolute()
    require(p.parent.is_dir() and not p.parent.is_symlink() and p.parent.resolve() == p.parent)
    if os.name == 'posix':
        s = p.parent.stat()
        require(s.st_uid == os.getuid() and not s.st_mode & 0o077)
    return p


@contextmanager
def _connect(path, *, write=False):
    p = _path(path)
    s = p.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1)
    if os.name == 'posix':
        require(s.st_uid == os.getuid() and not s.st_mode & 0o077)
    conn = sqlite3.connect(p.as_uri() + '?mode=rw', uri=True, timeout=10)
    try:
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init(path, request, expected_request, requester, reviewer, representative):
    plan = _scope(request, expected_request, requester, reviewer, representative)
    root = _root(request, plan)
    p = _path(path)
    fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with _connect(p, write=True) as c:
            c.execute('CREATE TABLE scope (id INTEGER PRIMARY KEY CHECK(id=1), request BLOB NOT NULL, plan BLOB NOT NULL)')
            c.execute('CREATE TABLE history (seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload BLOB NOT NULL, artifact BLOB NOT NULL, digest TEXT NOT NULL)')
            c.execute('INSERT INTO scope VALUES (1, ?, ?)', (canonical(request), canonical(plan)))
    except BaseException:
        p.unlink()
        raise
    return root


def _event_digest(root, previous, seq, kind, payload, artifact):
    return sha(canonical([SCHEMA, root, previous, seq, kind, sha(payload), sha(artifact)]))


def _load(c, expected_root, expected_head):
    require(clarification.hex64(expected_root) and clarification.hex64(expected_head))
    scope = c.execute('SELECT id, request, plan FROM scope LIMIT 2').fetchall()
    require(len(scope) == 1 and scope[0][0] == 1)
    request, plan = (parse(raw) for raw in scope[0][1:])
    req_digest = request['request_sha256']
    actors = plan['plan']
    require(_scope(request, req_digest, actors['requester'], actors['reviewer'], actors['vendor']) == plan)
    require(_root(request, plan) == expected_root)
    count, size, largest_payload = c.execute('SELECT count(*), coalesce(sum(length(artifact)),0), coalesce(max(length(payload)),0) FROM history').fetchone()
    require(count <= MAX_EVENTS and size <= MAX_TOTAL and largest_payload <= submission.MAX_MANIFEST)
    previous = expected_root
    clarifications, submissions = [], []
    latest = None
    for i, (seq, kind, payload, artifact, digest) in enumerate(c.execute('SELECT seq,kind,payload,artifact,digest FROM history ORDER BY seq'), 1):
        require(seq == i and isinstance(payload, bytes) and isinstance(artifact, bytes))
        require(_event_digest(expected_root, previous, seq, kind, payload, artifact) == digest)
        data = parse(payload)
        if kind == 'submission':
            require(len(artifact) <= submission.MAX_ARCHIVE)
            latest = submission.verify(request, req_digest, artifact)
            require(data == latest and latest['archive_sha256'] not in submissions)
            submissions.append(latest['archive_sha256'])
        elif kind == 'clarification':
            require(1 <= len(artifact) <= MAX_NOTE and data['body']['evidence_sha256'] == sha(artifact))
            clarifications.append(data)
        else:
            raise WorkbenchError('unknown history event')
        previous = digest
    require(previous == expected_head)
    plan_digest = plan['plan_sha256']
    clarification_head = clarifications[-1]['digest'] if clarifications else plan_digest
    # Replay full underlying state machine, including role and response binding.
    clarification.report(plan, plan_digest, clarifications, clarification_head,
                         clarifications[-1]['body']['date'] if clarifications else '2000-01-01')
    return request, plan, clarifications, submissions, latest, count, size


def _insert(c, root, head, count, size, kind, payload, artifact):
    raw = canonical(payload)
    require(count < MAX_EVENTS and size + len(artifact) <= MAX_TOTAL and len(raw) <= submission.MAX_MANIFEST)
    digest = _event_digest(root, head, count+1, kind, raw, artifact)
    c.execute('INSERT INTO history VALUES (?, ?, ?, ?, ?)', (count+1, kind, raw, artifact, digest))
    return digest


def import_submission(path, root, head, raw):
    """Record an authenticated-by-operator submission; never open attachments."""
    with _connect(path, write=True) as c:
        request, _, _, prior, _, count, size = _load(c, root, head)
        receipt = submission.verify(request, request['request_sha256'], raw)
        require(receipt['archive_sha256'] not in prior)
        return _insert(c, root, head, count, size, 'submission', receipt, raw)


def record_clarification(path, root, head, body, artifact):
    """The trusted operator supplies actor identity and exact evidence bytes."""
    require(isinstance(artifact, bytes) and 1 <= len(artifact) <= MAX_NOTE)
    require(isinstance(body, dict) and body.get('evidence_sha256') == sha(artifact))
    with _connect(path, write=True) as c:
        _, plan, events, _, _, count, size = _load(c, root, head)
        d = plan['plan_sha256']
        new = clarification.append(plan, d, events, events[-1]['digest'] if events else d, body)
        return _insert(c, root, head, count, size, 'clarification', new, artifact)


def status(path, root, head, as_of):
    with _connect(path) as c:
        request, plan, events, prior, latest, count, _ = _load(c, root, head)
        d = plan['plan_sha256']
        review = clarification.report(plan, d, events, events[-1]['digest'] if events else d, as_of)
        missing = latest['mandatory_missing'] if latest else [r['id'] for r in request['request']['requirements'] if r['mandatory']]
        return {'schema': SCHEMA, 'root': root, 'head': head, 'as_of': as_of,
                'history_events': count, 'submission_digests': prior, 'latest_receipt': latest,
                'mandatory_missing': missing, 'clarifications': review,
                'decision_grade': False, 'deployment_authorized': False, 'publication_safe': False}


def _read_json(path):
    return json.loads(submission._read(path, submission.MAX_MANIFEST), object_pairs_hook=submission._unique)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    pre = sub.add_parser('init')
    for name in ('db', 'request', 'expected-request', 'requester', 'reviewer', 'representative'):
        pre.add_argument('--'+name, required=True)
    for name in ('import', 'clarify', 'status'):
        cmd = sub.add_parser(name)
        for field in ('db', 'root', 'head'):
            cmd.add_argument('--'+field, required=True)
        if name == 'import':
            cmd.add_argument('--archive', required=True)
        elif name == 'clarify':
            cmd.add_argument('--body', required=True)
            cmd.add_argument('--artifact', required=True)
        else:
            cmd.add_argument('--as-of', required=True)
            cmd.add_argument('--output', required=True)
    a = parser.parse_args(argv)
    try:
        if a.command == 'init':
            root = init(a.db, _read_json(a.request), a.expected_request, a.requester, a.reviewer, a.representative)
            print(root)
        elif a.command == 'import':
            print(import_submission(a.db, a.root, a.head, submission._read(a.archive, submission.MAX_ARCHIVE)))
        elif a.command == 'clarify':
            print(record_clarification(a.db, a.root, a.head, _read_json(a.body), submission._read(a.artifact, MAX_NOTE)))
        else:
            report = status(a.db, a.root, a.head, a.as_of)
            submission._write(a.output, canonical(report)+b'\n')
        return 0
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, RecursionError):
        print('Workbench operation refused: invalid or inaccessible input, history, or checkpoint.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
