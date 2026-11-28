"""Experimental aggregate-only prospective multisite paired evaluation."""
from collections import Counter
from fractions import Fraction
import hashlib
import hmac
import json
import re

SCHEMA = 'multisite-paired-validation/1'
MAX_SITES = 100
MAX_UNITS = 100_000
CELLS = tuple(a + b for a in '01?' for b in '01?')


class ValidationError(ValueError):
    pass


def _fields(obj, names):
    if not isinstance(obj, dict) or set(obj) != set(names.split()):
        raise ValidationError('unexpected fields')


def _token(x):
    return isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', x) is not None


def _hex(x):
    return isinstance(x, str) and re.fullmatch(r'[0-9a-f]{64}', x) is not None


def _canonical(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def _key(k):
    if not isinstance(k, bytes) or len(k) != 32:
        raise ValidationError('32-byte site key required')
    return k


def _mac(k, domain, value):
    return hmac.new(_key(k), _canonical([SCHEMA, domain, value]), hashlib.sha256).hexdigest()


def roster_commitment(site, units, key):
    if not _token(site) or not isinstance(units, list) or not 1 <= len(units) <= MAX_UNITS:
        raise ValidationError('invalid roster')
    if not all(_token(u) for u in units) or len(set(units)) != len(units):
        raise ValidationError('invalid or duplicate unit')
    return {'site': site, 'n': len(units), 'roster_mac': _mac(key, 'roster', [site, sorted(units)])}


def freeze(spec):
    _fields(spec, 'study endpoint horizon reference_protocol baseline_version candidate_version sites')
    if not all(_token(spec[k]) for k in ('study', 'endpoint', 'horizon', 'reference_protocol', 'baseline_version', 'candidate_version')):
        raise ValidationError('invalid study tokens')
    if spec['baseline_version'] == spec['candidate_version']:
        raise ValidationError('distinct versions required')
    sites = spec['sites']
    if not isinstance(sites, list) or not 1 <= len(sites) <= MAX_SITES:
        raise ValidationError('invalid site inventory')
    seen = set()
    for s in sites:
        _fields(s, 'site n roster_mac')
        if not _token(s['site']) or s['site'] in seen or type(s['n']) is not int or not 1 <= s['n'] <= MAX_UNITS or not _hex(s['roster_mac']):
            raise ValidationError('invalid site declaration')
        seen.add(s['site'])
    plan = {**spec, 'sites': sorted((dict(s) for s in sites), key=lambda s: s['site'])}
    return {'schema': SCHEMA, 'plan': plan, 'plan_sha256': hashlib.sha256(_canonical([SCHEMA, plan])).hexdigest()}


def _plan(frozen, expected):
    _fields(frozen, 'schema plan plan_sha256')
    if freeze(frozen['plan']) != frozen or frozen['plan_sha256'] != expected:
        raise ValidationError('plan digest mismatch')
    return {s['site']: s for s in frozen['plan']['sites']}


def _value(x):
    if x is None:
        return '?'
    if type(x) is not bool:
        raise ValidationError('outcomes must be boolean or null')
    return str(int(x))


def site_receipt(frozen, expected, site, units, rows, key):
    sites = _plan(frozen, expected)
    if not _token(site) or site not in sites or roster_commitment(site, units, key) != sites[site]:
        raise ValidationError('site roster mismatch')
    if not isinstance(rows, list) or len(rows) > len(units):
        raise ValidationError('invalid outcomes inventory')
    ids, seen = set(units), set()
    counts = Counter()
    for row in rows:
        _fields(row, 'unit_id baseline candidate')
        u = row['unit_id']
        if not _token(u) or u not in ids or u in seen:
            raise ValidationError('unexpected or duplicate outcome unit')
        seen.add(u)
        counts[_value(row['baseline']) + _value(row['candidate'])] += 1
    counts['??'] += len(ids) - len(seen)
    body = {'schema': SCHEMA, 'plan_sha256': expected, 'site': site, 'n': len(ids),
            'counts': {c: counts[c] for c in CELLS}}
    return {**body, 'receipt_mac': _mac(key, 'receipt', body)}


def _verify(receipt, sites, expected, keys):
    _fields(receipt, 'schema plan_sha256 site n counts receipt_mac')
    s = receipt['site']
    if not _token(s) or s not in sites or receipt['schema'] != SCHEMA or receipt['plan_sha256'] != expected:
        raise ValidationError('unexpected receipt scope')
    n, counts = receipt['n'], receipt['counts']
    if type(n) is not int or n != sites[s]['n'] or not isinstance(counts, dict) or set(counts) != set(CELLS):
        raise ValidationError('invalid receipt denominator or cells')
    if any(type(v) is not int or not 0 <= v <= n for v in counts.values()) or sum(counts.values()) != n:
        raise ValidationError('invalid receipt counts')
    body = {k: v for k, v in receipt.items() if k != 'receipt_mac'}
    if not _hex(receipt['receipt_mac']) or not hmac.compare_digest(receipt['receipt_mac'], _mac(keys[s], 'receipt', body)):
        raise ValidationError('receipt authentication failed')
    return s, Counter(counts)


def _summary(counts):
    n = sum(counts.values())
    lower = upper = 0
    for cell, m in counts.items():
        b, c = cell
        lower += m * (int(c == '1') - int(b != '0'))
        upper += m * (int(c != '0') - int(b == '1'))
    complete = sum(counts[c] for c in ('00', '01', '10', '11'))
    return {'n': n, 'complete_pairs': complete, 'worsenings': counts['01'], 'improvements': counts['10'],
            'delta_numerator_bounds': [lower, upper],
            'cohort_delta_bounds': [float(Fraction(lower, n)), float(Fraction(upper, n))]}


def aggregate(frozen, expected, receipts, keys):
    """Authenticate receipts and preserve planned denominators for absent sites."""
    sites = _plan(frozen, expected)
    if not isinstance(keys, dict) or set(keys) != set(sites) or len(set(_key(k) for k in keys.values())) != len(sites):
        raise ValidationError('distinct provisioned keys required for every site')
    if not isinstance(receipts, list) or len(receipts) > len(sites):
        raise ValidationError('invalid receipt inventory')
    by_site = {}
    for receipt in receipts:
        s, counts = _verify(receipt, sites, expected, keys)
        if s in by_site:
            raise ValidationError('duplicate site receipt')
        by_site[s] = counts
    pooled = Counter()
    reports = []
    for s, spec in sites.items():
        counts = by_site.get(s, Counter({'??': spec['n']}))
        pooled.update(counts)
        reports.append({'site': s, 'received': s in by_site, **_summary(counts)})
    equal = [sum(Fraction(r['delta_numerator_bounds'][i], r['n']) for r in reports) / len(reports) for i in (0, 1)]
    return {'schema': SCHEMA, 'plan_sha256': expected, 'sites_planned': len(sites),
            'sites_received': len(by_site), 'site_reports': reports, 'unit_weighted': _summary(pooled),
            'equal_site_delta_bounds': [float(v) for v in equal],
            'decision_grade': False, 'publication_safe': False}
