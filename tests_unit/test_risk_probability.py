import copy
import itertools
import pytest
from caeval.risk_probability import freeze, analyze, RiskError, _delta_bounds, SCALE


def plan():
    return freeze(dict(endpoint='event', horizon='day30', baseline_version='v1', candidate_version='v2',
                       unit_ids=['a', 'b', 'c'], bin_edges_bp=[0, 5000, 10000]))


def row(u, y, b, c):
    return dict(unit_id=u, label=y, baseline_bp=b, candidate_bp=c)


def run(rows, p=None):
    p = p or plan()
    return analyze(p, p['plan_sha256'], rows)


def test_perfect_and_wrong_predictions():
    r = run([row('a', True, 0, 10000), row('b', False, 10000, 0), row('c', True, 0, 10000)])
    assert r['baseline']['cohort_brier_bounds'] == [1, 1]
    assert r['candidate']['cohort_brier_bounds'] == [0, 0]
    assert r['paired']['cohort_brier_delta_bounds'] == [-1, -1]
    assert r['paired']['complete_case_brier_delta'] == -1
    assert not r['decision_grade'] and not r['publication_safe']


def test_frozen_plan_and_versions():
    p = plan()
    assert freeze({**p['plan'], 'unit_ids': ['c', 'a', 'b']}) == p
    q = copy.deepcopy(p)
    q['plan']['candidate_version'] = 'v3'
    with pytest.raises(RiskError):
        run([], q)
    with pytest.raises(RiskError):
        analyze(p, 'bad', [])


def test_missing_label_shared_by_arms():
    r = run([row('a', None, 2000, 2000), row('b', None, 8000, 8000), row('c', None, 5000, 5000)])
    assert r['paired']['cohort_brier_delta_bounds'] == [0, 0]
    assert r['paired']['complete_case_brier_delta'] is None
    assert r['baseline']['complete_case_brier'] is None
    assert r['n_reference_known'] == 0


def test_absent_units_are_not_silently_removed():
    r = run([row('a', False, 0, 0)])
    assert r['n_planned'] == 3
    assert r['candidate']['n_complete'] == 1
    assert r['candidate']['cohort_brier_bounds'] == [0, 2/3]
    assert r['paired']['cohort_brier_delta_bounds'] == [-2/3, 2/3]
    assert run([])['candidate']['cohort_brier_bounds'] == [0, 1]


def test_bins_include_endpoint_and_unknown_labels():
    r = run([row('a', True, 0, 5000), row('b', None, 4999, 10000), row('c', False, 5000, 0)])
    low, high = r['candidate']['calibration_bins']
    assert low['n_scored'] == 1 and not low['upper_inclusive']
    assert high['n_scored'] == 2 and high['upper_inclusive']
    assert high['mean_probability'] == .75
    assert high['event_rate_bounds_given_scores'] == [.5, 1]
    assert high['observed_event_rate'] == 1
    assert run([])['candidate']['calibration_bins'][0]['mean_probability'] is None


def test_sharp_bounds_exhaustive_extreme_completions():
    scores = (0, 2000, 5000, 8000, SCALE)
    for y, b, c in itertools.product((False, True, None), (*scores, None), (*scores, None)):
        possible = [(cc-SCALE*int(yy))**2 - (bb-SCALE*int(yy))**2
                    for yy, bb, cc in itertools.product((False, True) if y is None else (y,),
                                                        (0, SCALE) if b is None else (b,),
                                                        (0, SCALE) if c is None else (c,))]
        assert _delta_bounds(b, c, y) == (min(possible), max(possible))


@pytest.mark.parametrize('bad', [[row('x', True, 0, 0)], [row('a', True, 0, 0)]*2,
                                 [row('a', 1, 0, 0)], [row('a', True, True, 0)],
                                 [row('a', True, -1, 0)], [row('a', True, 0, 10001)]])
def test_invalid_outcomes(bad):
    with pytest.raises(RiskError):
        run(bad)


@pytest.mark.parametrize('changes', [dict(unit_ids=['a', 'a']), dict(bin_edges_bp=[0, 1, 1, 10000]),
                                     dict(bin_edges_bp=[1, 10000]), dict(candidate_version='v1'),
                                     dict(horizon='')])
def test_invalid_plan(changes):
    with pytest.raises(RiskError):
        freeze({**plan()['plan'], **changes})
