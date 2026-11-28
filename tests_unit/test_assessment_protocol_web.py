import json
from urllib.parse import urlencode

import pytest
from caeval import assessment_protocol_web as form
from test_assessment_workspace_web import call, select, child_post, freeze, selector
from test_assessment_setup_web import app
from test_prospective_research import inputs


def fields():
    return dict(request_id='r',buyer='hospital',vendor='vendor',product='ai',candidate_version='v2',
        requester='operator',reviewer='reviewer',representative='representative',study='study',baseline_version='v1',
        eligibility_sha256='a'*64,reference_protocol_sha256='b'*64,prediction_endpoint='event',prediction_horizon='day30',
        action='refer',bin_edges_bp='0,5000,10000',thresholds_bp='2000,5000',binary_endpoint='harm',binary_horizon='day30',
        reference_protocol='r1',holdout_design='temporal_site',subgroup_levels='g1,g2',minimum_group='1',coding_version='dictionary',
        requirements='safety | mandatory | Safety report\noptional | optional | Other report',shift_features='age | 2500 | a,b',
        iid='no',unacceptable_rate='',anticipated_rate='',alpha='')


@pytest.fixture
def manager(selector):
    m=form.Manager(selector.root,selector.staging_root); m.host=selector.host
    for k in form.WRITTEN:
        (m.staging_root/'first'/(k+'.json')).unlink()
    assert select(m)[0]=='303 See Other'
    return m


def submit(m, f=None, **changes):
    raw=urlencode({'csrf':m.active.csrf,**(fields() if f is None else f)}).encode()
    return call(m,'/protocol','POST',raw,**changes)


def test_form_freeze(manager):
    m=manager
    assert b'Configure prospective protocol in browser' in call(m,'/workspaces')[2]
    assert b'Eligibility protocol' in call(m,'/protocol')[2]
    assert submit(m)[0]=='303 See Other'
    assert not m.active.destination.exists()
    p=json.loads((m.active.staging/'predictive-spec.json').read_text())
    assert p['unit_ids']==['u2','u3','u4']
    assert submit(m)[0]=='409 Conflict'
    freeze(m)
    assert call(m,'/protocol')[0]=='409 Conflict'
    assert submit(m)[0]=='409 Conflict'


@pytest.mark.parametrize('change',[{'thresholds_bp':'0'}, {'requirements':'a | maybe | Desc'}, {'shift_features':'age | 10 | a,a'}, {'iid':'no','alpha':'0.05'}, {'reviewer':'operator'}, {'candidate_version':''}])
def test_invalid_form_no_partial(manager,change):
    assert submit(manager,{**fields(),**change})[0]=='409 Conflict'
    assert not any((manager.active.staging/(k+'.json')).exists() for k in form.WRITTEN)


def test_iid_optional():
    _,m,g=inputs()
    spec=form.build({**fields(),'iid':'yes','unacceptable_rate':'0.5','anticipated_rate':'0.1','alpha':'0.05'},m,g)
    assert spec['binary-spec']['binary_design']['iid_assumed'] is True
    assert form.build(fields(),m,g)['binary-spec']['binary_design'] is None


def test_stale_tab(manager):
    m=manager; token=m.active.csrf
    assert select(m,'second')[0]=='303 See Other'
    raw=urlencode({'csrf':token,**fields()}).encode()
    assert call(m,'/protocol','POST',raw)[0]=='403 Forbidden'


def test_auth_and_duplicates(manager):
    m=manager
    assert submit(m,HTTP_AUTHORIZATION='')[0]=='401 Unauthorized'
    assert submit(m,HTTP_ORIGIN='https://evil.test')[0]=='403 Forbidden'
    assert submit(m,CONTENT_LENGTH=str(form.MAX_BODY+1))[0]=='403 Forbidden'
    raw=urlencode({'csrf':m.active.csrf,**fields()}).encode()+b'&buyer=other'
    assert call(m,'/protocol','POST',raw)[0]=='403 Forbidden'
    assert not any((m.active.staging/(k+'.json')).exists() for k in form.WRITTEN)


def test_existing_file_preserved(manager):
    m=manager; p=m.active.staging/'actors.json'; p.write_text('existing')
    assert submit(m)[0]=='409 Conflict' and p.read_text()=='existing'
    assert not (m.active.staging/'request-spec.json').exists()


def test_real_http(manager):
    from threading import Thread
    from http.client import HTTPConnection
    from wsgiref.simple_server import make_server
    from caeval.assessment_workbench_web import QuietHandler
    m=manager
    with make_server('127.0.0.1',0,m,handler_class=QuietHandler) as server:
        m.host=m.active.host='127.0.0.1:'+str(server.server_port)
        worker=Thread(target=server.serve_forever,daemon=True); worker.start()
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        headers={'Authorization':m.auth,'Content-Type':'application/x-www-form-urlencoded','Origin':'http://'+m.host}
        try:
            conn.request('GET','/protocol',headers=headers); response=conn.getresponse(); assert response.status==200; response.read()
            conn.request('POST','/protocol',urlencode({'csrf':m.active.csrf,**fields()}),headers); response=conn.getresponse(); assert response.status==303; response.read()
            conn.request('POST','/preview-plan','csrf='+m.active.csrf,headers); response=conn.getresponse(); assert response.status==200; response.read()
            approval=m.active.preview[1]['assessment-plan']['plan_sha256']
            conn.request('POST','/commit-plan',urlencode({'csrf':m.active.csrf,'approval':approval}),headers); response=conn.getresponse(); assert response.status==200; response.read()
        finally:
            conn.close(); server.shutdown(); worker.join(timeout=5)
