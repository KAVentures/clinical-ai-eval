import copy
import json
import pytest
from caeval import assessment_workbench as w, predictive_research as pred, vendor_workbench as vendor, vendor_submission as sub
from test_prospective_research import setup, outcomes, KEY, SITES
from test_predictive_research import spec


@pytest.fixture
def context(tmp_path):
    bp, manifest = setup()
    pp = pred.freeze({**spec(), 'unit_ids':['u2','u3','u4']})
    request = sub.freeze(dict(request_id='r',buyer='hospital',vendor='vendor',product='ai',version='v2',
                              requirements=[dict(id='safety',mandatory=True,description='Report')]))
    db = tmp_path/'local.db'
    root = vendor.init(db,request,request['request_sha256'],'operator','reviewer','representative')
    plan = w.freeze(request,request['request_sha256'],root,pp,bp)
    bundles = {r:pred.seal(pp,pp['plan_sha256'],r,'c'*64,[]) for r in pred.ROLES}
    args = dict(db=db,head=root,as_of='2026-01-03',predictive_plan=pp,bundles=bundles,
                expected_bundles={r:b['bundle_sha256'] for r,b in bundles.items()},binary_plan=bp,manifest=manifest,
                audit_key=KEY,site_keys=SITES,outcomes=outcomes(),reference_counts={'age':{'a':2,'b':0}},target_counts={'age':{'a':0,'b':2}})
    return plan,args,request


def test_coordinated_replay(context):
    p,a,_=context; r=w.analyze(p,p['plan_sha256'],**a)
    assert r['n_planned']==3 and r['summary']['mandatory_missing']==['safety']
    assert r['endpoints']=={'predictive':'event','binary':'harm'}
    assert r['summary']['predictive_coverage']['reference']['n_unknown']==3
    assert not r['deployment_authorized'] and not r['decision_grade']
    assert w.verify(p,p['plan_sha256'],r,r['report_sha256'],**a)
    d=r['report_sha256']; r['summary']['mandatory_missing']=[]
    assert not w.verify(p,p['plan_sha256'],r,d,**a)


@pytest.mark.parametrize('change',[{'study':'other'},{'candidate_version':'v3'},{'baseline_version':'v0'},{'unit_ids':['u2','u3','other']}])
def test_scope_mismatch(context,change):
    p,a,r=context; pp=pred.freeze({**a['predictive_plan']['spec'],**change})
    with pytest.raises(ValueError): w.freeze(r,r['request_sha256'],p['vendor_root'],pp,a['binary_plan'])


def test_tampering_and_stale_head(context):
    p,a,r=context; q=copy.deepcopy(p); q['n_planned']=1
    with pytest.raises(ValueError): w.analyze(q,p['plan_sha256'],**a)
    archive=sub.build(r,r['request_sha256'],{'safety':{'status':'supplied','explanation':''}},{'safety':b'doc'})
    head=vendor.import_submission(a['db'],p['vendor_root'],a['head'],archive)
    with pytest.raises(ValueError): w.analyze(p,p['plan_sha256'],**a)
    a['head']=head
    assert w.analyze(p,p['plan_sha256'],**a)['summary']['mandatory_missing']==[]


def test_wrong_input_checkpoint(context):
    p,a,_=context; a['expected_bundles']['reference']='d'*64
    with pytest.raises(ValueError): w.analyze(p,p['plan_sha256'],**a)


def test_cli(context,tmp_path,capsys):
    p,a,r=context
    def write(name,obj):
        path=tmp_path/name; path.write_text(json.dumps(obj)); path.chmod(0o600); return str(path)
    config={k:a[k] for k in ('head','as_of','expected_bundles')}; config['db']=str(a['db'])
    for k in ('predictive_plan','binary_plan','manifest','outcomes','reference_counts','target_counts'):
        config[k]=write(k,a[k])
    config['bundles']={k:write(k,v) for k,v in a['bundles'].items()}
    config['keys']=write('keys',{'audit':KEY.hex(),'sites':{k:v.hex() for k,v in SITES.items()}})
    plan=str(tmp_path/'plan'); report=str(tmp_path/'report')
    assert w.main(['freeze','--request',write('request',r),'--expected-request',r['request_sha256'],'--root',p['vendor_root'],
                   '--predictive-plan',config['predictive_plan'],'--binary-plan',config['binary_plan'],'--output',plan])==0
    assert capsys.readouterr().out.strip()==p['plan_sha256']
    args=['--plan',plan,'--expected-plan',p['plan_sha256'],'--config',write('config',config)]
    assert w.main(['analyze',*args,'--output',report])==0
    d=capsys.readouterr().out.strip()
    assert w.main(['verify',*args,'--report',report,'--expected-report',d])==0
    assert w.main(['analyze',*args,'--output',report])==2
