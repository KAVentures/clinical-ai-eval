"""Experimental prespecified subgroup analysis with fixed paired denominators."""
from collections import Counter, defaultdict
import hashlib
import json
import math

MAX_UNITS = 100_000
MAX_GROUPS = 100
SCHEMA = 'subgroup-evidence/1'


class SubgroupError(ValueError):
    pass


def _identifier(x):
    return isinstance(x, str) and 0 < len(x) <= 128 and x == x.strip() and all(32 <= ord(c) != 127 for c in x)


def _digest(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()


def freeze(units, groups, *, minimum_group=20):
    """Freeze exact eligible units and mutually exclusive, prespecified strata."""
    if type(minimum_group) is not int or not 1 <= minimum_group <= MAX_UNITS:
        raise SubgroupError('invalid minimum group size')
    if not isinstance(groups, list) or not 1 <= len(groups) <= MAX_GROUPS or not all(_identifier(g) for g in groups) or len(set(groups)) != len(groups):
        raise SubgroupError('invalid group inventory')
    if not isinstance(units, list) or not 1 <= len(units) <= MAX_UNITS:
        raise SubgroupError('invalid unit inventory')
    seen = set()
    clean = []
    for unit in units:
        if not isinstance(unit, dict) or set(unit) != {'unit_id', 'group'} or not _identifier(unit['unit_id']) or unit['unit_id'] in seen:
            raise SubgroupError('invalid or duplicate unit')
        if unit['group'] is not None and (not _identifier(unit['group']) or unit['group'] not in groups):
            raise SubgroupError('undeclared group')
        seen.add(unit['unit_id'])
        clean.append(dict(unit))
    plan = {'schema': SCHEMA, 'groups': sorted(groups), 'units': sorted(clean, key=lambda x: x['unit_id']), 'minimum_group': minimum_group}
    return {'plan': plan, 'plan_sha256': _digest(plan)}


def _plan(frozen, expected_digest):
    if not isinstance(frozen, dict) or set(frozen) != {'plan', 'plan_sha256'} or not isinstance(expected_digest, str):
        raise SubgroupError('invalid frozen plan')
    p = frozen['plan']
    if not isinstance(p, dict) or set(p) != {'schema', 'groups', 'units', 'minimum_group'}:
        raise SubgroupError('invalid plan fields')
    rebuilt = freeze(p['units'], p['groups'], minimum_group=p['minimum_group'])
    if rebuilt != frozen or rebuilt['plan_sha256'] != expected_digest:
        raise SubgroupError('plan digest mismatch')
    return p


def _outcomes(rows, ids):
    if not isinstance(rows, list) or len(rows) > len(ids):
        raise SubgroupError('invalid outcome inventory')
    indexed = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'unit_id', 'baseline', 'candidate'}:
            raise SubgroupError('invalid outcome fields')
        u = row['unit_id']
        if not _identifier(u) or u not in ids or u in indexed:
            raise SubgroupError('unknown or duplicate outcome unit')
        for arm in ('baseline', 'candidate'):
            if row[arm] is not None and type(row[arm]) is not bool:
                raise SubgroupError('outcomes must be boolean or null')
        indexed[u] = (row['baseline'], row['candidate'])
    return indexed


def _ratio(k, n):
    return k / n if n else None


def _summary(pairs):
    n = len(pairs)
    cells = Counter(pairs)
    arms = {}
    for i, name in enumerate(('baseline', 'candidate')):
        events = sum(v[i] is True for v in pairs)
        observed = sum(v[i] is not None for v in pairs)
        arms[name] = {'observed': observed, 'events': events, 'missing': n-observed,
                      'observed_rate': _ratio(events, observed),
                      'cohort_rate_bounds': [_ratio(events, n), _ratio(events+n-observed, n)]}
    # The extrema are attainable simultaneously within each pair; no imputation.
    lower = sum((0 if c is None else int(c)) - (1 if b is None else int(b)) for b, c in pairs)
    upper = sum((1 if c is None else int(c)) - (0 if b is None else int(b)) for b, c in pairs)
    complete = sum(v[0] is not None and v[1] is not None for v in pairs)
    worsening, improving = cells[False, True], cells[True, False]
    return {'n': n, 'arms': arms, 'complete_pairs': complete,
            'observed_worsenings': worsening, 'observed_improvements': improving,
            'observed_paired_delta': _ratio(worsening-improving, complete),
            'cohort_delta_bounds': [_ratio(lower, n), _ratio(upper, n)]}


def analyze(frozen, expected_digest, outcomes):
    """Analyze adverse binary events; positive candidate-minus-baseline is worse.

    Return local restricted descriptive data, not a population fairness verdict.
    """
    p = _plan(frozen, expected_digest)
    ids = {x['unit_id'] for x in p['units']}
    indexed = _outcomes(outcomes, ids)
    buckets = defaultdict(list)
    for unit in p['units']:
        buckets[unit['group']].append(indexed.get(unit['unit_id'], (None, None)))
    report = []
    for group in p['groups'] + [None]:
        pairs = buckets[group]
        if len(pairs) < p['minimum_group']:
            # This is an analysis adequacy flag, not privacy suppression.
            report.append({'group': group, 'status': 'INSUFFICIENT_UNITS', 'n': len(pairs)})
        else:
            report.append({'group': group, 'status': 'DESCRIPTIVE_ONLY', **_summary(pairs)})
    return {'schema': SCHEMA, 'plan_sha256': expected_digest, 'n_planned': len(ids),
            'n_outcome_rows': len(indexed), 'unknown_group_units': len(buckets[None]),
            'groups': report, 'decision_grade': False, 'publication_safe': False}


def contrast(report, group_a, group_b, *, arm='candidate'):
    """Sharp finite-cohort difference range between two disjoint declared groups."""
    if arm not in ('baseline', 'candidate', 'paired_delta') or not _identifier(group_a) or not _identifier(group_b) or group_a == group_b:
        raise SubgroupError('invalid contrast')
    # Recompute contrasts from an analyze() result in the trusted local process.
    groups = {r['group']: r for r in report['groups']}
    a, b = groups.get(group_a), groups.get(group_b)
    if a is None or b is None or any(g['status'] != 'DESCRIPTIVE_ONLY' for g in (a, b)):
        return {'status': 'INSUFFICIENT_EVIDENCE', 'difference_bounds': None, 'decision_grade': False}
    def bounds(g):
        v = g['cohort_delta_bounds'] if arm == 'paired_delta' else g['arms'][arm]['cohort_rate_bounds']
        if len(v) != 2 or not all(type(x) in (float, int) and math.isfinite(x) and -1 <= x <= 1 for x in v) or v[0] > v[1]:
            raise SubgroupError('invalid bounds')
        return v
    l1, h1 = bounds(a)
    l2, h2 = bounds(b)
    return {'status': 'DESCRIPTIVE_ONLY', 'arm': arm, 'difference_bounds': [l1-h2, h1-l2], 'decision_grade': False}
