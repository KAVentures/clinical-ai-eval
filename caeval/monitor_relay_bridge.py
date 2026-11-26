"""Read-only relay handoff for an institutional review ledger.

Relay acceptance and institutional review are distinct records. The gateway
never mutates the relay database or reads its credential.
"""
import sqlite3
from contextlib import closing, contextmanager
from . import monitor_alert_relay as relay

MAX_PAGE = 25


@contextmanager
def read_db(path, monitor_id, plan_digest):
    p = relay.private_path(path)
    with closing(sqlite3.connect(p.as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        scope = relay._scope(conn)
        relay.require(scope == (monitor_id, plan_digest), 'relay scope mismatch')
        yield conn, scope


def _item(row, scope):
    return {'envelope': relay._row(row, scope), 'payload_digest': row['payload_digest'],
            'accepted_at': row['accepted_at'], 'local_review_recorded': row['reviewed_at'] is not None}


def snapshot(path, monitor_id, plan_digest, reviewed, limit=MAX_PAGE):
    relay.require(type(limit) is int and 1 <= limit <= MAX_PAGE, 'invalid inbox limit')
    relay.require(isinstance(reviewed, dict) and all(relay.hex64(k) and relay.hex64(v) for k,v in reviewed.items()),
                  'invalid reviewed inventory')
    with read_db(path, monitor_id, plan_digest) as (conn, scope):
        rows = conn.execute('SELECT id,payload_digest FROM batches ORDER BY accepted_at,id LIMIT ?',
                            (relay.MAX_BATCHES+1,)).fetchall()
        relay.require(len(rows) <= relay.MAX_BATCHES, 'relay inventory exceeds limit')
        by_id = {r['id']:r['payload_digest'] for r in rows}
        relay.require(len(by_id) == len(rows) and all(relay.hex64(k) and relay.hex64(v) for k,v in by_id.items()),
                      'invalid relay inventory')
        missing = sorted(set(reviewed) - set(by_id))
        changed = sorted(k for k,v in reviewed.items() if k in by_id and by_id[k] != v)
        pending = [r['id'] for r in rows if r['id'] not in reviewed]
        items = []
        for ident in pending[:limit]:
            row = conn.execute('SELECT * FROM batches WHERE id=?', (ident,)).fetchone()
            items.append(_item(row, scope))
        return {'pending_count':len(pending), 'items':items, 'missing_reviewed_count':len(missing),
                'changed_reviewed_count':len(changed), 'source_consistent_with_review_metadata':not (missing or changed),
                'full_payload_inventory_verified':False, 'human_delivery_verified':False}


def attempt(path, monitor_id, plan_digest, attempt_id):
    relay.require(relay.hex64(attempt_id), 'invalid attempt identity')
    with read_db(path, monitor_id, plan_digest) as (conn, scope):
        row = conn.execute('SELECT * FROM batches WHERE id=?', (attempt_id,)).fetchone()
        relay.require(row is not None, 'unknown relay attempt')
        return _item(row, scope)


def review_record(item, expected_digest, external_record_digest):
    relay.keys(item, 'envelope payload_digest accepted_at local_review_recorded')
    envelope = relay.validate(item['envelope'])
    relay.require(relay.hex64(expected_digest) and expected_digest == item['payload_digest'] == relay.digest(envelope)
                  and relay.hex64(external_record_digest) and type(item['accepted_at']) is int and item['accepted_at'] >= 0
                  and type(item['local_review_recorded']) is bool, 'exact review evidence required')
    return {'schema':'institution-monitor-notification-review/1','monitor_id':envelope['monitor_id'],
            'plan_digest':envelope['plan_digest'],'attempt_id':envelope['attempt_digest'],
            'payload_digest':expected_digest,'envelope':envelope,'accepted_at':item['accepted_at'],
            'external_record_digest':external_record_digest,'external_record_verified':False,
            'alert_resolved':False,'human_delivery_verified':False,'deployment_authorized':False}


def verify_record(doc):
    item = {'envelope':doc['envelope'],'payload_digest':doc['payload_digest'],
            'accepted_at':doc['accepted_at'],'local_review_recorded':False}
    relay.require(review_record(item,doc['payload_digest'],doc['external_record_digest']) == doc,
                  'notification review replay mismatch')
    return doc
