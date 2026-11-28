import copy
import json
import pytest
from caeval import assessment_research_dossier as d, assessment_workbench as w
from caeval import predictive_reference_panel as panel, predictive_assisted_trial as t
from caeval import predictive_assisted_coordinator as c, predictive_research as p
from test_assessment_workbench import context
from test_assessment_setup_web import app, freeze, stage_collection, post, put


def policy(plan, required=True):
    return d.freeze(plan,plan['plan_sha256'],dict(minimum_coverage_bp={'baseline':9000,'candidate':9000,'reference':9000},
                    require_assisted_trial=required,minimum_trial_coverage_bp=9000))


def trial(inputs):
    pp=inputs['predictive_plan']; h=pp['plan_sha256']; ids=pp['spec']['unit_ids']
    ref=panel.freeze(pp,h,dict(coordinator='coord',reader_a='doctor-a',reader_b='doctor-b',adjudicator='doctor-c',source_inventory={u:'a'*64 for u in ids}))
    ss={}
    for role in panel.PRIMARY:
        packet=panel.primary_packet(pp,h,ref,ref['panel_sha256'],role)
        ss[role]=panel.seal(packet,packet['packet_sha256'],packet['reader'],[{**r,'label':True} for r in packet['items']])
    ds={r:s['submission_sha256'] for r,s in ss.items()}
    a=(pp,h,ref,ref['panel_sha256']); reference=panel.finalize(*a,ss,ds)
    design=t.freeze(*a,dict(clinicians=['clinician-a'],cluster_by_unit={u:u for u in ids},n_assisted=1,
        decision_protocol_sha256='e'*64,assistance_protocol_sha256='f'*64,decision_input_sha256={u:'a'*64 for u in ids},
        assistance_input_sha256={u:'b'*64 for u in ids},randomization_test=True))
    a=(*a,design,design['design_sha256']); allocation=t.allocate(*a,0)
    packet=t.packet(*a,allocation=allocation,allocation_digest=allocation['allocation_sha256'],clinician='clinician-a')
    s=t.seal(*a,allocation=allocation,allocation_digest=allocation['allocation_sha256'],clinician='clinician-a',source_receipt='c'*64,
             rows=[{**r,'action':True} for r in packet['items']])
    body={'schema':c.SCHEMA,'plan':pp,'panel':ref,'design':design,'allocation':allocation,'primary':ss,'expected_primary':ds,
          'adjudication':None,'expected_adjudication':None,'expected_reference':reference['receipt']['receipt_sha256'],
          'submissions':{'clinician-a':s},'expected_submissions':{'clinician-a':s['submission_sha256']},'randomization_assumptions_declared':True}
    body['report']=c._report({**body,'report':None,'package_sha256':None})
    return {**body,'package_sha256':p.digest(body)},reference['bundle']


def args(context):
    plan,inputs,_=context; f=policy(plan); report=w.analyze(plan,plan['plan_sha256'],**inputs)
    return (plan,plan['plan_sha256'],f,f['policy_sha256'],report['report_sha256'],inputs)


def test_missing_evidence_and_replay(context):
    a=args(context); r=d.build(*a)
    assert r['administrative_evidence_gaps']==['mandatory_vendor_documents_missing','baseline_coverage_below_minimum','candidate_coverage_below_minimum','reference_coverage_below_minimum','assisted_trial_missing']
    assert r['assisted_trial'] is None and not r['purchasing_authorized']
    assert d.verify(*a,dossier=r,expected_dossier=r['dossier_sha256'])
    r['administrative_evidence_gaps']=[]
    assert not d.verify(*a,dossier=r,expected_dossier=r['dossier_sha256'])


def test_trial_replay_and_reference_exact_match(context):
    plan,inputs,_=context; package,reference=trial(inputs)
    with pytest.raises(ValueError): d.build(*args(context),trial_package=package,expected_trial=package['package_sha256'])
    inputs['bundles']['reference']=reference; inputs['expected_bundles']['reference']=reference['bundle_sha256']
    a=args(context); out=d.build(*a,trial_package=package,expected_trial=package['package_sha256'])
    assert out['assisted_trial']['coverage']['known']==3
    assert out['assisted_trial']['reference_panel_resolution']['consensus']==3
    assert 'assisted_trial_missing' not in out['administrative_evidence_gaps']
    assert 'reference_coverage_below_minimum' not in out['administrative_evidence_gaps']
    assert 'clinician-a' not in str(out) and 'doctor-a' not in str(out) and 'u2' not in str(out)
    assert d.verify(*a,dossier=out,expected_dossier=out['dossier_sha256'],trial_package=package,expected_trial=package['package_sha256'])
    # Same reference receipt pointer with different native label inventory is not enough.
    replacement=p.seal(inputs['predictive_plan'],inputs['predictive_plan']['plan_sha256'],'reference',reference['source_receipt_sha256'],[])
    inputs['bundles']['reference']=replacement; inputs['expected_bundles']['reference']=replacement['bundle_sha256']
    with pytest.raises(ValueError): d.build(*args(context),trial_package=package,expected_trial=package['package_sha256'])


def test_wrong_trial_scope_and_digest(context):
    a=args(context); package,_=trial(a[-1]); other=copy.deepcopy(package)
    other['plan']['spec']['study']='different'
    with pytest.raises(ValueError): d.build(*a,trial_package=other,expected_trial=package['package_sha256'])
    with pytest.raises(ValueError): d.build(*a,trial_package=package)
    with pytest.raises(ValueError): d.build(*a,expected_trial=package['package_sha256'])


def test_policy_and_report_tampering(context):
    a=args(context); plan=a[0]
    for v in (True,-1,10001):
        with pytest.raises(ValueError): d.freeze(plan,a[1],{**a[2]['policy'],'minimum_trial_coverage_bp':v})
    with pytest.raises(ValueError): d.build(*a[:4],'0'*64,a[-1])
    altered=copy.deepcopy(a[2]); altered['policy']['require_assisted_trial']=False
    with pytest.raises(ValueError): d.build(*a[:2],altered,*a[3:])


def test_optional_trial_not_a_pass(context):
    plan,inputs,_=context; f=policy(plan,False); report=w.analyze(plan,plan['plan_sha256'],**inputs)
    r=d.build(plan,plan['plan_sha256'],f,f['policy_sha256'],report['report_sha256'],inputs)
    assert r['assisted_trial_status']=='not_supplied' and not r['clinical_validation_established']
    assert 'assisted_trial_missing' not in r['administrative_evidence_gaps']


def test_cli_completed_workspace(app,tmp_path,capsys):
    freeze(app); stage_collection(app)
    assert post(app,'/preview-collection')[0]=='200 OK'; report=app.collection[1]
    assert post(app,'/commit-collection')[0]=='200 OK'
    common=['--workspace',str(app.destination),'--expected-plan',app.prepared['plan_sha256']]
    f=policy(app.prepared); put(tmp_path/'policy.json',f['policy'])
    out=tmp_path/'frozen.json'; dossier=tmp_path/'dossier.json'
    assert d.main(['freeze',*common,'--policy',str(tmp_path/'policy.json'),'--output',str(out)])==0
    assert capsys.readouterr().out.strip()==f['policy_sha256']
    a=[*common,'--policy',str(out),'--expected-policy',f['policy_sha256'],'--expected-head',app.head,'--expected-report',report]
    assert d.main(['build',*a,'--output',str(dossier)])==0
    h=capsys.readouterr().out.strip()
    assert d.main(['verify',*a,'--dossier',str(dossier),'--expected-dossier',h])==0
    assert json.loads(capsys.readouterr().out)['dossier_replayed']
    assert d.main(['build',*a,'--output',str(dossier)])==2
