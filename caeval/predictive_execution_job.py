"""Durable, checkpoint-pinned local predictive execution; never retries a claimed unit."""
import argparse
import os
import time
import sys

from . import predictive_local_execution as local, predictive_research as pred
from . import vendor_workbench as storage

SCHEMA = 'predictive-execution-job/1'
RESULTS = ('ok', 'transport_error', 'http_error', 'invalid_response', 'interrupted')


def require(ok):
    if not ok:
        raise ValueError('job state or checkpoint mismatch')


def root(execution_sha):
    require(pred.hex64(execution_sha))
    return pred.digest([SCHEMA, execution_sha])


def init(path, plan, plan_sha, execution, execution_sha, rows):
    local._check(plan, plan_sha, execution, execution_sha, rows)
    p = storage._path(path)
    fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with storage._connect(p, write=True) as c:
            c.execute('CREATE TABLE binding (digest TEXT NOT NULL)')
            c.execute('CREATE TABLE events (seq INTEGER PRIMARY KEY, payload BLOB NOT NULL, digest TEXT NOT NULL)')
            c.execute('INSERT INTO binding VALUES (?)', (execution_sha,))
    except BaseException:
        p.unlink()
        raise
    return root(execution_sha)


def _eligible(s, execution):
    return (len(s['results']) < execution['options']['max_calls'] and
            s['elapsed_ms'] < 1000 * execution['options']['max_seconds'])


def _apply(s, event, execution, inventory):
    require(s['finished'] is None)
    kind = event['kind']
    i = len(s['results'])
    if kind == 'claim':
        pred.fields(event, 'kind unit_id')
        require(s['pending'] is None and _eligible(s, execution) and i < len(inventory) and event['unit_id'] == inventory[i]['unit_id'])
        s['pending'] = event['unit_id']
    elif kind == 'result':
        pred.fields(event, 'kind unit_id status probability_bp elapsed_ms')
        require(s['pending'] is not None and event['unit_id'] == s['pending'] and event['status'] in RESULTS)
        require(type(event['elapsed_ms']) is int and 0 <= event['elapsed_ms'] <= 2**53-1)
        require(local.risk._bp(event['probability_bp']) if event['status'] == 'ok' else event['probability_bp'] is None)
        if event['status'] == 'interrupted':
            require(event['elapsed_ms'] == execution['options']['timeout_seconds'] * 1000)
        s['results'].append({k: event[k] for k in ('unit_id', 'status', 'probability_bp')})
        s['elapsed_ms'] += event['elapsed_ms']
        s['pending'] = None
    elif kind == 'finish':
        pred.fields(event, 'kind reason')
        require(s['pending'] is None)
        reason = event['reason']
        natural = 'complete' if i == len(inventory) else 'budget' if not _eligible(s, execution) else 'operator_stop'
        require(reason == natural)
        s['finished'] = reason
    else:
        raise ValueError('invalid job event')


def _load(c, execution, execution_sha, inventory, expected, *, ancestor=False):
    require(pred.hex64(expected) and c.execute('SELECT digest FROM binding LIMIT 2').fetchall() == [(execution_sha,)])
    head = root(execution_sha)
    s = {'results': [], 'pending': None, 'finished': None, 'elapsed_ms': 0, 'seq': 0, 'head': head}
    found = expected == head
    events = c.execute('SELECT seq,payload,digest FROM events ORDER BY seq LIMIT ?', (2*local.MAX_CALLS+2,)).fetchall()
    require(len(events) <= 2*execution['options']['max_calls']+1)
    for i, (seq, raw, recorded) in enumerate(events, 1):
        event = storage.parse(raw)
        head = pred.digest([SCHEMA, head, i, event])
        require(seq == i and recorded == head)
        _apply(s, event, execution, inventory)
        found = found or expected == head
        s.update(seq=i, head=head)
    require(found if ancestor else head == expected)
    return s


def _append(c, s, event, execution, inventory):
    _apply(s, event, execution, inventory)
    seq = s['seq'] + 1
    head = pred.digest([SCHEMA, s['head'], seq, event])
    c.execute('INSERT INTO events VALUES (?,?,?)', (seq, pred.canonical(event), head))
    s.update(seq=seq, head=head)
    return head


def _summary(s, inventory, expected):
    return {'head': s['head'], 'matches_checkpoint': s['head'] == expected, 'pending': s['pending'],
            'finished': s['finished'], 'n_planned': len(inventory), 'n_recorded': len(s['results']),
            'counts': {k: sum(r['status']==k for r in s['results']) for k in RESULTS},
            'elapsed_ms': s['elapsed_ms'], 'decision_grade': False}


def status(path, plan, plan_sha, execution, execution_sha, rows, expected, *, inspect=False):
    inventory = local._check(plan, plan_sha, execution, execution_sha, rows)
    with storage._connect(path) as c:
        return _summary(_load(c, execution, execution_sha, inventory, expected, ancestor=inspect), inventory, expected)


def step(path, plan, plan_sha, execution, execution_sha, rows, expected, *, confirmed=False):
    require(confirmed is True)
    inventory = local._check(plan, plan_sha, execution, execution_sha, rows)
    with storage._connect(path, write=True) as c:
        s = _load(c, execution, execution_sha, inventory, expected)
        require(s['pending'] is None and s['finished'] is None)
        i = len(s['results'])
        if i == len(inventory) or not _eligible(s, execution):
            _append(c, s, {'kind':'finish','reason':'complete' if i == len(inventory) else 'budget'}, execution, inventory)
            return _summary(s, inventory, s['head'])
        row = inventory[i]
        claimed = _append(c, s, {'kind':'claim','unit_id':row['unit_id']}, execution, inventory)
    # Claim is durably committed before any network call. An interruption leaves
    # an ambiguous attempt, not a retryable task. No transaction spans the call.
    start = time.monotonic()
    result, value = local._call(execution, row)
    elapsed = max(0, int((time.monotonic()-start)*1000 + .999999))
    with storage._connect(path, write=True) as c:
        s = _load(c, execution, execution_sha, inventory, claimed)
        _append(c, s, {'kind':'result','unit_id':row['unit_id'],'status':result,'probability_bp':value,'elapsed_ms':elapsed}, execution, inventory)
        return _summary(s, inventory, s['head'])


def recover(path, plan, plan_sha, execution, execution_sha, rows, expected, *, confirmed_stopped=False):
    require(confirmed_stopped is True)
    inventory = local._check(plan, plan_sha, execution, execution_sha, rows)
    with storage._connect(path, write=True) as c:
        s = _load(c, execution, execution_sha, inventory, expected)
        require(s['pending'] is not None)
        _append(c, s, {'kind':'result','unit_id':s['pending'],'status':'interrupted','probability_bp':None,
                       'elapsed_ms':execution['options']['timeout_seconds']*1000}, execution, inventory)
        return _summary(s, inventory, s['head'])


def finish(path, plan, plan_sha, execution, execution_sha, rows, expected):
    inventory = local._check(plan, plan_sha, execution, execution_sha, rows)
    with storage._connect(path, write=True) as c:
        s = _load(c, execution, execution_sha, inventory, expected)
        require(s['pending'] is None and s['finished'] is None)
        reason = 'complete' if len(s['results']) == len(inventory) else 'budget' if not _eligible(s, execution) else 'operator_stop'
        _append(c, s, {'kind':'finish','reason':reason}, execution, inventory)
        return _summary(s, inventory, s['head'])


def export(path, plan, plan_sha, execution, execution_sha, rows, expected):
    inventory = local._check(plan, plan_sha, execution, execution_sha, rows)
    with storage._connect(path) as c:
        s = _load(c, execution, execution_sha, inventory, expected)
        require(s['finished'] is not None and s['pending'] is None)
        tail = 'not_attempted_budget' if s['finished']=='budget' else 'not_attempted_operator_stop'
        results = s['results'] + [{'unit_id':r['unit_id'],'status':tail,'probability_bp':None} for r in inventory[len(s['results']):]]
        body = {'schema':SCHEMA,'execution_sha256':execution_sha,'head':expected,'finish_reason':s['finished'],
                'elapsed_ms':s['elapsed_ms'],'results':results,'decision_grade':False,'publication_safe':False,
                'independent_model_identity_verified':False}
        receipt = {**body,'receipt_sha256':pred.digest(body)}
        bundle = pred.seal(plan,plan_sha,execution['options']['role'],receipt['receipt_sha256'],
                           [{k:r[k] for k in ('unit_id','probability_bp')} for r in results])
        return {'receipt':receipt,'bundle':bundle}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    for name in ('init','status','inspect','step','recover','finish','export'):
        cmd = sub.add_parser(name)
        for k in ('db','plan','expected-plan','execution','expected-execution','rows'):
            cmd.add_argument('--'+k,required=True)
        if name != 'init': cmd.add_argument('--expected-head',required=True)
        if name == 'step': cmd.add_argument('--confirm-loopback',action='store_true')
        if name == 'recover': cmd.add_argument('--confirm-worker-stopped',action='store_true')
        if name == 'export': cmd.add_argument('--output',required=True)
    a = parser.parse_args(argv)
    try:
        args=(a.db,pred._read(a.plan),a.expected_plan,pred._read(a.execution),a.expected_execution,pred._read(a.rows))
        if a.command=='init': result={'head':init(*args)}
        else:
            args=(*args,a.expected_head)
            if a.command in ('status','inspect'): result=status(*args,inspect=a.command=='inspect')
            elif a.command=='step': result=step(*args,confirmed=a.confirm_loopback)
            elif a.command=='recover': result=recover(*args,confirmed_stopped=a.confirm_worker_stopped)
            elif a.command=='finish': result=finish(*args)
            else:
                package=export(*args); pred._write(a.output,package)
                result={'receipt_sha256':package['receipt']['receipt_sha256'],'bundle_sha256':package['bundle']['bundle_sha256']}
        print(pred.canonical(result).decode('ascii')); return 0
    except (OSError,ValueError,TypeError,KeyError,RecursionError,storage.sqlite3.Error):
        print('Job operation refused. Reconcile retained checkpoints and local files.',file=sys.stderr); return 2


if __name__=='__main__':
    sys.exit(main())
