import copy
import json

import pytest

from caeval import predictive_shared_evidence as s, predictive_research as p
from caeval import predictive_reference_panel as panel
from test_predictive_reference_panel import scope, primary
from test_predictive_research import inputs


def setup():
    a=scope(); plan,head,ref,ref_head=a
    other=p.freeze({**plan['spec'],'candidate_version':'v3'})
    plans={'a':plan,'b':other}; heads={k:v['plan_sha256'] for k,v in plans.items()}
    d=s.freeze(plans,heads,'a')
    ss,ds=primary(a,{'a':True,'b':True},{'a':True,'b':False})
    packet=panel.adjudication_packet(*a,ss,ds)
    third=panel.seal(packet,packet['packet_sha256'],packet['reader'],[{**packet['items'][0],'label':False}])
    package=panel.finalize(*a,ss,ds,third,third['submission_sha256'])
    b=inputs(plan)['baseline']
    evidence=dict(panel=ref,expected_panel=ref_head,primary=ss,expected_primary=ds,adjudication=third,
                  expected_adjudication=third['submission_sha256'],expected_reference=package['receipt']['receipt_sha256'],
                  baseline=b,expected_baseline=b['bundle_sha256'])
    return d,d['design_sha256'],plans,heads,evidence


def test_native_shared_bundles():
    args=setup(); out=s.build(*args)
    left,right=(out['bundles'][k] for k in ('a','b'))
    for role in ('baseline','reference'):
        assert left[role]['bundle_sha256']!=right[role]['bundle_sha256']
        for k in ('scope','rows','source_receipt_sha256'):
            assert left[role][k]==right[role][k]
        for name,pair in out['bundles'].items():
            b=pair[role]; p._bundle(args[2][name],args[3][name],role,b,b['bundle_sha256'])
    assert left['reference']['rows']==[{'unit_id':'a','label':True},{'unit_id':'b','label':False},{'unit_id':'c','label':None}]
    assert out['reference_resolution']['adjudicated']==1
    assert not out['decision_grade']
    assert s.verify(*args,out,out['package_sha256'])
    e=s.export(*args,out,out['package_sha256'],'b')
    assert e['bundles']==right and e['plan_sha256']==args[3]['b']


def test_reuse_native_predictive_analysis():
    args=setup(); out=s.build(*args)
    for name,plan in args[2].items():
        b=inputs(plan); b.update(out['bundles'][name])
        r=p.analyze(plan,plan['plan_sha256'],b,{k:v['bundle_sha256'] for k,v in b.items()})
        assert r['coverage']['reference']['n_unknown']==1


@pytest.mark.parametrize('key',['eligibility_sha256','reference_protocol_sha256','study','baseline_version'])
def test_scope_mismatch(key):
    d,h,plans,heads,evidence=setup()
    replacement='e'*64 if key.endswith('sha256') else 'different'
    other=p.freeze({**plans['b']['spec'],key:replacement})
    with pytest.raises(ValueError): s.freeze({**plans,'b':other},{**heads,'b':other['plan_sha256']},'a')


def test_anchor_and_roster_pinned():
    args=setup(); d,h,plans,heads,evidence=args
    with pytest.raises(ValueError): s.build({**d,'anchor':'b'},h,plans,heads,evidence)
    with pytest.raises(ValueError): s.build(d,h,{'a':plans['a']},{'a':heads['a']},evidence)
    with pytest.raises(ValueError): s.freeze(plans,heads,'unknown')
    with pytest.raises(ValueError): s.build(d,'0'*64,plans,heads,evidence)


def test_tampering_and_missing_evidence():
    args=setup(); out=s.build(*args); d,h,plans,heads,evidence=args
    for key in ('expected_baseline','expected_reference','expected_panel'):
        with pytest.raises(ValueError): s.build(d,h,plans,heads,{**evidence,key:'0'*64})
    altered=copy.deepcopy(out); altered['bundles']['b']['reference']['rows'][0]['label']=False
    assert not s.verify(*args,altered,out['package_sha256'])
    with pytest.raises(ValueError): s.export(*args,altered,out['package_sha256'],'b')
    with pytest.raises(ValueError): s.export(*args,out,out['package_sha256'],'unknown')


def test_cli_roundtrip(tmp_path,capsys):
    d,h,plans,heads,evidence=setup()
    def put(name,obj):
        path=tmp_path/name; path.write_text(json.dumps(obj)); return str(path)
    roster=put('roster',dict(plans=plans,expected_plans=heads)); design=str(tmp_path/'design')
    assert s.main(['freeze','--plans',roster,'--anchor','a','--output',design])==0
    assert capsys.readouterr().out.strip()==h
    common=['--plans',roster,'--design',design,'--expected-design',h,'--evidence',put('evidence',evidence)]
    package=str(tmp_path/'package')
    assert s.main(['build',*common,'--output',package])==0
    digest=capsys.readouterr().out.strip()
    final=[*common,'--package',package,'--expected-package',digest]
    assert s.main(['verify',*final])==0
    assert json.loads(capsys.readouterr().out)['package_replayed']
    export=str(tmp_path/'export')
    assert s.main(['export',*final,'--product','b','--output',export])==0
    assert json.load(open(export))['plan_sha256']==heads['b']
    assert s.main(['export',*final,'--product','b','--output',export])==2
