from fractions import Fraction
from itertools import product
import pytest
from caeval import predictive_reader_cluster as r
from test_predictive_human_comparison import setup


def test_shared_bounds_exhaustive():
    vals=(None,False,True)
    for m,h1,h2,y in product(vals,repeat=4):
        actual=[]
        for a,b,c,d in product(*[(False,True) if x is None else (x,) for x in (m,h1,h2,y)]):
            actual.append(2*int(a==d)-int(b==d)-int(c==d))
        assert r.unit_bounds(m,[h1,h2],y)==(min(actual),max(actual))


def test_unequal_clusters():
    rows={'a':(True,[False,False],True),'b':(False,[True,True],True),'c':(False,[True,True],True)}
    s=r.summarize(rows,{'a':'one','b':'two','c':'two'},2)
    assert s['case_weighted_bounds']==[r._fraction(Fraction(-1,3))]*2
    assert s['equal_cluster_bounds']==[r._fraction(Fraction(0))]*2
    assert s['n_fully_observed_cases']==3


def design(args, **changes):
    spec=dict(cluster_by_unit={'a':'x','b':'y','c':'y'},mode='descriptive',alpha_bp=None,margin_bp=None,minimum_clusters=None)
    return r.freeze(*args,{**spec,**changes})


def test_report_replay_and_unknown():
    args,kw=setup(); d=design(args); a=(*args,d,d['analysis_sha256'])
    result=r.analyze(*a,**kw)
    assert result['summary']['n_readers']==2 and result['summary']['n_clusters']==2
    assert result['summary']['n_fully_observed_cases']==1
    assert result['conditional_inference'] is None
    assert r.verify(*a,report=result,expected_report=result['report_sha256'],**kw)
    assert not r.verify(*a,report=result,expected_report='0'*64,**kw)
    with pytest.raises(ValueError): r.analyze(*a,**{**kw,'expected_reference':'0'*64})


def test_conditional_inference():
    args,kw=setup(); d=design(args,mode='independent_clusters',alpha_bp=500,margin_bp=1000,minimum_clusters=2)
    result=r.analyze(*args,d,d['analysis_sha256'],**kw)
    i=result['conditional_inference']; assert i['one_sided_lower_bound']==-1 and not i['margin_signal']
    assert not result['reader_population_inference'] and not result['clinical_noninferiority_established']


@pytest.mark.parametrize('changes',[{'cluster_by_unit':{'a':'x'}},{'margin_bp':100},{'mode':'random_readers'},
    {'mode':'independent_clusters','alpha_bp':500,'margin_bp':1000,'minimum_clusters':3},
    {'mode':'independent_clusters','alpha_bp':True,'margin_bp':1000,'minimum_clusters':2}])
def test_invalid_design(changes):
    args,_=setup()
    with pytest.raises(ValueError): design(args,**changes)
