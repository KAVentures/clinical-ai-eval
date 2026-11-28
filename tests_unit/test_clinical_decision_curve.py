import copy
from collections import Counter
from itertools import product
import pytest
from caeval.clinical_decision_curve import freeze, analyze, CurveError, _ratio_bounds, _contrast, _utility


def spec():
    return dict(endpoint='event', horizon='day30', action='refer', baseline_version='v1',
                candidate_version='v2', unit_ids=['a', 'b', 'c'], thresholds_bp=[2000, 5000])


def row(u, y, b, c):
    return dict(unit_id=u, label=y, baseline_bp=b, candidate_bp=c)


def run(rows, p=None):
    p = p or freeze(spec())
    return analyze(p, p['plan_sha256'], rows)


def test_perfect_policy_and_comparators():
    r = run([row('a', True, 0, 10000), row('b', False, 10000, 0), row('c', True, 0, 5000)])
    t = r['thresholds'][1]
    assert t['policies']['candidate']['complete_confusion'] == dict(tp=2, fp=0, tn=1, fn=0)
    assert t['policies']['candidate']['cohort_net_benefit_bounds'] == [2/3, 2/3]
    assert t['candidate_minus']['baseline']['cohort_delta_bounds'] == [1, 1]
    assert t['candidate_minus']['act_all']['cohort_delta_bounds'] == [1/3, 1/3]
    assert t['candidate_minus']['act_none']['cohort_delta_bounds'] == [2/3, 2/3]
    assert not r['decision_grade'] and not r['publication_safe']


def test_threshold_weight_and_boundary():
    t = run([row('a', False, 1999, 2000), row('b', True, 2000, 2000), row('c', False, 0, 0)])['thresholds'][0]
    assert t['false_positive_weight'] == .25
    assert t['policies']['candidate']['complete_case_net_benefit'] == .25
    assert t['candidate_minus']['baseline']['complete_case_delta'] == pytest.approx(-1/12)


def test_shared_missing_reference_identical_actions():
    r = run([row('a', None, 10000, 10000), row('b', None, 0, 0), row('c', None, 5000, 5000)])
    for t in r['thresholds']:
        assert t['candidate_minus']['baseline']['cohort_delta_bounds'] == [0, 0]
        assert t['candidate_minus']['baseline']['complete_case_delta'] is None


def test_absent_rows_preserve_denominator():
    t = run([row('a', True, 10000, 10000)])['thresholds'][1]
    assert t['policies']['candidate']['cohort_net_benefit_bounds'] == [-1/3, 1]
    assert t['candidate_minus']['baseline']['cohort_delta_bounds'] == [-2/3, 2/3]
    assert t['policies']['candidate']['n_complete'] == 1
    assert t['policies']['candidate']['cohort_sensitivity_bounds_conditional'] == [1/3, 1]
    assert t['policies']['candidate']['cohort_specificity_bounds_conditional'] == [0, 1]


def test_no_possible_reference_class():
    t = run([row(u, False, 0, 0) for u in 'abc'])['thresholds'][0]
    assert t['policies']['candidate']['cohort_sensitivity_bounds_conditional'] == [None, None]
    assert t['policies']['candidate']['cohort_specificity_bounds_conditional'] == [1, 1]


def test_ratio_bounds_exhaustive():
    values = (0, 1, None)
    for pattern in product(product(values, repeat=2), repeat=3):
        flat = [v for pair in pattern for v in pair]
        completions = list(product(*[(0, 1) if v is None else (v,) for v in flat]))
        for label, action in ((1, 1), (0, 0)):
            ratios = []
            for seq in completions:
                denom = sum(y == label for y in seq[::2])
                if denom:
                    ratios.append(sum(y == label and a == action for y, a in zip(seq[::2], seq[1::2]))/denom)
            expected = [min(ratios), max(ratios)] if ratios else [None, None]
            assert _ratio_bounds(Counter(pattern), label, action) == expected


def test_contrast_bounds_exhaustive():
    values = (0, 1, None)
    for triple in product(values, repeat=3):
        for t in (1, 2000, 5000, 9999):
            possible = [_utility(y, c, t)-_utility(y, b, t)
                        for y, c, b in product(*[(0, 1) if v is None else (v,) for v in triple])]
            assert _contrast(Counter({triple: 1}), 1, t)['cohort_delta_bounds'] == [min(possible)/(10000-t), max(possible)/(10000-t)]


def test_plan_digest_and_order():
    p = freeze(spec())
    assert freeze({**spec(), 'unit_ids': ['b', 'c', 'a'], 'thresholds_bp': [5000, 2000]}) == p
    q = copy.deepcopy(p)
    q['plan']['action'] = 'admit'
    with pytest.raises(CurveError):
        run([], q)
    with pytest.raises(CurveError):
        analyze(p, 'bad', [])


@pytest.mark.parametrize('change', [dict(thresholds_bp=[0]), dict(thresholds_bp=[10000]), dict(thresholds_bp=[1, 1]),
                                    dict(unit_ids=['a', 'a']), dict(candidate_version='v1'), dict(action='')])
def test_invalid_plan(change):
    with pytest.raises(CurveError):
        freeze({**spec(), **change})


@pytest.mark.parametrize('rows', [[row('x', True, 0, 0)], [row('a', False, 0, 0)]*2,
                                  [row('a', 1, 0, 0)], [row('a', True, True, 0)],
                                  [row('a', True, -1, 0)], [row('a', True, 0, 10001)]])
def test_invalid_outcomes(rows):
    with pytest.raises(CurveError):
        run(rows)


def test_cli_roundtrip_and_private_output(tmp_path, capsys):
    import json
    import os
    from caeval.clinical_decision_curve_cli import main
    source = tmp_path / 'spec.json'
    source.write_text(json.dumps(spec()))
    plan_path = tmp_path / 'plan.json'
    args = ['freeze', '--spec', str(source), '--output', str(plan_path)]
    assert main(args) == 0
    digest = capsys.readouterr().out.strip()
    assert digest == freeze(spec())['plan_sha256']
    assert main(args) == 2
    outcomes = tmp_path / 'outcomes.json'
    outcomes.write_text(json.dumps([row('a', True, 0, 10000)]))
    report = tmp_path / 'report.json'
    args = ['analyze', '--plan', str(plan_path), '--expected-digest', digest,
            '--outcomes', str(outcomes), '--output', str(report)]
    assert main(args) == 0
    assert json.loads(report.read_text()) == run([row('a', True, 0, 10000)])
    assert main(args) == 2
    if os.name == 'posix':
        assert report.stat().st_mode & 0o777 == 0o600


def test_cli_rejects_duplicate_keys_without_echo(tmp_path, capsys):
    from caeval.clinical_decision_curve_cli import main
    source = tmp_path / 'spec.json'
    source.write_text('{"secret-identifier":1,"secret-identifier":2}')
    output = tmp_path / 'plan.json'
    assert main(['freeze', '--spec', str(source), '--output', str(output)]) == 2
    assert 'secret-identifier' not in capsys.readouterr().err
    assert not output.exists()
