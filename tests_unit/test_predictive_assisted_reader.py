import json
from urllib.parse import urlencode

import pytest
from caeval import predictive_assisted_reader as w, predictive_assisted_trial as t
from test_predictive_assisted_trial import setup
from test_assessment_setup_web import put
from test_assessment_workspace_web import call


@pytest.fixture
def scope(tmp_path):
    a,kw=setup(); d=tmp_path/'source'; d.mkdir(mode=0o700)
    root=tmp_path/'readers'; root.mkdir(mode=0o700)
    decisions={u:'decision '+u+' <script>' for u in 'abc'}
    assistance={u:'assistance '+u for u in 'abc'}
    dp='Decision protocol'; ap='Assistance protocol'
    for name,val in [('decision',dp),('assistance',ap)]:
        p=d/(name+'-protocol.txt'); p.write_text(val); p.chmod(0o600)
    put(d/'decisions.json',decisions); put(d/'assistance.json',assistance)
    spec={**a[-2]['spec'],'decision_protocol_sha256':w.ref.sha(dp.encode()),'assistance_protocol_sha256':w.ref.sha(ap.encode()),
          'decision_input_sha256':{u:w.ref.sha(v.encode()) for u,v in decisions.items()},'assistance_input_sha256':{u:w.ref.sha(v.encode()) for u,v in assistance.items()}}
    design=t.freeze(*a[:4],spec); a=(*a[:4],design,design['design_sha256']); alloc=t.allocate(*a,0)
    kw.update(allocation=alloc,allocation_digest=alloc['allocation_sha256']); return a,kw,d,root


def export(s, clinician='clinician-a'):
    a,kw,d,root=s; info=w.export(*a,allocation=kw['allocation'],allocation_digest=kw['allocation_digest'],clinician=clinician,source_directory=d,output_root=root)
    path=root/('reader-'+clinician); app=w.Reader(path,info['packet_sha256'],clinician); app.host='127.0.0.1:12345'
    return path,app


def post(app,path,revision,action=None,**changes):
    f=dict(csrf=app.csrf,revision=revision)
    if action is not None: f['action']=action
    return call(app,path,'POST',urlencode(f).encode(),**changes)


def test_two_readers_native_trial_analysis(scope):
    a,kw,_,_=scope; ss={}
    for clinician in a[-2]['spec']['clinicians']:
        d,app=export(scope,clinician)
        assert json.loads((d/'assistance.json').read_text())=={'a':'assistance a'}
        html=call(app)[2]; assert b'assistance a' in html and b'&lt;script&gt;' in html and b'assistance b' not in html
        assert post(app,'/rate',0,'act')[0]=='303 See Other'
        html=call(app)[2]; assert b'assistance a' not in html and b'Assistance protocol' not in html and b'assistance b' not in html
        assert post(app,'/rate',1,'do_not_act')[0]=='303 See Other'
        assert post(app,'/rate',2,'unknown')[0]=='303 See Other'
        assert post(app,'/seal',3)[0]=='303 See Other'
        ss[clinician]=json.loads((d/'submission.json').read_text())
        assert 'decision a' not in str(ss[clinician])
    kw.update(submissions=ss,expected_submissions={u:v['submission_sha256'] for u,v in ss.items()})
    report=t.analyze(*a,**kw,randomization_assumptions_declared=True)
    assert report['n_fully_observed_cases']==2 and report['conditional_sharp_null_test'] is None
    assert t.verify(*a,**kw,randomization_assumptions_declared=True,report=report,expected_report=report['report_sha256'])


def test_stale_and_auth(scope):
    _,app=export(scope)
    assert post(app,'/seal',0)[0]=='409 Conflict'
    assert post(app,'/rate',0,'act',HTTP_AUTHORIZATION='')[0]=='401 Unauthorized'
    assert post(app,'/rate',0,'act',HTTP_ORIGIN='https://evil.test')[0]=='403 Forbidden'
    assert post(app,'/rate',0,'act')[0]=='303 See Other'
    assert post(app,'/rate',0,'do_not_act')[0]=='409 Conflict'
    assert len(app.rows)==1


def test_tampering_and_extra_control_source(scope):
    d,app=export(scope)
    put(d/'assistance.json',{'a':'assistance a','b':'assistance b'})
    assert call(app)[0]=='409 Conflict' and post(app,'/rate',0,'act')[0]=='409 Conflict'
    put(d/'assistance.json',{'a':'changed'})
    with pytest.raises(ValueError): w.Reader(d,app.expected,app.clinician)


def test_exclusive_and_protocol(scope):
    d,app=export(scope)
    with pytest.raises(FileExistsError): export(scope)
    for i in range(3): assert post(app,'/rate',i,'unknown')[0]=='303 See Other'
    put(d/'submission.json',{'original':True})
    assert post(app,'/seal',3)[0]=='409 Conflict'
    assert json.loads((d/'submission.json').read_text())=={'original':True}
    (d/'decision-protocol.txt').write_text('changed')
    assert call(app)[0]=='409 Conflict'


def test_wrong_source_and_role(scope):
    a,kw,d,root=scope
    put(d/'decisions.json',{'a':'wrong'})
    with pytest.raises(ValueError): export(scope)
    assert not list(root.iterdir())
    with pytest.raises(ValueError): w.export(*a,allocation=kw['allocation'],allocation_digest=kw['allocation_digest'],clinician='doctor-a',source_directory=d,output_root=root)


def test_real_http(scope):
    from threading import Thread
    from http.client import HTTPConnection
    from wsgiref.simple_server import make_server
    d,app=export(scope)
    with make_server('127.0.0.1',0,app,handler_class=w.web.QuietHandler) as server:
        app.host='127.0.0.1:'+str(server.server_port)
        worker=Thread(target=server.serve_forever,daemon=True); worker.start(); conn=HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        headers={'Authorization':app.auth,'Content-Type':'application/x-www-form-urlencoded','Origin':'http://'+app.host}
        try:
            conn.request('GET','/',headers=headers); r=conn.getresponse(); assert r.status==200; r.read()
            for i in range(3):
                conn.request('POST','/rate',urlencode(dict(csrf=app.csrf,revision=i,action='unknown')),headers); r=conn.getresponse(); assert r.status==303; r.read()
            conn.request('POST','/seal',urlencode(dict(csrf=app.csrf,revision=3)),headers); r=conn.getresponse(); assert r.status==303; r.read()
        finally:
            conn.close(); server.shutdown(); worker.join(5)
    assert all(r['action'] is None for r in json.loads((d/'submission.json').read_text())['rows'])
