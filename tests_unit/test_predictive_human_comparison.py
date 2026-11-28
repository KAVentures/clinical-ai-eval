from itertools import product
import pytest
from caeval import predictive_human_comparison as h, predictive_reference_panel as panel
from test_predictive_reference_panel import scope, primary
from test_predictive_research import inputs


def setup():
    a = scope()
    spec = dict(clinicians=['clinician-z','clinician-a'], threshold_bp=5000, decision_protocol_sha256='e'*64,
                decision_input_sha256={u:'f'*64 for u in a[0]['spec']['unit_ids']})
    c = h.freeze(*a,spec)
    args = (*a,c,c['comparison_sha256'])
    ss, ds = primary(a, {'a':True,'b':False}, {'a':True,'b':False})
    ref = panel.finalize(*a,ss,ds)
    model = inputs(a[0])['candidate']
    subs = {u:h.seal(*args,u,'a'*64,[{'unit_id':'a','action':u=='clinician-a','input_sha256':'f'*64}]) for u in spec['clinicians']}
    kw = dict(primary=ss,expected_primary=ds,adjudication=None,expected_adjudication=None,
              expected_reference=ref['receipt']['receipt_sha256'],model=model,expected_model=model['bundle_sha256'],
              submissions=subs,expected_submissions={u:s['submission_sha256'] for u,s in subs.items()})
    return args, kw


def test_matched_report():
    args,kw=setup(); r=h.analyze(*args,**kw)
    a,z=(r['by_clinician'][x] for x in ('clinician-a','clinician-z'))
    assert a['n_complete_pairs']==z['n_complete_pairs']==1
    assert a['complete_pair_counts']['both_correct']==1 and z['complete_pair_counts']['model_only_correct']==1
    assert a['model_minus_clinician_correct_count_bounds']==[-1,2]
    assert z['model_minus_clinician_correct_count_bounds']==[0,3]
    assert h.verify(*args,report=r,expected_report=r['report_sha256'],**kw)
    assert not r['decision_grade']


def test_exhaustive_bounds():
    states=list(product((None,False,True),repeat=3))
    for a,b in product(states,repeat=2):
        r=h.metrics([a,b]); totals=[]
        for vals in product(*(h._poss(x) for x in (*a,*b))):
            m1,h1,y1,m2,h2,y2=vals
            m=int(m1==y1)+int(m2==y2); c=int(h1==y1)+int(h2==y2)
            totals.append((m,c,m-c))
        for i,k in enumerate(('model_correct_count_bounds','clinician_correct_count_bounds','model_minus_clinician_correct_count_bounds')):
            assert r[k]==[min(v[i] for v in totals),max(v[i] for v in totals)]


@pytest.mark.parametrize('change',[{'clinicians':['doctor-a']},{'clinicians':['coord']},{'clinicians':['x','x']},{'threshold_bp':3000},{'decision_input_sha256':{'a':'f'*64}}])
def test_invalid_plan(change):
    args,_=setup()
    with pytest.raises(ValueError): h.freeze(*args[:4],{**args[4]['spec'],**change})


def test_reject_stale_or_changed_inputs():
    args,kw=setup()
    with pytest.raises(ValueError): h.analyze(*args,**{**kw,'expected_reference':'0'*64})
    with pytest.raises(ValueError): h.analyze(*args,**{**kw,'expected_model':'0'*64})
    kw['submissions']['clinician-a']['rows'][0]['action']=False
    with pytest.raises(ValueError): h.analyze(*args,**kw)


def test_wrong_source_and_missing_clinician():
    args,kw=setup()
    with pytest.raises(ValueError): h.seal(*args,'clinician-a','a'*64,[dict(unit_id='a',action=True,input_sha256='a'*64)])
    del kw['submissions']['clinician-z']
    with pytest.raises(ValueError): h.analyze(*args,**kw)
