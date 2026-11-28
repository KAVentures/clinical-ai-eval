import json
from io import BytesIO
from http.client import HTTPConnection
from threading import Thread
from wsgiref.simple_server import make_server

import pytest
from caeval import evaluator_agent_gateway as g


@pytest.fixture
def app():
    calls=[]
    def native(env,start):
        calls.append((env['REQUEST_METHOD'],env['PATH_INFO']))
        raw=b'{"ok":true,"result":{}}'
        start('200 OK',[('Content-Type','application/json'),('Content-Length',str(len(raw))),('Cache-Control','no-store')])
        return [raw]
    return g.Gateway('https://hospital.example',native),calls


def request(app,path='/',method='GET',**changes):
    env=dict(PATH_INFO=path,REQUEST_METHOD=method,HTTP_HOST='hospital.example',REMOTE_ADDR='127.0.0.1',HTTP_AUTHORIZATION='Bearer test',QUERY_STRING='',SCRIPT_NAME='',CONTENT_LENGTH='0')
    env.update(changes); env['wsgi.input']=BytesIO(b''); result=[]
    raw=b''.join(app(env,lambda status,headers:result.extend([status,dict(headers)])))
    return result[0],json.loads(raw)


def test_contract_static_without_grant(app):
    a,calls=app; status,doc=request(a,g.CONTRACT,HTTP_AUTHORIZATION='')
    assert status=='200 OK' and not doc['client_installation_required'] and not doc['remote_execution']
    assert doc['human_approval_required'] and not doc['decision_grade'] and not calls
    assert len(doc['operations'])==5


def test_exact_delegated_routes(app):
    a,calls=app; rid='a'*32
    routes=[('GET','/v1/assessment'),('GET',g.REQUESTS),('POST',g.REQUESTS),('GET',g.REQUESTS+'/'+rid),('GET',g.REQUESTS+'/'+rid+'/receipt')]
    for method,path in routes: assert request(a,path,method)[0]=='200 OK'
    assert calls==routes


@pytest.mark.parametrize('method,path',[('GET','/'),('POST','/agent-contract'),('DELETE',g.REQUESTS),('GET',g.REQUESTS+'/bad'),('GET',g.REQUESTS+'/'+'a'*32+'/packages/family'),('POST',g.REQUESTS+'/'+'a'*32+'/approve'),('GET','/workspaces'),('GET','/v1/assessment/')])
def test_never_exposes_other_routes(app,method,path):
    a,calls=app; assert request(a,path,method)[0]=='403 Forbidden' and not calls


@pytest.mark.parametrize('changes',[{'HTTP_HOST':'evil.example'},{'REMOTE_ADDR':'192.0.2.1'},{'HTTP_ORIGIN':'https://hospital.example'},{'HTTP_COOKIE':'x=1'},{'QUERY_STRING':'token=x'},{'HTTP_AUTHORIZATION':''},{'SCRIPT_NAME':'/other'},{'HTTP_TRANSFER_ENCODING':'chunked'},{'CONTENT_LENGTH':'1'}])
def test_request_boundary(app,changes):
    a,calls=app; assert request(a,'/v1/assessment',**changes)[0]=='403 Forbidden' and not calls


@pytest.mark.parametrize('origin',['http://hospital.example','https://hospital.example/path','https://user@hospital.example','https://hospital.example?x=1','https://hospital.example.','https://bad_host','https://-bad.example','https://hospital.example:000443'])
def test_invalid_origin(origin):
    with pytest.raises(ValueError): g.approved_host(origin)


def test_real_loopback(app):
    a,calls=app
    with make_server('127.0.0.1',0,a,handler_class=g.QuietHandler) as server:
        server.timeout=2; worker=Thread(target=server.handle_request,daemon=True); worker.start()
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        try:
            conn.request('GET',g.CONTRACT,headers={'Host':'hospital.example'})
            r=conn.getresponse(); assert r.status==200 and r.getheader('Cache-Control')=='no-store'
            assert json.loads(r.read())['schema']=='clinical-ai-eval-agent-contract/1'
        finally: conn.close(); worker.join(4)
    assert not calls
