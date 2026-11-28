import json
from threading import Event, Thread

import pytest
from caeval import predictive_schedule as q, predictive_execution_job as job, predictive_local_execution as local
from test_predictive_execution_job import start, server


EARLY='2026-01-01T10:00:00Z'
DUE='2026-01-01T11:00:00Z'
END='2026-01-01T12:00:00Z'


def init(tmp_path,port,**opt):
    a,h=start(tmp_path,port,**opt)
    path=tmp_path/'queue.db'
    return path,a,q.init(path,a,h),h


def approve(path,a,head,jh,n=3):
    return q.schedule(path,a,head,jh,DUE,END,n,confirmed=True)['head']


def test_due_dispatch_and_native_export(tmp_path,server,monkeypatch):
    port,seen=server; p,a,h,jh=init(tmp_path,port)
    monkeypatch.setattr(q,'now',lambda:EARLY)
    with pytest.raises(ValueError): q.schedule(p,a,h,jh,DUE,END,3)
    h=approve(p,a,h,jh)
    with pytest.raises(ValueError): q.dispatch(p,a,h,confirmed=True)
    assert not seen and q.status(p,a,h)['state']=='queued'
    monkeypatch.setattr(q,'now',lambda:DUE)
    with pytest.raises(ValueError): q.dispatch(p,a,h)
    s=q.dispatch(p,a,h,confirmed=True)
    assert s['state']=='completed' and s['steps']==3 and len(seen)==3
    assert job.export(*a,s['final_job_head'])['receipt']['finish_reason']=='complete'
    with pytest.raises(ValueError): q.dispatch(p,a,h,confirmed=True)
    with pytest.raises(ValueError): q.dispatch(p,a,s['head'],confirmed=True)


def test_expiry_and_cancel(tmp_path,server,monkeypatch):
    port,seen=server; p,a,h,jh=init(tmp_path,port)
    monkeypatch.setattr(q,'now',lambda:EARLY); h=approve(p,a,h,jh)
    monkeypatch.setattr(q,'now',lambda:END)
    with pytest.raises(ValueError): q.dispatch(p,a,h,confirmed=True)
    assert not seen
    s=q.cancel(p,a,h); assert s['state']=='cancelled'
    with pytest.raises(ValueError): approve(p,a,s['head'],jh)


def test_stale_job_head_does_not_consume_ticket(tmp_path,server,monkeypatch):
    port,seen=server; p,a,h,jh=init(tmp_path,port)
    monkeypatch.setattr(q,'now',lambda:DUE); h=approve(p,a,h,jh)
    jh=job.step(*a,jh,confirmed=True)['head']
    with pytest.raises(ValueError): q.dispatch(p,a,h,confirmed=True)
    assert q.status(p,a,h)['state']=='queued' and len(seen)==1
    h=q.cancel(p,a,h)['head']; h=approve(p,a,h,jh,1)
    s=q.dispatch(p,a,h,confirmed=True)
    assert s['steps']==1 and len(seen)==2


def test_interruption_requires_separate_job_recovery(tmp_path,server,monkeypatch):
    port,seen=server; p,a,h,jh=init(tmp_path,port)
    monkeypatch.setattr(q,'now',lambda:DUE); h=approve(p,a,h,jh)
    original=local._call
    def interrupted(*args):
        original(*args); raise KeyboardInterrupt
    monkeypatch.setattr(local,'_call',interrupted)
    with pytest.raises(KeyboardInterrupt): q.dispatch(p,a,h,confirmed=True)
    claim=q.status(p,a,h,inspect=True)['head']
    assert q.status(p,a,claim)['state']=='running' and len(seen)==1
    with pytest.raises(ValueError): q.dispatch(p,a,claim,confirmed=True)
    pending=job.status(*a,jh,inspect=True)['head']
    with pytest.raises(ValueError): q.reconcile(p,a,claim,pending,confirmed_stopped=True)
    recovered=job.recover(*a,pending,confirmed_stopped=True)['head']
    with pytest.raises(ValueError): q.reconcile(p,a,claim,recovered)
    s=q.reconcile(p,a,claim,recovered,confirmed_stopped=True)
    assert s['state']=='reconciled' and s['steps'] is None
    monkeypatch.setattr(local,'_call',original)
    h=approve(p,a,s['head'],recovered,2)
    s=q.dispatch(p,a,h,confirmed=True)
    assert [r['unit_id'] for r in seen]==['a','b','c']
    assert job.export(*a,s['final_job_head'])['receipt']['results'][0]['status']=='interrupted'


def test_concurrent_dispatch_and_cancellation(tmp_path,monkeypatch):
    p,a,h,jh=init(tmp_path,12345)
    monkeypatch.setattr(q,'now',lambda:DUE); h=approve(p,a,h,jh,1)
    entered,release=Event(),Event(); result=[]
    def blocking(*args):
        entered.set(); assert release.wait(5); return 'ok',100
    monkeypatch.setattr(local,'_call',blocking)
    t=Thread(target=lambda:result.append(q.dispatch(p,a,h,confirmed=True))); t.start()
    try:
        assert entered.wait(5)
        claim=q.status(p,a,h,inspect=True)['head']
        with pytest.raises(ValueError): q.dispatch(p,a,claim,confirmed=True)
        with pytest.raises(ValueError): q.cancel(p,a,claim)
    finally:
        release.set(); t.join(5)
    assert result[0]['steps']==1


def test_expiry_between_attempts(tmp_path,monkeypatch):
    p,a,h,jh=init(tmp_path,12345)
    clock=[DUE]; monkeypatch.setattr(q,'now',lambda:clock[0]); h=approve(p,a,h,jh)
    def advance(*args):
        clock[0]=END; return 'ok',100
    monkeypatch.setattr(local,'_call',advance)
    s=q.dispatch(p,a,h,confirmed=True)
    assert s['steps']==1 and job.status(*a,s['final_job_head'])['n_recorded']==1


def test_tampering_and_invalid_schedule(tmp_path,monkeypatch):
    import sqlite3
    p,a,h,jh=init(tmp_path,12345)
    monkeypatch.setattr(q,'now',lambda:EARLY)
    for n in (0,101,True):
        with pytest.raises(ValueError): approve(p,a,h,jh,n)
    with pytest.raises(ValueError): q.schedule(p,a,h,jh,END,DUE,1,confirmed=True)
    h=approve(p,a,h,jh)
    with sqlite3.connect(p) as c: c.execute('UPDATE events SET digest=?',('a'*64,))
    with pytest.raises(ValueError): q.status(p,a,h)


def test_cli(tmp_path,server,monkeypatch,capsys):
    port,seen=server; a,jh=start(tmp_path,port,max_calls=1)
    monkeypatch.setattr(q,'now',lambda:DUE)
    def put(name,obj):
        p=tmp_path/name; p.write_text(json.dumps(obj)); return str(p)
    common=['--queue',str(tmp_path/'queue.db'),'--db',str(a[0]),'--plan',put('plan',a[1]),'--expected-plan',a[2],
            '--execution',put('execution',a[3]),'--expected-execution',a[4],'--rows',put('rows',a[5])]
    assert q.main(['init',*common,'--expected-job-head',jh])==0
    h=json.loads(capsys.readouterr().out)['head']
    assert q.main(['schedule',*common,'--expected-head',h,'--expected-job-head',jh,'--not-before',DUE,'--expires',END,'--max-steps','1','--confirm-loopback'])==0
    h=json.loads(capsys.readouterr().out)['head']
    assert q.main(['dispatch',*common,'--expected-head',h,'--confirm-loopback'])==0
    s=json.loads(capsys.readouterr().out); assert len(seen)==1 and s['state']=='completed'
