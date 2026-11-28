import copy
import json
import os

import pytest
from caeval import predictive_research as w


def spec():
    return dict(study='study', eligibility_sha256='a'*64, reference_protocol_sha256='b'*64,
                endpoint='event', horizon='day30', baseline_version='v1', candidate_version='v2',
                unit_ids=['a','b','c'], bin_edges_bp=[0,5000,10000], action='refer', thresholds_bp=[5000,2000])


def inputs(p, missing=False):
    rows = {'baseline': [{'unit_id':'a','probability_bp':0}, {'unit_id':'b','probability_bp':10000}],
            'candidate': [{'unit_id':'a','probability_bp':10000}, {'unit_id':'b','probability_bp':0}],
            'reference': [{'unit_id':'a','label':True}, {'unit_id':'b','label':False}]}
    if not missing:
        for role, field, value in [('baseline','probability_bp',0), ('candidate','probability_bp',10000), ('reference','label',True)]:
            rows[role].append({'unit_id':'c',field:value})
    return {r:w.seal(p,p['plan_sha256'],r,'c'*64,v) for r,v in rows.items()}


def digests(b):
    return {r:v['bundle_sha256'] for r,v in b.items()}


def run(p,b):
    return w.analyze(p,p['plan_sha256'],b,digests(b))


def test_complete():
    p=w.freeze(spec()); b=inputs(p); r=run(p,b)
    assert r['n_planned']==3
    assert r['calibration']['paired']['cohort_brier_delta_bounds']==[-1,-1]
    assert r['decision_curve']['thresholds'][1]['candidate_minus']['baseline']['cohort_delta_bounds']==[1,1]
    assert r['coverage']['reference']['n_unknown']==0
    assert not r['source_records_reverified'] and not r['decision_grade']
    assert w.verify(p,p['plan_sha256'],b,digests(b),r,r['report_sha256'])


def test_missing_kept_in_both_denominators():
    p=w.freeze(spec()); r=run(p,inputs(p,True))
    assert r['calibration']['n_planned']==r['decision_curve']['n_planned']==3
    assert r['calibration']['paired']['cohort_brier_delta_bounds']==[-1,-1/3]
    assert r['decision_curve']['thresholds'][1]['candidate_minus']['baseline']['cohort_delta_bounds']==[1/3,1]
    assert all(v['n_unknown']==1 for v in r['coverage'].values())


def test_all_missing():
    p=w.freeze(spec()); b={r:w.seal(p,p['plan_sha256'],r,'c'*64,[]) for r in w.ROLES}; r=run(p,b)
    assert r['calibration']['paired']['cohort_brier_delta_bounds']==[-1,1]
    assert r['decision_curve']['thresholds'][1]['candidate_minus']['baseline']['cohort_delta_bounds']==[-1,1]
    assert r['coverage']['reference']['n_unknown']==3


def test_order_invariance():
    p=w.freeze(spec()); assert w.freeze({**spec(),'unit_ids':['c','a','b'],'thresholds_bp':[2000,5000]})==p
    b=inputs(p); b2={r:w.seal(p,p['plan_sha256'],r,'c'*64,v['rows'][::-1]) for r,v in b.items()}
    assert b==b2 and run(p,b)==run(p,b2)


@pytest.mark.parametrize('change',[{'role':'reference'},{'scope':'v3'},{'source_receipt_sha256':'d'*64},{'plan_sha256':'d'*64}])
def test_tampered_bundle(change):
    p=w.freeze(spec()); b=inputs(p); b['baseline'].update(change)
    with pytest.raises(w.ResearchError): run(p,b)


def test_swapped_roles_and_stale_checkpoint():
    p=w.freeze(spec()); b=inputs(p); b['baseline'],b['candidate']=b['candidate'],b['baseline']
    with pytest.raises(w.ResearchError): run(p,b)
    b=inputs(p); d=digests(b); b['baseline']=w.seal(p,p['plan_sha256'],'baseline','d'*64,b['baseline']['rows'])
    with pytest.raises(w.ResearchError): w.analyze(p,p['plan_sha256'],b,d)


@pytest.mark.parametrize('role,rows',[('baseline',[{'unit_id':'a','probability_bp':True}]),('reference',[{'unit_id':'a','label':1}]),('baseline',[{'unit_id':'x','probability_bp':0}]),('baseline',[{'unit_id':'a','probability_bp':0}]*2)])
def test_bad_rows(role,rows):
    p=w.freeze(spec())
    with pytest.raises(w.ResearchError): w.seal(p,p['plan_sha256'],role,'c'*64,rows)


def test_plan_and_report_tampering():
    p=w.freeze(spec()); b=inputs(p); r=run(p,b); checkpoint=r['report_sha256']; r['n_planned']=1
    assert not w.verify(p,p['plan_sha256'],b,digests(b),r,checkpoint)
    q=copy.deepcopy(p); q['spec']['eligibility_sha256']='d'*64
    with pytest.raises(w.ResearchError): run(q,b)
    q=w.freeze({**spec(),'candidate_version':'v3'})
    with pytest.raises(w.ResearchError): run(q,b)


def test_cli_roundtrip(tmp_path,capsys):
    p=w.freeze(spec()); b=inputs(p); src=tmp_path/'spec.json'; src.write_text(json.dumps(spec())); dest=tmp_path/'plan.json'
    assert w.main(['freeze','--spec',str(src),'--output',str(dest)])==0
    assert capsys.readouterr().out.strip()==p['plan_sha256']
    common=['--plan',str(dest),'--expected-plan',p['plan_sha256']]; args=common[:]
    for role in w.ROLES:
        source=tmp_path/(role+'-rows.json'); source.write_text(json.dumps(b[role]['rows'])); target=tmp_path/(role+'.json')
        assert w.main(['seal',*common,'--role',role,'--source-receipt','c'*64,'--rows',str(source),'--output',str(target)])==0
        assert capsys.readouterr().out.strip()==b[role]['bundle_sha256']
        args.extend(['--'+role,str(target),'--expected-'+role,b[role]['bundle_sha256']])
    report=tmp_path/'report.json'; assert w.main(['analyze',*args,'--output',str(report)])==0
    checkpoint=capsys.readouterr().out.strip(); assert json.loads(report.read_text())==run(p,b)
    assert w.main(['verify',*args,'--report',str(report),'--expected-report',checkpoint])==0
    assert w.main(['analyze',*args,'--output',str(report)])==2
    if os.name=='posix': assert report.stat().st_mode & 0o777==0o600


def test_duplicate_json_rejected(tmp_path,capsys):
    src=tmp_path/'bad.json'; src.write_text('{"private-id":1,"private-id":2}')
    dest=tmp_path/'plan.json'
    assert w.main(['freeze','--spec',str(src),'--output',str(dest)])==2
    assert not dest.exists() and 'private-id' not in capsys.readouterr().err
