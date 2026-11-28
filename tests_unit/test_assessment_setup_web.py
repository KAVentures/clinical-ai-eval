import io
import json
from pathlib import Path
from wsgiref.util import setup_testing_defaults

import pytest
from caeval import assessment_setup_web as setup, vendor_submission as sub, vendor_workbench as vendor
from test_prospective_research import inputs, outcomes
from test_predictive_research import spec


def put(path, obj):
    path.write_text(json.dumps(obj)); path.chmod(0o600)


@pytest.fixture
def app(tmp_path):
    staging=tmp_path/'staging'; staging.mkdir(mode=0o700)
    bs,manifest,groups=inputs()
    plan={'request-spec':dict(request_id='r',buyer='hospital',vendor='vendor',product='ai',version='v2',requirements=[dict(id='safety',mandatory=True,description='Safety report')]),
          'actors':dict(requester='operator',reviewer='reviewer',representative='representative'),
          'predictive-spec':{**spec(),'unit_ids':['u2','u3','u4']},'binary-spec':bs,'manifest':manifest,'memberships':groups}
    for k,v in plan.items(): put(staging/(k+'.json'),v)
    a=setup.Setup(staging,tmp_path/'workspace'); a.host='127.0.0.1:12345'
    return a


def call(app,path='/',method='GET',approval=None,**changes):
    env={}; setup_testing_defaults(env)
    if approval is None:
        if path=='/commit-plan' and app.preview: approval=app.preview[1]['assessment-plan']['plan_sha256']
        if path=='/commit-collection' and app.collection: approval=app.collection[1]
    raw=('csrf='+app.csrf+('' if approval is None else '&approval='+approval)).encode()
    env.update(PATH_INFO=path,REQUEST_METHOD=method,HTTP_HOST=app.host,REMOTE_ADDR='127.0.0.1',HTTP_AUTHORIZATION=app.auth,
               CONTENT_LENGTH=str(len(raw)),CONTENT_TYPE='application/x-www-form-urlencoded')
    env['wsgi.input']=io.BytesIO(raw); env.update(changes); result=[]
    body=b''.join(app(env,lambda status,headers:result.extend([status,dict(headers)])))
    return result[0],result[1],body


def post(app,path,**changes): return call(app,path,'POST',**changes)


def stage_collection(app):
    files={'collection':{'as_of':'2026-01-03','source_receipts':{r:'c'*64 for r in ('baseline','candidate','reference')}},
           'baseline-rows':[],'candidate-rows':[],'reference-rows':[], 'outcomes':outcomes(),
           'reference-counts':{'age':{'a':2,'b':0}},'target-counts':{'age':{'a':0,'b':2}}}
    for k,v in files.items(): put(app.staging/(k+'.json'),v)


def freeze(app):
    assert post(app,'/preview-plan')[0]=='200 OK'
    assert post(app,'/commit-plan')[0]=='200 OK'


def test_complete_browser_workflow(app):
    assert call(app)[0]=='200 OK'; assert not app.destination.exists()
    assert post(app,'/preview-collection')[0]=='409 Conflict'
    freeze(app)
    request=json.loads((app.destination/'request.json').read_text())
    archive=sub.build(request,request['request_sha256'],{'safety':{'status':'supplied','explanation':''}},{'safety':b'doc'})
    p=app.staging/'submission.zip'; p.write_bytes(archive); p.chmod(0o600)
    assert post(app,'/import-vendor')[0]=='200 OK'
    note=b'question'; p=app.staging/'clarification.bin'; p.write_bytes(note); p.chmod(0o600)
    put(app.staging/'clarification.json',dict(id='q',requirement='safety',actor='operator',action='open',date='2026-01-01',due='2026-01-02',evidence_sha256=vendor.sha(note),response_sha256=None))
    assert post(app,'/clarify')[0]=='200 OK'
    stage_collection(app)
    assert post(app,'/preview-collection')[0]=='200 OK'; assert not (app.destination/'config.json').exists()
    assert post(app,'/commit-collection')[0]=='200 OK'
    assert call(app,'/review')[0]=='200 OK'
    assert post(app,'/run')[0]=='200 OK'; assert post(app,'/verify')[0]=='200 OK'
    status,headers,body=post(app,'/download'); assert status=='200 OK'
    report=json.loads(body); assert report['summary']['mandatory_missing']==[] and report['summary']['overdue_clarifications']==1
    assert report['n_planned']==3 and not report['decision_grade']
    assert post(app,'/import-vendor')[0]=='409 Conflict'
    assert post(app,'/commit-collection')[0]=='409 Conflict'


def test_stale_plan_preview(app):
    assert post(app,'/preview-plan')[0]=='200 OK'
    p=app.staging/'request-spec.json'; r=json.loads(p.read_text()); r['buyer']='other'; put(p,r)
    assert post(app,'/commit-plan')[0]=='409 Conflict'; assert not app.destination.exists()
    freeze(app)


def test_wrong_preview_approval(app):
    assert post(app,'/preview-plan')[0]=='200 OK'
    assert post(app,'/commit-plan',approval='a'*64)[0]=='403 Forbidden'
    assert not app.destination.exists()


def test_collection_changed_after_preview(app):
    freeze(app); stage_collection(app)
    assert post(app,'/preview-collection')[0]=='200 OK'
    put(app.staging/'outcomes.json',[])
    assert post(app,'/commit-collection')[0]=='409 Conflict'
    assert not (app.destination/'config.json').exists()
    assert post(app,'/preview-collection')[0]=='200 OK'; assert post(app,'/commit-collection')[0]=='200 OK'


def test_leakage_refused(app):
    p=app.staging/'manifest.json'; m=json.loads(p.read_text()); m[2]['patient_key']=m[0]['patient_key']; put(p,m)
    assert post(app,'/preview-plan')[0]=='409 Conflict'; assert not app.destination.exists()


@pytest.mark.parametrize('changes,status',[({'HTTP_AUTHORIZATION':''},'401 Unauthorized'),({'HTTP_HOST':'evil.test'},'403 Forbidden'),({'REMOTE_ADDR':'192.0.2.1'},'403 Forbidden'),({'HTTP_ORIGIN':'https://evil.test'},'403 Forbidden'),({'QUERY_STRING':'path=/etc/passwd'},'400 Bad Request')])
def test_request_boundaries(app,changes,status):
    assert call(app,**changes)[0]==status; assert app.preview is None


def test_no_get_mutation_or_unbounded_body(app):
    assert call(app,'/preview-plan')[0]=='405 Method Not Allowed'
    assert post(app,'/preview-plan',CONTENT_LENGTH='999999')[0]=='403 Forbidden'
    assert post(app,'/upload')[0]=='404 Not Found'
    assert app.preview is None


def test_private_file_and_symlink(app,tmp_path):
    p=app.staging/'manifest.json'; other=tmp_path/'other.json'; p.rename(other); p.symlink_to(other)
    assert post(app,'/preview-plan')[0]=='409 Conflict'
    p.unlink(); other.rename(p)
    import os
    if os.name=='posix':
        p.chmod(0o644); assert post(app,'/preview-plan')[0]=='409 Conflict'; p.chmod(0o600)
    freeze(app)
    if os.name=='posix': assert (app.destination/'keys.json').stat().st_mode & 0o777==0o600
    with pytest.raises(ValueError): setup.Setup(app.staging,app.destination)


def test_no_partial_on_collision(app):
    freeze(app); stage_collection(app); assert post(app,'/preview-collection')[0]=='200 OK'
    existing=app.destination/'candidate.json'; existing.write_text('existing')
    assert post(app,'/commit-collection')[0]=='409 Conflict'
    assert existing.read_text()=='existing' and not (app.destination/'baseline.json').exists()
    assert not (app.destination/'config.json').exists()


def test_real_http(app):
    from threading import Thread
    from http.client import HTTPConnection
    from wsgiref.simple_server import make_server
    from caeval.assessment_workbench_web import QuietHandler
    with make_server('127.0.0.1',0,app,handler_class=QuietHandler) as server:
        app.host='127.0.0.1:'+str(server.server_port)
        worker=Thread(target=server.serve_forever,daemon=True); worker.start()
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        def request(path,approval=None):
            raw='csrf='+app.csrf+('' if approval is None else '&approval='+approval)
            conn.request('POST',path,body=raw,headers={'Authorization':app.auth,'Content-Type':'application/x-www-form-urlencoded','Origin':'http://'+app.host})
            response=conn.getresponse(); assert response.status==200; return response.read()
        try:
            request('/preview-plan'); request('/commit-plan',app.preview[1]['assessment-plan']['plan_sha256'])
            stage_collection(app); request('/preview-collection'); request('/commit-collection',app.collection[1])
            request('/run'); request('/verify'); report=json.loads(request('/download'))
            assert report['n_planned']==3 and report['summary']['mandatory_missing']==['safety']
        finally:
            conn.close(); server.shutdown(); worker.join(timeout=5)
