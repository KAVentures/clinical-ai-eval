"""Local orchestration of prospective binary clinical evidence, not a release gate."""
import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import sys
from pathlib import Path

from . import dataset_integrity as data, study_design_exact as design
from . import subgroup_evidence as subgroup, multisite_validation as multi
from . import population_shift as shift

SCHEMA = 'prospective-research/1'
LIMIT = 64 * 1024 * 1024


class ResearchError(ValueError):
    pass


def require(ok):
    if not ok:
        raise ResearchError('invalid research scope, inputs or checkpoint')


def fields(obj, names):
    require(isinstance(obj, dict) and set(obj) == set(names.split()))


def digest(value):
    return hashlib.sha256(data.canonical(value)).hexdigest()


def _mac(key, value):
    return hmac.new(key, data.canonical([SCHEMA, value]), hashlib.sha256).hexdigest()


def _keys(audit_key, site_keys, sites):
    require(isinstance(audit_key, bytes) and len(audit_key) == 32)
    require(isinstance(site_keys, dict) and set(site_keys) == set(sites))
    require(all(isinstance(k, bytes) and len(k) == 32 for k in site_keys.values()))
    require(len(set([audit_key, *site_keys.values()])) == len(sites) + 1)


def _rosters(rows):
    result = {}
    for r in rows:
        if r['partition'] == 'test':
            require(multi._token(r['site_key']) and multi._token(r['record_id']))
            result.setdefault(r['site_key'], []).append(r['record_id'])
    return result


def freeze(spec, manifest, memberships, audit_key, site_keys):
    """Freeze an audited holdout and prospective child plans before outcomes."""
    fields(spec, 'study endpoint horizon reference_protocol baseline_version candidate_version holdout_design subgroup_levels minimum_group coding_version shift_features binary_design')
    rows = data.validate(manifest)
    sites = _rosters(rows)
    _keys(audit_key, site_keys, sites)
    audit = data.audit(rows, audit_key, design=spec['holdout_design'])
    require(audit['status'] == 'NO_DETECTED_OVERLAP')
    test = [r for r in rows if r['partition'] == 'test']
    ids = {r['record_id'] for r in test}
    require(isinstance(memberships, dict) and set(memberships) == ids)
    group_plan = subgroup.freeze([{'unit_id': u, 'group': memberships[u]} for u in ids], spec['subgroup_levels'], minimum_group=spec['minimum_group'])
    multi_plan = multi.freeze({**{k: spec[k] for k in ('study', 'endpoint', 'horizon', 'reference_protocol', 'baseline_version', 'candidate_version')},
                               'sites': [multi.roster_commitment(s, u, site_keys[s]) for s, u in sites.items()]})
    shift_plan = shift.freeze({'study': spec['study'], 'reference_population': 'development', 'target_population': 'holdout',
                               'coding_version': spec['coding_version'], 'reference_n': len(rows)-len(test),
                               'target_n': len(test), 'features': spec['shift_features']})
    power = None
    if spec['binary_design'] is not None:
        d = spec['binary_design']
        fields(d, 'iid_assumed unacceptable_rate anticipated_rate alpha')
        require(d['iid_assumed'] is True)
        # Unique declared patient keys are necessary, not sufficient, for IID.
        require(len({r['patient_key'] for r in test}) == len(test))
        power = design.fixed_design(len(test), d['unacceptable_rate'], d['anticipated_rate'], d['alpha'])
    body = {'schema': SCHEMA, 'spec': spec, 'dataset_audit': audit, 'subgroup': group_plan,
            'multisite': multi_plan, 'population_shift': shift_plan, 'binary_design': power}
    # Detach nested caller-owned lists and dictionaries.
    body = json.loads(data.canonical(body))
    return {**body, 'plan_sha256': digest(body)}


def _check(plan, expected, manifest, audit_key, site_keys):
    fields(plan, 'schema spec dataset_audit subgroup multisite population_shift binary_design plan_sha256')
    require(isinstance(expected, str) and plan['plan_sha256'] == expected)
    memberships = {u['unit_id']: u['group'] for u in plan['subgroup']['plan']['units']}
    rebuilt = freeze(plan['spec'], manifest, memberships, audit_key, site_keys)
    require(data.canonical(rebuilt) == data.canonical(plan))
    return _rosters(data.validate(manifest))


def analyze(plan, expected, manifest, audit_key, site_keys, outcomes, reference_counts, target_counts):
    """Run all child analyses on the bound local cohort and aggregate counts."""
    sites = _check(plan, expected, manifest, audit_key, site_keys)
    group_plan = plan['subgroup']
    result = subgroup.analyze(group_plan, group_plan['plan_sha256'], outcomes)
    indexed = {r['unit_id']: r for r in outcomes}  # validated by subgroup engine
    multi_plan = plan['multisite']
    receipts = [multi.site_receipt(multi_plan, multi_plan['plan_sha256'], s, ids,
                [indexed[u] for u in ids if u in indexed], site_keys[s]) for s, ids in sorted(sites.items())]
    pooled = multi.aggregate(multi_plan, multi_plan['plan_sha256'], receipts, site_keys)
    shift_plan = plan['population_shift']
    population = shift.analyze(shift_plan, shift_plan['plan_sha256'], reference_counts, target_counts)
    n = result['n_planned']
    require(n == pooled['unit_weighted']['n'] == population['target_n'] == plan['dataset_audit']['counts']['test'])
    sensitivity = None
    if plan['binary_design'] is not None:
        observed = [r['candidate'] for r in outcomes if r['candidate'] is not None]
        sensitivity = design.outcome_sensitivity(n, len(observed), sum(observed), plan['binary_design']['alpha'])
    body = {'schema': SCHEMA, 'plan_sha256': expected, 'n_planned': n,
            'input_mac': _mac(audit_key, [expected, sorted(outcomes, key=lambda x: x['unit_id']), reference_counts, target_counts]),
            'dataset_audit': plan['dataset_audit'], 'binary_design': plan['binary_design'],
            'candidate_iid_sensitivity': sensitivity, 'subgroup': result, 'multisite': pooled,
            'population_shift': population, 'site_receipts': receipts,
            'source_records_reverified': False, 'independent_site_execution_verified': False,
            'decision_grade': False, 'publication_safe': False}
    return {**body, 'report_sha256': digest(body)}


def verify(plan, expected, manifest, audit_key, site_keys, outcomes, reference_counts, target_counts, report, expected_report):
    rebuilt = analyze(plan, expected, manifest, audit_key, site_keys, outcomes, reference_counts, target_counts)
    return rebuilt['report_sha256'] == expected_report and data.canonical(rebuilt) == data.canonical(report)


def _unique(pairs):
    out = {}
    for k, v in pairs:
        require(k not in out)
        out[k] = v
    return out


def _read(path):
    with open(path, 'rb') as f:
        raw = f.read(LIMIT+1)
    require(len(raw) <= LIMIT)
    return json.loads(raw, object_pairs_hook=_unique)


def _read_keys(path):
    p = Path(path)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    require(not p.is_symlink())
    with os.fdopen(os.open(p, flags), 'rb') as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1)
        if os.name == 'posix':
            require(s.st_uid == os.getuid() and not s.st_mode & 0o077)
        raw = f.read(65537)
    require(len(raw) <= 65536)
    keys = json.loads(raw, object_pairs_hook=_unique)
    fields(keys, 'audit sites')
    require(isinstance(keys['sites'], dict))
    def decode(v):
        require(isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v) is not None)
        return bytes.fromhex(v)
    return decode(keys['audit']), {s: decode(v) for s, v in keys['sites'].items()}


def _write(path, obj):
    raw = data.canonical(obj) + b'\n'
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
    for name in ('freeze', 'analyze', 'verify'):
        cmd = sub.add_parser(name)
        for field in ('manifest', 'keys'):
            cmd.add_argument('--'+field, required=True)
        if name == 'freeze':
            for field in ('spec', 'memberships'):
                cmd.add_argument('--'+field, required=True)
        else:
            for field in ('plan', 'expected-plan', 'outcomes', 'reference-counts', 'target-counts'):
                cmd.add_argument('--'+field, required=True)
        if name == 'verify':
            cmd.add_argument('--report', required=True)
            cmd.add_argument('--expected-report', required=True)
        else:
            cmd.add_argument('--output', required=True)
    a = parser.parse_args(argv)
    try:
        manifest = _read(a.manifest)
        key, sites = _read_keys(a.keys)
        if a.command == 'freeze':
            result = freeze(_read(a.spec), manifest, _read(a.memberships), key, sites)
        else:
            args = (_read(a.plan), a.expected_plan, manifest, key, sites, _read(a.outcomes),
                    _read(a.reference_counts), _read(a.target_counts))
            if a.command == 'verify':
                valid = verify(*args, _read(a.report), a.expected_report)
                print(json.dumps({'report_replayed': valid, 'decision_grade': False}))
                return 0 if valid else 1
            result = analyze(*args)
        _write(a.output, result)
        print(result['plan_sha256' if a.command == 'freeze' else 'report_sha256'])
        return 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print('Prospective research operation refused: invalid or inaccessible input/output.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
