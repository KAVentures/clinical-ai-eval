from fractions import Fraction
from itertools import product
import copy
import pytest
from caeval.population_shift import freeze, analyze, tv_bounds, ShiftError


def spec():
    return dict(study='s', reference_population='dev', target_population='hospital',
                coding_version='v1', reference_n=4, target_n=4,
                features=[dict(feature='age-band', levels=['younger', 'older'], warning_bp=2500)])


def run(a, b):
    p = freeze(spec())
    return analyze(p, p['plan_sha256'], a, b)['features'][0]


def counts(a, b):
    return {'age-band': {'older': a, 'younger': b}}


def test_full_observation_and_boundary():
    assert run(counts(2, 2), counts(2, 2))['status'] == 'BELOW_LIMIT'
    r = run(counts(2, 2), counts(3, 1))
    assert r['cohort_tv_bounds'] == [.25, .25]
    assert r['status'] == 'AT_OR_ABOVE_LIMIT'
    assert r['observed_only_tv'] == .25


def test_missingness_and_absent_feature():
    r = run(counts(2, 2), {})
    assert r['cohort_tv_bounds'] == [0, .5]
    assert r['status'] == 'INCONCLUSIVE'
    assert r['observed_only_tv'] is None
    assert r['missing_rate_delta'] == 1
    assert run({}, {})['cohort_tv_bounds'] == [0, 1]


def test_known_shift_robust_to_unknowns():
    r = run(counts(4, 0), counts(0, 3))
    assert r['cohort_tv_bounds'] == [.75, 1]
    assert r['status'] == 'AT_OR_ABOVE_LIMIT'
    assert r['target_missing'] == 1


def compositions(n, k):
    if k == 1:
        yield (n,)
    else:
        for x in range(n+1):
            for rest in compositions(n-x, k-1):
                yield (x, *rest)


def test_exhaustive_integer_completions():
    for na, nb in product(range(1, 4), repeat=2):
        for a, b in product(compositions(na, 4), compositions(nb, 4)):
            values = [sum(abs(Fraction(x+u, na)-Fraction(y+v, nb)) for x,y,u,v in zip(a,b,aa,bb))/2
                      for aa, bb in product(compositions(a[-1], 3), compositions(b[-1], 3))]
            lo, hi = tv_bounds(a[:-1], b[:-1], na, nb)
            assert 0 <= lo <= min(values) <= max(values) == hi <= 1
            if na == nb:
                assert lo == min(values)


def test_plan_tampering_and_order():
    p = freeze(spec())
    q = spec()
    q['features'][0]['levels'].reverse()
    assert freeze(q) == p
    q = copy.deepcopy(p)
    q['plan']['target_n'] += 1
    with pytest.raises(ShiftError):
        analyze(q, p['plan_sha256'], {}, {})
    with pytest.raises(ShiftError):
        analyze(p, 'bad', {}, {})
    r = analyze(p, p['plan_sha256'], {}, {})
    assert not r['decision_grade'] and not r['publication_safe']


@pytest.mark.parametrize('bad', [{'unknown': {}}, {'age-band': {'older': 1}}, counts(3, 2), counts(-1, 2), counts(True, 2), counts(1.0, 2)])
def test_bad_counts(bad):
    with pytest.raises(ShiftError):
        run(bad, {})


@pytest.mark.parametrize('change', [dict(reference_n=0), dict(target_n=True), dict(target_population='dev'), dict(features=[]), dict(features=[dict(feature='x', levels=['a','a'], warning_bp=2000)]), dict(features=[dict(feature='x', levels=['a','b'], warning_bp=0)])])
def test_bad_plan(change):
    with pytest.raises(ShiftError):
        freeze({**spec(), **change})


def test_cli_roundtrip(tmp_path, capsys):
    import json
    import os
    from caeval.population_shift_cli import main
    source = tmp_path / 'spec.json'
    source.write_text(json.dumps(spec()))
    frozen = tmp_path / 'plan.json'
    args = ['freeze', '--spec', str(source), '--output', str(frozen)]
    assert main(args) == 0
    digest = capsys.readouterr().out.strip()
    assert digest == freeze(spec())['plan_sha256']
    assert main(args) == 2
    a, b = tmp_path / 'a.json', tmp_path / 'b.json'
    a.write_text(json.dumps(counts(2, 2)))
    b.write_text(json.dumps(counts(3, 1)))
    output = tmp_path / 'report.json'
    args = ['analyze', '--plan', str(frozen), '--expected-digest', digest,
            '--reference-counts', str(a), '--target-counts', str(b), '--output', str(output)]
    assert main(args) == 0
    assert json.loads(output.read_text())['features'][0] == run(counts(2, 2), counts(3, 1))
    assert main(args) == 2
    if os.name == 'posix':
        assert output.stat().st_mode & 0o777 == 0o600


def test_cli_duplicate_keys_and_invalid_plan(tmp_path, capsys):
    from caeval.population_shift_cli import main
    source = tmp_path / 'spec.json'
    source.write_text('{"private-id":1,"private-id":2}')
    output = tmp_path / 'plan.json'
    assert main(['freeze', '--spec', str(source), '--output', str(output)]) == 2
    assert 'private-id' not in capsys.readouterr().err
    assert not output.exists()
