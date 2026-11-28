import json
from urllib.parse import urlencode

import pytest
from caeval import predictive_job_web as web, predictive_local_execution as local
from test_assessment_workspace_web import call, select, child_post, freeze, selector
from test_assessment_setup_web import app, put, stage_collection
from test_predictive_local_execution import server


@pytest.fixture
def manager(selector):
    m=web.Manager(selector.root,selector.staging_root); m.host=selector.host
    assert select(m)[0]=='303 See Other'; freeze(m)
    return m


def stage(m, port, role='candidate', calls=3):
    put(m.active.staging/(role+'-features.json'),[{'unit_id':u,'features':{'score':1}} for u in ('u2','u3','u4')])
    put(m.active.staging/(role+'-execution-options.json'),dict(role=role,port=port,feature_names=['score'],feature_protocol_sha256='a'*64,max_calls=calls,timeout_seconds=2,max_seconds=20))
    return web.material(m.active,role)[4]


def post(m, action, execution, head='', role='candidate', **changes):
    raw=urlencode(dict(csrf=m.active.csrf,action=action,execution=execution,head=head)).encode()
    return call(m,'/job-'+role,'POST',raw,**changes)


def status(m,role,head,inspect=False):
    return web.job.status(*web.material(m.active,role),head,inspect=inspect)


def test_browser_jobs_to_collection(manager,server):
    m=manager; port,seen=server; execution=stage(m,port)
    assert b'Candidate prediction job' in call(m,'/workspaces')[2]
    assert call(m,'/job-candidate')[0]=='200 OK'; assert not seen
    assert post(m,'init',execution)[0]=='200 OK'
    head=web.job.root(execution)
    assert post(m,'batch',execution,head)[0]=='200 OK'; assert len(seen)==3
    current=status(m,'candidate',head,True)['head']
    assert post(m,'batch',execution,head)[0]=='409 Conflict'; assert len(seen)==3
    assert post(m,'finish',execution,current)[0]=='200 OK'
    final=status(m,'candidate',current,True)['head']
    stage_collection(m.active)
    # Existing manual collection rows cannot be overwritten.
    assert post(m,'stage',execution,final)[0]=='409 Conflict'
    (m.active.staging/'candidate-rows.json').unlink()
    assert post(m,'stage',execution,final)[0]=='200 OK'
    pkg=json.loads((m.active.staging/'candidate-execution-package.json').read_text())
    c=m.active.staging/'collection.json'; meta=json.loads(c.read_text()); meta['source_receipts']['candidate']=pkg['receipt']['receipt_sha256']; put(c,meta)
    assert child_post(m,'/preview-collection')[0]=='200 OK'
    assert child_post(m,'/commit-collection',m.active.collection[1])[0]=='200 OK'
    for path in ('/run','/verify','/download'):
        result=child_post(m,path); assert result[0]=='200 OK'
    report=json.loads(result[2]); assert report['summary']['predictive_coverage']['candidate']['n_known']==3
    assert report['reports']['predictive']['inputs']['candidate']['source_receipt_sha256']==pkg['receipt']['receipt_sha256']
    assert post(m,'batch',execution,final)[0]=='409 Conflict'


def test_explicit_scope_and_auth(manager,server):
    m=manager; port,seen=server; ex=stage(m,port)
    assert post(m,'init','a'*64)[0]=='409 Conflict'
    assert post(m,'init',ex,HTTP_AUTHORIZATION='')[0]=='401 Unauthorized'
    assert post(m,'init',ex,HTTP_ORIGIN='https://evil.test')[0]=='403 Forbidden'
    assert post(m,'init',ex)[0]=='200 OK'
    h=web.job.root(ex); p=m.active.staging/'candidate-features.json'; rows=json.loads(p.read_text()); rows[0]['features']['score']=9; put(p,rows)
    assert post(m,'batch',ex,h)[0]=='409 Conflict' and not seen


def test_interruption_and_recovery(manager,server,monkeypatch):
    m=manager; port,seen=server; ex=stage(m,port,calls=2); assert post(m,'init',ex)[0]=='200 OK'; h=web.job.root(ex)
    original=local._call
    def fail(*args):
        original(*args); raise KeyboardInterrupt
    monkeypatch.setattr(local,'_call',fail)
    with pytest.raises(KeyboardInterrupt): post(m,'batch',ex,h)
    assert len(seen)==1
    assert post(m,'inspect',ex,h)[0]=='200 OK'
    claim=status(m,'candidate',h,True)['head']
    assert post(m,'recover',ex,claim)[0]=='200 OK'
    recovered=status(m,'candidate',claim,True)['head']
    monkeypatch.setattr(local,'_call',original)
    assert post(m,'batch',ex,recovered)[0]=='200 OK'
    final=status(m,'candidate',recovered,True)['head']
    assert post(m,'stage',ex,final)[0]=='200 OK'
    pkg=json.loads((m.active.staging/'candidate-execution-package.json').read_text())
    assert [r['status'] for r in pkg['receipt']['results']]==['interrupted','ok','not_attempted_budget']
    assert len(seen)==2


def test_switch_blocks_old_form(manager,server):
    m=manager; port,seen=server; ex=stage(m,port); token=m.active.csrf
    assert select(m,'second')[0]=='303 See Other'
    raw=urlencode(dict(csrf=token,action='init',execution=ex,head='')).encode()
    assert call(m,'/job-candidate','POST',raw)[0] in ('403 Forbidden','409 Conflict') and not seen


def test_roles_and_budgets(manager,server):
    m=manager; port,seen=server; ex=stage(m,port,'baseline',1)
    assert post(m,'init',ex,role='baseline')[0]=='200 OK'
    h=web.job.root(ex); assert post(m,'batch',ex,h,role='baseline')[0]=='200 OK'
    current=status(m,'baseline',h,True); assert current['finished']=='budget' and len(seen)==1
    assert post(m,'stage',ex,current['head'],role='baseline')[0]=='200 OK'
    assert not (m.active.staging/'candidate-rows.json').exists()


def test_real_http(manager,server):
    from threading import Thread
    from http.client import HTTPConnection
    from wsgiref.simple_server import make_server
    from caeval.assessment_workbench_web import QuietHandler
    m=manager; port,seen=server; ex=stage(m,port,calls=1)
    with make_server('127.0.0.1',0,m,handler_class=QuietHandler) as s:
        m.host=m.active.host='127.0.0.1:'+str(s.server_port)
        t=Thread(target=s.serve_forever,daemon=True); t.start(); conn=HTTPConnection('127.0.0.1',s.server_port,timeout=5)
        headers={'Authorization':m.auth,'Content-Type':'application/x-www-form-urlencoded','Origin':'http://'+m.host}
        try:
            for action,head in [('init',''),('batch',web.job.root(ex))]:
                conn.request('POST','/job-candidate',urlencode(dict(csrf=m.active.csrf,action=action,execution=ex,head=head)),headers)
                r=conn.getresponse(); assert r.status==200; r.read()
            assert len(seen)==1
        finally:
            conn.close(); s.shutdown(); t.join(timeout=5)


def test_link_both_jobs_metadata(manager,server):
    m=manager; port,seen=server; heads={}
    for role in web.ROLES:
        ex=stage(m,port,role,1); assert post(m,'init',ex,role=role)[0]=='200 OK'
        h=web.job.root(ex); assert post(m,'batch',ex,h,role=role)[0]=='200 OK'
        heads[role]=status(m,role,h,True)['head']
        assert post(m,'stage',ex,heads[role],role=role)[0]=='200 OK'
    def link(**changes):
        values=dict(csrf=m.active.csrf,baseline_head=heads['baseline'],candidate_head=heads['candidate'],as_of='2026-01-03',reference_sha256='c'*64)
        return call(m,'/job-collection','POST',urlencode({**values,**changes}).encode())
    assert call(m,'/job-collection')[0]=='200 OK'
    assert link(candidate_head='a'*64)[0]=='409 Conflict'
    p=m.active.staging/'candidate-rows.json'; old=json.loads(p.read_text()); put(p,[])
    assert link()[0]=='409 Conflict'; put(p,old)
    assert link()[0]=='200 OK'
    meta=json.loads((m.active.staging/'collection.json').read_text())
    for role in web.ROLES:
        pkg=json.loads((m.active.staging/(role+'-execution-package.json')).read_text())
        assert meta['source_receipts'][role]==pkg['receipt']['receipt_sha256']
    assert link()[0]=='409 Conflict' and len(seen)==2
