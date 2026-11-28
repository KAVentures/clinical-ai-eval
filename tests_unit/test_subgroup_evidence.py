import copy
import itertools
import pytest
from caeval.subgroup_evidence import freeze, analyze, contrast, SubgroupError


def plan():
    return freeze([{'unit_id': str(i), 'group': 'a' if i < 3 else 'b' if i < 6 else None} for i in range(7)], ['b', 'a', 'empty'], minimum_group=2)


def run(rows, p=None):
    p = p or plan()
    return analyze(p, p['plan_sha256'], rows)


def outcome(i, b, c):
    return {'unit_id': str(i), 'baseline': b, 'candidate': c}


def test_frozen_inventory_and_digest():
    p = plan()
    assert freeze(p['plan']['units'][::-1], p['plan']['groups'][::-1], minimum_group=2) == p
    q = copy.deepcopy(p)
    q['plan']['minimum_group'] = 1
    with pytest.raises(SubgroupError):
        run([], q)
    with pytest.raises(SubgroupError):
        analyze(p, 'bad', [])


def test_missing_outcomes_and_group_adequacy():
    r = run([outcome(0, False, True), outcome(1, True, None), outcome(3, False, False)])
    a, b, empty, unknown = r['groups']
    assert a['arms']['candidate']['cohort_rate_bounds'] == [1/3, 1]
    assert a['complete_pairs'] == 1
    assert a['observed_paired_delta'] == 1
    assert a['cohort_delta_bounds'] == pytest.approx([-1/3, 2/3])
    assert b['arms']['baseline']['missing'] == 2
    assert empty == {'group': 'empty', 'status': 'INSUFFICIENT_UNITS', 'n': 0}
    assert unknown['status'] == 'INSUFFICIENT_UNITS'
    assert not r['decision_grade'] and not r['publication_safe']
    assert r['n_planned'] == 7 and r['unknown_group_units'] == 1


def test_all_binary_completions_are_bounded():
    # Exhaustively check 9^3 observed pair patterns against all completions.
    values = (False, True, None)
    for partial in itertools.product(itertools.product(values, repeat=2), repeat=3):
        r = run([outcome(i, *v) for i, v in enumerate(partial)])['groups'][0]
        flat = list(itertools.chain.from_iterable(partial))
        unknown = [i for i, v in enumerate(flat) if v is None]
        deltas = []
        for assignment in itertools.product((False, True), repeat=len(unknown)):
            completed = flat[:]
            for i, v in zip(unknown, assignment):
                completed[i] = v
            deltas.append((sum(completed[1::2])-sum(completed[::2]))/3)
        assert r['cohort_delta_bounds'] == pytest.approx([min(deltas), max(deltas)])


def test_contrasts_and_unknown_groups():
    rows = [outcome(i, False, i < 3) for i in range(6)]
    r = run(rows)
    assert contrast(r, 'a', 'b')['difference_bounds'] == [1, 1]
    assert contrast(r, 'b', 'a', arm='paired_delta')['difference_bounds'] == [-1, -1]
    assert contrast(r, 'a', 'empty')['difference_bounds'] is None
    assert contrast(run([]), 'a', 'b')['difference_bounds'] == [-1, 1]
    assert contrast(run([]), 'a', 'b', arm='paired_delta')['difference_bounds'] == [-2, 2]
    with pytest.raises(SubgroupError):
        contrast(r, 'a', 'a')


@pytest.mark.parametrize('rows', [[outcome(99, True, True)], [outcome(1, False, True)]*2, [outcome(0, 0, False)], [{'unit_id': '0', 'baseline': True}], [outcome(0, True, 'false')]])
def test_invalid_outcomes(rows):
    with pytest.raises(SubgroupError):
        run(rows)


@pytest.mark.parametrize('units,groups', [([], ['a']), ([{'unit_id': '0', 'group': 'z'}], ['a']), ([{'unit_id': '0', 'group': 'a'}]*2, ['a']), ([{'unit_id': '0', 'group': 'a'}], ['a', 'a'])])
def test_invalid_plans(units, groups):
    with pytest.raises(SubgroupError):
        freeze(units, groups)


def test_cli_roundtrip_and_exclusive_output(tmp_path, capsys):
    import json
    import os
    from caeval.subgroup_evidence_cli import main
    p = plan()['plan']
    inventory = tmp_path / 'inventory.json'
    inventory.write_text(json.dumps({'units': p['units'], 'groups': p['groups']}))
    frozen = tmp_path / 'plan.json'
    args = ['freeze', '--inventory', str(inventory), '--minimum-group', '2', '--output', str(frozen)]
    assert main(args) == 0
    digest = capsys.readouterr().out.strip()
    assert digest == plan()['plan_sha256']
    assert main(args) == 2
    outcomes = tmp_path / 'outcomes.json'
    outcomes.write_text(json.dumps([outcome(i, False, i < 3) for i in range(6)]))
    report = tmp_path / 'report.json'
    common = ['--plan', str(frozen), '--expected-digest', digest, '--outcomes', str(outcomes)]
    assert main(['analyze', *common, '--output', str(report)]) == 0
    assert json.loads(report.read_text()) == run(json.loads(outcomes.read_text()))
    if os.name == 'posix':
        assert report.stat().st_mode & 0o777 == 0o600
    pair = tmp_path / 'pair.json'
    assert main(['contrast', *common, '--output', str(pair), '--group-a', 'a', '--group-b', 'b']) == 0
    assert json.loads(pair.read_text())['contrast']['difference_bounds'] == [1, 1]


def test_cli_duplicate_keys_and_digest_mismatch(tmp_path, capsys):
    import json
    from caeval.subgroup_evidence_cli import main
    inv = tmp_path / 'bad.json'
    inv.write_text('{"secret-identifier":1,"secret-identifier":2}')
    out = tmp_path / 'out.json'
    assert main(['freeze', '--inventory', str(inv), '--output', str(out)]) == 2
    assert 'secret-identifier' not in capsys.readouterr().err
    assert not out.exists()
    inv.write_text(json.dumps(plan()))
    outcomes = tmp_path / 'outcomes.json'
    outcomes.write_text('[]')
    assert main(['analyze', '--plan', str(inv), '--expected-digest', 'wrong', '--outcomes', str(outcomes), '--output', str(out)]) == 2
    assert not out.exists()
