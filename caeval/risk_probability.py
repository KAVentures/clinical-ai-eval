"""Prospective descriptive calibration of matched clinical risk probabilities."""
import hashlib
import json
from collections import defaultdict
from fractions import Fraction
import re

MAX_UNITS = 100_000
MAX_BINS = 50
SCALE = 10_000
SCHEMA = 'risk-probability-study/1'


class RiskError(ValueError):
    pass


def _token(x):
    return isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', x) is not None


def _keys(x, keys):
    if not isinstance(x, dict) or set(x) != set(keys.split()):
        raise RiskError('unexpected fields')


def _bp(x):
    return type(x) is int and 0 <= x <= SCALE


def _hash(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()


def freeze(spec):
    _keys(spec, 'endpoint horizon baseline_version candidate_version unit_ids bin_edges_bp')
    if not all(_token(spec[k]) for k in ('endpoint', 'horizon', 'baseline_version', 'candidate_version')):
        raise RiskError('invalid protocol token')
    if spec['baseline_version'] == spec['candidate_version']:
        raise RiskError('distinct versions required')
    ids, edges = spec['unit_ids'], spec['bin_edges_bp']
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_UNITS or not all(_token(u) for u in ids) or len(set(ids)) != len(ids):
        raise RiskError('invalid eligible unit inventory')
    if not isinstance(edges, list) or not 2 <= len(edges) <= MAX_BINS + 1 or not all(_bp(e) for e in edges) or edges != sorted(set(edges)) or edges[0] != 0 or edges[-1] != SCALE:
        raise RiskError('invalid calibration bins')
    plan = {**spec, 'unit_ids': sorted(ids), 'bin_edges_bp': list(edges)}
    return {'schema': SCHEMA, 'plan': plan, 'plan_sha256': _hash([SCHEMA, plan])}


def _index(rows, ids):
    if not isinstance(rows, list) or len(rows) > len(ids):
        raise RiskError('invalid outcomes inventory')
    out = {}
    for r in rows:
        _keys(r, 'unit_id label baseline_bp candidate_bp')
        u = r['unit_id']
        if not _token(u) or u not in ids or u in out:
            raise RiskError('unexpected or duplicate outcome unit')
        if r['label'] is not None and type(r['label']) is not bool:
            raise RiskError('invalid reference label')
        if any(r[k] is not None and not _bp(r[k]) for k in ('baseline_bp', 'candidate_bp')):
            raise RiskError('invalid probability')
        out[u] = (r['label'], r['baseline_bp'], r['candidate_bp'])
    return out


def _loss(p, y):
    # Integer squared loss in basis-point-squared units.
    return (p - SCALE * int(y)) ** 2


def _bounds(p, y):
    if p is None:
        return 0, SCALE ** 2
    loss = [_loss(p, t) for t in ((False, True) if y is None else (y,))]
    return min(loss), max(loss)


def _delta_bounds(b, c, y):
    # Both arms share the same unknown true label; do not vary it independently.
    bounds = []
    for t in ((False, True) if y is None else (y,)):
        bl, bh = _bounds(b, t)
        cl, ch = _bounds(c, t)
        bounds.append((cl - bh, ch - bl))
    return min(x[0] for x in bounds), max(x[1] for x in bounds)


def _mean(k, n):
    return float(Fraction(k, n)) if n else None


def _arm(rows, pos, edges):
    from bisect import bisect_right
    bins = defaultdict(list)
    observed = 0
    loss = 0
    lo = hi = 0
    n = len(rows)
    for row in rows:
        y, p = row[0], row[pos]
        l, h = _bounds(p, y)
        lo += l
        hi += h
        if p is not None:
            bins[min(bisect_right(edges, p) - 1, len(edges)-2)].append((y, p))
            if y is not None:
                observed += 1
                loss += _loss(p, y)
    result = []
    for i, (left, right) in enumerate(zip(edges, edges[1:])):
        bucket = bins[i]
        k = len(bucket)
        known = sum(y is not None for y, _ in bucket)
        events = sum(y is True for y, _ in bucket)
        prob_sum = sum(p for _, p in bucket)
        result.append({'lower_bp': left, 'upper_bp': right, 'upper_inclusive': i == len(edges)-2,
                       'n_scored': k, 'n_reference_known': known,
                       'mean_probability': _mean(prob_sum, k*SCALE),
                       'observed_event_rate': _mean(events, known),
                       'event_rate_bounds_given_scores': [_mean(events, k), _mean(events+k-known, k)]})
    return {'n_scored': sum(len(v) for v in bins.values()), 'n_complete': observed,
            'complete_case_brier': _mean(loss, observed*SCALE**2),
            'cohort_brier_bounds': [_mean(lo, n*SCALE**2), _mean(hi, n*SCALE**2)], 'calibration_bins': result}


def analyze(frozen, expected_digest, rows):
    _keys(frozen, 'schema plan plan_sha256')
    if freeze(frozen['plan']) != frozen or frozen['plan_sha256'] != expected_digest:
        raise RiskError('prospective plan digest mismatch')
    plan = frozen['plan']
    indexed = _index(rows, set(plan['unit_ids']))
    cohort = [indexed.get(u, (None, None, None)) for u in plan['unit_ids']]
    n = len(cohort)
    lo = hi = complete = observed_delta = 0
    for y, b, c in cohort:
        l, h = _delta_bounds(b, c, y)
        lo += l
        hi += h
        if y is not None and b is not None and c is not None:
            complete += 1
            observed_delta += _loss(c, y) - _loss(b, y)
    return {'schema': SCHEMA, 'plan_sha256': expected_digest, 'n_planned': n,
            'n_reference_known': sum(y is not None for y, _, _ in cohort),
            'n_outcome_rows': len(indexed),
            'baseline': _arm(cohort, 1, plan['bin_edges_bp']),
            'candidate': _arm(cohort, 2, plan['bin_edges_bp']),
            'paired': {'n_complete': complete, 'complete_case_brier_delta': _mean(observed_delta, complete*SCALE**2),
                       'cohort_brier_delta_bounds': [_mean(lo, n*SCALE**2), _mean(hi, n*SCALE**2)]},
            'decision_grade': False, 'publication_safe': False}
