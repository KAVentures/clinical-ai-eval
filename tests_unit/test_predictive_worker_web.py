import json
from threading import Event, Thread
from urllib.parse import urlencode

import pytest
from caeval import predictive_worker_web as w, predictive_local_execution as local
from test_predictive_job_web import stage, post, status, server
from test_assessment_workspace_web import call, select, freeze, selector
from test_assessment_setup_web import app, put


@pytest.fixture
def manager(selector):
    m=w.Manager(selector.root,selector.staging_root); m.host=selector.host
    assert select(m)[0]=='303 See Other'; freeze(m)
    yield m
    m.worker.shutdown()


def start(m, ex, head, limit=3, role='candidate', **changes):
    raw=urlencode(dict(csrf=m.active.csrf,action='start',role=role,execution=ex,head=head,limit=limit)).encode()
    return call(m,'/worker','POST',raw,**changes)


def pause(m, run_id):
    raw=urlencode(dict(csrf=m.active.csrf,action='pause',run_id=run_id)).encode()
    return call(m,'/worker','POST',raw)


def init(m, port, calls=3):
    ex=stage(m,port,calls=calls)
    assert post(m,'init',ex)[0]=='200 OK'
    return ex,w.job.root(ex)


def done(m):
    m.worker.thread.join(5)
    assert not m.worker.thread.is_alive()
    return m.worker.snapshot()


def test_real_model_worker_finish_and_stage(manager,server):
    m=manager; port,seen=server; ex,h=init(m,port)
    assert b'Background prediction worker' in call(m,'/workspaces')[2]
    assert start(m,ex,h)[0]=='303 See Other'
    s=done(m); assert s['state']=='finished' and s['progress']['finished']=='complete'
    assert len(seen)==3 and s['steps']==3
    assert post(m,'stage',ex,s['progress']['head'])[0]=='200 OK'
    pkg=json.loads((m.active.staging/'candidate-execution-package.json').read_text())
    assert pkg['receipt']['head']==s['progress']['head']
    assert start(m,ex,h)[0]=='409 Conflict'


def test_limit_then_resume(manager,server):
    m=manager; port,seen=server; ex,h=init(m,port)
    assert start(m,ex,h,1)[0]=='303 See Other'
    s=done(m); assert s['state']=='limit_reached' and len(seen)==1
    assert start(m,ex,h)[0]=='409 Conflict'
    assert start(m,ex,s['progress']['head'],2)[0]=='303 See Other'
    assert done(m)['state']=='finished' and len(seen)==3


def test_pause_and_nonblocking_real_http(manager,monkeypatch):
    from http.client import HTTPConnection
    from wsgiref.simple_server import make_server
    from caeval.assessment_workbench_web import QuietHandler
    m=manager; ex,h=init(m,12345); entered,release=Event(),Event(); calls=[]
    def blocking(*args):
        calls.append(args[1]['unit_id']); entered.set(); assert release.wait(5); return 'ok',100
    monkeypatch.setattr(local,'_call',blocking)
    with make_server('127.0.0.1',0,m,handler_class=QuietHandler) as server:
        m.host=m.active.host='127.0.0.1:'+str(server.server_port)
        t=Thread(target=server.serve_forever,daemon=True); t.start(); conn=HTTPConnection('127.0.0.1',server.server_port,timeout=2)
        headers={'Authorization':m.auth,'Content-Type':'application/x-www-form-urlencoded','Origin':'http://'+m.host}
        def request(path,fields=None):
            conn.request('GET' if fields is None else 'POST',path,None if fields is None else urlencode(fields),headers)
            r=conn.getresponse(); return r.status,r.read()
        try:
            assert request('/worker',dict(csrf=m.active.csrf,action='start',role='candidate',execution=ex,head=h,limit=3))[0]==303
            assert entered.wait(2)
            assert b'running' in request('/worker')[1]
            assert request('/preview-collection',dict(csrf=m.active.csrf))[0]==409
            assert select(m,'second')[0]=='409 Conflict'
            s=m.worker.snapshot(); assert s['progress']['n_recorded']==0
            assert request('/worker',dict(csrf=m.active.csrf,action='pause',run_id='wrong'))[0]==409
            assert request('/worker',dict(csrf=m.active.csrf,action='pause',run_id=s['run_id']))[0]==303
            assert b'pausing' in request('/worker')[1]
            release.set(); s=done(m)
            assert s['state']=='paused' and len(calls)==1 and s['progress']['n_recorded']==1
            assert start(m,ex,s['progress']['head'],2)[0]=='303 See Other'
            assert done(m)['state']=='finished' and len(calls)==3
        finally:
            release.set(); conn.close(); server.shutdown(); t.join(5)


def test_interruption_requires_reconciliation(manager,monkeypatch):
    m=manager; ex,h=init(m,12345)
    def fail(*args): raise KeyboardInterrupt
    monkeypatch.setattr(local,'_call',fail)
    assert start(m,ex,h)[0]=='303 See Other'
    s=done(m); assert s['state']=='error' and s['progress']['head']==h
    assert start(m,ex,h)[0]=='409 Conflict'
    claim=status(m,'candidate',h,True)['head']
    assert post(m,'recover',ex,claim)[0]=='200 OK'
    assert status(m,'candidate',claim,True)['counts']['interrupted']==1


def test_scope_changes_between_calls(manager,monkeypatch):
    m=manager; ex,h=init(m,12345); calls=[]
    def mutate(*args):
        calls.append(args[1]['unit_id']); p=m.active.staging/'candidate-features.json'
        rows=json.loads(p.read_text()); rows[0]['features']['score']=5; put(p,rows)
        return 'ok',100
    monkeypatch.setattr(local,'_call',mutate)
    assert start(m,ex,h)[0]=='303 See Other'
    s=done(m); assert s['state']=='error' and s['progress']['n_recorded']==1 and len(calls)==1


def test_auth_limits_and_stale_tabs(manager):
    m=manager; ex,h=init(m,12345)
    for limit in (0,101,'abc'):
        assert start(m,ex,h,limit)[0]=='409 Conflict'
    assert start(m,ex,h,HTTP_AUTHORIZATION='')[0]=='401 Unauthorized'
    assert start(m,ex,h,HTTP_ORIGIN='https://evil.test')[0]=='403 Forbidden'
    assert start(m,'a'*64,h)[0]=='409 Conflict'
    old=m.active.csrf; assert select(m,'second')[0]=='303 See Other'
    raw=urlencode(dict(csrf=old,action='start',role='candidate',execution=ex,head=h,limit=3)).encode()
    assert call(m,'/worker','POST',raw)[0] in ('403 Forbidden','409 Conflict')
    assert m.worker.snapshot() is None
