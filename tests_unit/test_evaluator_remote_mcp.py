import json
from io import BytesIO
from http.client import HTTPConnection
from threading import Thread
from wsgiref.simple_server import make_server

import pytest
from caeval import evaluator_remote_mcp as m

AUTH='Bearer '+'a'*43

@pytest.fixture
def app():
    calls=[]; valid=[True]
    def native(env,start):
        calls.append((env['REQUEST_METHOD'],env['PATH_INFO'],env['wsgi.input'].read()))
        raw=b'{"ok":true,"result":{"status":"awaiting_local_approval"}}' if valid[0] else b'{"ok":false}'
        start('200 OK' if valid[0] else '403 Forbidden',[('Content-Type','application/json')]); return [raw]
    return m.App('https://hospital.example',native),calls,valid


def request(app,method='tools/list',params=None,*,raw=None,notification=False,**env):
    d={'jsonrpc':'2.0','method':method,'params':params or {}}
    if not notification: d['id']=1
    body=m.encoded(d) if raw is None else raw
    e=dict(REQUEST_METHOD='POST',PATH_INFO='/mcp',HTTP_HOST='hospital.example',REMOTE_ADDR='127.0.0.1',HTTP_AUTHORIZATION=AUTH,
           HTTP_ACCEPT='application/json, text/event-stream',CONTENT_LENGTH=str(len(body)),CONTENT_TYPE='application/json',HTTP_MCP_PROTOCOL_VERSION='2025-06-18')
    e.update(env); e['wsgi.input']=BytesIO(body); result=[]
    data=b''.join(app(e,lambda status,headers:result.extend([status,dict(headers)])))
    return result[0],json.loads(data) if data else None


def test_discover_and_five_tools(app):
    a,calls,_=app
    s,d=request(a,'initialize',{'protocolVersion':'2025-11-25','clientInfo':{'name':'test','version':'1'},'capabilities':{}})
    assert s=='200 OK' and d['result']['protocolVersion']=='2025-06-18'
    assert request(a,'notifications/initialized',notification=True)==('202 Accepted',None)
    tools=request(a)[1]['result']['tools']; assert {x['name'] for x in tools}==set(m.TOOLS)
    assert len(calls)==3 and all(c[1]=='/v1/assessment' for c in calls)
    assert next(t for t in tools if t['name']=='request_evaluation')['annotations']['readOnlyHint'] is False


def test_all_native_calls_and_revocation(app):
    a,calls,valid=app; rid='b'*32; key='approved-key-0001'
    for name,args in [('discover_assessment',{}),('list_evaluation_requests',{}),('request_evaluation',{'idempotency_key':key}),('evaluation_status',{'request_id':rid}),('evaluation_receipt',{'request_id':rid})]:
        out=request(a,'tools/call',{'name':name,'arguments':args})[1]
        assert json.loads(out['result']['content'][0]['text'])['status']=='awaiting_local_approval'
    assert calls==[('GET','/v1/assessment',b''),('GET',m.gateway.REQUESTS,b''),('POST',m.gateway.REQUESTS,m.encoded({'idempotency_key':key})),('GET',m.gateway.REQUESTS+'/'+rid,b''),('GET',m.gateway.REQUESTS+'/'+rid+'/receipt',b'')]
    valid[0]=False
    for method,params in [('initialize',{'protocolVersion':'2025-06-18'}),('tools/list',{}),('tools/call',{'name':'discover_assessment'})]:
        assert 'error' in request(a,method,params)[1]


@pytest.mark.parametrize('name,args',[('execute',{}),('download_package',{}),('evaluation_status',{'request_id':'../packages'}),('request_evaluation',{'idempotency_key':'approved-key-0001','execute':True}),('discover_assessment',{'workspace':'/tmp'})])
def test_no_unapproved_operations(app,name,args):
    a,calls,_=app; assert 'error' in request(a,'tools/call',{'name':name,'arguments':args})[1] and not calls


@pytest.mark.parametrize('changes,status',[({'HTTP_HOST':'evil.example'},'403 Forbidden'),({'REMOTE_ADDR':'192.0.2.1'},'403 Forbidden'),({'HTTP_ORIGIN':'https://hospital.example'},'403 Forbidden'),({'HTTP_COOKIE':'x=1'},'403 Forbidden'),({'QUERY_STRING':'token=x'},'403 Forbidden'),({'HTTP_AUTHORIZATION':''},'401 Unauthorized'),({'HTTP_MCP_SESSION_ID':'x'},'403 Forbidden'),({'HTTP_TRANSFER_ENCODING':'chunked'},'403 Forbidden'),({'PATH_INFO':'/v1/assessment'},'403 Forbidden'),({'HTTP_MCP_PROTOCOL_VERSION':'invalid'},'400 Bad Request'),({'HTTP_MCP_PROTOCOL_VERSION':'2025-03-26'},'400 Bad Request'),({'HTTP_MCP_PROTOCOL_VERSION':None},'400 Bad Request'),({'HTTP_ACCEPT':'application/json'},'406 Not Acceptable'),({'REQUEST_METHOD':'GET'},'405 Method Not Allowed'),({'REQUEST_METHOD':'DELETE'},'405 Method Not Allowed'),({'CONTENT_LENGTH':'99999'},'400 Bad Request')])
def test_transport_boundaries(app,changes,status):
    a,calls,_=app; assert request(a,**changes)[0]==status and not calls


def test_malformed_and_batch(app):
    a,calls,_=app
    for raw in (b'[]',b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}',b'{"jsonrpc":"2.0","id":true,"method":"ping"}',b'NaN',b'{'):
        assert request(a,raw=raw)[0]=='400 Bad Request'
    assert not calls


def test_real_http(app):
    a,calls,_=app
    with make_server('127.0.0.1',0,a,handler_class=m.gateway.QuietHandler) as server:
        server.timeout=2; worker=Thread(target=server.handle_request,daemon=True); worker.start()
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        try:
            conn.request('POST','/mcp',m.encoded({'jsonrpc':'2.0','id':1,'method':'tools/list'}),{'Host':'hospital.example','Authorization':AUTH,'Accept':'application/json, text/event-stream','Content-Type':'application/json','MCP-Protocol-Version':'2025-06-18'})
            r=conn.getresponse(); assert r.status==200 and r.getheader('Cache-Control')=='no-store'
            assert len(json.loads(r.read())['result']['tools'])==5
        finally: conn.close(); worker.join(4)
