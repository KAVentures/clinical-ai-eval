import itertools
import math
from fractions import Fraction
import pytest
from caeval.study_design_exact import (binomial_cdf, upper_event_bound, fixed_design,
                                       grid_design, zero_event_n, outcome_sensitivity, DesignError)


def test_cdf_matches_small_rational_oracle():
    for n, p in itertools.product(range(1, 21), [Fraction(1, 10), Fraction(1, 2), Fraction(9, 10)]):
        for k in range(-1, n + 1):
            expected = sum(math.comb(n, j) * p**j * (1-p)**(n-j) for j in range(k+1))
            assert binomial_cdf(k, n, float(p)) == pytest.approx(float(expected), abs=1e-12)


def test_edges_and_rare_events():
    assert binomial_cdf(-1, 20, 0) == 0
    assert binomial_cdf(0, 20, 0) == 1
    assert binomial_cdf(19, 20, 1) == 0
    assert binomial_cdf(20, 20, 1) == 1
    assert binomial_cdf(0, 100000, 1e-8) == pytest.approx(math.exp(100000 * math.log1p(-1e-8)))
    assert binomial_cdf(50000, 100000, 0.5) > 0.5
    assert binomial_cdf(49999, 100000, 0.5) < 0.5


def test_exact_upper_bound_inversion():
    for k in (0, 1, 5, 50, 99):
        upper = upper_event_bound(k, 100)
        assert binomial_cdf(k, 100, upper) == pytest.approx(0.05, abs=1e-10)
    assert upper_event_bound(100, 100) == 1
    assert upper_event_bound(0, 100) == pytest.approx(0.0295130496)


def test_fixed_design_exhaustive_critical_counts():
    for n in range(1, 50):
        report = fixed_design(n, 0.2, 0.05)
        eligible = [k for k in range(n+1) if binomial_cdf(k, n, 0.2) <= 0.05 * (1-1e-8)]
        assert report['critical_events'] == (max(eligible) if eligible else None)
        assert report['null_rejection_probability'] <= 0.05
        assert report['decision_grade'] is False
    assert fixed_design(1, 0.1, 0)['critical_events'] is None


def test_grid_does_not_claim_global_minimum():
    grid = grid_design([100, 20, 100, 500], 0.1, 0.02)
    assert [r['n'] for r in grid['candidate_designs']] == [20, 100, 500]
    assert grid['smallest_tested_n_meeting_target'] == 100
    assert grid_design([1], 0.1, 0.02)['smallest_tested_n_meeting_target'] is None


@pytest.mark.parametrize('rate,expected', [(0.01, 299), (0.001, 2995), (0.05, 59)])
def test_zero_event_n_minimal(rate, expected):
    n = zero_event_n(rate)
    assert n == expected
    assert upper_event_bound(0, n) <= rate < upper_event_bound(0, n-1)


def test_missingness_bounds_cover_every_completion():
    report = outcome_sensitivity(20, 15, 2)
    assert report['finite_cohort_rate_range'] == [0.1, 0.35]
    for k in range(2, 8):
        assert report['upper_bound_best_case'] <= upper_event_bound(k, 20) <= report['upper_bound_worst_case']
    assert outcome_sensitivity(20, 0, 0)['complete_case_rate'] is None
    assert outcome_sensitivity(20, 0, 0)['upper_bound_worst_case'] == 1
    complete = outcome_sensitivity(20, 20, 2)
    assert complete['upper_bound_best_case'] == complete['upper_bound_worst_case']


@pytest.mark.parametrize('call', [lambda: fixed_design(0, .1, .01), lambda: fixed_design(100, .1, .2),
                                  lambda: fixed_design(100, .1, float('nan')), lambda: upper_event_bound(2, 1),
                                  lambda: binomial_cdf(True, 10, .5), lambda: zero_event_n(1e-12),
                                  lambda: outcome_sensitivity(100, 90, 91), lambda: grid_design([], .1, .01)])
def test_invalid(call):
    with pytest.raises(DesignError):
        call()
