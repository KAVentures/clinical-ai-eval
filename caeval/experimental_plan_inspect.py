"""Small machine-readable projection of an already verified research plan."""
import hashlib
import json

PROTOCOLS = {
    'citation': ('queries', 'independent_review'),
    'scribe': ('items', 'independent_review'),
    'decision': ('items', 'structured_response_scoring'),
    'robustness': ('pairs', 'separate_validity_and_answer_review'),
}


def canonical_equal(a, b):
    def encode(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    return encode(a) == encode(b)


def project(frozen):
    """No plan internals, case material, responses, or local paths are returned."""
    rows = []
    for kind, entry in sorted(frozen['modules'].items()):
        inventory, workflow = PROTOCOLS[kind]
        plan = entry['plan']
        if type(plan['spec']['synthetic']) is not bool or plan['decision_grade'] is not False:
            raise ValueError('invalid experimental plan provenance')
        stages = ['exchange', 'response-template']
        if kind != 'decision':
            stages += ['prepare-review', 'independent-native-review']
        stages += ['complete-module', 'assemble', 'verify']
        rows.append({'kind': kind, 'plan_hash': plan['plan_hash'],
                     'related_suites': list(entry['related_suites']), 'synthetic': plan['spec']['synthetic'],
                     'inventory_unit': inventory, 'planned_units': len(plan[inventory]),
                     'workflow': workflow, 'stages': stages,
                     'completion_state': 'not_inspected', 'canonical_requirement_satisfied': False})
    required = []
    for suite in frozen['scope']['selection']['required_suites']:
        required.append({'suite': suite['suite'], 'canonical_runnable': suite['implemented'],
                         'related_planned_modules': [r['kind'] for r in rows if suite['suite'] in r['related_suites']],
                         'canonical_execution_verified': False})
    result = {'schema': 'experimental-plan-inspection/1', 'project_plan_digest': frozen['digest'],
              'target': {k: frozen['scope']['target'][k] for k in ('name', 'version')},
              'modules': rows, 'required_suites': required,
              'unplanned_related_modules': list(frozen['unplanned_related_modules']),
              'native_workspaces_inspected': False, 'network_authorized': False,
              'decision_grade': False, 'deployment_authorized': False}
    raw = json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return {**result, 'digest': hashlib.sha256(raw.encode('utf-8')).hexdigest()}


def project_result(frozen, result, receipt):
    """Project only metadata from a result already replayed by the native verifier."""
    plan = project(frozen)
    if (receipt['status'] != 'REPLAY_VERIFIED' or receipt['decision_grade'] is not False or
            receipt['digest'] != result['digest'] or receipt['project_plan_digest'] != frozen['digest'] or
            result['project_plan_digest'] != frozen['digest'] or result['decision_grade'] is not False or
            type(receipt['expected_digest_matched']) is not bool):
        raise ValueError('result replay scope mismatch')
    rows = plan['modules']
    completed = result['module_receipts']
    planned = {r['kind'] for r in rows}
    missing = sorted(planned - set(completed))
    if (not completed or not set(completed) <= planned or
            receipt['uncompleted_planned_modules'] != missing or result['uncompleted_planned_modules'] != missing or
            receipt['all_planned_modules_replayed'] is not (not missing) or
            result['all_planned_modules_replayed'] is not (not missing) or
            receipt['unplanned_related_modules'] != plan['unplanned_related_modules'] or
            result['unplanned_related_modules'] != plan['unplanned_related_modules'] or
            not canonical_equal(receipt['coverage'], result['coverage'])):
        raise ValueError('result replay inventory mismatch')
    for row in rows:
        row['completion_state'] = 'replayed' if row['kind'] in completed else 'not_in_result'
        row['package_digest'] = row['report_digest'] = None
        if row['kind'] in completed:
            native = completed[row['kind']]
            if (native['plan_hash'] != row['plan_hash'] or native['synthetic'] is not row['synthetic'] or
                    native['related_suites'] != row['related_suites'] or native['decision_grade'] is not False):
                raise ValueError('module receipt scope mismatch')
            for key in ('package_digest', 'report_digest'):
                value = native[key]
                if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                    raise ValueError('invalid module receipt digest')
                row[key] = value
    coverage = {r['suite']: r for r in receipt['coverage']}
    required = plan['required_suites']
    if len(coverage) != len(receipt['coverage']) or set(coverage) != {r['suite'] for r in required}:
        raise ValueError('result coverage inventory mismatch')
    for row in required:
        native = coverage[row['suite']]
        expected = [k for k in row['related_planned_modules'] if k in completed]
        if (native['canonical_runnable'] is not row['canonical_runnable'] or
                native['canonical_execution_verified'] is not False or native['canonical_requirement_satisfied'] is not False or
                native['planned_experimental_modules'] != row['related_planned_modules'] or
                native['replayed_experimental_modules'] != expected):
            raise ValueError('result coverage scope mismatch')
        row['replayed_experimental_modules'] = expected
        row['canonical_requirement_satisfied'] = False
    doc = {k: v for k, v in plan.items() if k != 'digest'}
    doc.update(schema='experimental-result-inspection/1', result_digest=result['digest'],
               result_replayed=True, expected_digest_matched=receipt['expected_digest_matched'],
               uncompleted_planned_modules=missing, all_planned_modules_replayed=not missing,
               scientific_scores_included=False)
    raw = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return {**doc, 'digest': hashlib.sha256(raw.encode('utf-8')).hexdigest()}
