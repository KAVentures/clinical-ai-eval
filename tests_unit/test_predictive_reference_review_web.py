import json
from urllib.parse import urlencode

import pytest
from caeval import predictive_reference_review_web as w, predictive_reference_panel as panel, predictive_research as pred
from test_predictive_research import spec, inputs, digests
from test_assessment_workspace_web import call
from test_assessment_setup_web import put


def prepare(tmp_path, role='reader_a'):
    protocol=b'Assess the endpoint at the stated horizon.'
    sources={'a':'Synthetic <script>alert(1)</script>', 'b':'Synthetic second', 'c':'Synthetic third'}
    plan=pred.freeze({**spec(),'reference_protocol_sha256':w.sha(protocol)})
    p=panel.freeze(plan,plan['plan_sha256'],dict(coordinator='coord',reader_a='doctor-a',reader_b='doctor-b',adjudicator='doctor-c',source_inventory={k:w.sha(v.encode()) for k,v in sources.items()}))
    args=(plan,plan['plan_sha256'],p,p['panel_sha256'])
    packet=panel.primary_packet(*args,role)
    d=tmp_path/role; d.mkdir(mode=0o700)
    put(d/'packet.json',packet); put(d/'sources.json',sources)
    (d/'protocol.txt').write_bytes(protocol); (d/'protocol.txt').chmod(0o600)
    return d,args,packet


def open_app(d,packet):
    a=w.Reviewer(d,packet['packet_sha256'],packet['reader']); a.host='127.0.0.1:12345'; return a


def post(a, path, revision, label=None, **changes):
    f=dict(csrf=a.csrf,revision=revision)
    if label is not None: f['label']=label
    return call(a,path,'POST',urlencode(f).encode(),**changes)


def test_two_independent_reviewers_and_analysis(tmp_path):
    submissions={}
    for role,labels in [('reader_a',['present','absent','unknown']),('reader_b',['present','absent','present'])]:
        d,args,packet=prepare(tmp_path,role); a=open_app(d,packet)
        html=call(a)[2]; assert b'&lt;script&gt;' in html and b'<script>' not in html
        assert b'Synthetic second' not in html and b'candidate_version' not in html
        assert post(a,'/seal',0)[0]=='409 Conflict'
        for i,label in enumerate(labels): assert post(a,'/rate',i,label)[0]=='303 See Other'
        assert post(a,'/seal',3)[0]=='303 See Other'
        assert post(a,'/seal',3)[0]=='409 Conflict'
        submissions[role]=json.loads((d/'submission.json').read_text())
        assert 'Synthetic' not in json.dumps(submissions[role])
    ds={r:s['submission_sha256'] for r,s in submissions.items()}
    package=panel.finalize(*args,submissions,ds)
    b=inputs(args[0]); b['reference']=package['bundle']
    assert pred.analyze(args[0],args[1],b,digests(b))['coverage']['reference']['n_unknown']==1


def test_changed_source_protocol_and_packet(tmp_path):
    d,args,packet=prepare(tmp_path); a=open_app(d,packet)
    original=json.loads((d/'sources.json').read_text()); put(d/'sources.json',{**original,'a':'changed'})
    assert call(a)[0]=='409 Conflict' and post(a,'/rate',0,'present')[0]=='409 Conflict'
    with pytest.raises(ValueError): open_app(d,packet)
    put(d/'sources.json',original); (d/'protocol.txt').write_text('changed')
    with pytest.raises(ValueError): open_app(d,packet)


def test_stale_and_invalid_forms(tmp_path):
    d,_,packet=prepare(tmp_path); a=open_app(d,packet)
    assert post(a,'/rate',0,'present')[0]=='303 See Other'
    assert post(a,'/rate',0,'absent')[0]=='409 Conflict'
    assert post(a,'/rate',1,'bad')[0]=='409 Conflict'
    assert post(a,'/rate',1,'present',HTTP_ORIGIN='https://evil.test')[0]=='403 Forbidden'
    assert post(a,'/rate',1,'present',HTTP_AUTHORIZATION='')[0]=='401 Unauthorized'
    assert len(a.rows)==1


def test_exclusive_submission(tmp_path):
    d,_,packet=prepare(tmp_path); a=open_app(d,packet)
    for i in range(3): assert post(a,'/rate',i,'unknown')[0]=='303 See Other'
    put(d/'submission.json',{'existing':True})
    assert post(a,'/seal',3)[0]=='409 Conflict'
    assert json.loads((d/'submission.json').read_text())=={'existing':True}
    with pytest.raises(ValueError): open_app(d,packet)


def test_packet_role_and_private_paths(tmp_path):
    d,_,packet=prepare(tmp_path)
    with pytest.raises(ValueError): w.Reviewer(d,packet['packet_sha256'],'doctor-b')
    with pytest.raises(ValueError): w.Reviewer(d,'a'*64,packet['reader'])
    p=d/'sources.json'; p.rename(d/'actual.json'); p.symlink_to(d/'actual.json')
    with pytest.raises(ValueError): open_app(d,packet)


def test_real_http(tmp_path):
    from threading import Thread
    from http.client import HTTPConnection
    from wsgiref.simple_server import make_server
    d,_,packet=prepare(tmp_path); a=open_app(d,packet)
    with make_server('127.0.0.1',0,a,handler_class=w.web.QuietHandler) as server:
        a.host='127.0.0.1:'+str(server.server_port)
        t=Thread(target=server.serve_forever,daemon=True); t.start(); conn=HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        headers={'Authorization':a.auth,'Content-Type':'application/x-www-form-urlencoded','Origin':'http://'+a.host}
        try:
            conn.request('GET','/',headers=headers); r=conn.getresponse(); assert r.status==200; r.read()
            for i in range(3):
                conn.request('POST','/rate',urlencode(dict(csrf=a.csrf,revision=i,label='unknown')),headers); r=conn.getresponse(); assert r.status==303; r.read()
            conn.request('POST','/seal',urlencode(dict(csrf=a.csrf,revision=3)),headers); r=conn.getresponse(); assert r.status==303; r.read()
        finally:
            conn.close(); server.shutdown(); t.join(5)
    assert all(r['label'] is None for r in json.loads((d/'submission.json').read_text())['rows'])


def test_adjudicator_sees_only_disagreements(tmp_path):
    from test_predictive_reference_panel import primary
    d,args,_=prepare(tmp_path)
    ss,ds=primary(args,{'a':True,'b':False},{'a':False,'b':False})
    packet=panel.adjudication_packet(*args,ss,ds)
    assert [r['unit_id'] for r in packet['items']]==['a']
    put(d/'packet.json',packet)
    sources=json.loads((d/'sources.json').read_text()); put(d/'sources.json',{'a':sources['a']})
    a=open_app(d,packet); html=call(a)[2]
    assert b'Synthetic second' not in html and b'doctor-a' not in html and b'doctor-b' not in html
    assert post(a,'/rate',0,'present')[0]=='303 See Other'
    assert post(a,'/seal',1)[0]=='303 See Other'
    s=json.loads((d/'submission.json').read_text())
    receipt=panel.finalize(*args,ss,ds,s,s['submission_sha256'])['receipt']
    assert receipt['resolution']==dict(consensus=1,adjudicated=1,disagreement_unresolved=0,primary_incomplete=1)


def test_extra_source_and_incomplete_inventory_refused(tmp_path):
    d,_,packet=prepare(tmp_path); s=json.loads((d/'sources.json').read_text())
    for changed in ({**s,'other':'extra'}, {'a':s['a']}):
        put(d/'sources.json',changed)
        with pytest.raises(ValueError): open_app(d,packet)
