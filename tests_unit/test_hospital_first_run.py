from io import BytesIO
from threading import Thread
from urllib.parse import urlencode
from http.client import HTTPConnection

import pytest
from caeval import hospital_first_run as f


@pytest.fixture
def app(tmp_path):
    root=tmp_path/'private'; root.mkdir(mode=0o700)
    a=f.Setup(root); a.host='127.0.0.1:12345'
    return a


def call(a,path='/',method='GET',body=b'',**kw):
    env=dict(PATH_INFO=path,REQUEST_METHOD=method,HTTP_HOST=a.host,REMOTE_ADDR='127.0.0.1',HTTP_AUTHORIZATION=a.auth,CONTENT_LENGTH=str(len(body)),CONTENT_TYPE='application/x-www-form-urlencoded',QUERY_STRING='')
    env.update(kw); env['wsgi.input']=BytesIO(body); result=[]
    raw=b''.join(a(env,lambda status,headers:result.extend([status,dict(headers)])))
    return (*result,raw)


def form(a,**kw):
    return urlencode(dict(csrf=a.csrf,layout=f.checkpoint(f.launcher.diagnose(a.root)),confirm='yes')|kw).encode()


def test_read_only_and_explicit_start(app):
    status,headers,raw=call(app)
    assert status=='200 OK' and headers['Cache-Control']=='no-store' and b'Initialize and launch' in raw
    assert app.password.encode() not in raw and not list(app.root.iterdir())
    assert call(app,'/start','POST',form(app))[0]=='200 OK'
    assert app.launch_requested and set(p.name for p in app.root.iterdir())==set(f.launcher.LOCAL)
    assert call(app,'/start','POST',form(app))[0]=='409 Conflict'


def test_stale_layout(app):
    body=form(app); (app.root/'staging').mkdir(mode=0o700)
    assert call(app,'/start','POST',body)[0]=='409 Conflict'
    assert not app.launch_requested and not (app.root/'workspaces').exists()
    assert call(app,'/start','POST',form(app))[0]=='200 OK'


def test_unsafe_layout(app,tmp_path):
    outside=tmp_path/'outside'; outside.mkdir(mode=0o700)
    try: (app.root/'comparisons').symlink_to(outside,target_is_directory=True)
    except (OSError,NotImplementedError): pytest.skip('symlink unavailable')
    assert b'Unsafe or unavailable' in call(app)[2]
    assert call(app,'/start','POST',form(app))[0]=='409 Conflict'
    assert not app.launch_requested and not (app.root/'workspaces').exists()


def test_existing_evidence_and_institution_untouched(app):
    f.launcher.init_local(app.root); (app.root/'institution').mkdir(mode=0o700)
    files=[app.root/'institution'/'account-data',app.root/'workspaces'/'sentinel']
    for p in files: p.write_bytes(b'original')
    assert call(app,'/start','POST',form(app))[0]=='200 OK'
    assert all(p.read_bytes()==b'original' for p in files)


@pytest.mark.parametrize('changes,status',[({'HTTP_AUTHORIZATION':''},'401 Unauthorized'),({'HTTP_HOST':'evil.test'},'403 Forbidden'),({'REMOTE_ADDR':'192.0.2.1'},'403 Forbidden'),({'HTTP_ORIGIN':'https://evil.test'},'403 Forbidden'),({'HTTP_SEC_FETCH_SITE':'cross-site'},'403 Forbidden'),({'QUERY_STRING':'root=x'},'400 Bad Request')])
def test_request_boundaries(app,changes,status):
    assert call(app,'/start','POST',form(app),**changes)[0]==status
    assert not list(app.root.iterdir())


def test_malformed_and_unapproved(app):
    for body in (form(app,confirm='no'),form(app,csrf='bad'),form(app)+b'&csrf=other',form(app)+b'&root=elsewhere',b'broken'):
        assert call(app,'/start','POST',body)[0]=='403 Forbidden'
    assert call(app,'/start','POST',form(app),CONTENT_LENGTH='1025')[0]=='403 Forbidden'
    assert call(app,'/start')[0]=='405 Method Not Allowed'
    assert not list(app.root.iterdir())


def test_real_http(app):
    with f.make_server('127.0.0.1',0,app,handler_class=f.QuietHandler) as server:
        app.host='127.0.0.1:'+str(server.server_port)
        server.timeout=2
        worker=Thread(target=server.handle_request,daemon=True); worker.start()
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        try:
            conn.request('POST','/start',form(app),{'Authorization':app.auth,'Content-Type':'application/x-www-form-urlencoded','Origin':'http://'+app.host})
            r=conn.getresponse(); assert r.status==200 and b'Setup complete' in r.read()
        finally:
            conn.close(); worker.join(4)
    assert app.launch_requested


def test_main_closes_setup_before_launch(app,monkeypatch):
    seen=[]
    class Server:
        server_port=12345
        def __enter__(self): seen.append('open'); return self
        def handle_request(self):
            assert call(app,'/start','POST',form(app))[0]=='200 OK'
        def __exit__(self,*args): seen.append('closed')
    monkeypatch.setattr(f,'Setup',lambda root:app)
    monkeypatch.setattr(f,'make_server',lambda *a,**kw:Server())
    def launch(root):
        assert seen==['open','closed'] and root==app.root
        seen.append('launch'); return 7
    monkeypatch.setattr(f.launcher,'launch_local',launch)
    assert f.main(['--root',str(app.root)])==7
    assert seen==['open','closed','launch']
