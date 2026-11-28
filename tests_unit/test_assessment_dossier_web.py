import json
from urllib.parse import urlencode

import pytest
from caeval import assessment_dossier_web as d
from test_assessment_workspace_web import call, select, child_post, freeze, selector
from test_assessment_setup_web import app, stage_collection, put


@pytest.fixture
def manager(selector):
    m=d.Manager(selector.root,selector.staging_root); m.host=selector.host
    assert select(m)[0]=='303 See Other'; freeze(m)
    return m


def policy_fields():
    return dict(baseline='9000',candidate='9000',reference='9000',trial_minimum='9000',trial_required='yes',approval='')


def post(m,action,fields,**changes):
    raw=urlencode(dict(csrf=m.active.csrf,action=action,**fields)).encode()
    return call(m,'/dossier','POST',raw,**changes)


def freeze_policy(m):
    f=policy_fields(); p=d.policy_preview(m.active,f); h=p['policy_sha256']
    assert post(m,'preview_policy',f)[0]=='200 OK'
    assert not (m.active.destination/(d.POLICY+'.json')).exists()
    assert post(m,'freeze_policy',{**f,'approval':h})[0]=='200 OK'
    return h


def complete(m,h):
    stage_collection(m.active)
    assert child_post(m,'/preview-collection')[0]=='200 OK'; report=m.active.collection[1]
    assert child_post(m,'/commit-collection',report)[0]=='200 OK'
    return dict(policy=h,head=m.active.head,report=report,trial='',approval='')


def test_full_browser_and_resume(manager):
    m=manager; assert b'Research evidence dossier' in call(m,'/workspaces')[2]
    h=freeze_policy(m); f=complete(m,h)
    assert b'assisted_trial_missing' in post(m,'preview',f)[2]
    expected=d.build(m.active,f); f['approval']=expected['dossier_sha256']
    assert post(m,'seal',f)[0]=='200 OK'
    assert post(m,'seal',f)[0]=='409 Conflict'
    assert post(m,'verify',f)[0]=='200 OK'
    s,headers,body=post(m,'download',f)
    assert s=='200 OK' and headers['Content-Disposition']=='attachment; filename="research-dossier.json"'
    assert json.loads(body)==expected
    plan=m.active.prepared['plan_sha256']; assert select(m,'second')[0]=='303 See Other'
    assert select(m,'first','resume',plan,f['head'],f['report'])[0]=='303 See Other'
    assert json.loads(post(m,'download',f)[2])==expected


def test_policy_no_overwrite_and_approval(manager):
    m=manager; f=policy_fields(); assert post(m,'freeze_policy',{**f,'approval':'0'*64})[0]=='409 Conflict'
    h=freeze_policy(m); assert post(m,'freeze_policy',{**f,'approval':h})[0]=='409 Conflict'
    assert d.resume.read(m.active.destination,d.POLICY)['policy_sha256']==h
    assert post(m,'preview',dict(policy=h,head=m.active.head,report='0'*64,trial='',approval=''))[0]=='409 Conflict'


def test_no_late_policy(manager):
    m=manager; complete(m,'0'*64)
    assert post(m,'preview_policy',policy_fields())[0]=='409 Conflict'


def test_stale_report_and_tampered_dossier(manager):
    m=manager; f=complete(m,freeze_policy(m))
    assert post(m,'preview',{**f,'report':'0'*64})[0]=='409 Conflict'
    f['approval']=d.build(m.active,f)['dossier_sha256']; assert post(m,'seal',f)[0]=='200 OK'
    path=m.active.destination/(d.RESULT+'.json'); changed=json.loads(path.read_text()); changed['administrative_evidence_gaps']=[]; put(path,changed)
    assert post(m,'download',f)[0]=='409 Conflict'


def test_present_trial_never_ignored(manager):
    m=manager; f=complete(m,freeze_policy(m)); put(m.active.staging/(d.TRIAL+'.json'),{'invalid':True})
    assert post(m,'preview',f)[0]=='409 Conflict'
    assert post(m,'preview',{**f,'trial':'0'*64})[0]=='409 Conflict'


def test_request_boundaries(manager):
    m=manager; f=policy_fields()
    assert post(m,'preview_policy',f,HTTP_AUTHORIZATION='')[0]=='401 Unauthorized'
    assert post(m,'preview_policy',f,HTTP_ORIGIN='https://evil.test')[0]=='403 Forbidden'
    raw=urlencode(dict(csrf=m.active.csrf,action='preview_policy',**f)).encode()
    assert call(m,'/dossier','POST',raw+b'&baseline=0')[0]=='403 Forbidden'
    assert select(m,'second')[0]=='303 See Other'; freeze_token=raw
    assert call(m,'/dossier','POST',freeze_token)[0] in ('403 Forbidden','409 Conflict')


def test_real_http(manager):
    from threading import Thread
    from http.client import HTTPConnection
    from wsgiref.simple_server import make_server
    m=manager; f=policy_fields(); h=d.policy_preview(m.active,f)['policy_sha256']
    with make_server('127.0.0.1',0,m,handler_class=d.web.QuietHandler) as server:
        m.host=m.active.host='127.0.0.1:'+str(server.server_port)
        thread=Thread(target=server.serve_forever,daemon=True); thread.start()
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        headers={'Authorization':m.auth,'Content-Type':'application/x-www-form-urlencoded','Origin':'http://'+m.host}
        try:
            conn.request('POST','/dossier',urlencode(dict(csrf=m.active.csrf,action='freeze_policy',**{**f,'approval':h})),headers)
            r=conn.getresponse(); assert r.status==200; r.read()
            f=complete(m,h); f['approval']=d.build(m.active,f)['dossier_sha256']
            for action in ('seal','download'):
                conn.request('POST','/dossier',urlencode(dict(csrf=m.active.csrf,action=action,**f)),headers)
                r=conn.getresponse(); assert r.status==200; raw=r.read()
            assert json.loads(raw)['dossier_sha256']==f['approval']
        finally:
            conn.close(); server.shutdown(); thread.join(5)


def test_valid_trial_browser_handoff(manager):
    from test_assessment_research_dossier import trial
    m=manager; h=freeze_policy(m)
    plan,inputs=d.resume.base_inputs(m.active.destination,m.active.prepared['plan_sha256'],m.active.head)
    package,reference=trial(inputs)
    stage_collection(m.active)
    put(m.active.staging/'reference-rows.json',reference['rows'])
    meta=d.resume.read(m.active.staging,'collection'); meta['source_receipts']['reference']=reference['source_receipt_sha256']
    put(m.active.staging/'collection.json',meta)
    put(m.active.staging/(d.TRIAL+'.json'),package)
    assert child_post(m,'/preview-collection')[0]=='200 OK'; report=m.active.collection[1]
    assert child_post(m,'/commit-collection',report)[0]=='200 OK'
    f=dict(policy=h,head=m.active.head,report=report,trial=package['package_sha256'],approval='')
    assert post(m,'preview',f)[0]=='200 OK'
    result=d.build(m.active,f); assert result['assisted_trial']['coverage']['known']==3
    f['approval']=result['dossier_sha256']; assert post(m,'seal',f)[0]=='200 OK'
    assert json.loads(post(m,'download',f)[2])==result
