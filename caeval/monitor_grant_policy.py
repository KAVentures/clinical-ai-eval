"""Operational checks for exact-scope monitor polling credentials."""
import json


def check_polling_grant(grant, monitor_id, plan_digest, actual_digest, *, now, minimum_remaining):
    """Called only after native grant authentication and inventory verification."""
    if (type(now) is not int or type(minimum_remaining) is not int or
            not 1 <= minimum_remaining <= 86400 or type(grant['expires']) is not int):
        raise ValueError('invalid polling lifetime policy')
    if (grant['monitor'] != monitor_id or actual_digest != plan_digest or
            json.loads(grant['scopes']) != ['check'] or grant['revoked_seq'] is not None):
        raise ValueError('exact check-only monitoring credential required')
    if grant['expires'] - now < minimum_remaining:
        raise ValueError('polling credential expires too soon')
    return {'schema':'monitor-polling-credential-status/1', 'monitor_id':monitor_id,
            'plan_digest':plan_digest, 'expires':grant['expires'], 'remaining_seconds':grant['expires']-now,
            'scope':'check', 'source_authentication_checked':True, 'live_http_checked':False}


def assert_receipt_lifetime(conn, audit_seq, expires):
    """Recheck the actual persisted audit time before committing a receipt."""
    row = conn.execute('SELECT at FROM audit WHERE seq=?',(audit_seq,)).fetchone()
    if (not row or type(row[0]) is not int or type(expires) is not int or row[0] >= expires):
        raise ValueError('ingestion receipt outside grant lifetime')
