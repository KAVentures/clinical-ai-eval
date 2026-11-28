import copy
import json
import shutil

import pytest
from caeval import assessment_multivendor as m, assessment_workbench as work
from caeval import predictive_research as pred, prospective_research as binary
from caeval import vendor_workbench as vendor, vendor_submission as submission
from test_assessment_workbench import context
from test_assessment_research_dossier import policy
from test_assessment_setup_web import app, freeze as freeze_app, stage_collection, post, put


def member(plan, inputs):
    p=policy(plan,False)
    return dict(plan=plan,expected_plan=plan['plan_sha256'],inputs=inputs,policy=p,expected_policy=p['policy_sha256'])


def reseal(inputs, role, rows, receipt='c'*64):
    pp=inputs['predictive_plan']
    b=pred.seal(pp,pp['plan_sha256'],role,receipt,rows)
    inputs['bundles'][role]=b; inputs['expected_bundles'][role]=b['bundle_sha256']


def result(a):
    plan,inputs=a['plan'],a['inputs']; r=work.analyze(plan,plan['plan_sha256'],**inputs)
    d=m.dossier.build(plan,a['expected_plan'],a['policy'],a['expected_policy'],r['report_sha256'],inputs)
    return dict(expected_report=r['report_sha256'],dossier=d,expected_dossier=d['dossier_sha256'],trial_package=None,expected_trial=None)


@pytest.fixture
def products(context,tmp_path):
    p,a,r=context
    pp=pred.freeze({**a['predictive_plan']['spec'],'candidate_version':'v3'})
    old=a['binary_plan']; groups={u['unit_id']:u['group'] for u in old['subgroup']['plan']['units']}
    bp=binary.freeze({**old['spec'],'candidate_version':'v3'},a['manifest'],groups,a['audit_key'],a['site_keys'])
    req=submission.freeze({**r['request'],'vendor':'vendor-b','product':'ai-b','version':'v3'})
    path=tmp_path/'second.db'; root=vendor.init(path,req,req['request_sha256'],'operator','reviewer','representative')
    p2=work.freeze(req,req['request_sha256'],root,pp,bp)
    b=copy.deepcopy(a); b.update(db=path,head=root,predictive_plan=pp,binary_plan=bp)
    for i,inp in enumerate((a,b)):
        reseal(inp,'baseline',[{'unit_id':'u2','probability_bp':5000}])
        reseal(inp,'reference',[{'unit_id':'u2','label':True},{'unit_id':'u3','label':False}])
        reseal(inp,'candidate',[{'unit_id':'u2','probability_bp':10000*i},{'unit_id':'u3','probability_bp':10000*(1-i)}])
    return {'a':member(p,a),'b':member(p2,b)}


def test_direct_matched_contrast(products):
    f=m.freeze(products); rs={k:result(v) for k,v in products.items()}
    r=m.analyze(f,f['comparison_sha256'],products,rs)
    pair=r['predictive_pairwise_contrasts'][0]
    assert pair['direction']=='right_minus_left' and pair['n_planned']==3
    assert pair['brier_difference']['complete_case_brier_delta']==-1
    assert pair['brier_difference']['cohort_brier_delta_bounds']==[-1,-1/3]
    assert pair['net_benefit_differences'][1]['cohort_delta_bounds']==[1/3,1]
    assert not r['purchasing_authorized'] and r['all_products_replayed']
    assert 'u2' not in str(r) and m.verify(f,f['comparison_sha256'],products,rs,r,r['report_sha256'])
    r['all_products_replayed']=False
    assert not m.verify(f,f['comparison_sha256'],products,rs,r,r['report_sha256'])


def test_missing_product_preserved(products):
    f=m.freeze(products); r=m.analyze(f,f['comparison_sha256'],products,{'a':result(products['a']),'b':None})
    assert r['n_declared']==2 and r['n_replayed']==1 and not r['all_products_replayed']
    assert r['products']['b']=={'status':'not_supplied','dossier':None} and not r['predictive_pairwise_contrasts']
    with pytest.raises(ValueError): m.analyze(f,f['comparison_sha256'],products,{'a':None})


@pytest.mark.parametrize('role,rows,receipt',[('reference',[{'unit_id':'u2','label':False}],'c'*64),
    ('reference',[{'unit_id':'u2','label':True},{'unit_id':'u3','label':False}],'d'*64),
    ('baseline',[{'unit_id':'u2','probability_bp':7000}],'c'*64)])
def test_refuses_unmatched_common_bundles(products,role,rows,receipt):
    f=m.freeze(products); reseal(products['b']['inputs'],role,rows,receipt)
    rs={k:result(v) for k,v in products.items()}
    with pytest.raises(ValueError): m.analyze(f,f['comparison_sha256'],products,rs)


def test_policy_roster_and_digest_checks(products):
    f=m.freeze(products); rs={k:result(v) for k,v in products.items()}
    with pytest.raises(ValueError): m.freeze({'a':products['a'],'b':products['a']})
    with pytest.raises(ValueError): m.analyze(f,'0'*64,products,rs)
    rs['b']['expected_dossier']='0'*64
    with pytest.raises(ValueError): m.analyze(f,f['comparison_sha256'],products,rs)
    b=products['b']; b['policy']=policy(b['plan'],True); b['expected_policy']=b['policy']['policy_sha256']
    with pytest.raises(ValueError): m.freeze(products)


def test_different_as_of_refused(products):
    f=m.freeze(products); products['b']['inputs']['as_of']='2026-01-04'
    with pytest.raises(ValueError): m.analyze(f,f['comparison_sha256'],products,{k:result(v) for k,v in products.items()})


def test_cli_two_workspaces(app,tmp_path,capsys):
    other=tmp_path/'staging-b'; shutil.copytree(app.staging,other)
    for name in ('request-spec','predictive-spec','binary-spec'):
        p=other/(name+'.json'); data=json.loads(p.read_text())
        if name=='request-spec': data.update(vendor='vendor-b',product='ai-b',version='v3')
        else: data['candidate_version']='v3'
        put(p,data)
    b=m.storage.Setup(other,tmp_path/'workspace-b'); b.host=app.host
    cfg={}; apps={'a':app,'b':b}
    for k,obj in apps.items():
        freeze_app(obj); p=policy(obj.prepared,False); put(obj.destination/'research-dossier-policy.json',p)
        cfg[k]=dict(workspace=str(obj.destination),expected_plan=obj.prepared['plan_sha256'],expected_head=obj.head,expected_policy=p['policy_sha256'],result=None)
    c=tmp_path/'config.json'; put(c,cfg); f=tmp_path/'comparison.json'; out=tmp_path/'report.json'
    assert m.main(['freeze','--config',str(c),'--output',str(f)])==0
    h=capsys.readouterr().out.strip()
    for k,obj in apps.items():
        stage_collection(obj); assert post(obj,'/preview-collection')[0]=='200 OK'; rh=obj.collection[1]
        assert post(obj,'/commit-collection')[0]=='200 OK'
        plan,inp,_=m.resume.complete_inputs(obj.destination,obj.prepared['plan_sha256'],obj.head,rh)
        r=result(member(plan,inp)); put(obj.destination/'research-dossier.json',r['dossier'])
        cfg[k]['result']={x:r[x] for x in ('expected_report','expected_dossier','trial_package','expected_trial')}
    put(c,cfg)
    args=['--config',str(c),'--comparison',str(f),'--expected-comparison',h]
    assert m.main(['analyze',*args,'--output',str(out)])==0
    rh=capsys.readouterr().out.strip(); assert json.loads(out.read_text())['n_replayed']==2
    assert m.main(['verify',*args,'--report',str(out),'--expected-report',rh])==0
    assert json.loads(capsys.readouterr().out)['report_replayed']
    cfg['b']['result']=None; put(c,cfg)
    assert m.main(['analyze',*args,'--output',str(tmp_path/'other.json')])==2
