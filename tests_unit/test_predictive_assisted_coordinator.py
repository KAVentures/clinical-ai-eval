import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from caeval import predictive_assisted_coordinator as c, predictive_assisted_trial as t
from caeval import predictive_assisted_reader as reader, predictive_reference_handoff as handoff
from caeval import predictive_research as pred, predictive_reference_review_web as ref
from test_predictive_reference_handoff import setup as reference_setup, primary, review
from test_predictive_assisted_reader import post
from test_assessment_setup_web import put


@pytest.fixture
def scope(reference_setup, tmp_path):
    a, reference_root = reference_setup
    directory = a[0]
    for key in ('decision','assistance'):
        f=directory/(key+'-protocol.txt'); f.write_text(key+' protocol'); f.chmod(0o600)
    put(directory/'decisions.json', {u:'decision '+u for u in 'abc'})
    put(directory/'assistance.json', {u:'AI assistance '+u for u in 'abc'})
    settings=dict(clinicians=['clinician-a','clinician-b'],cluster_by_unit={'a':'x','b':'y','c':'y'},n_assisted=1,randomization_test=True)
    d=c.preview(*a,settings)
    c.freeze(*a,settings,d['design_sha256'])
    reader_root=tmp_path/'trial-readers'; reader_root.mkdir(mode=0o700)
    return a, d, settings, reference_root, reader_root


def allocate(scope, monkeypatch):
    a,d,*_=scope
    monkeypatch.setattr(t,'randbelow',lambda n:0)
    al=c.draw(*a,d['design_sha256'],confirmed=True)
    return (*a,d['design_sha256'],al['allocation_sha256'])


def submissions(scope, args):
    a,d,settings,reference_root,reader_root=scope
    ss=primary(a,reference_root)
    ad=review(a,reference_root,'adjudicator',['present'],ss)
    rp=handoff.result(*a,ss,ad)
    digests={}
    for clinician in settings['clinicians']:
        info=c.export(*args,clinician,reader_root)
        folder=reader_root/('reader-'+clinician)
        assert set(json.loads((folder/'assistance.json').read_text()))=={'a'}
        app=reader.Reader(folder,info['packet_sha256'],clinician); app.host='127.0.0.1:12345'
        for i,v in enumerate(['act','do_not_act','unknown']): assert post(app,'/rate',i,v)[0]=='303 See Other'
        assert post(app,'/seal',3)[0]=='303 See Other'
        val=json.loads((folder/'submission.json').read_text())
        put(a[0]/('trial-reader-'+clinician+'-submission.json'),val)
        digests[clinician]=val['submission_sha256']
    return dict(expected_primary=ss,expected_adjudication=ad,expected_reference=rp['receipt']['receipt_sha256'],
                expected_submissions=digests,randomization_assumptions_declared=True)


def test_coordinator_end_to_end(scope,monkeypatch):
    args=allocate(scope,monkeypatch); cp=submissions(scope,args)
    package=c.result(*args,cp)
    assert package['report']['n_fully_observed_cases']==2
    assert package['report']['conditional_sharp_null_test'] is None
    assert c.seal(*args,cp,package['package_sha256'])==package
    assert c.verify_package(package,package['package_sha256'])==package['report']
    assert 'AI assistance a' not in str(package) and 'first source' not in str(package)
    with pytest.raises(FileExistsError): c.seal(*args,cp,package['package_sha256'])


def test_single_draw_concurrent(scope,monkeypatch):
    a,d,*_=scope; calls=[]
    monkeypatch.setattr(t,'randbelow',lambda n: calls.append(n) or 0)
    def run():
        try: return c.draw(*a,d['design_sha256'],confirmed=True)
        except FileExistsError: return None
    with ThreadPoolExecutor(2) as ex: results=list(ex.map(lambda _:run(),range(2)))
    assert len(calls)==1 and sum(x is not None for x in results)==1


def test_failed_draw_reserves_slot(scope,monkeypatch):
    a,d,*_=scope
    def fail(n): raise RuntimeError('interrupted')
    monkeypatch.setattr(t,'randbelow',fail)
    with pytest.raises(RuntimeError): c.draw(*a,d['design_sha256'],confirmed=True)
    assert (a[0]/'assisted-allocation.json').exists()
    monkeypatch.setattr(t,'randbelow',lambda n:0)
    with pytest.raises(FileExistsError): c.draw(*a,d['design_sha256'],confirmed=True)


def test_scope_and_source_changes(scope,monkeypatch):
    a,d,settings,_,root=scope
    with pytest.raises(ValueError): c.draw(*a,d['design_sha256'])
    with pytest.raises(ValueError): c.freeze(*a,settings,'0'*64)
    args=allocate(scope,monkeypatch)
    put(a[0]/'assistance.json',{'a':'changed','b':'AI assistance b','c':'AI assistance c'})
    with pytest.raises(ValueError): c.export(*args,'clinician-a',root)
    assert not list(root.iterdir())


def test_reject_tampered_checkpoint_and_package(scope,monkeypatch):
    args=allocate(scope,monkeypatch); cp=submissions(scope,args)
    package=c.result(*args,cp)
    with pytest.raises(ValueError): c.seal(*args,cp,'0'*64)
    with pytest.raises(ValueError): c.result(*args,{**cp,'expected_reference':'0'*64})
    with pytest.raises(ValueError): c.result(*args,{**cp,'expected_submissions':{'clinician-a':'0'*64}})
    altered={**package,'report':{**package['report'],'n_fully_observed_cases':3}}
    altered['package_sha256']=pred.digest({k:v for k,v in altered.items() if k!='package_sha256'})
    with pytest.raises(ValueError): c.verify_package(altered,altered['package_sha256'])


def test_cli_and_retained_checkpoint(scope,monkeypatch,capsys):
    a,d,settings,_,root=scope
    p=a[0]/'plan.json'; put(p,a[1]); st=a[0]/'settings.json'; put(st,settings)
    common=['--directory',str(a[0]),'--plan',str(p),'--expected-plan',a[2],'--expected-panel',a[3]]
    assert c.main(['preview',*common,'--settings',str(st)])==0
    assert json.loads(capsys.readouterr().out)['design_sha256']==d['design_sha256']
    monkeypatch.setattr(t,'randbelow',lambda n:0)
    assert c.main(['draw',*common,'--expected-design',d['design_sha256'],'--confirm-single-draw'])==0
    al=json.loads(capsys.readouterr().out)
    args=(*a,d['design_sha256'],al['allocation_sha256']); cp=submissions(scope,args)
    f=a[0]/'checkpoints.json'; put(f,cp)
    final=[*common,'--expected-design',d['design_sha256'],'--expected-allocation',al['allocation_sha256'],'--checkpoints',str(f)]
    assert c.main(['result',*final])==0; out=json.loads(capsys.readouterr().out)
    assert c.main(['seal',*final,'--expected-package',out['package_sha256']])==0; capsys.readouterr()
    assert c.main(['verify','--package',str(a[0]/'assisted-trial-package.json'),'--expected-package',out['package_sha256']])==0
    assert json.loads(capsys.readouterr().out)==out['report']
