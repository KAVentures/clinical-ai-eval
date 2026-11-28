"""Prospective cluster allocation for fixed-reader assisted decision studies."""
from fractions import Fraction
from itertools import combinations
from math import comb
from secrets import randbelow

from . import predictive_research as pred, predictive_reference_panel as panel
from . import risk_probability as risk

SCHEMA = 'predictive-assisted-trial/1'
MAX_CLUSTERS = 200
MAX_EXACT = 100000


def freeze(plan, expected, reference_panel, panel_digest, spec):
    p = pred._plan(plan, expected)
    ref = panel._panel(plan, expected, reference_panel, panel_digest)
    pred.fields(spec, 'clinicians cluster_by_unit n_assisted decision_protocol_sha256 assistance_protocol_sha256 decision_input_sha256 assistance_input_sha256 randomization_test')
    pred.require(type(spec['randomization_test']) is bool)
    readers = spec['clinicians']
    pred.require(isinstance(readers, list) and 1 <= len(readers) <= 30)
    pred.require(all(risk._token(x) for x in readers) and len(set(readers)) == len(readers))
    pred.require(not set(readers).intersection(ref['actors'].values()))
    units = p['unit_ids']
    mapping = spec['cluster_by_unit']
    pred.require(isinstance(mapping, dict) and set(mapping) == set(units) and all(risk._token(x) for x in mapping.values()))
    clusters = sorted(set(mapping.values()))
    n = len(clusters); k = spec['n_assisted']
    pred.require(2 <= n <= MAX_CLUSTERS and type(k) is int and 1 <= k < n)
    for key in ('decision_protocol_sha256', 'assistance_protocol_sha256'):
        pred.require(pred.hex64(spec[key]))
    for key in ('decision_input_sha256', 'assistance_input_sha256'):
        inv = spec[key]
        pred.require(isinstance(inv, dict) and set(inv) == set(units) and all(pred.hex64(x) for x in inv.values()))
    body = {'schema': SCHEMA, 'plan_sha256': expected, 'panel_sha256': panel_digest,
            'spec': {**spec, 'clinicians': sorted(readers)}, 'unit_ids': units, 'clusters': clusters,
            'model_version': p['candidate_version'], 'endpoint': p['endpoint'], 'horizon': p['horizon'], 'action': p['action']}
    # Detach the frozen design from mutable caller input.
    import json
    body = json.loads(pred.canonical(body))
    return {**body, 'design_sha256': pred.digest(body)}


def check(plan, expected, reference_panel, panel_digest, design, design_digest):
    rebuilt = freeze(plan, expected, reference_panel, panel_digest, design['spec'])
    pred.require(rebuilt['design_sha256'] == design_digest and pred.canonical(rebuilt) == pred.canonical(design))
    return rebuilt


def unrank(items, k, rank):
    """Lexicographic combination unranking: each allocation has exactly one rank."""
    n = len(items)
    pred.require(type(k) is int and 0 <= k <= n and type(rank) is int and 0 <= rank < comb(n, k))
    result = []
    for i, item in enumerate(items):
        if not k:
            break
        size = comb(n-i-1, k-1)
        if rank < size:
            result.append(item); k -= 1
        else:
            rank -= size
    return result


def allocate(plan, expected, reference_panel, panel_digest, design, design_digest, rank):
    d = check(plan, expected, reference_panel, panel_digest, design, design_digest)
    assisted = unrank(d['clusters'], d['spec']['n_assisted'], rank)
    body = {'schema': SCHEMA, 'design_sha256': design_digest, 'rank': rank, 'assisted_clusters': assisted}
    return {**body, 'allocation_sha256': pred.digest(body)}


def draw(plan, expected, reference_panel, panel_digest, design, design_digest, *, confirmed=False):
    pred.require(confirmed is True)
    d = check(plan, expected, reference_panel, panel_digest, design, design_digest)
    return allocate(plan, expected, reference_panel, panel_digest, d, design_digest,
                    randbelow(comb(len(d['clusters']), d['spec']['n_assisted'])))


def _allocation(args, allocation, allocation_digest):
    rebuilt = allocate(*args, allocation['rank'])
    pred.require(rebuilt['allocation_sha256'] == allocation_digest and pred.canonical(rebuilt) == pred.canonical(allocation))
    return rebuilt


def packet(*args, allocation, allocation_digest, clinician):
    a = _allocation(args, allocation, allocation_digest)
    d = args[-2]; s = d['spec']
    pred.require(clinician in s['clinicians'])
    assisted = set(a['assisted_clusters'])
    rows = []
    for u in d['unit_ids']:
        arm = 'assisted' if s['cluster_by_unit'][u] in assisted else 'unassisted'
        rows.append({'unit_id':u,'arm':arm,'input_sha256':s['decision_input_sha256'][u],
                     'assistance_sha256':s['assistance_input_sha256'][u] if arm == 'assisted' else None})
    body = {'schema':SCHEMA,'allocation_sha256':allocation_digest,'clinician':clinician,
            'decision_protocol_sha256':s['decision_protocol_sha256'],'assistance_protocol_sha256':s['assistance_protocol_sha256'],'items':rows}
    return {**body,'packet_sha256':pred.digest(body)}


def seal(*args, allocation, allocation_digest, clinician, source_receipt, rows):
    p = packet(*args, allocation=allocation, allocation_digest=allocation_digest, clinician=clinician)
    pred.require(pred.hex64(source_receipt) and isinstance(rows,list) and len(rows) <= len(p['items']))
    eligible = {x['unit_id']:x for x in p['items']}; values = {}
    for r in rows:
        pred.fields(r,'unit_id arm input_sha256 assistance_sha256 action')
        u = r['unit_id']; v = r['action']
        pred.require(risk._token(u) and u in eligible and u not in values and (v is None or type(v) is bool))
        pred.require({k:r[k] for k in eligible[u]} == eligible[u])
        values[u] = {**eligible[u], 'action':v}
    body = {'schema':SCHEMA,'packet_sha256':p['packet_sha256'],'clinician':clinician,
            'source_receipt_sha256':source_receipt,'rows':[values[u] for u in sorted(values)]}
    return {**body,'submission_sha256':pred.digest(body)}


def unit_bounds(actions, label):
    pred.require(bool(actions) and all(x is None or type(x) is bool for x in [*actions,label]))
    unknown = sum(x is None for x in actions)
    counts = [sum(x is not None and x == y for x in actions) for y in ((False,True) if label is None else (label,))]
    return min(counts), max(counts)+unknown


def _f(x):
    return {'numerator':x.numerator,'denominator':x.denominator}


def contrast(groups, treated):
    """Equal cluster means and sharp arm contrast bounds."""
    t = [groups[k] for k in groups if k in treated]
    c = [groups[k] for k in groups if k not in treated]
    pred.require(bool(t) and bool(c))
    mean = lambda a,i: sum((v[i] for v in a),Fraction())/len(a)
    return mean(t,0)-mean(c,1), mean(t,1)-mean(c,0)


def exact_test(groups, treated):
    """Two-sided Fisher randomization test of the sharp null; complete data only."""
    n,k=len(groups),len(treated); total=comb(n,k)
    pred.require(0<k<n and set(treated)<=set(groups))
    if total>MAX_EXACT or any(a!=b for a,b in groups.values()):
        return None
    # Linear transformation of the treated sum; exact rational comparisons.
    overall=sum((v[0] for v in groups.values()),Fraction())
    def statistic(ids):
        return abs(n*sum((groups[u][0] for u in ids),Fraction())-k*overall)
    observed=statistic(treated)
    extreme=sum(statistic(ids)>=observed for ids in combinations(sorted(groups),k))
    return {'extreme_allocations':extreme,'total_allocations':total,'p_value':_f(Fraction(extreme,total))}


def analyze(*args, allocation, allocation_digest, primary, expected_primary, adjudication, expected_adjudication,
            expected_reference, submissions, expected_submissions, randomization_assumptions_declared=False):
    a = _allocation(args, allocation, allocation_digest); d=args[-2]; readers=d['spec']['clinicians']
    pred.require(type(randomization_assumptions_declared) is bool)
    ref=panel.finalize(*args[:4],primary,expected_primary,adjudication,expected_adjudication)
    pred.require(ref['receipt']['receipt_sha256']==expected_reference)
    labels=pred._bundle(args[0],args[1],'reference',ref['bundle'],ref['bundle']['bundle_sha256'])
    pred.require(isinstance(submissions,dict) and isinstance(expected_submissions,dict) and set(submissions)==set(expected_submissions)==set(readers))
    actions={}
    for reader in readers:
        s=submissions[reader]
        rebuilt=seal(*args,allocation=a,allocation_digest=allocation_digest,clinician=reader,source_receipt=s['source_receipt_sha256'],rows=s['rows'])
        pred.require(rebuilt['submission_sha256']==expected_submissions[reader] and pred.canonical(rebuilt)==pred.canonical(s))
        actions[reader]={x['unit_id']:x['action'] for x in s['rows']}
    counts={k:[0,0,0] for k in d['clusters']}; complete=0
    for u in d['unit_ids']:
        y=labels.get(u); hs=[actions[r].get(u) for r in readers]; lo,hi=unit_bounds(hs,y)
        g=counts[d['spec']['cluster_by_unit'][u]]; g[0]+=lo; g[1]+=hi; g[2]+=len(readers)
        complete+=int(y is not None and all(x is not None for x in hs))
    groups={k:(Fraction(lo,n),Fraction(hi,n)) for k,(lo,hi,n) in counts.items()}
    treated=set(a['assisted_clusters']); bounds=contrast(groups,treated)
    test=None
    if d['spec']['randomization_test'] and randomization_assumptions_declared and complete==len(d['unit_ids']):
        test=exact_test(groups,treated)
    body={'schema':SCHEMA,'design_sha256':args[-1],'allocation_sha256':allocation_digest,'reference_receipt_sha256':expected_reference,
          'submission_sha256':dict(expected_submissions),'n_planned_cases':len(d['unit_ids']),'n_readers':len(readers),
          'n_fully_observed_cases':complete,'n_assisted_clusters':len(treated),'n_unassisted_clusters':len(groups)-len(treated),
          'assisted_minus_unassisted_equal_cluster_bounds':[_f(x) for x in bounds],
          'randomization_assumptions_declared':randomization_assumptions_declared,'conditional_sharp_null_test':test,
          'randomization_integrity_verified':False,'external_blinding_verified':False,'clinical_benefit_established':False,
          'decision_grade':False,'publication_safe':False}
    return {**body,'report_sha256':pred.digest(body)}


def verify(*args, report, expected_report, **kw):
    r=analyze(*args,**kw)
    return r['report_sha256']==expected_report and pred.canonical(r)==pred.canonical(report)
