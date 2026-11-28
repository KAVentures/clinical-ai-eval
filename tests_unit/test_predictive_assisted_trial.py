from fractions import Fraction
from itertools import combinations, product
from math import comb
import copy

import pytest
from caeval import predictive_assisted_trial as t, predictive_reference_panel as panel
from test_predictive_reference_panel import scope, primary


def design(**changes):
    a=scope(); s=dict(clinicians=['clinician-a','clinician-b'],cluster_by_unit={'a':'x','b':'y','c':'y'},n_assisted=1,
        decision_protocol_sha256='e'*64,assistance_protocol_sha256='f'*64,decision_input_sha256={u:'a'*64 for u in 'abc'},
        assistance_input_sha256={u:'b'*64 for u in 'abc'},randomization_test=True)
    d=t.freeze(*a,{**s,**changes}); return (*a,d,d['design_sha256'])


def setup(missing=False,**changes):
    a=design(**changes); alloc=t.allocate(*a,0); dh=alloc['allocation_sha256']
    ss,ds=primary(a[:4],dict(a=True,b=False,c=True),dict(a=True,b=False,c=True))
    reference=panel.finalize(*a[:4],ss,ds)['receipt']['receipt_sha256']
    subs={}
    for reader in a[-2]['spec']['clinicians']:
        p=t.packet(*a,allocation=alloc,allocation_digest=dh,clinician=reader)
        rows=[{**r,'action':True} for r in p['items'] if not missing or r['unit_id']!='b']
        subs[reader]=t.seal(*a,allocation=alloc,allocation_digest=dh,clinician=reader,source_receipt='c'*64,rows=rows)
    kw=dict(allocation=alloc,allocation_digest=dh,primary=ss,expected_primary=ds,adjudication=None,expected_adjudication=None,
            expected_reference=reference,submissions=subs,expected_submissions={r:s['submission_sha256'] for r,s in subs.items()})
    return a,kw


def test_exact_allocation_bijection():
    for n in range(2,10):
        for k in range(1,n):
            assert [tuple(t.unrank(list(range(n)),k,r)) for r in range(comb(n,k))]==list(combinations(range(n),k))


def test_packet_cluster_and_no_control_assistance():
    a=design(); alloc=t.allocate(*a,0)
    p=t.packet(*a,allocation=alloc,allocation_digest=alloc['allocation_sha256'],clinician='clinician-a')
    assert [r['arm'] for r in p['items']]==['assisted','unassisted','unassisted']
    assert [r['assistance_sha256'] for r in p['items']]==['b'*64,None,None]
    assert 'clinician-b' not in str(p) and 'doctor-a' not in str(p)
    p=t.packet(*a,allocation=t.allocate(*a,1),allocation_digest=t.allocate(*a,1)['allocation_sha256'],clinician='clinician-a')
    assert [r['arm'] for r in p['items']]==['unassisted','assisted','assisted']


def test_draw_requires_explicit_approval(monkeypatch):
    a=design(); monkeypatch.setattr(t,'randbelow',lambda n: n-1)
    with pytest.raises(ValueError): t.draw(*a)
    assert t.draw(*a,confirmed=True)==t.allocate(*a,1)
    with pytest.raises(ValueError): t.allocate(*a,2)


def test_complete_report_and_replay():
    a,kw=setup(); r=t.analyze(*a,**kw,randomization_assumptions_declared=True)
    assert r['n_fully_observed_cases']==3
    assert r['assisted_minus_unassisted_equal_cluster_bounds']==[t._f(Fraction(1,2))]*2
    assert r['conditional_sharp_null_test']['p_value']==t._f(Fraction(1))
    assert not r['clinical_benefit_established']
    assert t.verify(*a,**kw,randomization_assumptions_declared=True,report=r,expected_report=r['report_sha256'])
    assert t.analyze(*a,**kw)['conditional_sharp_null_test'] is None
    a,kw=setup(randomization_test=False)
    assert t.analyze(*a,**kw,randomization_assumptions_declared=True)['conditional_sharp_null_test'] is None


def test_missingness_withholds_test():
    a,kw=setup(missing=True); r=t.analyze(*a,**kw,randomization_assumptions_declared=True)
    assert r['assisted_minus_unassisted_equal_cluster_bounds']==[t._f(Fraction(0)),t._f(Fraction(1,2))]
    assert r['conditional_sharp_null_test'] is None and r['n_fully_observed_cases']==2


def test_exhaustive_shared_reference_bounds():
    for h1,h2,y in product((None,False,True),repeat=3):
        choices=lambda v:(False,True) if v is None else (v,)
        v=[int(a==c)+int(b==c) for a,b,c in product(choices(h1),choices(h2),choices(y))]
        assert t.unit_bounds([h1,h2],y)==(min(v),max(v))


def test_exact_test_known_distribution():
    g={str(i):(Fraction(v),Fraction(v)) for i,v in enumerate([0,0,1,1])}
    assert t.exact_test(g,{'2','3'})==dict(extreme_allocations=2,total_allocations=6,p_value=t._f(Fraction(1,3)))
    g={str(i):(Fraction(0),Fraction(0)) for i in range(30)}
    assert t.exact_test(g,set(map(str,range(15)))) is None


@pytest.mark.parametrize('bad',[{'clinicians':['doctor-a']},{'n_assisted':2},{'cluster_by_unit':{'a':'x'}},
    {'randomization_test':1},{'assistance_input_sha256':{'a':'b'*64}}])
def test_invalid_design(bad):
    with pytest.raises(ValueError): design(**bad)


def test_tamper_arm_and_reference():
    a,kw=setup(); altered=copy.deepcopy(kw); altered['submissions']['clinician-a']['rows'][1]['assistance_sha256']='b'*64
    with pytest.raises(ValueError): t.analyze(*a,**altered)
    with pytest.raises(ValueError): t.analyze(*a,**{**kw,'expected_reference':'0'*64})
    altered=copy.deepcopy(kw); altered['allocation']['assisted_clusters']=['y']
    with pytest.raises(ValueError): t.analyze(*a,**altered)
    altered=copy.deepcopy(kw); del altered['submissions']['clinician-b']
    with pytest.raises(ValueError): t.analyze(*a,**altered)


def test_design_detached_from_mutation():
    a=design(); s=copy.deepcopy(a[-2]['spec']); d=t.freeze(*a[:4],s)
    s['cluster_by_unit']['a']='y'
    assert d['spec']['cluster_by_unit']['a']=='x'
