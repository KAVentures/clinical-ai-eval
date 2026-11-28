import copy
import itertools
import pytest
from caeval.multisite_validation import freeze, roster_commitment, site_receipt, aggregate, ValidationError, _summary
from collections import Counter

KEYS = {'a': b'a'*32, 'b': b'b'*32}
UNITS = {'a': ['u1', 'u2'], 'b': ['u3']}


def plan():
    return freeze(dict(study='s', endpoint='harm', horizon='day30', reference_protocol='r1',
                       baseline_version='v1', candidate_version='v2',
                       sites=[roster_commitment(s, u, KEYS[s]) for s, u in UNITS.items()]))


def receipt(p, s, rows):
    return site_receipt(p, p['plan_sha256'], s, UNITS[s], rows, KEYS[s])


def row(u, b, c):
    return dict(unit_id=u, baseline=b, candidate=c)


def run(p, receipts, keys=KEYS):
    return aggregate(p, p['plan_sha256'], receipts, keys)


def test_multisite_complete_and_weighting():
    p = plan()
    a = receipt(p, 'a', [row('u1', False, True), row('u2', False, True)])
    b = receipt(p, 'b', [row('u3', True, False)])
    r = run(p, [b, a])
    assert r['unit_weighted']['cohort_delta_bounds'] == [1/3, 1/3]
    assert r['equal_site_delta_bounds'] == [0, 0]
    assert r['sites_received'] == 2
    assert not r['publication_safe'] and not r['decision_grade']
    assert 'u1' not in str(a) and 'u2' not in str(a)
    assert run(p, [a, b]) == r


def test_absent_site_and_absent_unit():
    p = plan()
    a = receipt(p, 'a', [row('u1', False, True)])
    r = run(p, [a])
    assert r['sites_received'] == 1
    assert r['unit_weighted']['n'] == 3
    assert r['unit_weighted']['cohort_delta_bounds'] == [-1/3, 1]
    assert r['equal_site_delta_bounds'] == [-.5, 1]
    assert run(p, [])['unit_weighted']['cohort_delta_bounds'] == [-1, 1]


def test_exhaustive_missingness_completions():
    for pairs in itertools.product(itertools.product('01?', repeat=2), repeat=3):
        flat = [x for pair in pairs for x in pair]
        deltas = [sum(int(c)-int(b) for b, c in zip(v[::2], v[1::2]))/3
                  for v in itertools.product(*[('0', '1') if x == '?' else (x,) for x in flat])]
        assert _summary(Counter(''.join(p) for p in pairs))['cohort_delta_bounds'] == [min(deltas), max(deltas)]


def test_tamper_and_replay():
    p = plan()
    a = receipt(p, 'a', [])
    bad = copy.deepcopy(a)
    bad['counts']['??'] -= 1
    bad['counts']['00'] += 1
    with pytest.raises(ValidationError):
        run(p, [bad])
    with pytest.raises(ValidationError):
        run(p, [a, a])
    q = freeze({**p['plan'], 'study': 'other'})
    with pytest.raises(ValidationError):
        run(q, [a])
    with pytest.raises(ValidationError):
        run(p, [a], {'a': b'c'*32, 'b': KEYS['b']})


def test_roster_scope_and_duplicate():
    p = plan()
    assert roster_commitment('a', ['u2', 'u1'], KEYS['a']) == p['plan']['sites'][0]
    with pytest.raises(ValidationError):
        site_receipt(p, p['plan_sha256'], 'a', ['u1', 'new'], [], KEYS['a'])
    with pytest.raises(ValidationError):
        roster_commitment('a', ['u1', 'u1'], KEYS['a'])
    with pytest.raises(ValidationError):
        aggregate(p, 'bad', [], KEYS)


@pytest.mark.parametrize('rows', [[row('other', True, False)], [row('u1', True, False)]*2, [row('u1', 1, False)], [row('u1', False, 'false')]])
def test_invalid_rows(rows):
    with pytest.raises(ValidationError):
        receipt(plan(), 'a', rows)


@pytest.mark.parametrize('keys', [{}, {'a': b'a'*32, 'b': b'a'*32}, {'a': b'a', 'b': b'b'*32}])
def test_invalid_keys(keys):
    with pytest.raises(ValidationError):
        run(plan(), [], keys)
