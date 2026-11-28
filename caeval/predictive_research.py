"""Local paired predictive research workflow with independently pinned inputs."""
import argparse
import hashlib
import json
import os
import re
import sys

from . import clinical_decision_curve as curve, risk_probability as risk

SCHEMA = 'predictive-research/1'
ROLES = ('baseline', 'candidate', 'reference')
LIMIT = 64 * 1024 * 1024
COMMON = 'endpoint horizon baseline_version candidate_version unit_ids'.split()


class ResearchError(ValueError):
    pass


def require(ok):
    if not ok:
        raise ResearchError('invalid research scope, input or checkpoint')


def fields(obj, names):
    require(isinstance(obj, dict) and set(obj) == set(names.split()))


def hex64(x):
    return isinstance(x, str) and re.fullmatch(r'[a-f0-9]{64}', x) is not None


def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def digest(x):
    return hashlib.sha256(canonical(x)).hexdigest()


def _children(spec):
    common = {k: spec[k] for k in COMMON}
    return (risk.freeze({**common, 'bin_edges_bp': spec['bin_edges_bp']}),
            curve.freeze({**common, 'action': spec['action'], 'thresholds_bp': spec['thresholds_bp']}))


def freeze(spec):
    fields(spec, 'study eligibility_sha256 reference_protocol_sha256 endpoint horizon baseline_version candidate_version unit_ids bin_edges_bp action thresholds_bp')
    require(risk._token(spec['study']) and hex64(spec['eligibility_sha256']) and hex64(spec['reference_protocol_sha256']))
    calibration, decision = _children(spec)
    normalized = {**spec, 'unit_ids': calibration['plan']['unit_ids'], 'bin_edges_bp': calibration['plan']['bin_edges_bp'],
                  'thresholds_bp': decision['plan']['thresholds_bp']}
    body = {'schema': SCHEMA, 'spec': normalized, 'child_plans': {'calibration': calibration['plan_sha256'],
                                                              'decision_curve': decision['plan_sha256']}}
    return {**body, 'plan_sha256': digest(body)}


def _plan(plan, expected):
    fields(plan, 'schema spec child_plans plan_sha256')
    require(hex64(expected) and plan['plan_sha256'] == expected and canonical(freeze(plan['spec'])) == canonical(plan))
    return plan['spec']


def seal(plan, expected, role, source_receipt_sha256, rows):
    """Seal a separate prediction or reference inventory; omitted values remain unknown."""
    spec = _plan(plan, expected)
    require(role in ROLES and hex64(source_receipt_sha256))
    ids = set(spec['unit_ids'])
    require(isinstance(rows, list) and len(rows) <= len(ids))
    field = 'label' if role == 'reference' else 'probability_bp'
    indexed = {}
    for row in rows:
        fields(row, 'unit_id ' + field)
        u, value = row['unit_id'], row[field]
        require(risk._token(u) and u in ids and u not in indexed)
        require(value is None or (type(value) is bool if role == 'reference' else risk._bp(value)))
        indexed[u] = value
    scope = spec['reference_protocol_sha256'] if role == 'reference' else spec[role + '_version']
    body = {'schema': SCHEMA, 'plan_sha256': expected, 'role': role, 'scope': scope,
            'source_receipt_sha256': source_receipt_sha256,
            'rows': [{'unit_id': u, field: indexed[u]} for u in sorted(indexed)]}
    return {**body, 'bundle_sha256': digest(body)}


def _bundle(plan, expected, role, bundle, expected_bundle):
    fields(bundle, 'schema plan_sha256 role scope source_receipt_sha256 rows bundle_sha256')
    require(hex64(expected_bundle) and bundle['bundle_sha256'] == expected_bundle)
    rebuilt = seal(plan, expected, role, bundle['source_receipt_sha256'], bundle['rows'])
    require(canonical(rebuilt) == canonical(bundle))
    field = 'label' if role == 'reference' else 'probability_bp'
    return {r['unit_id']: r[field] for r in rebuilt['rows']}


def analyze(plan, expected, bundles, expected_bundles):
    """Use exactly the same fixed cohort and references in both child analyses."""
    spec = _plan(plan, expected)
    fields(bundles, 'baseline candidate reference')
    fields(expected_bundles, 'baseline candidate reference')
    values = {r: _bundle(plan, expected, r, bundles[r], expected_bundles[r]) for r in ROLES}
    union = sorted(set().union(*(set(v) for v in values.values())))
    rows = [{'unit_id': u, 'label': values['reference'].get(u), 'baseline_bp': values['baseline'].get(u),
             'candidate_bp': values['candidate'].get(u)} for u in union]
    calibration, decision = _children(spec)
    cal = risk.analyze(calibration, calibration['plan_sha256'], rows)
    dec = curve.analyze(decision, decision['plan_sha256'], rows)
    n = len(spec['unit_ids'])
    require(cal['n_planned'] == dec['n_planned'] == n and cal['n_reference_known'] == dec['n_reference_known'])
    coverage = {role: {'n_rows': len(v), 'n_known': sum(x is not None for x in v.values()),
                       'n_unknown': n - sum(x is not None for x in v.values())} for role, v in values.items()}
    body = {'schema': SCHEMA, 'plan_sha256': expected, 'study': spec['study'], 'n_planned': n,
            'inputs': {r: {'bundle_sha256': expected_bundles[r], 'source_receipt_sha256': bundles[r]['source_receipt_sha256']} for r in ROLES},
            'coverage': coverage, 'calibration': cal, 'decision_curve': dec,
            'source_records_reverified': False, 'decision_grade': False, 'publication_safe': False}
    return {**body, 'report_sha256': digest(body)}


def verify(plan, expected, bundles, expected_bundles, report, expected_report):
    require(hex64(expected_report))
    rebuilt = analyze(plan, expected, bundles, expected_bundles)
    return rebuilt['report_sha256'] == expected_report and canonical(rebuilt) == canonical(report)


def _unique(pairs):
    obj = {}
    for k, v in pairs:
        require(k not in obj)
        obj[k] = v
    return obj


def _read(path):
    with open(path, 'rb') as f:
        raw = f.read(LIMIT + 1)
    require(len(raw) <= LIMIT)
    return json.loads(raw, object_pairs_hook=_unique)


def _write(path, obj):
    raw = canonical(obj) + b'\n'
    require(len(raw) <= LIMIT)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
    except BaseException:
        os.unlink(path)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    pre = sub.add_parser('freeze')
    pre.add_argument('--spec', required=True)
    pre.add_argument('--output', required=True)
    for name in ('seal', 'analyze', 'verify'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--plan', required=True)
        cmd.add_argument('--expected-plan', required=True)
        if name == 'seal':
            cmd.add_argument('--role', choices=ROLES, required=True)
            cmd.add_argument('--source-receipt', required=True)
            cmd.add_argument('--rows', required=True)
        else:
            for role in ROLES:
                cmd.add_argument('--'+role, required=True)
                cmd.add_argument('--expected-'+role, required=True)
        if name == 'verify':
            cmd.add_argument('--report', required=True)
            cmd.add_argument('--expected-report', required=True)
        else:
            cmd.add_argument('--output', required=True)
    a = parser.parse_args(argv)
    try:
        if a.command == 'freeze':
            result = freeze(_read(a.spec))
        else:
            plan = _read(a.plan)
            if a.command == 'seal':
                result = seal(plan, a.expected_plan, a.role, a.source_receipt, _read(a.rows))
            else:
                bundles = {r: _read(getattr(a, r)) for r in ROLES}
                digests = {r: getattr(a, 'expected_'+r) for r in ROLES}
                if a.command == 'verify':
                    valid = verify(plan, a.expected_plan, bundles, digests, _read(a.report), a.expected_report)
                    print(json.dumps({'report_replayed': valid, 'decision_grade': False}))
                    return 0 if valid else 1
                result = analyze(plan, a.expected_plan, bundles, digests)
        _write(a.output, result)
        print(result[{'freeze':'plan_sha256', 'seal':'bundle_sha256', 'analyze':'report_sha256'}[a.command]])
        return 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print('Predictive research operation refused: invalid or inaccessible input/output.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
