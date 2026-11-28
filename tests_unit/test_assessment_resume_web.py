import json

import pytest
from caeval import assessment_resume_web as resume, vendor_submission as sub
from test_assessment_setup_web import app, freeze, stage_collection, post, call, put


def reopen(a, report=None, head=None):
    b = resume.Resume(a.staging,a.destination,a.prepared['plan_sha256'],a.head if head is None else head,report)
    b.host = a.host
    return b


def finish(a):
    stage_collection(a)
    assert post(a,'/preview-collection')[0]=='200 OK'
    checkpoint = a.collection[1]
    assert post(a,'/commit-collection')[0]=='200 OK'
    return checkpoint


def test_resume_frozen_then_complete(app):
    freeze(app)
    r = reopen(app)
    assert r.auth != app.auth and r.csrf != app.csrf
    request = json.loads((r.destination/'request.json').read_text())
    raw = sub.build(request,request['request_sha256'],{'safety':{'status':'supplied','explanation':''}},{'safety':b'doc'})
    p = r.staging/'submission.zip'; p.write_bytes(raw); p.chmod(0o600)
    assert post(r,'/import-vendor')[0]=='200 OK'
    with pytest.raises(ValueError): reopen(app)
    checkpoint = finish(r)
    assert post(r,'/run')[0]=='200 OK' and post(r,'/verify')[0]=='200 OK'
    assert json.loads(post(r,'/download')[2])['report_sha256']==checkpoint
    b = reopen(r,checkpoint)
    assert call(b,'/review')[0]=='200 OK'
    assert post(b,'/import-vendor')[0]=='409 Conflict'
    assert post(b,'/run')[0]=='200 OK' and post(b,'/verify')[0]=='200 OK'
    assert json.loads(post(b,'/download')[2])['report_sha256']==checkpoint


def test_wrong_plan_or_head(app):
    freeze(app)
    with pytest.raises(ValueError): resume.Resume(app.staging,app.destination,'a'*64,app.head)
    with pytest.raises(ValueError): reopen(app,head='a'*64)


@pytest.mark.parametrize('name',['request','manifest','binary-plan','predictive-plan','keys'])
def test_corrupt_frozen_inputs(app,name):
    freeze(app)
    put(app.destination/(name+'.json'),{})
    with pytest.raises((ValueError,KeyError)): reopen(app)


def test_partial_collection_refused(app):
    freeze(app); put(app.destination/'baseline.json',{})
    with pytest.raises(ValueError): reopen(app)


def test_complete_requires_external_report(app):
    freeze(app); checkpoint=finish(app)
    with pytest.raises(ValueError): reopen(app)
    with pytest.raises(ValueError): reopen(app,'a'*64)
    assert reopen(app,checkpoint).review is not None


def test_recovered_config_cannot_redirect_reads(app):
    freeze(app); checkpoint=finish(app)
    p=app.destination/'config.json'; cfg=json.loads(p.read_text()); cfg['outcomes']='/untrusted/path'; put(p,cfg)
    with pytest.raises(ValueError): reopen(app,checkpoint)


def test_report_pin_survives_restart_and_changed_data(app):
    freeze(app); checkpoint=finish(app); r=reopen(app,checkpoint)
    assert post(r,'/run')[0]=='200 OK' and post(r,'/verify')[0]=='200 OK'
    put(r.destination/'outcomes.json',[])
    assert post(r,'/download')[0]=='409 Conflict'
    assert post(r,'/run')[0]=='409 Conflict'
    with pytest.raises(ValueError): reopen(app,checkpoint)


def test_symlink_and_pending_journal(app,tmp_path):
    freeze(app); p=app.destination/'manifest.json'; q=tmp_path/'other'; p.rename(q); p.symlink_to(q)
    with pytest.raises(ValueError): reopen(app)
    p.unlink(); q.rename(p)
    (app.destination/'procurement.db-journal').touch()
    with pytest.raises(ValueError): reopen(app)


def test_real_http_resume(app):
    from threading import Thread
    from http.client import HTTPConnection
    from wsgiref.simple_server import make_server
    from caeval.assessment_workbench_web import QuietHandler
    freeze(app); checkpoint=finish(app); r=reopen(app,checkpoint)
    with make_server('127.0.0.1',0,r,handler_class=QuietHandler) as server:
        r.host='127.0.0.1:'+str(server.server_port)
        worker=Thread(target=server.serve_forever,daemon=True); worker.start()
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        try:
            conn.request('GET','/review',headers={'Authorization':r.auth}); response=conn.getresponse(); assert response.status==200; response.read()
            for path in ('/run','/verify','/download'):
                conn.request('POST',path,body='csrf='+r.csrf,headers={'Authorization':r.auth,'Content-Type':'application/x-www-form-urlencoded','Origin':'http://'+r.host})
                response=conn.getresponse(); assert response.status==200; body=response.read()
            assert json.loads(body)['report_sha256']==checkpoint
        finally:
            conn.close(); server.shutdown(); worker.join(timeout=5)


def test_failed_post_commit_review_locks_mutations(app, monkeypatch):
    freeze(app); r=reopen(app); stage_collection(r)
    assert post(r,'/preview-collection')[0]=='200 OK'
    checkpoint=r.collection[1]
    def fail(_):
        raise ValueError('simulated review initialization failure')
    monkeypatch.setattr(r,'_review',fail)
    assert post(r,'/commit-collection')[0]=='409 Conflict'
    assert r.collection_committed and (r.destination/'config.json').exists()
    for path in ('/import-vendor','/clarify','/preview-collection','/commit-collection'):
        assert post(r,path)[0]=='409 Conflict'
    assert reopen(r,checkpoint).review is not None
