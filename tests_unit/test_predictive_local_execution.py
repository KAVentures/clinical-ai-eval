import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest
from caeval import predictive_local_execution as w, predictive_research as p
from test_predictive_research import spec, inputs, digests


@pytest.fixture
def server():
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            assert self.path=='/predict'
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(data)
            answer={k:data[k] for k in ('request_sha256','unit_id','model_version')}
            answer['probability_bp']=2000
            if data['unit_id']=='b': answer['model_version']='wrong'
            if data['unit_id']=='c':
                self.send_response(302); self.send_header('Location','http://example.test/'); self.end_headers(); return
            raw=json.dumps(answer).encode()
            self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    with HTTPServer(('127.0.0.1',0),Handler) as s:
        t=Thread(target=s.serve_forever,daemon=True); t.start()
        try: yield s.server_port,seen
        finally: s.shutdown(); t.join(timeout=5)


def setup(default_port, **changes):
    plan=p.freeze(spec()); rows=[{'unit_id':u,'features':{'score':None if u=='a' else 1}} for u in plan['spec']['unit_ids']]
    options=dict(role='candidate',port=default_port,feature_names=['score'],feature_protocol_sha256='a'*64,max_calls=3,timeout_seconds=2,max_seconds=20)
    options.update(changes)
    ex=w.preview(plan,plan['plan_sha256'],options,rows)
    return plan,rows,options,ex


def args(port,**changes):
    plan,rows,_,ex=setup(port,**changes)
    return plan,plan['plan_sha256'],ex,ex['execution_sha256'],rows


def test_loopback_to_native_bundle(server):
    port,seen=server; a=args(port)
    r=w.execute(*a,confirmed=True)
    assert [x['status'] for x in r['results']]==['ok','invalid_response','http_error']
    assert len(seen)==3 and all(set(x)=={'schema','unit_id','model_version','endpoint','horizon','features','request_sha256'} for x in seen)
    assert all(x['request_sha256']==p.digest({k:v for k,v in x.items() if k!='request_sha256'}) for x in seen)
    b=w.seal(*a,r,r['receipt_sha256']); bs=inputs(a[0]); bs['candidate']=b
    report=p.analyze(a[0],a[1],bs,digests(bs))
    assert report['coverage']['candidate']['n_unknown']==2
    assert not r['independent_model_identity_verified'] and not r['decision_grade']


def test_explicit_approval_and_binding(server):
    port,seen=server; a=args(port)
    with pytest.raises(ValueError): w.execute(*a)
    with pytest.raises(ValueError): w.execute(*a[:3],'a'*64,a[4],confirmed=True)
    a[4][0]['features']['score']=5
    with pytest.raises(ValueError): w.execute(*a,confirmed=True)
    assert not seen


def test_budget(server):
    port,seen=server; a=args(port,max_calls=1)
    r=w.execute(*a,confirmed=True)
    assert len(seen)==1 and r['counts']['not_attempted_budget']==2
    assert len(w.seal(*a,r,r['receipt_sha256'])['rows'])==3


@pytest.mark.parametrize('changes',[{'role':'reference'},{'port':True},{'port':65536},{'max_calls':4},{'timeout_seconds':31},{'max_seconds':0},{'feature_names':['score','score']}])
def test_options(changes):
    with pytest.raises(ValueError): setup(12345,**changes)


@pytest.mark.parametrize('bad',[{'score':'text'},{'score':float('nan')},{'score':1e16},{'score':0,'other':True}])
def test_invalid_features(bad):
    plan,rows,opt,_=setup(12345); rows[0]['features']=bad
    with pytest.raises(ValueError): w.preview(plan,plan['plan_sha256'],opt,rows)


def test_missing_or_duplicate_unit():
    plan,rows,opt,_=setup(12345)
    for value in (rows[:-1], [rows[0],rows[0],rows[2]]):
        with pytest.raises(ValueError): w.preview(plan,plan['plan_sha256'],opt,value)
    assert w.preview(plan,plan['plan_sha256'],opt,rows[::-1])==w.preview(plan,plan['plan_sha256'],opt,rows)


def test_receipt_tampering(server):
    port,_=server; a=args(port); r=w.execute(*a,confirmed=True); d=r['receipt_sha256']
    r['results'][1]['probability_bp']=0
    with pytest.raises(ValueError): w.seal(*a,r,d)
    r['receipt_sha256']=p.digest({k:v for k,v in r.items() if k!='receipt_sha256'})
    with pytest.raises(ValueError): w.seal(*a,r,r['receipt_sha256'])


def test_cli(server,tmp_path,capsys):
    port,seen=server; plan,rows,opt,ex=setup(port)
    def put(name,obj):
        q=tmp_path/name; q.write_text(json.dumps(obj)); return str(q)
    common=['--plan',put('plan',plan),'--expected-plan',plan['plan_sha256'],'--rows',put('rows',rows)]
    exe=str(tmp_path/'execution'); receipt=str(tmp_path/'receipt'); bundle=str(tmp_path/'bundle')
    assert w.main(['preview',*common,'--options',put('options',opt),'--output',exe])==0
    assert capsys.readouterr().out.strip()==ex['execution_sha256']
    run=['run',*common,'--execution',exe,'--expected-execution',ex['execution_sha256'],'--output',receipt]
    assert w.main(run)==2 and not seen
    assert w.main(run+['--confirm-loopback'])==0
    d=capsys.readouterr().out.strip(); assert len(seen)==3
    assert w.main(run+['--confirm-loopback'])==2 and len(seen)==3
    assert w.main(['seal',*common,'--execution',exe,'--expected-execution',ex['execution_sha256'],'--receipt',receipt,'--expected-receipt',d,'--output',bundle])==0
    assert json.load(open(bundle))['source_receipt_sha256']==d
