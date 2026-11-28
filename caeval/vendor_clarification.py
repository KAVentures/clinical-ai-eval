"""Offline, version-bound buyer/vendor clarification ledger; no clinical authority."""
import hashlib
import json
import re
from datetime import date

SCHEMA = 'vendor-clarification/1'
MAX_EVENTS = 2000
MAX_ITEMS = 200


class ClarificationError(ValueError):
    pass


def require(ok):
    if not ok:
        raise ClarificationError('invalid clarification scope, event or transition')


def fields(x, keys):
    require(isinstance(x, dict) and set(x) == set(keys.split()))


def token(x):
    return isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', x) is not None


def hex64(x):
    return isinstance(x, str) and re.fullmatch(r'[a-f0-9]{64}', x) is not None


def day(x):
    try:
        require(isinstance(x, str) and date.fromisoformat(x).isoformat() == x)
    except (ValueError, TypeError):
        raise ClarificationError('invalid calendar date') from None
    return x


def digest(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')).hexdigest()


def freeze(spec):
    fields(spec, 'request_sha256 version requester reviewer vendor requirements')
    require(hex64(spec['request_sha256']) and all(token(spec[k]) for k in ('version', 'requester', 'reviewer', 'vendor')))
    require(len({spec[k] for k in ('requester', 'reviewer', 'vendor')}) == 3)
    rs = spec['requirements']
    require(isinstance(rs, list) and 1 <= len(rs) <= MAX_ITEMS and all(token(r) for r in rs) and len(set(rs)) == len(rs))
    plan = {**spec, 'requirements': sorted(rs)}
    return {'schema': SCHEMA, 'plan': plan, 'plan_sha256': digest([SCHEMA, plan])}


def _replay(frozen, expected_plan, events):
    fields(frozen, 'schema plan plan_sha256')
    require(freeze(frozen['plan']) == frozen and frozen['plan_sha256'] == expected_plan)
    require(isinstance(events, list) and len(events) <= MAX_EVENTS)
    p = frozen['plan']
    head, last_day = expected_plan, None
    items = {}
    for i, event in enumerate(events, 1):
        fields(event, 'seq previous body digest')
        b = event['body']
        fields(b, 'id requirement actor action date due evidence_sha256 response_sha256')
        require(type(event['seq']) is int and event['seq'] == i and event['previous'] == head)
        require(event['digest'] == digest([SCHEMA, expected_plan, i, head, b]))
        ident, req, actor, action = (b[k] for k in ('id', 'requirement', 'actor', 'action'))
        require(token(ident) and token(req) and req in p['requirements'] and token(actor) and hex64(b['evidence_sha256']))
        stamp = day(b['date'])
        require(last_day is None or stamp >= last_day)
        old = items.get(ident)
        if action == 'open':
            require(old is None and len(items) < MAX_ITEMS and actor == p['requester'] and b['response_sha256'] is None)
            due = day(b['due'])
            require(due >= stamp)
            items[ident] = {'requirement': req, 'due': due, 'state': 'OPEN', 'response_sha256': None, 'last_date': stamp}
        else:
            require(old is not None and old['requirement'] == req and b['due'] is None)
            if action == 'respond':
                require(actor == p['vendor'] and old['state'] in ('OPEN', 'REOPENED') and b['response_sha256'] is None)
                old.update(state='AWAITING_REVIEW', response_sha256=b['evidence_sha256'])
            elif action in ('accept', 'reject'):
                require(actor == p['reviewer'] and old['state'] == 'AWAITING_REVIEW' and b['response_sha256'] == old['response_sha256'])
                old['state'] = 'ADMINISTRATIVELY_RESOLVED' if action == 'accept' else 'REOPENED'
            elif action == 'reopen':
                require(actor == p['reviewer'] and old['state'] == 'ADMINISTRATIVELY_RESOLVED' and b['response_sha256'] == old['response_sha256'])
                old['state'] = 'REOPENED'
            else:
                raise ClarificationError('unknown action')
            old['last_date'] = stamp
        head, last_day = event['digest'], stamp
    return head, last_day, items


def append(frozen, expected_plan, events, expected_head, body):
    head, _, _ = _replay(frozen, expected_plan, events)
    require(head == expected_head and len(events) < MAX_EVENTS)
    fields(body, 'id requirement actor action date due evidence_sha256 response_sha256')
    new = {'seq': len(events)+1, 'previous': head, 'body': dict(body)}
    new['digest'] = digest([SCHEMA, expected_plan, new['seq'], head, body])
    _replay(frozen, expected_plan, events + [new])
    return new


def report(frozen, expected_plan, events, expected_head, as_of):
    head, last_day, items = _replay(frozen, expected_plan, events)
    day(as_of)
    require(head == expected_head and (last_day is None or as_of >= last_day))
    rows = []
    for ident, item in sorted(items.items()):
        overdue = item['state'] != 'ADMINISTRATIVELY_RESOLVED' and as_of > item['due']
        rows.append({'id': ident, **item, 'overdue': overdue})
    return {'schema': SCHEMA, 'plan_sha256': expected_plan, 'head': head, 'as_of': as_of,
            'items': rows, 'unresolved': sum(r['state'] != 'ADMINISTRATIVELY_RESOLVED' for r in rows),
            'overdue': sum(r['overdue'] for r in rows), 'decision_grade': False, 'publication_safe': False}
