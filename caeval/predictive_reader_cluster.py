"""Fixed-reader matched analysis with shared-reference bounds and cluster inference."""
from fractions import Fraction
from itertools import product
import math

from . import predictive_human_comparison as human, predictive_reference_panel as panel
from . import predictive_research as pred, risk_probability as risk

SCHEMA = 'predictive-reader-cluster/1'


def freeze(plan, expected, reference_panel, panel_digest, comparison, digest, spec):
    c = human.check(plan, expected, reference_panel, panel_digest, comparison, digest)
    pred.fields(spec, 'cluster_by_unit mode alpha_bp margin_bp minimum_clusters')
    clusters = spec['cluster_by_unit']
    pred.require(isinstance(clusters, dict) and set(clusters) == set(c['unit_ids']))
    pred.require(all(risk._token(v) for v in clusters.values()))
    pred.require(spec['mode'] in ('descriptive', 'independent_clusters'))
    n = len(set(clusters.values()))
    if spec['mode'] == 'descriptive':
        pred.require(all(spec[k] is None for k in ('alpha_bp','margin_bp','minimum_clusters')))
    else:
        for k, lo, hi in [('alpha_bp',1,1000),('margin_bp',0,10000),('minimum_clusters',2,n)]:
            pred.require(type(spec[k]) is int and lo <= spec[k] <= hi)
    body = {'schema':SCHEMA,'comparison_sha256':digest,'spec':{**spec,'cluster_by_unit':dict(clusters)}}
    return {**body,'analysis_sha256':pred.digest(body)}


def _fraction(v):
    return {'numerator':v.numerator,'denominator':v.denominator}


def unit_bounds(model, clinicians, reference):
    """Sharp bounds on sum of paired correctness differences for one case."""
    pred.require(bool(clinicians) and all(v is None or type(v) is bool for v in [model,reference,*clinicians]))
    n = len(clinicians)
    lo, hi = [], []
    for m,y in product(human._poss(model),human._poss(reference)):
        correct = sum(v is not None and v == y for v in clinicians)
        unknown = sum(v is None for v in clinicians)
        base = n * int(m == y) - correct
        lo.append(base-unknown); hi.append(base)
    return min(lo),max(hi)


def summarize(rows, mapping, readers):
    """Equal fixed-reader weighting; cluster means retain all assigned cases."""
    pred.require(type(readers) is int and readers > 0 and bool(rows) and set(rows)==set(mapping))
    groups = {}
    complete = 0
    for u,(m,hs,y) in rows.items():
        pred.require(len(hs)==readers and risk._token(mapping[u]))
        lo,hi=unit_bounds(m,hs,y)
        g=groups.setdefault(mapping[u],[0,0,0])
        g[0]+=lo; g[1]+=hi; g[2]+=1
        complete+=int(m is not None and y is not None and all(x is not None for x in hs))
    means=[(Fraction(a,readers*n),Fraction(b,readers*n)) for a,b,n in groups.values()]
    cluster_bounds=[sum((x[i] for x in means),Fraction())/len(means) for i in range(2)]
    unit_weighted=[Fraction(sum(g[i] for g in groups.values()),readers*len(rows)) for i in range(2)]
    return {'n_cases':len(rows),'n_readers':readers,'n_clusters':len(groups),'n_fully_observed_cases':complete,
            'case_weighted_bounds':[_fraction(v) for v in unit_weighted],
            'equal_cluster_bounds':[_fraction(v) for v in cluster_bounds],
            'cluster_sizes':sorted(g[2] for g in groups.values())}


def analyze(plan, expected, reference_panel, panel_digest, comparison, digest, design, expected_design, **inputs):
    rebuilt=freeze(plan,expected,reference_panel,panel_digest,comparison,digest,design['spec'])
    pred.require(rebuilt['analysis_sha256']==expected_design and pred.canonical(rebuilt)==pred.canonical(design))
    checked=human.analyze(plan,expected,reference_panel,panel_digest,comparison,digest,**inputs)
    ref=panel.finalize(plan,expected,reference_panel,panel_digest,inputs['primary'],inputs['expected_primary'],inputs['adjudication'],inputs['expected_adjudication'])
    labels=pred._bundle(plan,expected,'reference',ref['bundle'],ref['bundle']['bundle_sha256'])
    scores=pred._bundle(plan,expected,'candidate',inputs['model'],inputs['expected_model'])
    clinicians=comparison['spec']['clinicians']
    values={r:{x['unit_id']:x['action'] for x in inputs['submissions'][r]['rows']} for r in clinicians}
    threshold=comparison['spec']['threshold_bp']
    rows={u:(None if scores.get(u) is None else scores[u]>=threshold,[values[r].get(u) for r in clinicians],labels.get(u)) for u in comparison['unit_ids']}
    spec=design['spec']; summary=summarize(rows,spec['cluster_by_unit'],len(clinicians))
    inference=None
    if spec['mode']=='independent_clusters':
        n=summary['n_clusters']; alpha=spec['alpha_bp']/10000
        # Hoeffding: independent cluster means in [-1,1], fixed reader roster.
        radius=math.sqrt(2*math.log(1/alpha)/n)
        v=summary['equal_cluster_bounds'][0]
        lower=max(-1.0,float(Fraction(v['numerator'],v['denominator']))-radius)
        inference={'assumption':'independent_clusters_fixed_readers_not_verified','alpha_bp':spec['alpha_bp'],
                   'margin_bp':spec['margin_bp'],'one_sided_lower_bound':lower,'hoeffding_radius':radius,
                   'margin_signal':lower > -spec['margin_bp']/10000}
    body={'schema':SCHEMA,'analysis_sha256':expected_design,'comparison_report_sha256':checked['report_sha256'],
          'summary':summary,'conditional_inference':inference,'reader_population_inference':False,
          'clinical_noninferiority_established':False,'decision_grade':False,'publication_safe':False}
    return {**body,'report_sha256':pred.digest(body)}


def verify(*args, report, expected_report, **inputs):
    rebuilt=analyze(*args,**inputs)
    return rebuilt['report_sha256']==expected_report and pred.canonical(rebuilt)==pred.canonical(report)
