"""Disposable, all-mock remote MCP acceptance rehearsal using the native evaluator."""
import argparse
import json
from http.client import HTTPConnection
from pathlib import Path
import tempfile
from threading import Thread
from wsgiref.simple_server import make_server

from . import buyer_agent as buyer, buyer_plan, buyer_manual as manual
from . import buyer_assessment as assessment, buyer_jobs as jobs
from . import evaluator_service as api, evaluator_remote_mcp as mcp
from . import institution_access as access, intake, pipeline
from .util import repo_root

ORIGIN = 'https://eval.synthetic.invalid'
HOST = 'eval.synthetic.invalid'
KEY = 'synthetic-request-0001'


def require(ok):
    if not ok:
        raise ValueError('native evaluator acceptance check failed')


def prepare(root):
    """Build only shipped synthetic inputs; never accept an existing workspace."""
    from targets import demo_target
    buyer.init(root, 'Synthetic MCP acceptance', 'Decision support', 'ED', 'SE')
    buyer.add_vendor(root, 'fixture', 'Synthetic fixture, not a clinical product')
    plan = buyer_plan.preview(root, {
        'vendor_id':'fixture','target_name':'Synthetic fixture','target_version':'1.0',
        'profiles':['clinician_decision_support'],
        'answers':{q:'Synthetic answer '+q for q in intake.INTAKE_QUESTIONS}})
    pack = root/'approved-packs'/'fixture'
    pack.mkdir(parents=True,mode=0o700)
    (pack/'cases.json').write_text(json.dumps(demo_target.base_cases()),encoding='utf-8')
    template = manual.prepare(root,plan['plan_id'],'fixture')
    template['submission']['operator']='Synthetic operator'
    template['submission']['tested_product_version']='1.0'
    for row in template['submission']['answers']:
        row.update(status='provided',answer='Synthetic answer')
    submission=manual.import_response(root,json.dumps(template).encode())
    panel_dir=root/'approved-panels'; panel_dir.mkdir(mode=0o700)
    panel=panel_dir/'mock.toml'
    panel.write_bytes((repo_root()/'configs'/'judge_panel.toml').read_bytes())
    judges=pipeline.load_panel(panel)['judges']
    require(bool(judges) and all(j.get('mock') is True for j in judges))
    gov={'influences_diagnosis_or_treatment':'yes','clinician_reviews_every_response':'yes',
         'regulated_medical_device':'unsure','deployment_jurisdictions':'SE'}
    bound=assessment.prepare(root,submission['submission_id'],'mock',gov)
    require(bound['mode']=='demonstration' and bound['network_authorized'] is False
            and bound['decision_grade'] is False)
    access.provision(root,'operator','operator','Synthetic acceptance password')
    return bound['assessment_id']


class Client:
    """Minimal protocol probe, not a general remote MCP client."""
    def __init__(self,port,token):
        self.port=port; self.token=token; self.counter=0

    def request(self,method,params=None,*,notification=False,token=None):
        self.counter+=1
        doc={'jsonrpc':'2.0','method':method,'params':params or {}}
        if not notification: doc['id']=self.counter
        headers={'Host':HOST,'Authorization':'Bearer '+(self.token if token is None else token),
                 'Accept':'application/json, text/event-stream','Content-Type':'application/json',
                 'MCP-Protocol-Version':'2025-06-18'}
        conn=HTTPConnection('127.0.0.1',self.port,timeout=30)
        try:
            conn.request('POST','/mcp',mcp.encoded(doc),headers)
            response=conn.getresponse(); raw=response.read(2*mcp.MAX_REPLY+1)
            require(len(raw)<=2*mcp.MAX_REPLY and response.getheader('Cache-Control')=='no-store')
            if notification:
                require(response.status==202 and raw==b''); return None
            require(response.status==200)
            result=mcp.decode(raw)
            require(isinstance(result,dict) and result.get('jsonrpc')=='2.0' and result.get('id')==self.counter)
            require(('result' in result)!=('error' in result))
            return result
        finally: conn.close()

    def tool(self,name,args=None,*,denied=False,token=None):
        doc=self.request('tools/call',{'name':name,'arguments':args or {}},token=token)
        if denied:
            require('error' in doc); return None
        require('result' in doc)
        result=doc['result']
        require(result.get('isError') is False and len(result['content'])==1 and result['content'][0]['type']=='text')
        value=mcp.decode(result['content'][0]['text'].encode('utf-8'))
        require(isinstance(value,dict)); return value


def rehearse():
    """Run an isolated demonstration. No model endpoints, real records or credentials."""
    with tempfile.TemporaryDirectory(prefix='caeval-native-mcp-') as tmp:
        root=Path(tmp)/'buyer'; aid=prepare(root)
        grant=api.issue(root,'operator',aid,3600,confirmed=True)
        other=api.issue(root,'operator',aid,3600,confirmed=True)
        with make_server('127.0.0.1',0,mcp.App(ORIGIN,api.App(root))) as server:
            # Never log bearer grants or model outputs.
            server.RequestHandlerClass=mcp.gateway.QuietHandler
            thread=Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                c=Client(server.server_port,grant['token'])
                hello=c.request('initialize',{'protocolVersion':'2025-06-18','capabilities':{},
                        'clientInfo':{'name':'synthetic-acceptance','version':'1'}})
                require(hello['result']['protocolVersion']=='2025-06-18')
                c.request('notifications/initialized',notification=True)
                tools=c.request('tools/list')['result']['tools']
                require(len(tools)==5 and {t['name'] for t in tools}==set(mcp.TOOLS))
                require(c.tool('discover_assessment')['assessment_id']==aid)
                require(c.tool('list_evaluation_requests')['requests']==[])
                row=c.tool('request_evaluation',{'idempotency_key':KEY})
                rid=row['request_id']; args={'request_id':rid}
                require(row['status']=='awaiting_local_approval' and row['execution_authorized_by_api'] is False)
                require(c.tool('request_evaluation',{'idempotency_key':KEY})==row)
                require(c.tool('list_evaluation_requests')['requests']==[row])
                require(c.tool('evaluation_status',args)==row and not jobs.list_jobs(root))
                c.tool('evaluation_receipt',args,denied=True)
                c.tool('evaluation_status',args,token=other['token'],denied=True)
                c.tool('execute',args,denied=True)
                require(not jobs.list_jobs(root))
                # Separate trusted local action, never exposed by the MCP server.
                approved=api.approve_queue(root,rid,confirmed=True)
                jid=approved['job_id']
                require(approved['status']=='queued' and api.approve_queue(root,rid,confirmed=True)['status']=='already_approved')
                inventory=jobs.list_jobs(root)
                require(len(inventory)==1 and inventory[0]['id']==jid and inventory[0]['real_judges'] is False)
                require(c.tool('evaluation_status',args)['status']=='queued')
                job=jobs.execute(root,jid)
                require(job['status']=='completed' and job['decision_grade'] is False)
                status=c.tool('evaluation_status',args)
                require(status['status']=='completed' and status['packages'])
                receipt=c.tool('evaluation_receipt',args)
                # Compare the received receipt against an independently retrieved
                # native receipt, then use the native strict metadata verifier.
                local=api.receipt(root,grant['token'],rid)
                require(receipt==local and api.verify_receipt(receipt,local['digest'])['status']=='METADATA_INTEGRITY_VERIFIED')
                require(all(set(p)=={'family_id','manifest_hash','claim','n_responses','review_count'}
                            and p['claim'] in ('none','demonstration') for p in status['packages']))
                c.tool('evaluation_receipt',args,token=other['token'],denied=True)
                n=len(status['packages'])
                api.revoke(root,grant['grant_id'])
                require('error' in c.request('tools/list'))
                c.tool('evaluation_status',args,denied=True)
            finally:
                server.shutdown(); thread.join(timeout=5)
                require(not thread.is_alive())
    return {'schema':'native-remote-mcp-acceptance/1','protocol':'2025-06-18',
            'native_grant':'passed','request_idempotency':'passed','explicit_local_approval':'passed',
            'mock_evaluation':'passed','metadata_receipt_integrity':'passed','grant_isolation_and_revocation':'passed',
            'n_verified_packages':n,'real_client_tested':False,'tls_proxy_tested':False,
            'clinical_validation_established':False,'decision_grade':False}


def main():
    try:
        print(json.dumps(rehearse(),sort_keys=True)); return 0
    except (OSError,ValueError,TypeError,KeyError,RuntimeError,ImportError):
        print('Native remote MCP rehearsal failed. Check the approved installation and synthetic fixtures.')
        return 2


if __name__=='__main__':
    raise SystemExit(main())
