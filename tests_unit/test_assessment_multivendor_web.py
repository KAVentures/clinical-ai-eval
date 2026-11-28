import json
import shutil
from urllib.parse import urlencode

import pytest
from caeval import assessment_multivendor_web as w
from test_assessment_workspace_web import call, select, freeze, selector, child_post
from test_assessment_setup_web import app, put, stage_collection
from test_assessment_research_dossier import policy
from test_assessment_multivendor import result, member


@pytest.fixture
def scope(selector,tmp_path):
    root=tmp_path/'comparisons'; root.mkdir(mode=0o700)
    m=w.Manager(selector.root,selector.staging_root,root); m.host=selector.host
    shutil.copytree(m.staging_root/'first',m.staging_root/'second')
    for k in ('request-spec','predictive-spec','binary-spec'):
        p=m.staging_root/'second'/(k+'.json'); data=json.loads(p.read_text())
        if k=='request-spec': data.update(vendor='vendor-b',product='ai-b',version='v3')
        else: data['candidate_version']='v3'
        put(p,data)
    f={k:'' for k in w.KEYS-{'csrf','action'}}; f['name']='study'
    for i,n in enumerate(('first','second'),1):
        assert select(m,n)[0]=='303 See Other'; plan,head=freeze(m)
        p=policy(m.active.prepared,False); put(m.active.destination/(w.parent.POLICY+'.json'),p)
        f.update({k+str(i):v for k,v in dict(workspace=n,plan=plan,head=head,policy=p['policy_sha256']).items()})
    return m,f


def post(m,action,f,**changes):
    return call(m,'/comparisons','POST',urlencode(dict(csrf=m.csrf,action=action,**f)).encode(),**changes)


def design(m,f):
    assert post(m,'preview_design',f)[0]=='200 OK'
    h=w.action(m,{**f,'action':'preview_design'})['comparison_sha256']
    assert post(m,'freeze_design',{**f,'approval':h})[0]=='200 OK'
    f['comparison']=h


def complete(m,f,i):
    n=str(i); assert select(m,f['workspace'+n],'resume',f['plan'+n],f['head'+n])[0]=='303 See Other'
    stage_collection(m.active); assert child_post(m,'/preview-collection')[0]=='200 OK'; rh=m.active.collection[1]
    assert child_post(m,'/commit-collection',rh)[0]=='200 OK'
    plan,inputs,_=w.resume.complete_inputs(m.active.destination,f['plan'+n],f['head'+n],rh)
    r=result(member(plan,inputs)); put(m.active.destination/(w.parent.RESULT+'.json'),r['dossier'])
    f.update({'report'+n:rh,'dossier'+n:r['expected_dossier']})


def test_full_browser(scope):
    m,f=scope; assert b'Matched product comparisons' in call(m,'/workspaces')[2]
    design(m,f); complete(m,f,1); complete(m,f,2)
    s,_,body=post(m,'preview',f); assert s=='200 OK' and b'second minus first' in body
    r=w.action(m,{**f,'action':'preview'}); f['approval']=r['report_sha256']
    assert post(m,'seal',f)[0]=='200 OK' and post(m,'seal',f)[0]=='409 Conflict'
    assert post(m,'verify',f)[0]=='200 OK'
    s,h,body=post(m,'download',f); assert s=='200 OK' and h['Content-Disposition'].endswith('"comparison-report.json"')
    assert json.loads(body)==r
    # New browser session can replay the saved comparison without selecting a child.
    m2=w.Manager(m.root,m.staging_root,m.comparison_root); m2.host=m.host
    assert json.loads(post(m2,'download',f)[2])==r


def test_missing_vendor_and_refusal_to_omit_saved(scope):
    m,f=scope; design(m,f); complete(m,f,1)
    r=w.action(m,{**f,'action':'preview'}); assert r['products']['second']['status']=='not_supplied'
    complete(m,f,2)
    assert post(m,'preview',{**f,'report2':'','dossier2':''})[0]=='409 Conflict'


def test_late_design_and_path_rejection(scope):
    m,f=scope
    for changes in ({'name':'../bad'},{'workspace2':'../first'},{'workspace2':'first'},{'workspace2':''}):
        assert post(m,'preview_design',{**f,**changes})[0]=='409 Conflict'
    complete(m,f,1)
    assert post(m,'preview_design',{**f,'report1':'','dossier1':''})[0]=='409 Conflict'


def test_auth_and_stale_form(scope):
    m,f=scope; assert post(m,'preview_design',f,HTTP_AUTHORIZATION='')[0]=='401 Unauthorized'
    assert post(m,'preview_design',f,HTTP_ORIGIN='https://evil.test')[0]=='403 Forbidden'
    raw=urlencode(dict(csrf=m.csrf,action='preview_design',**f)).encode()
    assert call(m,'/comparisons','POST',raw+b'&name=other')[0]=='403 Forbidden'
    assert select(m,'third')[0]=='303 See Other'; assert call(m,'/comparisons','POST',raw)[0]=='403 Forbidden'


def test_tampering_and_trial_not_ignored(scope):
    m,f=scope; design(m,f); complete(m,f,1); complete(m,f,2)
    assert post(m,'preview',{**f,'comparison':'0'*64})[0]=='409 Conflict'
    put(m.staging_root/'second'/(w.parent.TRIAL+'.json'),{'invalid':True})
    assert post(m,'preview',f)[0]=='409 Conflict'
    (m.staging_root/'second'/(w.parent.TRIAL+'.json')).unlink()
    r=w.action(m,{**f,'action':'preview'}); f['approval']=r['report_sha256']
    assert post(m,'seal',f)[0]=='200 OK'
    put(m.comparison_root/'study-report.json',{**r,'n_replayed':1})
    assert post(m,'download',f)[0]=='409 Conflict'


def test_separate_roots_and_worker_guard(scope,monkeypatch):
    m,f=scope
    with pytest.raises(ValueError): w.Manager(m.root,m.staging_root,m.root)
    monkeypatch.setattr(m.worker,'busy',lambda:True)
    assert post(m,'preview_design',f)[0]=='409 Conflict'


def test_real_http(scope):
    from threading import Thread
    from http.client import HTTPConnection
    from wsgiref.simple_server import make_server
    m,f=scope; design(m,f); complete(m,f,1); complete(m,f,2)
    r=w.action(m,{**f,'action':'preview'}); f['approval']=r['report_sha256']
    with make_server('127.0.0.1',0,m,handler_class=w.web.QuietHandler) as server:
        m.host=m.active.host='127.0.0.1:'+str(server.server_port)
        t=Thread(target=server.serve_forever,daemon=True); t.start()
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        headers={'Authorization':m.auth,'Content-Type':'application/x-www-form-urlencoded','Origin':'http://'+m.host}
        try:
            for op in ('seal','download'):
                conn.request('POST','/comparisons',urlencode(dict(csrf=m.csrf,action=op,**f)),headers)
                response=conn.getresponse(); assert response.status==200; raw=response.read()
            assert json.loads(raw)==r
        finally:
            conn.close(); server.shutdown(); t.join(5)
