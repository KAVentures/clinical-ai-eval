import io
import json
from wsgiref.util import setup_testing_defaults

import pytest
from caeval import assessment_workbench_web as web
from test_assessment_workbench import context, KEY, SITES


@pytest.fixture
def app(context,tmp_path):
    p,a,r=context
    def write(name,obj):
        path=tmp_path/name; path.write_text(json.dumps(obj)); path.chmod(0o600); return str(path)
    cfg={k:a[k] for k in ('head','as_of','expected_bundles')}; cfg['db']=str(a['db'])
    for k in ('predictive_plan','binary_plan','manifest','outcomes','reference_counts','target_counts'):
        cfg[k]=write(k,a[k])
    cfg['bundles']={k:write(k,v) for k,v in a['bundles'].items()}
    cfg['keys']=write('keys',{'audit':KEY.hex(),'sites':{k:v.hex() for k,v in SITES.items()}})
    app=web.Operator(write('plan',p),p['plan_sha256'],write('config',cfg)); app.host='127.0.0.1:12345'
    return app


def call(app,path='/',method='GET',**changes):
    env={}; setup_testing_defaults(env)
    raw=('csrf='+app.csrf).encode()
    env.update(PATH_INFO=path,REQUEST_METHOD=method,HTTP_HOST=app.host,REMOTE_ADDR='127.0.0.1',
               HTTP_AUTHORIZATION=app.auth,CONTENT_LENGTH=str(len(raw)),CONTENT_TYPE='application/x-www-form-urlencoded')
    env['wsgi.input']=io.BytesIO(raw); env.update(changes); result=[]
    body=b''.join(app(env,lambda status,headers:result.extend([status,dict(headers)])))
    return result[0],result[1],body


def test_real_workflow(app):
    assert call(app)[0]=='200 OK'
    assert call(app,'/download','POST')[0]=='409 Conflict'
    status,h,body=call(app,'/run','POST'); assert status=='200 OK' and b'Missing mandatory documents' in body
    assert b'safety' in body and b'Adverse outcomes and population shift' in body
    assert call(app,'/download','POST')[0]=='409 Conflict'
    assert call(app,'/verify','POST')[0]=='200 OK'
    status,h,body=call(app,'/download','POST'); assert status=='200 OK'
    assert h['Content-Disposition'].startswith('attachment;')
    assert json.loads(body)==app.report
    assert h['Cache-Control']=='no-store' and "frame-ancestors 'none'" in h['Content-Security-Policy']


@pytest.mark.parametrize('changes,status',[({'HTTP_AUTHORIZATION':''},'401 Unauthorized'),({'HTTP_HOST':'evil.test'},'403 Forbidden'),({'REMOTE_ADDR':'192.0.2.1'},'403 Forbidden'),({'HTTP_ORIGIN':'https://evil.test'},'403 Forbidden'),({'HTTP_SEC_FETCH_SITE':'cross-site'},'403 Forbidden'),({'QUERY_STRING':'path=/etc/passwd'},'400 Bad Request')])
def test_request_boundaries(app,changes,status):
    assert call(app,**changes)[0]==status
    assert app.report is None


def test_write_boundaries(app):
    assert call(app,'/run')[0]=='405 Method Not Allowed'
    assert call(app,'/run','POST',CONTENT_LENGTH='999999')[0]=='403 Forbidden'
    assert call(app,'/run','POST',**{'wsgi.input':io.BytesIO(b'x'*69)})[0]=='403 Forbidden'
    assert call(app,'/run','POST',CONTENT_TYPE='application/json')[0]=='403 Forbidden'
    assert call(app,'/upload','POST')[0]=='404 Not Found'
    assert app.report is None


def test_config_change_invalidates_export(app):
    assert call(app,'/run','POST')[0]=='200 OK'
    assert call(app,'/verify','POST')[0]=='200 OK'
    from pathlib import Path
    p=Path(app.config_path); cfg=json.loads(p.read_text()); cfg['as_of']='2026-01-04'; p.write_text(json.dumps(cfg))
    assert call(app,'/verify','POST')[0]=='409 Conflict'
    assert call(app,'/download','POST')[0]=='409 Conflict'
    assert app.report is None


def test_escaping_and_no_secrets(app):
    assert call(app,'/run','POST')[0]=='200 OK'
    app.report['scope']['buyer']='<script>alert(1)</script>'
    body=call(app)[2]
    assert b'&lt;script&gt;' in body and b'<script>' not in body
    assert app.password.encode() not in body and app.config_path.encode() not in body


def test_download_rechecks_inputs(app):
    assert call(app,'/run','POST')[0]=='200 OK'
    assert call(app,'/verify','POST')[0]=='200 OK'
    from pathlib import Path
    cfg=json.loads(Path(app.config_path).read_text()); Path(cfg['outcomes']).write_text('[]')
    assert call(app,'/download','POST')[0]=='409 Conflict'
    assert not app.verified and app.report is None


def test_real_http(app):
    from threading import Thread
    from http.client import HTTPConnection
    from wsgiref.simple_server import make_server
    with make_server('127.0.0.1',0,app,handler_class=web.QuietHandler) as server:
        app.host='127.0.0.1:'+str(server.server_port)
        worker=Thread(target=server.serve_forever,daemon=True); worker.start()
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        try:
            conn.request('GET','/'); response=conn.getresponse(); assert response.status==401; response.read()
            conn.request('GET','/',headers={'Authorization':app.auth}); response=conn.getresponse(); assert response.status==200; assert b'Local hospital assessment' in response.read()
            for path in ('/run','/verify','/download'):
                conn.request('POST',path,body='csrf='+app.csrf,headers={'Authorization':app.auth,'Content-Type':'application/x-www-form-urlencoded','Origin':'http://'+app.host})
                response=conn.getresponse(); assert response.status==200; body=response.read()
            assert json.loads(body)['decision_grade'] is False
        finally:
            conn.close(); server.shutdown(); worker.join(timeout=5)
