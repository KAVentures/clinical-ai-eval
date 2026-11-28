"""Restricted HTTP agent entrypoint behind a separately approved HTTPS proxy."""
import argparse
import json
import re
import sys
from urllib.parse import urlsplit
from wsgiref.simple_server import make_server, WSGIRequestHandler

from . import hospital_launcher as launcher

CONTRACT = '/agent-contract'
REQUESTS = '/v1/evaluation-requests'
DETAIL = re.compile(r'/v1/evaluation-requests/[a-f0-9]{32}(?:/receipt)?')
HOST = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?')


def approved_host(origin):
    p = urlsplit(launcher.https_origin(origin))
    launcher.require(len(p.hostname) <= 253 and all(HOST.fullmatch(x) for x in p.hostname.split('.')))
    launcher.require(p.netloc == p.hostname or p.netloc == p.hostname+':'+str(p.port))
    return p.netloc


def contract():
    return {'schema':'clinical-ai-eval-agent-contract/1', 'authentication':'scoped_bearer_grant',
            'transport':'approved_https_reverse_proxy', 'client_installation_required':False,
            'operations':[{'method':'GET','path':'/v1/assessment','purpose':'discover granted assessment'},
                          {'method':'GET','path':REQUESTS,'purpose':'list owned requests'},
                          {'method':'POST','path':REQUESTS,'body':{'idempotency_key':'16–80 ASCII letters, digits, underscore or hyphen'},'purpose':'request local human approval'},
                          {'method':'GET','path':REQUESTS+'/{request_id}','purpose':'read owned request status'},
                          {'method':'GET','path':REQUESTS+'/{request_id}/receipt','purpose':'read completed verified metadata receipt'}],
            'request_id_format':'32 lowercase hexadecimal characters', 'success_envelope':{'ok':True,'result':'object'},
            'raw_package_download':False,'clinical_records':False,'remote_execution':False,
            'human_approval_required':True,'clinical_validation_established':False,'decision_grade':False}


def allowed(method,path):
    return ((method=='GET' and (path in ('/v1/assessment',REQUESTS) or DETAIL.fullmatch(path) is not None))
            or (method=='POST' and path==REQUESTS))


class Gateway:
    def __init__(self, origin, evaluator):
        self.host=approved_host(origin); self.evaluator=evaluator

    def __call__(self,env,start):
        def reply(status,doc):
            body=json.dumps(doc,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii')
            start(status,[('Content-Type','application/json'),('Content-Length',str(len(body))),
                          ('Cache-Control','no-store'),('X-Content-Type-Options','nosniff')])
            return [body]
        denied={'ok':False,'error':'Agent request denied'}
        if (env.get('REMOTE_ADDR')!='127.0.0.1' or env.get('HTTP_HOST')!=self.host or env.get('QUERY_STRING')
                or env.get('SCRIPT_NAME') or env.get('HTTP_COOKIE') or env.get('HTTP_ORIGIN')
                or env.get('HTTP_TRANSFER_ENCODING')):
            return reply('403 Forbidden',denied)
        method,path=env.get('REQUEST_METHOD'),env.get('PATH_INFO','')
        if method=='GET' and env.get('CONTENT_LENGTH') not in (None,'','0'):
            return reply('403 Forbidden',denied)
        if method=='GET' and path==CONTRACT:
            # Static public capability description. Never inspect a workspace.
            return reply('200 OK',contract())
        if not allowed(method,path): return reply('403 Forbidden',denied)
        auth=env.get('HTTP_AUTHORIZATION','')
        if not isinstance(auth,str) or not auth.startswith('Bearer '): return reply('403 Forbidden',denied)
        # The existing service remains authoritative for scope, expiry, audit,
        # idempotency, input validation, status and completed receipt verification.
        return self.evaluator(env,start)


class QuietHandler(WSGIRequestHandler):
    def log_message(self,*args):
        pass


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace',required=True); p.add_argument('--origin',required=True)
    p.add_argument('--port',type=int,default=8772)
    a=p.parse_args(argv)
    try:
        launcher.require(1024<=a.port<=65535)
        directory=launcher.private(a.workspace)
        from . import evaluator_service
        app=Gateway(a.origin,evaluator_service.App(str(directory)))
        with make_server('127.0.0.1',a.port,app,handler_class=QuietHandler) as server:
            print('Agent gateway bound to loopback; approved HTTPS proxy required.',flush=True)
            server.serve_forever()
        return 0
    except KeyboardInterrupt: return 0
    except (OSError,ValueError,TypeError,ImportError):
        print('Agent gateway startup refused.',file=sys.stderr); return 2


if __name__=='__main__':
    sys.exit(main())
