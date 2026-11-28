import json
import sqlite3

import pytest
from caeval import predictive_execution_job as job, predictive_local_execution as local, predictive_research as pred
from test_predictive_local_execution import server, args
from test_predictive_research import inputs, digests


def start(tmp_path, port, **options):
    a = (tmp_path/'job.db', *args(port, **options))
    h = job.init(*a)
    return a, h


def test_real_http_resume_and_export(tmp_path, server):
    port, seen = server; a, h = start(tmp_path, port)
    for i in range(3):
        s = job.step(*a, h, confirmed=True); h = s['head']
        assert s['n_recorded'] == i+1
        assert job.status(*a, h) == s
    h = job.finish(*a, h)['head']
    package = job.export(*a, h)
    assert [r['status'] for r in package['receipt']['results']] == ['ok','invalid_response','http_error']
    assert len(seen) == 3
    b = inputs(a[1]); b['candidate'] = package['bundle']
    assert pred.analyze(a[1],a[2],b,digests(b))['coverage']['candidate']['n_unknown'] == 2
    assert job.export(*a,h) == package and len(seen) == 3
    with pytest.raises(ValueError): job.step(*a,h,confirmed=True)


def test_interrupt_no_replay(tmp_path, server, monkeypatch):
    port, seen = server; a,h = start(tmp_path,port)
    original=local._call
    def interrupted(*x):
        original(*x)
        raise KeyboardInterrupt
    monkeypatch.setattr(local,'_call',interrupted)
    with pytest.raises(KeyboardInterrupt): job.step(*a,h,confirmed=True)
    with pytest.raises(ValueError): job.status(*a,h)
    s=job.status(*a,h,inspect=True); claim=s['head']; assert s['pending']=='a' and not s['matches_checkpoint']
    with pytest.raises(ValueError): job.step(*a,claim,confirmed=True)
    with pytest.raises(ValueError): job.recover(*a,claim)
    h=job.recover(*a,claim,confirmed_stopped=True)['head']
    monkeypatch.setattr(local,'_call',original)
    h=job.step(*a,h,confirmed=True)['head']; h=job.finish(*a,h)['head']
    r=job.export(*a,h)['receipt']; assert [v['unit_id'] for v in seen]==['a','b']
    assert [v['status'] for v in r['results']]==['interrupted','invalid_response','not_attempted_operator_stop']
    assert r['results'][0]['probability_bp'] is None


def test_budget_and_stale_head(tmp_path,server):
    port,seen=server; a,h=start(tmp_path,port,max_calls=1)
    with pytest.raises(ValueError): job.step(*a,h)
    s=job.step(*a,h,confirmed=True)
    with pytest.raises(ValueError): job.step(*a,h,confirmed=True)
    h=job.step(*a,s['head'],confirmed=True)['head']
    r=job.export(*a,h)['receipt']; assert len(seen)==1 and r['finish_reason']=='budget'
    assert [v['status'] for v in r['results']]==['ok','not_attempted_budget','not_attempted_budget']


def test_elapsed_budget(tmp_path, monkeypatch):
    a,h=start(tmp_path,12345,max_seconds=1)
    clock=iter([10,12]); monkeypatch.setattr(job.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(local,'_call',lambda *a:('ok',100))
    s=job.step(*a,h,confirmed=True); assert s['elapsed_ms']==2000
    h=job.step(*a,s['head'],confirmed=True)['head']
    assert job.export(*a,h)['receipt']['finish_reason']=='budget'


def test_tampering_and_input_binding(tmp_path,server):
    port,seen=server; a,h=start(tmp_path,port)
    with pytest.raises(ValueError): job.export(*a,h)
    with pytest.raises(FileExistsError): job.init(*a)
    a[-1][0]['features']['score']=5
    with pytest.raises(ValueError): job.step(*a,h,confirmed=True)
    a[-1][0]['features']['score']=None
    h=job.step(*a,h,confirmed=True)['head']
    with sqlite3.connect(a[0]) as c: c.execute("UPDATE events SET digest=? WHERE seq=1",('a'*64,))
    with pytest.raises(ValueError): job.status(*a,h)
    assert len(seen)==1


def test_cli(tmp_path,server,capsys):
    port,seen=server; a=(tmp_path/'job.db',*args(port,max_calls=1))
    def put(name,obj):
        p=tmp_path/name; p.write_text(json.dumps(obj)); return str(p)
    common=['--db',str(a[0]),'--plan',put('plan',a[1]),'--expected-plan',a[2],
            '--execution',put('execution',a[3]),'--expected-execution',a[4],'--rows',put('rows',a[5])]
    assert job.main(['init',*common])==0; h=json.loads(capsys.readouterr().out)['head']
    assert job.main(['step',*common,'--expected-head',h])==2 and not seen
    assert job.main(['step',*common,'--expected-head',h,'--confirm-loopback'])==0; h=json.loads(capsys.readouterr().out)['head']
    assert job.main(['finish',*common,'--expected-head',h])==0; h=json.loads(capsys.readouterr().out)['head']
    out=tmp_path/'out.json'; argv=['export',*common,'--expected-head',h,'--output',str(out)]
    assert job.main(argv)==0 and job.main(argv)==2
    assert json.loads(out.read_text())['receipt']['finish_reason']=='budget'


def test_concurrent_claim_and_late_result(tmp_path,monkeypatch):
    from threading import Event, Thread
    a,h=start(tmp_path,12345)
    started, release=Event(),Event(); errors=[]
    def blocking(*args):
        started.set(); assert release.wait(5); return 'ok',100
    monkeypatch.setattr(local,'_call',blocking)
    def worker():
        try: job.step(*a,h,confirmed=True)
        except Exception as e: errors.append(e)
    t=Thread(target=worker); t.start()
    try:
        assert started.wait(5)
        claim=job.status(*a,h,inspect=True)['head']
        with pytest.raises(ValueError): job.step(*a,claim,confirmed=True)
        with pytest.raises(ValueError): job.step(*a,h,confirmed=True)
        # A mistaken recovery assertion cannot overwrite the recovered event.
        recovered=job.recover(*a,claim,confirmed_stopped=True)['head']
    finally:
        release.set(); t.join(5)
    assert len(errors)==1 and isinstance(errors[0],ValueError)
    assert job.status(*a,recovered)['counts']['interrupted']==1
