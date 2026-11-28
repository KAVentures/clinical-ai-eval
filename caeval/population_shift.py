"""Prospective aggregate-only marginal population comparability analysis."""
from fractions import Fraction
import hashlib
import json
import re

SCHEMA = 'population-shift/1'
MAX_N = 10_000_000
MAX_FEATURES = 100
MAX_LEVELS = 100


class ShiftError(ValueError):
    pass


def _fields(obj, keys):
    if not isinstance(obj, dict) or set(obj) != set(keys.split()):
        raise ShiftError('unexpected fields')


def _token(x):
    return isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', x) is not None


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()


def freeze(spec):
    """Lock populations, denominators, coding version, features and warning limits."""
    _fields(spec, 'study reference_population target_population coding_version reference_n target_n features')
    if not all(_token(spec[k]) for k in ('study', 'reference_population', 'target_population', 'coding_version')):
        raise ShiftError('invalid population or protocol token')
    if spec['reference_population'] == spec['target_population']:
        raise ShiftError('distinct population identifiers required')
    if any(type(spec[k]) is not int or not 1 <= spec[k] <= MAX_N for k in ('reference_n', 'target_n')):
        raise ShiftError('invalid cohort size')
    fs = spec['features']
    if not isinstance(fs, list) or not 1 <= len(fs) <= MAX_FEATURES:
        raise ShiftError('invalid feature inventory')
    seen = set()
    clean = []
    for f in fs:
        _fields(f, 'feature levels warning_bp')
        name, levels, warning = f['feature'], f['levels'], f['warning_bp']
        if not _token(name) or name in seen:
            raise ShiftError('invalid or duplicate feature')
        if not isinstance(levels, list) or not 2 <= len(levels) <= MAX_LEVELS or not all(_token(x) for x in levels) or len(set(levels)) != len(levels):
            raise ShiftError('invalid category inventory')
        if type(warning) is not int or not 0 < warning <= 10000:
            raise ShiftError('invalid warning limit')
        seen.add(name)
        clean.append({'feature': name, 'levels': sorted(levels), 'warning_bp': warning})
    plan = {**spec, 'features': sorted(clean, key=lambda x: x['feature'])}
    return {'schema': SCHEMA, 'plan': plan, 'plan_sha256': _digest([SCHEMA, plan])}


def _counts(rows, features, n):
    """Absent feature rows are unknown; present rows must enumerate every level."""
    if not isinstance(rows, dict) or not set(rows) <= set(features):
        raise ShiftError('unknown feature')
    result = {}
    for name, levels in features.items():
        if name not in rows:
            result[name] = [0] * len(levels)
            continue
        counts = rows[name]
        if not isinstance(counts, dict) or set(counts) != set(levels):
            raise ShiftError('category inventory mismatch')
        if any(type(v) is not int or not 0 <= v <= n for v in counts.values()) or sum(counts.values()) > n:
            raise ShiftError('invalid count or denominator')
        result[name] = [counts[level] for level in levels]
    return result


def tv_bounds(a, b, na, nb):
    """Exact rational continuous-relaxation lower and attainable upper TV bounds.

    Counts are aligned on at least two exhaustive, disjoint categories. Unknown
    values may be assigned arbitrarily. The lower bound can be conservative for
    indivisible units with different cohort denominators.
    """
    if (type(na) is not int or type(nb) is not int or min(na, nb) < 1
            or not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple))
            or len(a) != len(b) or len(a) < 2
            or any(type(v) is not int or v < 0 for v in (*a, *b))
            or sum(a) > na or sum(b) > nb):
        raise ShiftError('invalid aligned counts')
    p = [Fraction(x, na) for x in a]
    q = [Fraction(x, nb) for x in b]
    mp, mq = 1 - sum(p), 1 - sum(q)
    d = sorted((x-y for x, y in zip(p, q)), reverse=True)
    lower = max(Fraction(0), sum(x for x in d if x > 0) - mq)
    # TV = max_S (p(S)-q(S)). For a nonempty proper subset allocate all
    # reference missing mass inside S and all target missing mass outside S.
    # The best subset of each size is the sorted prefix.
    prefix = Fraction(0)
    best = Fraction(-1)
    for x in d[:-1]:
        prefix += x
        best = max(best, prefix)
    upper = max(Fraction(0), mp + best)
    return lower, upper


def _observed_tv(a, b):
    if not sum(a) or not sum(b):
        return None
    return float(sum(abs(Fraction(x, sum(a)) - Fraction(y, sum(b))) for x, y in zip(a, b)) / 2)


def analyze(frozen, expected_digest, reference_counts, target_counts):
    """Compare complete-case and missingness-aware marginal distributions."""
    _fields(frozen, 'schema plan plan_sha256')
    if freeze(frozen['plan']) != frozen or frozen['plan_sha256'] != expected_digest:
        raise ShiftError('prospective plan digest mismatch')
    p = frozen['plan']
    na, nb = p['reference_n'], p['target_n']
    features = {f['feature']: f['levels'] for f in p['features']}
    a = _counts(reference_counts, features, na)
    b = _counts(target_counts, features, nb)
    reports = []
    for f in p['features']:
        name, levels = f['feature'], f['levels']
        x, y = a[name], b[name]
        lo, hi = tv_bounds(x, y, na, nb)
        threshold = Fraction(f['warning_bp'], 10000)
        status = ('AT_OR_ABOVE_LIMIT' if lo >= threshold else
                  'BELOW_LIMIT' if hi < threshold else 'INCONCLUSIVE')
        reports.append({'feature': name, 'warning_bp': f['warning_bp'], 'status': status,
                        'reference_observed': sum(x), 'target_observed': sum(y),
                        'reference_missing': na-sum(x), 'target_missing': nb-sum(y),
                        'missing_rate_delta': float(Fraction(nb-sum(y), nb)-Fraction(na-sum(x), na)),
                        'observed_only_tv': _observed_tv(x, y),
                        'cohort_tv_bounds': [float(lo), float(hi)],
                        'levels': [{'level': level, 'reference_count': u, 'target_count': v,
                                    'reference_share_bounds': [u/na, (u+na-sum(x))/na],
                                    'target_share_bounds': [v/nb, (v+nb-sum(y))/nb]}
                                   for level, u, v in zip(levels, x, y)]})
    return {'schema': SCHEMA, 'plan_sha256': expected_digest,
            'reference_n': na, 'target_n': nb, 'features': reports,
            'decision_grade': False, 'publication_safe': False}
