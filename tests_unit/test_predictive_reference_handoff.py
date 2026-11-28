import json
from pathlib import Path

import pytest
from caeval import predictive_reference_handoff as h, predictive_reference_review_web as r, predictive_research as p
from test_predictive_reference_review_web import open_app, post
from test_predictive_research import spec, inputs, digests
from test_assessment_setup_web import put


@pytest.fixture
def setup(tmp_path):
    directory=tmp_path/'coordinator'; directory.mkdir(mode=0o700)
    root=tmp_path/'packets'; root.mkdir(mode=0o700)
    protocol=b'Synthetic protocol for outcome review.'
    (directory/'reference-protocol.txt').write_bytes(protocol); (directory/'reference-protocol.txt').chmod(0o600)
    put(directory/'reference-sources.json',{'a':'first source','b':'second source','c':'third source'})
    plan=p.freeze({**spec(),'reference_protocol_sha256':r.sha(protocol)})
    actors=dict(coordinator='coord',reader_a='doctor-a',reader_b='doctor-b',adjudicator='doctor-c')
    args=(directory,plan,plan['plan_sha256'])
    panel=h.preview(*args,actors)
    h.freeze(*args,actors,panel['panel_sha256'])
    return (*args,panel['panel_sha256']),root


def review(args,root,role,labels,ds=None):
    info=h.export(*args,root,role,primary_digests=ds)
    path=root/role; packet=json.loads((path/'packet.json').read_text())
    a=open_app(path,packet)
    for i,label in enumerate(labels): assert post(a,'/rate',i,label)[0]=='303 See Other'
    assert post(a,'/seal',len(labels))[0]=='303 See Other'
    s=json.loads((path/'submission.json').read_text())
    put(args[0]/(role+'-submission.json'),s)
    assert info['n_assigned']==len(labels)
    return s['submission_sha256']


def primary(args,root):
    return {'reader_a':review(args,root,'reader_a',['present','absent','unknown']),
            'reader_b':review(args,root,'reader_b',['absent','absent','present'])}


def test_full_reference_handoff(setup):
    args,root=setup; ds=primary(args,root)
    assert set(p.name for p in (root/'reader_a').iterdir())=={'packet.json','protocol.txt','sources.json','submission.json'}
    d=review(args,root,'adjudicator',['present'],ds)
    assert json.loads((root/'adjudicator'/'sources.json').read_text())=={'a':'first source'}
    out=h.result(*args,ds,d)
    assert out['receipt']['resolution']==dict(consensus=1,adjudicated=1,disagreement_unresolved=0,primary_incomplete=1)
    assert h.stage(*args,ds,out['receipt']['receipt_sha256'],d)==out
    assert json.loads((args[0]/'reference-rows.json').read_text())==out['bundle']['rows']
    b=inputs(args[1]); b['reference']=out['bundle']
    assert p.analyze(args[1],args[2],b,digests(b))['coverage']['reference']['n_unknown']==1
    with pytest.raises(ValueError): h.stage(*args,ds,out['receipt']['receipt_sha256'],d)


def test_changed_source_refuses_handoff(setup):
    args,root=setup; ds=primary(args,root)
    put(args[0]/'reference-sources.json',{'a':'changed','b':'second source','c':'third source'})
    with pytest.raises(ValueError): h.result(*args,ds)
    with pytest.raises(ValueError): h.export(*args,root,'adjudicator',primary_digests=ds)
    assert not (root/'adjudicator').exists()


def test_wrong_receipt_and_exclusive_output(setup):
    args,root=setup; ds=primary(args,root)
    out=h.result(*args,ds); assert out['receipt']['resolution']['disagreement_unresolved']==1
    with pytest.raises(ValueError): h.stage(*args,ds,'a'*64)
    put(args[0]/'collection.json',{'existing':True})
    with pytest.raises(ValueError): h.stage(*args,ds,out['receipt']['receipt_sha256'])
    assert not (args[0]/'reference-rows.json').exists()
    with pytest.raises(FileExistsError): h.export(*args,root,'reader_a')


def test_third_not_silently_ignored(setup):
    args,root=setup; ds=primary(args,root)
    d=review(args,root,'adjudicator',['present'],ds)
    with pytest.raises(ValueError): h.result(*args,ds)
    with pytest.raises(ValueError): h.result(*args,ds,'a'*64)
    assert h.result(*args,ds,d)['receipt']['resolution']['adjudicated']==1


def test_refuses_nested_root_and_wrong_reader(setup):
    args,root=setup
    with pytest.raises(ValueError): h.export(*args,args[0],'reader_a')
    with pytest.raises(ValueError): h.export(*args,root,'wrong')
    with pytest.raises(ValueError): h.export(*args,root,'adjudicator')
    assert list(root.iterdir())==[]


def test_cli_result_and_stage(setup,capsys):
    args,root=setup; ds=primary(args,root)
    plan_path=args[0]/'plan.json'; put(plan_path,args[1])
    common=['--directory',str(args[0]),'--plan',str(plan_path),'--expected-plan',args[2],'--expected-panel',args[3],
            '--expected-reader-a',ds['reader_a'],'--expected-reader-b',ds['reader_b']]
    assert h.main(['result',*common])==0
    d=json.loads(capsys.readouterr().out)['receipt_sha256']
    assert h.main(['stage',*common,'--expected-receipt',d])==0
    assert h.main(['stage',*common,'--expected-receipt',d])==2


from test_assessment_setup_web import app
from test_assessment_workspace_web import selector, select, freeze as freeze_workspace, child_post
from test_predictive_job_web import server, stage as stage_job, post as post_job, status
from caeval import predictive_job_web as jobs


def test_verified_three_arm_collection(selector, server, tmp_path):
    m=jobs.Manager(selector.root,selector.staging_root); m.host=selector.host
    protocol=b'Synthetic protocol for outcome review.'
    d=selector.staging_root/'first'; s=json.loads((d/'predictive-spec.json').read_text())
    put(d/'predictive-spec.json',{**s,'reference_protocol_sha256':r.sha(protocol)})
    (d/'reference-protocol.txt').write_bytes(protocol); (d/'reference-protocol.txt').chmod(0o600)
    put(d/'reference-sources.json',{'u2':'first source','u3':'second source','u4':'third source'})
    assert select(m)[0]=='303 See Other'; freeze_workspace(m)
    pp=json.loads((m.active.destination/'predictive-plan.json').read_text()); args=(d,pp,pp['plan_sha256'])
    actors=dict(coordinator='coord',reader_a='doctor-a',reader_b='doctor-b',adjudicator='doctor-c')
    panel=h.preview(*args,actors); h.freeze(*args,actors,panel['panel_sha256']); args=(*args,panel['panel_sha256'])
    root=tmp_path/'reviewers'; root.mkdir(mode=0o700)
    ds=primary(args,root); ad=review(args,root,'adjudicator',['present'],ds)
    result=h.result(*args,ds,ad); receipt=result['receipt']['receipt_sha256']; h.stage(*args,ds,receipt,ad)
    port,seen=server; heads={}
    for role in jobs.ROLES:
        ex=stage_job(m,port,role,1); assert post_job(m,'init',ex,role=role)[0]=='200 OK'
        jh=jobs.job.root(ex); assert post_job(m,'batch',ex,jh,role=role)[0]=='200 OK'
        heads[role]=status(m,role,jh,True)['head']; assert post_job(m,'stage',ex,heads[role],role=role)[0]=='200 OK'
    values=h.read(d,'reference-rows'); put(d/'reference-rows.json',[])
    with pytest.raises(ValueError): h.link(m.active,args[3],ds,receipt,heads['baseline'],heads['candidate'],'2026-01-03',ad)
    assert not (d/'collection.json').exists(); put(d/'reference-rows.json',values)
    out=h.link(m.active,args[3],ds,receipt,heads['baseline'],heads['candidate'],'2026-01-03',ad)
    assert out['reference']==receipt and len(seen)==2
    from test_prospective_research import outcomes
    for k,v in {'outcomes':outcomes(),'reference-counts':{'age':{'a':2,'b':0}},'target-counts':{'age':{'a':0,'b':2}}}.items(): put(d/(k+'.json'),v)
    assert child_post(m,'/preview-collection')[0]=='200 OK'
    assert child_post(m,'/commit-collection',m.active.collection[1])[0]=='200 OK'
    for path in ('/run','/verify','/download'):
        response=child_post(m,path); assert response[0]=='200 OK'
    report=json.loads(response[2]); assert report['reports']['predictive']['inputs']['reference']['source_receipt_sha256']==receipt
    assert report['summary']['predictive_coverage']['reference']['n_unknown']==1
