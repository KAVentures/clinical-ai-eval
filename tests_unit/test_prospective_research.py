import copy
import json
import os
import pytest
from caeval import prospective_research as w

KEY = b'a'*32
SITES = {'h1': b'b'*32, 'h2': b'c'*32}


def inputs():
    spec = dict(study='study', endpoint='harm', horizon='day30', reference_protocol='r1', baseline_version='v1',
                candidate_version='v2', holdout_design='temporal_site', subgroup_levels=['g1','g2'], minimum_group=1,
                coding_version='dictionary', shift_features=[dict(feature='age', levels=['a','b'], warning_bp=2500)],
                binary_design=dict(iid_assumed=True, unacceptable_rate=.5, anticipated_rate=.1, alpha=.05))
    rows = [dict(record_id='u'+str(i), partition='train' if i < 2 else 'test', patient_key='p'+str(i),
                 lineage_key='l'+str(i), content_sha256=format(i,'064x'), site_key='dev' if i < 2 else 'h1' if i < 4 else 'h2',
                 observed_at='2025-01-01' if i < 2 else '2026-01-01') for i in range(5)]
    return spec, rows, {'u2':'g1','u3':'g1','u4':None}


def setup():
    s,m,g=inputs()
    return w.freeze(s,m,g,KEY,SITES),m


def outcomes():
    return [dict(unit_id='u2',baseline=True,candidate=False),dict(unit_id='u3',baseline=False,candidate=True)]


def run(p,m,rows=None):
    return w.analyze(p,p['plan_sha256'],m,KEY,SITES,outcomes() if rows is None else rows,{'age':{'a':2,'b':0}},{'age':{'a':0,'b':2}})


def test_workflow():
    p,m=setup(); r=run(p,m)
    assert r['n_planned']==r['subgroup']['n_planned']==r['multisite']['unit_weighted']['n']==3
    assert r['population_shift']['target_n']==3 and r['population_shift']['reference_n']==2
    assert r['dataset_audit']['status']=='NO_DETECTED_OVERLAP'
    assert r['multisite']['unit_weighted']['cohort_delta_bounds']==[-1/3,1/3]
    assert r['candidate_iid_sensitivity']['finite_cohort_rate_range']==[1/3,2/3]
    assert r['population_shift']['features'][0]['status']=='AT_OR_ABOVE_LIMIT'
    assert r['subgroup']['unknown_group_units']==1
    assert not r['independent_site_execution_verified'] and not r['decision_grade']


def test_empty_outcomes_and_no_iid():
    s,m,g=inputs(); s['binary_design']=None; p=w.freeze(s,m,g,KEY,SITES)
    r=run(p,m,[])
    assert r['candidate_iid_sensitivity'] is None
    assert r['multisite']['unit_weighted']['cohort_delta_bounds']==[-1,1]


@pytest.mark.parametrize('field,value',[('patient_key','p0'),('site_key','dev'),('observed_at','2025-01-01'),('content_sha256',None)])
def test_leakage_or_incomplete_blocks_freeze(field,value):
    s,m,g=inputs(); m[2][field]=value
    with pytest.raises(ValueError): w.freeze(s,m,g,KEY,SITES)


def test_iid_repeated_patient_rejected():
    s,m,g=inputs(); m[3]['patient_key']=m[2]['patient_key']
    with pytest.raises(w.ResearchError): w.freeze(s,m,g,KEY,SITES)
    s['binary_design']=None
    assert w.freeze(s,m,g,KEY,SITES)['binary_design'] is None


@pytest.mark.parametrize('groups',[{}, {'u2':'g1','u3':'g1','u4':'undeclared'}, {'u2':'g1','u3':'g1','u4':None,'u0':'g2'}])
def test_membership_exact(groups):
    s,m,_=inputs()
    with pytest.raises(ValueError): w.freeze(s,m,groups,KEY,SITES)


def test_key_scope():
    s,m,g=inputs()
    for keys in ({'h1':b'b'*32}, {'h1':KEY,'h2':b'c'*32}):
        with pytest.raises(w.ResearchError): w.freeze(s,m,g,KEY,keys)


def test_tamper_and_checkpoint():
    p,m=setup(); r=run(p,m)
    args=(p,p['plan_sha256'],m,KEY,SITES,outcomes(),{'age':{'a':2,'b':0}},{'age':{'a':0,'b':2}})
    assert w.verify(*args,r,r['report_sha256'])
    bad=copy.deepcopy(r); bad['n_planned']=1
    assert not w.verify(*args,bad,r['report_sha256'])
    q=copy.deepcopy(p); q['spec']['candidate_version']='v3'
    with pytest.raises(ValueError): run(q,m)
    m[2]['lineage_key']='new'
    with pytest.raises(ValueError): run(p,m)


def test_input_binding_and_reorder():
    p,m=setup(); assert run(p,m)==run(p,m[::-1],outcomes()[::-1])
    r=run(p,m); new=outcomes(); new[0]['candidate']=None
    assert run(p,m,new)['input_mac']!=r['input_mac']
    with pytest.raises(ValueError): run(p,m,outcomes()+[dict(unit_id='u0',baseline=False,candidate=False)])


def test_cli(tmp_path,capsys):
    s,m,g=inputs(); p=w.freeze(s,m,g,KEY,SITES)
    def write(name,obj):
        path=tmp_path/name; path.write_text(json.dumps(obj)); path.chmod(0o600); return str(path)
    keys=write('keys',{'audit':KEY.hex(),'sites':{k:v.hex() for k,v in SITES.items()}})
    common=['--manifest',write('manifest',m),'--keys',keys]; plan=str(tmp_path/'plan')
    args=['freeze',*common,'--spec',write('spec',s),'--memberships',write('memberships',g),'--output',plan]
    assert w.main(args)==0; assert capsys.readouterr().out.strip()==p['plan_sha256']
    assert w.main(args)==2
    args=[*common,'--plan',plan,'--expected-plan',p['plan_sha256'],'--outcomes',write('outcomes',outcomes()),
          '--reference-counts',write('ref',{'age':{'a':2,'b':0}}),'--target-counts',write('target',{'age':{'a':0,'b':2}})]
    report=str(tmp_path/'report'); assert w.main(['analyze',*args,'--output',report])==0
    d=capsys.readouterr().out.strip(); assert w.main(['verify',*args,'--report',report,'--expected-report',d])==0
    if os.name=='posix':
        assert os.stat(report).st_mode & 0o777==0o600
        os.chmod(keys,0o644); assert w.main(['verify',*args,'--report',report,'--expected-report',d])==2
