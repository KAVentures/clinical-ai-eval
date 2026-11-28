"""Local coordinated assessment: replay the three workbenches, never approve deployment."""
import argparse
import hashlib
import json
import sys

from . import predictive_research as pred, prospective_research as binary
from . import vendor_workbench as vendor, vendor_submission as submission

SCHEMA = 'assessment-workbench/1'


def require(ok):
    if not ok:
        raise ValueError('assessment scope or checkpoint mismatch')


def digest(value):
    return hashlib.sha256(pred.canonical(value)).hexdigest()


def freeze(request, expected_request, root, predictive_plan, binary_plan):
    req = submission._request(request, expected_request)
    require(pred.hex64(root))
    ps = pred._plan(predictive_plan, predictive_plan['plan_sha256'])
    bs = binary_plan['spec']
    require(binary_plan['schema'] == binary.SCHEMA and
            digest({k: v for k, v in binary_plan.items() if k != 'plan_sha256'}) == binary_plan['plan_sha256'])
    require(ps['study'] == bs['study'] and ps['baseline_version'] == bs['baseline_version'] and
            ps['candidate_version'] == bs['candidate_version'] == req['version'])
    ids = sorted(u['unit_id'] for u in binary_plan['subgroup']['plan']['units'])
    require(ids == ps['unit_ids'])
    body = {'schema': SCHEMA, 'scope': {k: req[k] for k in ('request_id', 'buyer', 'vendor', 'product', 'version')},
            'study': ps['study'], 'baseline_version': ps['baseline_version'], 'n_planned': len(ids),
            'request_sha256': expected_request, 'vendor_root': root,
            'predictive_plan_sha256': predictive_plan['plan_sha256'], 'binary_plan_sha256': binary_plan['plan_sha256']}
    return {**body, 'plan_sha256': digest(body)}


def analyze(plan, expected, *, db, head, as_of, predictive_plan, bundles, expected_bundles,
            binary_plan, manifest, audit_key, site_keys, outcomes, reference_counts, target_counts):
    require(pred.hex64(expected) and plan['plan_sha256'] == expected)
    root = plan['vendor_root']
    # The pinned history head makes these reads consistent or refuses a stale snapshot.
    with vendor._connect(db) as conn:
        request, *_ = vendor._load(conn, root, head)
    require(freeze(request, plan['request_sha256'], root, predictive_plan, binary_plan) == plan)
    procurement = vendor.status(db, root, head, as_of)
    predictive = pred.analyze(predictive_plan, plan['predictive_plan_sha256'], bundles, expected_bundles)
    prospective = binary.analyze(binary_plan, plan['binary_plan_sha256'], manifest, audit_key, site_keys,
                                 outcomes, reference_counts, target_counts)
    require(predictive['n_planned'] == prospective['n_planned'] == plan['n_planned'])
    # Preserve distinct endpoints; never combine prediction accuracy and adverse events.
    body = {'schema': SCHEMA, 'plan_sha256': expected, 'scope': plan['scope'], 'study': plan['study'],
            'n_planned': plan['n_planned'], 'vendor_head': head,
            'endpoints': {'predictive': predictive_plan['spec']['endpoint'], 'binary': binary_plan['spec']['endpoint']},
            'source_digests': {'procurement': digest(procurement), 'predictive': predictive['report_sha256'],
                               'binary': prospective['report_sha256']},
            'summary': {'mandatory_missing': procurement['mandatory_missing'],
                        'unresolved_clarifications': procurement['clarifications']['unresolved'],
                        'overdue_clarifications': procurement['clarifications']['overdue'],
                        'predictive_coverage': predictive['coverage'],
                        'population_shift_flags': [{'feature': f['feature'], 'status': f['status']}
                                                   for f in prospective['population_shift']['features']]},
            'reports': {'procurement': procurement, 'predictive': predictive, 'binary': prospective},
            'source_records_reverified': False, 'decision_grade': False, 'deployment_authorized': False,
            'publication_safe': False}
    return {**body, 'report_sha256': digest(body)}


def verify(plan, expected, report, expected_report, **inputs):
    rebuilt = analyze(plan, expected, **inputs)
    return rebuilt['report_sha256'] == expected_report and pred.canonical(rebuilt) == pred.canonical(report)


def _inputs(cfg):
    # Trusted local operator config only: paths intentionally refer to local files.
    pred.fields(cfg, 'db head as_of predictive_plan bundles expected_bundles binary_plan manifest keys outcomes reference_counts target_counts')
    pred.fields(cfg['bundles'], 'baseline candidate reference')
    key, sites = binary._read_keys(cfg['keys'])
    result = {k: cfg[k] for k in ('db', 'head', 'as_of', 'expected_bundles')}
    for k in ('predictive_plan', 'binary_plan', 'manifest', 'outcomes', 'reference_counts', 'target_counts'):
        result[k] = pred._read(cfg[k])
    return {**result, 'audit_key': key, 'site_keys': sites,
            'bundles': {k: pred._read(v) for k, v in cfg['bundles'].items()}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    pre = sub.add_parser('freeze')
    for k in ('request', 'expected-request', 'root', 'predictive-plan', 'binary-plan', 'output'):
        pre.add_argument('--'+k, required=True)
    for name in ('analyze', 'verify'):
        cmd = sub.add_parser(name)
        for k in ('plan', 'expected-plan', 'config'):
            cmd.add_argument('--'+k, required=True)
        if name == 'analyze':
            cmd.add_argument('--output', required=True)
        else:
            cmd.add_argument('--report', required=True)
            cmd.add_argument('--expected-report', required=True)
    a = parser.parse_args(argv)
    try:
        if a.command == 'freeze':
            result = freeze(pred._read(a.request), a.expected_request, a.root,
                            pred._read(a.predictive_plan), pred._read(a.binary_plan))
        else:
            plan = pred._read(a.plan)
            inputs = _inputs(pred._read(a.config))
            if a.command == 'verify':
                valid = verify(plan, a.expected_plan, pred._read(a.report), a.expected_report, **inputs)
                print(json.dumps({'report_replayed': valid, 'decision_grade': False}))
                return 0 if valid else 1
            result = analyze(plan, a.expected_plan, **inputs)
        pred._write(a.output, result)
        print(result['plan_sha256' if a.command == 'freeze' else 'report_sha256'])
        return 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError, vendor.sqlite3.Error):
        print('Assessment operation refused: invalid or inaccessible input or checkpoint.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
