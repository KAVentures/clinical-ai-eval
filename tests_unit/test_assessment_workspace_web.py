import io
import json
import shutil
from urllib.parse import urlencode
from wsgiref.util import setup_testing_defaults

import pytest
from caeval import assessment_workspace_web as manager
from test_assessment_setup_web import app, stage_collection


@pytest.fixture
def selector(app,tmp_path):
    root=tmp_path/'workspaces'; root.mkdir(mode=0o700)
    stage=tmp_path/'stages'; stage.mkdir(mode=0o700)
    shutil.copytree(app.staging,stage/'first')
    m=manager.Manager(root,stage); m.host='127.0.0.1:12345'
    return m


def call(m,path='/',method='GET',raw=b'',**changes):
    env={}; setup_testing_defaults(env)
    env.update(PATH_INFO=path,REQUEST_METHOD=method,HTTP_HOST=m.host,REMOTE_ADDR='127.0.0.1',HTTP_AUTHORIZATION=m.auth,
               CONTENT_LENGTH=str(len(raw)),CONTENT_TYPE='application/x-www-form-urlencoded')
    env['wsgi.input']=io.BytesIO(raw); env.update(changes); result=[]
    body=b''.join(m(env,lambda status,headers:result.extend([status,dict(headers)])))
    return result[0],result[1],body


def select(m,name='first',action='new',plan='',head='',report='',**changes):
    raw=urlencode(dict(csrf=m.csrf,action=action,name=name,plan=plan,head=head,report=report)).encode()
    return call(m,'/select-workspace','POST',raw,**changes)


def child_post(m,path,approval=None,csrf=None):
    fields={'csrf':csrf or m.active.csrf}
    if approval is not None: fields['approval']=approval
    return call(m,path,'POST',urlencode(fields).encode())


def freeze(m):
    assert child_post(m,'/preview-plan')[0]=='200 OK'
    assert child_post(m,'/commit-plan',m.active.preview[1]['assessment-plan']['plan_sha256'])[0]=='200 OK'
    return m.active.prepared['plan_sha256'],m.active.head


def test_select_switch_resume_and_complete(selector):
    m=selector
    assert b'Create a workspace' in call(m)[2]
    assert select(m)[0]=='303 See Other'
    assert b'Workspace selector' in call(m)[2]
    assert not (m.root/'first').exists()
    plan,head=freeze(m); old=m.active.csrf
    assert select(m,'second')[0]=='303 See Other'
    assert (m.staging_root/'second').is_dir()
    assert child_post(m,'/preview-plan',csrf=old)[0]=='403 Forbidden'
    assert select(m,'first','resume',plan,head)[0]=='303 See Other'
    assert m.active.prepared['plan_sha256']==plan
    stage_collection(m.active)
    assert child_post(m,'/preview-collection')[0]=='200 OK'
    report=m.active.collection[1]
    assert child_post(m,'/commit-collection',report)[0]=='200 OK'
    assert select(m,'third')[0]=='303 See Other'
    assert select(m,'first','resume',plan,head)[0]=='409 Conflict'
    assert m.active_name=='third'
    assert select(m,'first','resume',plan,head,report)[0]=='303 See Other'
    for path in ('/run','/verify','/download'):
        result=child_post(m,path); assert result[0]=='200 OK'
    assert json.loads(result[2])['report_sha256']==report


@pytest.mark.parametrize('name',['../first','/etc','a/b','a.b','A','x'*49])
def test_bad_names(selector,name):
    assert select(selector,name)[0]=='409 Conflict'
    assert selector.active is None


@pytest.mark.parametrize('changes,status',[({'HTTP_AUTHORIZATION':''},'401 Unauthorized'),({'HTTP_HOST':'evil.test'},'403 Forbidden'),({'REMOTE_ADDR':'192.0.2.1'},'403 Forbidden'),({'HTTP_ORIGIN':'https://evil.test'},'403 Forbidden'),({'QUERY_STRING':'x=1'},'400 Bad Request')])
def test_boundaries(selector,changes,status):
    assert select(selector,**changes)[0]==status
    assert selector.active is None


def test_duplicate_fields_and_stale_manager_form(selector):
    m=selector; old=m.csrf
    raw=urlencode(dict(csrf=old,action='new',name='first',plan='',head='',report='')).encode()
    assert call(m,'/select-workspace','POST',raw+b'&name=second')[0]=='403 Forbidden'
    assert call(m,'/select-workspace')[0]=='405 Method Not Allowed'
    assert call(m,'/select-workspace','POST',raw,CONTENT_LENGTH='5000')[0]=='403 Forbidden'
    assert select(m)[0]=='303 See Other'
    assert call(m,'/select-workspace','POST',raw)[0]=='403 Forbidden'


def test_inventory_does_not_follow_symlinks_or_read_evidence(selector,tmp_path):
    m=selector
    (m.root/'opaque').mkdir(mode=0o700)
    (m.root/'opaque'/'assessment-plan.json').write_text('not json')
    (m.root/'link').symlink_to(tmp_path,target_is_directory=True)
    assert manager.inventory(m.root)==['opaque']
    assert b'opaque' in call(m,'/workspaces')[2]
    assert select(m,'link','resume','a'*64,'b'*64)[0]=='409 Conflict'
    assert select(m,'opaque')[0]=='409 Conflict'


def test_real_http(selector):
    from threading import Thread
    from http.client import HTTPConnection
    from wsgiref.simple_server import make_server
    from caeval.assessment_workbench_web import QuietHandler
    m=selector
    with make_server('127.0.0.1',0,m,handler_class=QuietHandler) as server:
        m.host='127.0.0.1:'+str(server.server_port)
        worker=Thread(target=server.serve_forever,daemon=True); worker.start()
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        headers={'Authorization':m.auth,'Content-Type':'application/x-www-form-urlencoded','Origin':'http://'+m.host}
        try:
            conn.request('GET','/workspaces',headers=headers); response=conn.getresponse(); assert response.status==200; response.read()
            raw=urlencode(dict(csrf=m.csrf,action='new',name='first',plan='',head='',report=''))
            conn.request('POST','/select-workspace',raw,headers); response=conn.getresponse(); assert response.status==303; response.read()
            conn.request('GET','/',headers=headers); response=conn.getresponse(); assert response.status==200; assert b'Hospital assessment setup' in response.read()
        finally:
            conn.close(); server.shutdown(); worker.join(timeout=5)
