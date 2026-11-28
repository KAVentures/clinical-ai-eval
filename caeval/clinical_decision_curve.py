"""Experimental fixed-threshold clinical decision curves, with missingness bounds."""
from collections import Counter
from fractions import Fraction
from itertools import product
import hashlib
import json
import re

SCALE = 10000
MAX_UNITS = 100000
MAX_THRESHOLDS = 50
SCHEMA = 'clinical-decision-curve/1'
POLICIES = ('baseline', 'candidate', 'act_all', 'act_none')


class CurveError(ValueError):
    pass


def _fields(obj, names):
    if not isinstance(obj, dict) or set(obj) != set(names.split()):
        raise CurveError('unexpected fields')


def _token(x):
    return isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', x) is not None


def _bp(x):
    return type(x) is int and 0 <= x <= SCALE


def _digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()


def freeze(spec):
    """Lock endpoint, horizon, action, versions, eligible units and thresholds."""
    _fields(spec, 'endpoint horizon action baseline_version candidate_version unit_ids thresholds_bp')
    if not all(_token(spec[k]) for k in ('endpoint', 'horizon', 'action', 'baseline_version', 'candidate_version')):
        raise CurveError('invalid protocol token')
    if spec['baseline_version'] == spec['candidate_version']:
        raise CurveError('distinct versions required')
    ids, thresholds = spec['unit_ids'], spec['thresholds_bp']
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_UNITS or not all(_token(i) for i in ids) or len(set(ids)) != len(ids):
        raise CurveError('invalid eligible inventory')
    if (not isinstance(thresholds, list) or not 1 <= len(thresholds) <= MAX_THRESHOLDS
            or not all(_bp(t) and 0 < t < SCALE for t in thresholds)
            or len(set(thresholds)) != len(thresholds)):
        raise CurveError('thresholds must be distinct and strictly inside (0,1)')
    plan = {**spec, 'unit_ids': sorted(ids), 'thresholds_bp': sorted(thresholds)}
    return {'schema': SCHEMA, 'plan': plan, 'plan_sha256': _digest([SCHEMA, plan])}


def _index(rows, ids):
    if not isinstance(rows, list) or len(rows) > len(ids):
        raise CurveError('invalid outcome inventory')
    out = {}
    for r in rows:
        _fields(r, 'unit_id label baseline_bp candidate_bp')
        u = r['unit_id']
        if not _token(u) or u not in ids or u in out:
            raise CurveError('unknown or duplicate unit')
        if r['label'] is not None and type(r['label']) is not bool:
            raise CurveError('label must be boolean or null')
        if any(r[k] is not None and not _bp(r[k]) for k in ('baseline_bp', 'candidate_bp')):
            raise CurveError('probability must be integer basis points or null')
        out[u] = (r['label'], r['baseline_bp'], r['candidate_bp'])
    return out


def _choices(x):
    return (0, 1) if x is None else (int(x),)


def _action(p, threshold):
    return None if p is None else int(p >= threshold)


def _utility(y, a, t):
    return a * (SCALE - t if y else -t)


def _range(values):
    return min(values), max(values)


def _rate(k, n):
    return float(Fraction(k, n)) if n else None


def _ratio_bounds(counts, label, action):
    """Sharp conditional ratio bounds over completions with nonzero denominator."""
    mandatory = mandatory_min = mandatory_max = optional_bad = optional_good = 0
    for (y, a), n in counts.items():
        if y == label:
            mandatory += n
            mandatory_min += n * (a == action)
            mandatory_max += n * (a in (None, action))
        elif y is None:
            optional_bad += n * (a in (None, 1-action))
            optional_good += n * (a in (None, action))
    if mandatory + optional_bad + optional_good == 0:
        return [None, None]
    low_den = mandatory + optional_bad
    high_den = mandatory + optional_good
    return [_rate(mandatory_min, low_den) if low_den else 1.0,
            _rate(mandatory_max + optional_good, high_den) if high_den else 0.0]


def _policy(counts, n, t):
    tp = counts[1, 1]
    fp = counts[0, 1]
    tn = counts[0, 0]
    fn = counts[1, 0]
    known = tp + fp + tn + fn
    lo = hi = 0
    for (y, a), m in counts.items():
        l, h = _range([_utility(yy, aa, t) for yy, aa in product(_choices(y), _choices(a))])
        lo += m*l
        hi += m*h
    denom = n*(SCALE-t)
    return {'n_complete': known, 'n_action_known': sum(m for (y, a), m in counts.items() if a is not None),
            'complete_confusion': {'tp': tp, 'fp': fp, 'tn': tn, 'fn': fn},
            'complete_sensitivity': _rate(tp, tp+fn), 'complete_specificity': _rate(tn, tn+fp),
            'cohort_sensitivity_bounds_conditional': _ratio_bounds(counts, 1, 1),
            'cohort_specificity_bounds_conditional': _ratio_bounds(counts, 0, 0),
            'complete_case_net_benefit': _rate(tp*(SCALE-t)-fp*t, known*(SCALE-t)),
            'cohort_net_benefit_bounds': [_rate(lo, denom), _rate(hi, denom)]}


def _contrast(counts, n, t):
    """Candidate minus comparator, with one shared reference outcome per unit."""
    lo = hi = known = observed = 0
    for (y, c, b), m in counts.items():
        l, h = _range([_utility(yy, cc, t)-_utility(yy, bb, t)
                       for yy, cc, bb in product(_choices(y), _choices(c), _choices(b))])
        lo += m*l
        hi += m*h
        if y is not None and c is not None and b is not None:
            known += m
            observed += m*(_utility(y, c, t)-_utility(y, b, t))
    return {'n_complete': known, 'complete_case_delta': _rate(observed, known*(SCALE-t)),
            'cohort_delta_bounds': [_rate(lo, n*(SCALE-t)), _rate(hi, n*(SCALE-t))]}


def _threshold(cohort, t):
    policies = {p: Counter() for p in POLICIES}
    contrasts = {p: Counter() for p in POLICIES if p != 'candidate'}
    for (y, bp, cp), m in cohort.items():
        a = dict(zip(POLICIES, (_action(bp, t), _action(cp, t), 1, 0)))
        for p in POLICIES:
            policies[p][y, a[p]] += m
        for p in contrasts:
            contrasts[p][y, a['candidate'], a[p]] += m
    n = sum(cohort.values())
    return {'threshold_bp': t, 'false_positive_weight': _rate(t, SCALE-t),
            'policies': {p: _policy(counts, n, t) for p, counts in policies.items()},
            'candidate_minus': {p: _contrast(counts, n, t) for p, counts in contrasts.items()}}


def analyze(frozen, expected_digest, rows):
    _fields(frozen, 'schema plan plan_sha256')
    if freeze(frozen['plan']) != frozen or frozen['plan_sha256'] != expected_digest:
        raise CurveError('prospective plan digest mismatch')
    plan = frozen['plan']
    indexed = _index(rows, set(plan['unit_ids']))
    cohort = Counter(indexed.get(u, (None, None, None)) for u in plan['unit_ids'])
    return {'schema': SCHEMA, 'plan_sha256': expected_digest, 'n_planned': len(plan['unit_ids']),
            'n_reference_known': sum(m for (y, b, c), m in cohort.items() if y is not None),
            'n_outcome_rows': len(indexed), 'thresholds': [_threshold(cohort, t) for t in plan['thresholds_bp']],
            'decision_grade': False, 'publication_safe': False}
