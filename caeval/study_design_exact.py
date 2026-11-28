"""Experimental fixed-sample binary endpoint planning for independent units."""
import math

MAX_N = 100_000
MAX_GRID = 200
SCHEMA = 'independent-binary-study-design/1'


class DesignError(ValueError):
    pass


def _n(n):
    if type(n) is not int or not 1 <= n <= MAX_N:
        raise DesignError('sample size outside supported range')
    return n


def _prob(p, name, *, interior=False):
    if type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 or (interior and not 0 < p < 1):
        raise DesignError('invalid ' + name)
    return float(p)


def _count(k, n):
    if type(k) is not int or not 0 <= k <= n:
        raise DesignError('invalid event count')
    return k


def _lower_tail(k, n, p):
    """Sum from k down to zero, in log space to avoid PMF underflow."""
    logp, logq = math.log(p), math.log1p(-p)
    term = math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1) + k * logp + (n - k) * logq
    peak = term
    scaled = 1.0
    for j in range(k, 0, -1):
        term += math.log(j) - math.log(n - j + 1) + logq - logp
        if term > peak:
            scaled = scaled * math.exp(peak - term) + 1.0
            peak = term
        else:
            scaled += math.exp(term - peak)
    return min(1.0, math.exp(peak + math.log(scaled)))


def binomial_cdf(k, n, p):
    """P(X <= k) for X~Bin(n,p), evaluated numerically without normal approximation."""
    _n(n)
    p = _prob(p, 'event probability')
    if type(k) is not int or not -1 <= k <= n:
        raise DesignError('invalid cumulative count')
    if k < 0:
        return 0.0
    if k == n or p == 0:
        return 1.0
    if p == 1:
        return 0.0
    if k == 0:
        return math.exp(n * math.log1p(-p))
    # Sum the lower side when k is below the mean, otherwise use symmetry.
    if k < n * p or 1.0 - p == 1.0:
        return _lower_tail(k, n, p)
    return max(0.0, 1.0 - _lower_tail(n - k - 1, n, 1.0 - p))


def upper_event_bound(k, n, alpha=0.05):
    """One-sided Clopper-Pearson upper bound; confidence is 1-alpha."""
    _n(n)
    _count(k, n)
    alpha = _prob(alpha, 'alpha', interior=True)
    if k == n:
        return 1.0
    if k == 0:
        return -math.expm1(math.log(alpha) / n)
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if binomial_cdf(k, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def fixed_design(n, unacceptable_rate, anticipated_rate, alpha=0.05):
    """Predeclare a lower-tail test of H0: event rate >= unacceptable_rate."""
    _n(n)
    p0 = _prob(unacceptable_rate, 'unacceptable rate', interior=True)
    p1 = _prob(anticipated_rate, 'anticipated rate')
    alpha = _prob(alpha, 'alpha', interior=True)
    if p1 >= p0:
        raise DesignError('anticipated event rate must be below unacceptable rate')
    # Largest permitted event count. -1 denotes no possible rejection.
    lo, hi = -1, n
    # Small numerical margin guards against optimistic rounding at alpha.
    threshold = alpha * (1 - 1e-8)
    while hi - lo > 1:
        k = (lo + hi) // 2
        if binomial_cdf(k, n, p0) <= threshold:
            lo = k
        else:
            hi = k
    return {'schema': SCHEMA, 'n': n, 'alpha': alpha, 'unacceptable_rate': p0,
            'anticipated_rate': p1, 'critical_events': lo if lo >= 0 else None,
            'null_rejection_probability': binomial_cdf(lo, n, p0),
            'power_at_anticipated_rate': binomial_cdf(lo, n, p1),
            'decision_grade': False}


def grid_design(sample_sizes, unacceptable_rate, anticipated_rate, *, alpha=0.05, target_power=0.8):
    """Evaluate a prespecified grid; never claim an untested global minimum."""
    if not isinstance(sample_sizes, (list, tuple)) or not 1 <= len(sample_sizes) <= MAX_GRID:
        raise DesignError('invalid candidate grid')
    sizes = sorted({_n(n) for n in sample_sizes})
    target = _prob(target_power, 'target power', interior=True)
    rows = [fixed_design(n, unacceptable_rate, anticipated_rate, alpha) for n in sizes]
    feasible = [r['n'] for r in rows if r['power_at_anticipated_rate'] >= target]
    return {'schema': SCHEMA, 'target_power': target, 'candidate_designs': rows,
            'smallest_tested_n_meeting_target': min(feasible) if feasible else None,
            'decision_grade': False}


def zero_event_n(max_upper_rate, alpha=0.05):
    """Smallest supported n for a zero-event upper bound at most max_upper_rate."""
    target = _prob(max_upper_rate, 'upper rate', interior=True)
    alpha = _prob(alpha, 'alpha', interior=True)
    if upper_event_bound(0, MAX_N, alpha) > target:
        raise DesignError('required sample size exceeds supported range')
    lo, hi = 0, MAX_N
    while hi - lo > 1:
        mid = (hi + lo) // 2
        if upper_event_bound(0, mid, alpha) <= target:
            hi = mid
        else:
            lo = mid
    return hi


def outcome_sensitivity(n_planned, n_observed, events, alpha=0.05):
    """Best/worst finite-cohort event rates and one-sided upper bounds.

    Assumes the planned units are fixed, eligible, independent Bernoulli units;
    unknown outcomes remain unknown, including failures and unreviewed outputs.
    No missing-at-random assumption is made. Not a clinical acceptance rule.
    """
    _n(n_planned)
    _count(n_observed, n_planned)
    _count(events, n_observed)
    alpha = _prob(alpha, 'alpha', interior=True)
    missing = n_planned - n_observed
    return {'schema': SCHEMA, 'planned': n_planned, 'observed': n_observed,
            'events_observed': events, 'missing': missing, 'alpha': alpha,
            'complete_case_rate': events / n_observed if n_observed else None,
            'finite_cohort_rate_range': [events / n_planned, (events + missing) / n_planned],
            'upper_bound_best_case': upper_event_bound(events, n_planned, alpha),
            'upper_bound_worst_case': upper_event_bound(events + missing, n_planned, alpha),
            'decision_grade': False}
