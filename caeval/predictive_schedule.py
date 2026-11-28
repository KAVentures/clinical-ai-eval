"""Durable, time-windowed dispatch of an explicitly approved local prediction batch."""
import argparse
import datetime as dt
import os
import sys

from . import predictive_execution_job as job, predictive_research as pred
from . import predictive_local_execution as local, vendor_workbench as storage

SCHEMA = 'predictive-schedule/1'
MAX_EVENTS = 600
MAX_STEPS = 100


def require(ok):
    if not ok:
        raise ValueError('schedule scope, state or checkpoint mismatch')


def timestamp(value):
    require(isinstance(value, str) and len(value) == 20 and value.endswith('Z'))
    parsed = dt.datetime.fromisoformat(value[:-1] + '+00:00')
    require(parsed.strftime('%Y-%m-%dT%H:%M:%SZ') == value)
    return parsed


def now():
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def root(execution):
    require(pred.hex64(execution))
    return pred.digest([SCHEMA, execution])


def init(path, args, expected_job):
    require(job.status(*args, expected_job)['pending'] is None)
    p = storage._path(path)
    fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with storage._connect(p, write=True) as c:
            c.execute('CREATE TABLE binding (execution TEXT NOT NULL)')
            c.execute('CREATE TABLE events (seq INTEGER PRIMARY KEY, payload BLOB NOT NULL, digest TEXT NOT NULL)')
            c.execute('INSERT INTO binding VALUES (?)', (args[4],))
    except BaseException:
        p.unlink()
        raise
    return root(args[4])


def _apply(s, event):
    kind = event['kind']
    if kind == 'schedule':
        pred.fields(event, 'kind job_head not_before expires max_steps')
        require(s['state'] in ('idle','completed','cancelled','reconciled'))
        require(pred.hex64(event['job_head']) and type(event['max_steps']) is int and 1 <= event['max_steps'] <= MAX_STEPS)
        require(timestamp(event['not_before']) < timestamp(event['expires']))
        s.update(state='queued', ticket=event, final_job_head=None, steps=None)
    elif kind == 'claim':
        pred.fields(event, 'kind at')
        require(s['state']=='queued')
        require(timestamp(s['ticket']['not_before']) <= timestamp(event['at']) < timestamp(s['ticket']['expires']))
        s['state']='running'
    elif kind == 'cancel':
        pred.fields(event, 'kind')
        require(s['state']=='queued')
        s['state']='cancelled'
    elif kind in ('complete','reconcile'):
        pred.fields(event, 'kind job_head steps')
        require(s['state']=='running' and pred.hex64(event['job_head']))
        if kind=='complete':
            require(type(event['steps']) is int and 0 <= event['steps'] <= s['ticket']['max_steps'])
        else:
            require(event['steps'] is None)
        s.update(state='completed' if kind=='complete' else 'reconciled', final_job_head=event['job_head'], steps=event['steps'])
    else:
        raise ValueError('unknown schedule event')


def _load(c, execution, expected, *, inspect=False):
    require(pred.hex64(expected) and c.execute('SELECT execution FROM binding LIMIT 2').fetchall()==[(execution,)])
    s = {'state':'idle','ticket':None,'final_job_head':None,'steps':None,'seq':0,'head':root(execution)}
    found = expected==s['head']
    count, largest = c.execute('SELECT count(*), coalesce(max(length(payload)),0) FROM events').fetchone()
    require(count <= MAX_EVENTS and largest <= 2048)
    for i,(seq,raw,digest) in enumerate(c.execute('SELECT seq,payload,digest FROM events ORDER BY seq'),1):
        event=storage.parse(raw)
        calculated=pred.digest([SCHEMA,s['head'],i,event])
        require(seq==i and digest==calculated)
        _apply(s,event)
        s.update(seq=i,head=calculated)
        found=found or expected==calculated
    require(found if inspect else s['head']==expected)
    return s


def _append(c,s,event):
    require(s['seq']<MAX_EVENTS)
    _apply(s,event)
    seq=s['seq']+1
    head=pred.digest([SCHEMA,s['head'],seq,event])
    c.execute('INSERT INTO events VALUES (?,?,?)',(seq,pred.canonical(event),head))
    s.update(seq=seq,head=head)
    return s


def status(path, args, expected, *, inspect=False):
    local._check(*args[1:])
    with storage._connect(path) as c:
        return _load(c,args[4],expected,inspect=inspect)


def schedule(path,args,expected,expected_job,not_before,expires,max_steps,*,confirmed=False):
    require(confirmed is True)
    require(timestamp(now()) < timestamp(expires))
    state=job.status(*args,expected_job)
    require(state['pending'] is None and state['finished'] is None)
    with storage._connect(path,write=True) as c:
        s=_load(c,args[4],expected)
        # Reserve enough history capacity to claim and complete this ticket.
        require(s['seq']<=MAX_EVENTS-3)
        return _append(c,s,{'kind':'schedule','job_head':expected_job,'not_before':not_before,'expires':expires,'max_steps':max_steps})


def cancel(path,args,expected):
    local._check(*args[1:])
    with storage._connect(path,write=True) as c:
        return _append(c,_load(c,args[4],expected),{'kind':'cancel'})


def dispatch(path,args,expected,*,confirmed=False):
    """One claimed ticket, no replay after crash; no network before due time."""
    require(confirmed is True)
    local._check(*args[1:])
    with storage._connect(path,write=True) as c:
        s=_load(c,args[4],expected)
        require(s['state']=='queued')
        ticket=s['ticket']
        at=now()
        require(timestamp(ticket['not_before'])<=timestamp(at)<timestamp(ticket['expires']))
        # Check job before consuming the ticket. Job.step rechecks under its own lock.
        state=job.status(*args,ticket['job_head'])
        require(state['pending'] is None and state['finished'] is None)
        claim=_append(c,s,{'kind':'claim','at':at})['head']
    head=ticket['job_head']; steps=0
    for _ in range(ticket['max_steps']):
        if timestamp(now())>=timestamp(ticket['expires']):
            break
        state=job.step(*args,head,confirmed=True)
        head=state['head']; steps+=1
        if state['finished'] is not None:
            break
        opt=args[3]['options']
        if state['n_recorded']==state['n_planned'] or state['n_recorded']>=opt['max_calls'] or state['elapsed_ms']>=1000*opt['max_seconds']:
            head=job.finish(*args,head)['head']; break
    with storage._connect(path,write=True) as c:
        return _append(c,_load(c,args[4],claim),{'kind':'complete','job_head':head,'steps':steps})


def reconcile(path,args,expected,expected_job,*,confirmed_stopped=False):
    """Close a claimed ticket after separate job recovery; never dispatch again."""
    require(confirmed_stopped is True)
    with storage._connect(path,write=True) as c:
        s=_load(c,args[4],expected)
        require(s['state']=='running')
        state=job.status(*args,s['ticket']['job_head'],inspect=True)
        require(state['head']==expected_job and state['pending'] is None)
        return _append(c,s,{'kind':'reconcile','job_head':expected_job,'steps':None})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('init','status','inspect','schedule','cancel','dispatch','reconcile'):
        cmd=sub.add_parser(name)
        for k in ('queue','db','plan','expected-plan','execution','expected-execution','rows'):
            cmd.add_argument('--'+k,required=True)
        if name!='init': cmd.add_argument('--expected-head',required=True)
        if name in ('init','schedule','reconcile'): cmd.add_argument('--expected-job-head',required=True)
        if name=='schedule':
            cmd.add_argument('--not-before',required=True); cmd.add_argument('--expires',required=True)
            cmd.add_argument('--max-steps',required=True,type=int)
        if name in ('schedule','dispatch'): cmd.add_argument('--confirm-loopback',action='store_true')
        if name=='reconcile': cmd.add_argument('--confirm-worker-stopped',action='store_true')
    a=parser.parse_args(argv)
    try:
        args=(a.db,pred._read(a.plan),a.expected_plan,pred._read(a.execution),a.expected_execution,pred._read(a.rows))
        if a.command=='init': result={'head':init(a.queue,args,a.expected_job_head)}
        else:
            q=(a.queue,args,a.expected_head)
            if a.command in ('status','inspect'): result=status(*q,inspect=a.command=='inspect')
            elif a.command=='schedule': result=schedule(*q,a.expected_job_head,a.not_before,a.expires,a.max_steps,confirmed=a.confirm_loopback)
            elif a.command=='cancel': result=cancel(*q)
            elif a.command=='dispatch': result=dispatch(*q,confirmed=a.confirm_loopback)
            else: result=reconcile(*q,a.expected_job_head,confirmed_stopped=a.confirm_worker_stopped)
        print(pred.canonical(result).decode('ascii')); return 0
    except (OSError,ValueError,TypeError,KeyError,RecursionError,storage.sqlite3.Error):
        print('Schedule operation refused. Reconcile retained schedule and job checkpoints.',file=sys.stderr); return 2


if __name__=='__main__':
    sys.exit(main())
