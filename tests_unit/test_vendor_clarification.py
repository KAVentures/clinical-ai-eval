import copy
import pytest
from caeval.vendor_clarification import freeze, append, report, ClarificationError


def plan():
    return freeze(dict(request_sha256='a'*64, version='v1', requester='buyer', reviewer='reviewer', vendor='vendor', requirements=['safety', 'monitoring']))


def body(action='open', actor='buyer', date='2026-01-01', due='2026-01-03', evidence='b'*64, response=None, ident='q1', requirement='safety'):
    return dict(id=ident, requirement=requirement, actor=actor, action=action, date=date, due=due, evidence_sha256=evidence, response_sha256=response)


def add(p, events, b):
    d = p['plan_sha256']
    return events + [append(p, d, events, events[-1]['digest'] if events else d, b)]


def project(p, events, date='2026-01-05'):
    d = p['plan_sha256']
    return report(p, d, events, events[-1]['digest'] if events else d, date)


def response(evidence='c'*64):
    return body('respond', 'vendor', '2026-01-02', None, evidence)


def review(action='accept', response_hash='c'*64, date='2026-01-03'):
    return body(action, 'reviewer', date, None, 'd'*64, response_hash)


def test_full_cycle():
    p = plan()
    e = add(p, [], body())
    assert project(p, e)['overdue'] == 1
    e = add(p, e, response())
    assert project(p, e)['items'][0]['state'] == 'AWAITING_REVIEW'
    e = add(p, e, review())
    r = project(p, e)
    assert r['unresolved'] == r['overdue'] == 0
    assert not r['decision_grade'] and not r['publication_safe']
    e = add(p, e, review('reopen', date='2026-01-04'))
    assert project(p, e)['overdue'] == 1
    e = add(p, e, body('respond', 'vendor', '2026-01-05', None, 'e'*64))
    with pytest.raises(ClarificationError):
        add(p, e, review(date='2026-01-05'))
    e = add(p, e, review(response_hash='e'*64, date='2026-01-05'))
    assert project(p, e)['unresolved'] == 0


def test_reject_and_deadline():
    p = plan()
    e = add(p, add(p, [], body()), response())
    e = add(p, e, review('reject'))
    assert project(p, e, '2026-01-03')['overdue'] == 0
    assert project(p, e)['items'][0]['state'] == 'REOPENED'


@pytest.mark.parametrize('bad', [body(actor='vendor'), body(requirement='unknown'), body(due='2025-01-01'), body(evidence='bad'), body(date='2026-02-30')])
def test_invalid_open(bad):
    with pytest.raises(ClarificationError):
        add(plan(), [], bad)


@pytest.mark.parametrize('bad', [body(), review(), body('respond','buyer',due=None), body('respond','vendor',due='2026-01-03'), body('respond','vendor',due=None,requirement='monitoring')])
def test_invalid_transitions(bad):
    p = plan()
    with pytest.raises(ClarificationError):
        add(p, add(p, [], body()), bad)


def test_chain_tamper_and_stale_head():
    p = plan()
    e = add(p, add(p, [], body()), response())
    d = p['plan_sha256']
    q = copy.deepcopy(e)
    q[0]['body']['evidence_sha256'] = 'f'*64
    for events in (q, e[::-1], e[:1]):
        with pytest.raises(ClarificationError):
            report(p, d, events, e[-1]['digest'], '2026-01-05')
    with pytest.raises(ClarificationError):
        append(p, d, e, e[0]['digest'], review())
    with pytest.raises(ClarificationError):
        report(p, d, e, e[-1]['digest'], '2026-01-01')


def test_plan_scope():
    p = plan()
    with pytest.raises(ClarificationError):
        freeze({**p['plan'], 'reviewer': 'buyer'})
    q = freeze({**p['plan'], 'version': 'v2'})
    with pytest.raises(ClarificationError):
        project(q, add(p, [], body()))
    assert project(p, [])['items'] == []
