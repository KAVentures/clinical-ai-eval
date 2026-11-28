"""Bounded live-grant capacity, with retained historical audit records."""
MAX_ACTIVE=200
MAX_HISTORY=8192
RECENT_HISTORY=100


def capacity(conn,now):
    if type(now) is not int or now<0:
        raise ValueError('invalid inventory clock')
    total,active,expired,revoked=conn.execute('''SELECT count(*),
        count(CASE WHEN revoked_seq IS NULL AND expires>? THEN 1 END),
        count(CASE WHEN revoked_seq IS NULL AND expires<=? THEN 1 END),
        count(CASE WHEN revoked_seq IS NOT NULL THEN 1 END)
        FROM lifecycle_ingestion_grants''',(now,now)).fetchone()
    return {'total':total,'active':active,'expired':expired,'revoked':revoked,
            'active_limit':MAX_ACTIVE,'history_limit':MAX_HISTORY,
            'active_remaining':max(0,MAX_ACTIVE-active),'history_remaining':max(0,MAX_HISTORY-total),
            'issuance_available':active<MAX_ACTIVE and total<MAX_HISTORY,
            'history_retained':True}


def require_available(conn,now):
    status=capacity(conn,now)
    if not status['issuance_available']:
        raise ValueError('monitoring grant capacity exhausted; inspect inventory')
    return status


def visible_rows(conn,now):
    """Show every time-active grant and a bounded recent historical page."""
    status=capacity(conn,now)
    if status['active']>MAX_ACTIVE or status['total']>MAX_HISTORY:
        raise ValueError('grant inventory exceeds supported capacity')
    active=conn.execute('''SELECT * FROM lifecycle_ingestion_grants
        WHERE revoked_seq IS NULL AND expires>? ORDER BY audit_seq DESC''',(now,)).fetchall()
    history=conn.execute('''SELECT * FROM lifecycle_ingestion_grants
        WHERE revoked_seq IS NOT NULL OR expires<=? ORDER BY audit_seq DESC LIMIT ?''',(now,RECENT_HISTORY)).fetchall()
    return status,active+history
