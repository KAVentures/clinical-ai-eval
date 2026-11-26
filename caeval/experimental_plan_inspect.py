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
