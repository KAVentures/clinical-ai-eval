"""Stateless JSON-response MCP adapter for the restricted evaluator gateway."""
import argparse
from io import BytesIO
import json
import re
import sys
from wsgiref.simple_server import make_server

from . import evaluator_agent_gateway as gateway, hospital_launcher as launcher

VERSIONS = ('2025-06-18',)
MAX_BODY = 16384
MAX_REPLY = 256 * 1024
TOKEN = re.compile(r'Bearer [A-Za-z0-9_-]{43}')
KEY = r'^[A-Za-z0-9_-]{16,80}$'
ID = r'^[a-f0-9]{32}$'
TOOLS = {
    'discover_assessment': ('Discover the granted assessment.', None, True),
    'list_evaluation_requests': ('List owned requests.', None, True),
    'request_evaluation': ('Request local human approval, not execution.', 'idempotency_key', False),
    'evaluation_status': ('Read owned request status.', 'request_id', True),
    'evaluation_receipt': ('Read completed verified metadata receipt.', 'request_id', True),
}


def require(ok):
    if not ok: raise ValueError('MCP request refused')


def unique(pairs):
    result = dict(pairs); require(len(result)==len(pairs)); return result


def decode(raw):
    def invalid(_): raise ValueError('invalid JSON constant')
    return json.loads(raw.decode('utf-8'),object_pairs_hook=unique,parse_constant=invalid)


def encoded(doc):
    return json.dumps(doc,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii')


def schemas():
    result=[]
    for name,(desc,field,readonly) in TOOLS.items():
        schema={'type':'object','properties':{},'additionalProperties':False}
        if field:
            schema['properties'][field]={'type':'string','pattern':KEY if field=='idempotency_key' else ID}
            schema['required']=[field]
        result.append({'name':name,'description':desc,'inputSchema':schema,'annotations':{'readOnlyHint':readonly,'destructiveHint':False,'idempotentHint':True,'openWorldHint':False}})
    return result


def route(name,args):
    require(isinstance(name,str) and name in TOOLS and isinstance(args,dict))
    field=TOOLS[name][1]
    require(set(args)==({field} if field else set()))
    if field:
        require(isinstance(args[field],str) and re.fullmatch(KEY if field=='idempotency_key' else ID,args[field]))
    base=gateway.REQUESTS
    if name=='discover_assessment': return 'GET','/v1/assessment',b''
    if name=='list_evaluation_requests': return 'GET',base,b''
    if name=='request_evaluation': return 'POST',base,encoded(args)
    return 'GET',base+'/'+args['request_id']+('/receipt' if name=='evaluation_receipt' else ''),b''


class App:
    def __init__(self,origin,native):
        self.gateway=gateway.Gateway(origin,native); self.host=self.gateway.host

    def call(self,auth,name,args):
        method,path,body=route(name,args)
        env={'REQUEST_METHOD':method,'PATH_INFO':path,'HTTP_HOST':self.host,'REMOTE_ADDR':'127.0.0.1',
             'HTTP_AUTHORIZATION':auth,'CONTENT_LENGTH':str(len(body)),'CONTENT_TYPE':'application/json','wsgi.input':BytesIO(body)}
        response=[]
        stream=self.gateway(env,lambda status,headers:response.append((status,dict(headers))))
        try:
            raw=bytearray()
            for block in stream:
                require(len(raw)+len(block)<=MAX_REPLY); raw.extend(block)
        finally:
            if hasattr(stream,'close'): stream.close()
        require(len(response)==1 and response[0][0]=='200 OK' and response[0][1].get('Content-Type')=='application/json')
        doc=decode(raw); require(isinstance(doc,dict) and set(doc)=={'ok','result'} and doc['ok'] is True and isinstance(doc['result'],dict))
        return doc['result']

    def __call__(self,env,start):
        def reply(status,doc=None,extra=()):
            body=b'' if doc is None else encoded(doc)
            start(status,[('Content-Type','application/json'),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),*extra]); return [body]
        def error(identifier,code,message): return reply('200 OK',{'jsonrpc':'2.0','id':identifier,'error':{'code':code,'message':message}})
        if (env.get('REMOTE_ADDR')!='127.0.0.1' or env.get('HTTP_HOST')!=self.host or env.get('PATH_INFO')!='/mcp'
                or any(env.get(k) for k in ('QUERY_STRING','SCRIPT_NAME','HTTP_COOKIE','HTTP_ORIGIN','HTTP_TRANSFER_ENCODING','HTTP_MCP_SESSION_ID'))):
            return reply('403 Forbidden')
        auth=env.get('HTTP_AUTHORIZATION','')
        if not isinstance(auth,str) or not TOKEN.fullmatch(auth): return reply('401 Unauthorized',extra=[('WWW-Authenticate','Bearer')])
        version=env.get('HTTP_MCP_PROTOCOL_VERSION')
        if version is not None and version not in VERSIONS: return reply('400 Bad Request')
        if env.get('REQUEST_METHOD')!='POST': return reply('405 Method Not Allowed',extra=[('Allow','POST')])
        accept={v.strip().lower() for v in env.get('HTTP_ACCEPT','').split(',')}
        if not {'application/json','text/event-stream'}<=accept: return reply('406 Not Acceptable')
        try:
            n=int(env.get('CONTENT_LENGTH','')); require(0<n<=MAX_BODY and env.get('CONTENT_TYPE','').split(';')[0].strip()=='application/json')
            raw=env['wsgi.input'].read(n); require(len(raw)==n)
            req=decode(raw); require(isinstance(req,dict) and req.get('jsonrpc')=='2.0' and set(req)<={'jsonrpc','id','method','params'})
            method=req.get('method'); params=req.get('params',{}); require(isinstance(method,str) and isinstance(params,dict))
            require(method=='initialize' or version in VERSIONS)
            if 'id' not in req:
                require(method=='notifications/initialized' and not params)
                self.call(auth,'discover_assessment',{})
                return reply('202 Accepted')
            identifier=req['id']; require(type(identifier) in (str,int) and len(str(identifier))<=256)
        except (ValueError,TypeError,KeyError,UnicodeError,RecursionError,OSError,RuntimeError): return reply('400 Bad Request')
        # No session ID or persistent state: the native grant is revalidated on
        # every request. Initialization/list/ping use the native discovery check.
        try:
            if method in ('initialize','ping','tools/list'):
                if method=='initialize':
                    requested=params.get('protocolVersion'); require(isinstance(requested,str))
                    result={'protocolVersion':requested if requested in VERSIONS else VERSIONS[0],
                        'capabilities':{'tools':{'listChanged':False}},'serverInfo':{'name':'clinical-ai-eval-remote','version':'1.0'},
                        'instructions':'Request local approval and read scoped metadata only. No clinical records or remote execution.'}
                else:
                    require(not params); result={'tools':schemas()} if method=='tools/list' else {}
                self.call(auth,'discover_assessment',{})
            elif method=='tools/call':
                require(set(params)<={'name','arguments'} and 'name' in params)
                value=self.call(auth,params['name'],params.get('arguments',{}))
                result={'content':[{'type':'text','text':encoded(value).decode('ascii')}],'isError':False}
            else: return error(identifier,-32601,'Method not found')
            return reply('200 OK',{'jsonrpc':'2.0','id':identifier,'result':result})
        except (ValueError,TypeError,KeyError,UnicodeError,RecursionError,OSError,RuntimeError):
            return error(identifier,-32000,'Invalid request or evaluation grant denied')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace',required=True); p.add_argument('--origin',required=True); p.add_argument('--port',type=int,default=8773)
    a=p.parse_args(argv)
    try:
        require(1024<=a.port<=65535); directory=launcher.private(a.workspace)
        from . import evaluator_service
        app=App(a.origin,evaluator_service.App(str(directory)))
        with make_server('127.0.0.1',a.port,app,handler_class=gateway.QuietHandler) as server:
            print('Remote MCP adapter bound to loopback; approved HTTPS proxy required.',flush=True); server.serve_forever()
        return 0
    except KeyboardInterrupt: return 0
    except (OSError,ValueError,TypeError,ImportError):
        print('Remote MCP adapter startup refused.',file=sys.stderr); return 2


if __name__=='__main__': sys.exit(main())
