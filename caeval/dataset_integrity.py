"""Local, experimental dataset partition integrity and reproducible receipts."""
from collections import Counter, defaultdict
from datetime import date
import hashlib
import hmac
import json
import re

PARTITIONS = ('train', 'tune', 'test')
LINKS = ('patient_key', 'lineage_key', 'content_sha256')
FIELDS = {'record_id', 'partition', 'patient_key', 'lineage_key', 'content_sha256', 'site_key', 'observed_at'}
MAX_ROWS = 100_000
SCHEMA = 'dataset-partition-integrity/1'


class IntegrityError(ValueError):
    """Invalid input or an impossible split; never includes source values."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def _key(key):
    if not isinstance(key, bytes) or len(key) < 32:
        raise IntegrityError('audit key must contain at least 32 bytes')
    return key


def _mac(key, domain, value):
    return hmac.new(_key(key), domain.encode('ascii') + b'\0' + canonical(value), hashlib.sha256).hexdigest()


def _identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 256 and value == value.strip() and all(ord(c) >= 32 and ord(c) != 127 for c in value)


def validate(rows, *, unassigned=False):
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise IntegrityError('invalid dataset size')
    seen = set()
    clean = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise IntegrityError('invalid record fields')
        rid = row['record_id']
        if not _identifier(rid) or rid in seen:
            raise IntegrityError('invalid or duplicate record ID')
        seen.add(rid)
        if row['partition'] not in ((None,) if unassigned else PARTITIONS):
            raise IntegrityError('invalid partition')
        for field in ('patient_key', 'lineage_key', 'site_key'):
            if row[field] is not None and not _identifier(row[field]):
                raise IntegrityError('invalid grouping key')
        digest = row['content_sha256']
        if digest is not None and (not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None):
            raise IntegrityError('invalid content digest')
        day = row['observed_at']
        if day is not None:
            try:
                if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                    raise ValueError()
            except ValueError:
                raise IntegrityError('invalid observation date') from None
        clean.append(dict(row))
    return sorted(clean, key=lambda row: row['record_id'])


def _cross(rows, field):
    groups = defaultdict(Counter)
    for row in rows:
        if row[field] is not None:
            groups[row[field]][row['partition']] += 1
    bad = [g for g in groups.values() if len(g) > 1]
    return {'groups': len(bad), 'records': sum(sum(g.values()) for g in bad)}


def audit(rows, key, *, design='grouped'):
    """Return aggregate-only report; absence of detected overlap is not clinical validation."""
    _key(key)
    if design not in ('grouped', 'temporal', 'site', 'temporal_site'):
        raise IntegrityError('unknown study design')
    rows = validate(rows)
    counts = Counter(row['partition'] for row in rows)
    required = LINKS + (('site_key',) if design in ('site', 'temporal_site') else ()) + (('observed_at',) if design in ('temporal', 'temporal_site') else ())
    missing = {f: sum(row[f] is None for row in rows) for f in required}
    overlap = {f: _cross(rows, f) for f in LINKS}
    violations = [f + '_cross_partition' for f in LINKS if overlap[f]['groups']]
    incomplete = [f + '_missing' for f in required if missing[f]]
    if not counts['test'] or not (counts['train'] or counts['tune']):
        incomplete.append('development_or_test_empty')
    development = [r for r in rows if r['partition'] != 'test']
    test = [r for r in rows if r['partition'] == 'test']
    design_checks = {}
    if design in ('site', 'temporal_site'):
        a = {r['site_key'] for r in development if r['site_key'] is not None}
        b = {r['site_key'] for r in test if r['site_key'] is not None}
        design_checks['shared_sites'] = len(a & b)
        if a & b:
            violations.append('site_holdout_overlap')
    if design in ('temporal', 'temporal_site'):
        a = [r['observed_at'] for r in development if r['observed_at'] is not None]
        b = [r['observed_at'] for r in test if r['observed_at'] is not None]
        # Equal days are not sufficient evidence of a strictly prospective holdout.
        crossing = bool(a and b and max(a) >= min(b))
        design_checks['temporal_boundary_crossed'] = crossing if a and b else None
        if crossing:
            violations.append('temporal_holdout_overlap')
    report = {'schema': SCHEMA, 'design': design, 'counts': {p: counts[p] for p in PARTITIONS},
              'missing': missing, 'overlap': overlap, 'design_checks': design_checks,
              'violations': violations, 'incomplete': incomplete,
              'status': 'FAIL' if violations else 'INCOMPLETE' if incomplete else 'NO_DETECTED_OVERLAP',
              'decision_grade': False, 'manifest_mac': _mac(key, 'manifest/v1', rows)}
    report['receipt_mac'] = _mac(key, 'receipt/v1', report)
    return report


def verify(rows, key, report):
    """Recompute all fields, not just the supplied receipt digest."""
    if not isinstance(report, dict):
        return False
    try:
        expected = audit(rows, key, design=report.get('design'))
        return hmac.compare_digest(canonical(expected), canonical(report))
    except (IntegrityError, ValueError, TypeError):
        return False


def plan_grouped(rows, key, *, train=60, tune=20):
    """Deterministic component allocation; ratios are targets, not guarantees.

    Connect transitively on patient, source-case lineage, or exact content.
    Missing linkage metadata is not safe to treat as a singleton.
    """
    _key(key)
    if type(train) is not int or type(tune) is not int or train <= 0 or tune <= 0 or train + tune >= 100:
        raise IntegrityError('invalid partition targets')
    rows = validate(rows, unassigned=True)
    if any(row[f] is None for row in rows for f in LINKS):
        raise IntegrityError('complete linkage metadata required for planning')
    parent = list(range(len(rows)))
    size = [1] * len(rows)

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        a, b = root(a), root(b)
        if a == b:
            return
        if size[a] < size[b]:
            a, b = b, a
        parent[b] = a
        size[a] += size[b]

    first = {}
    for i, row in enumerate(rows):
        for f in LINKS:
            token = (f, row[f])
            if token in first:
                union(i, first[token])
            else:
                first[token] = i
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        groups[root(i)].append(row)
    if len(groups) < 3:
        raise IntegrityError('fewer than three independent components')
    components = sorted(groups.values(), key=lambda g: (-len(g), _mac(key, 'split-order/v1', [r['record_id'] for r in g])))
    ratios = dict(zip(PARTITIONS, (train, tune, 100 - train - tune)))
    counts = Counter()
    for i, group in enumerate(components):
        empty = [p for p in PARTITIONS if not counts[p]]
        choices = empty if len(components) - i == len(empty) else PARTITIONS
        # Integer arithmetic avoids floating-point and platform-dependent rounding.
        partition = max(choices, key=lambda p: len(rows) * ratios[p] - 100 * counts[p])
        for row in group:
            row['partition'] = partition
        counts[partition] += len(group)
    result = audit(rows, key)
    if result['status'] != 'NO_DETECTED_OVERLAP':
        raise IntegrityError('planned partition failed audit')
    return rows, {'component_count': len(groups), 'largest_component': max(map(len, components)),
                  'target_percent': ratios, 'audit': result}
