"""Portable administrative work-count handoff, without native job identities."""
import hashlib
import json
import re

MAX_ROWS = 80
MAX_COUNT = 1000
MAX_BYTES = 8 * 1024 * 1024
READY = frozenset(('completed', 'report_available', 'reports_available', 'sealed'))
ACTIVE = frozenset(('prepared_not_executed', 'queued', 'running', 'collecting', 'reviewing', 'ready'))
STATES = ('bounded_scan', 'bounded_legacy_scan', 'partial', 'unavailable', 'stale_plan')
ROW_FIELDS = 'requirement_id kind plan_id family vendor_id target coverage_state work_inventory_state scan_truncated'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def keys(doc, fields):
    require(isinstance(doc, dict) and set(doc) == set(fields.split()), 'unexpected handoff fields')


def digest(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=True, separators=(',', ':'),
                                     allow_nan=False).encode('ascii')).hexdigest()


def seal(doc):
    return {**doc, 'digest': digest(doc)}


def hex64(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def counts(records):
    require(isinstance(records, list) and len(records) <= MAX_COUNT, 'work record count exceeds handoff bound')
    ready = sum(r['state'] in READY for r in records)
    active = sum(r['state'] in ACTIVE for r in records)
    return {'ready': ready, 'active': active, 'other': len(records)-ready-active}


def summarize(work):
    result = {k: work[k] for k in ('programme_id', 'programme_digest', 'programme_state')}
    rows = work['requirements']
    require(isinstance(rows, list) and 1 <= len(rows) <= MAX_ROWS, 'invalid work row count')
    result['requirements'] = sorted(({**{k: r[k] for k in ROW_FIELDS.split()},
                                      'counts': counts(r['native_work_records'])} for r in rows),
                                    key=lambda r: r['requirement_id'])
    # Reuse strict validation even for a locally generated summary.
    expand(result)
    return result


def expand(summary):
    keys(summary, 'programme_id programme_digest programme_state requirements')
    require(hex64(summary['programme_digest']) and summary['programme_state'] in ('current', 'stale_plan'),
            'invalid work scope')
    rows = summary['requirements']
    require(isinstance(rows, list) and 1 <= len(rows) <= MAX_ROWS, 'invalid work row count')
    result = {k: summary[k] for k in ('programme_id', 'programme_digest', 'programme_state')}
    result['requirements'] = []
    seen = set()
    for r in rows:
        keys(r, ROW_FIELDS+' counts')
        rid = r['requirement_id']
        require(isinstance(rid, str) and rid not in seen, 'duplicate work requirement')
        seen.add(rid)
        scan = r['work_inventory_state']
        require(scan in STATES and (r['scan_truncated'] is None or type(r['scan_truncated']) is bool), 'invalid scan metadata')
        c = r['counts']; keys(c, 'ready active other')
        require(all(type(v) is int and 0 <= v <= MAX_COUNT for v in c.values()) and sum(c.values()) <= MAX_COUNT,
                'invalid work counts')
        require(scan not in ('unavailable', 'stale_plan') or not sum(c.values()), 'unavailable scan has records')
        records = [{'state': state} for group, state in (('ready','completed'),('active','running'),('other','other'))
                   for _ in range(c[group])]
        result['requirements'].append({**{k:r[k] for k in ROW_FIELDS.split()}, 'native_work_records':records})
    require([r['requirement_id'] for r in rows] == sorted(seen), 'noncanonical work row ordering')
    return result


def build(coverage, work, *, project):
    """Call the same projector on native and redacted inputs; refuse any drift."""
    summary = summarize(work)
    report = project(coverage, work)
    require(report == project(coverage, expand(summary)), 'work-count projection lost information')
    return seal({'schema':'unified-evidence-gap-handoff/1', 'coverage':coverage, 'work_summary':summary,
                 'report':report, 'source_records_reverified':False, 'decision_grade':False,
                 'deployment_authorized':False})


def verify(package, expected_digest=None, *, project, verify_coverage):
    keys(package, 'schema coverage work_summary report source_records_reverified decision_grade deployment_authorized digest')
    require(package['schema']=='unified-evidence-gap-handoff/1' and
            all(package[k] is False for k in ('source_records_reverified','decision_grade','deployment_authorized')),
            'invalid handoff claims')
    require(hex64(package['digest']) and digest({k:v for k,v in package.items() if k!='digest'})==package['digest'] and
            (expected_digest is None or expected_digest==package['digest']), 'handoff digest mismatch')
    verify_coverage(package['coverage'])
    report = project(package['coverage'], expand(package['work_summary']))
    require(report == package['report'], 'handoff report replay mismatch')
    return {'status':'HANDOFF_PROJECTION_REPLAYED', 'digest':package['digest'],
            'expected_digest_matched':expected_digest is not None, 'source_records_reverified':False,
            'decision_grade':False, 'deployment_authorized':False}
