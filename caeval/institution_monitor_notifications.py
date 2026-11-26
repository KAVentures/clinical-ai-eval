"""Role-scoped browser review of a read-only local monitoring relay."""
import json
import sqlite3
from . import institution_access as access, unified_assessment as u
from . import institution_lifecycle_monitor as monitor
from . import monitor_alert_relay as relay, monitor_relay_bridge as bridge

EVENT="monitor-notification-review-locked"
MAX_REVIEWS=2000
MAX_STORAGE=64*1024*1024


def _schema(conn):
    monitor._schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS monitor_notification_reviews(
        monitor TEXT NOT NULL REFERENCES lifecycle_monitors(id),
        attempt TEXT NOT NULL,payload_digest TEXT NOT NULL,
        payload TEXT NOT NULL,digest TEXT NOT NULL,
        actor TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE,
        PRIMARY KEY(monitor,attempt))""")


def _inventory(conn):
    monitor._inventory(conn)
    rows=conn.execute("""SELECT r.monitor,r.attempt,r.digest,r.actor,r.audit_seq,
        m.audit_seq AS monitor_seq FROM monitor_notification_reviews r
        LEFT JOIN lifecycle_monitors m ON m.id=r.monitor""").fetchall()
    if len(rows)>MAX_REVIEWS or any(r["monitor_seq"] is None or r["audit_seq"]<=r["monitor_seq"] for r in rows):
        raise access.AccessDenied("notification review inventory or ordering mismatch")
    expected={(r["audit_seq"],r["actor"],r["monitor"]+":"+r["attempt"]+":"+r["digest"]) for r in rows}
    actual={(r["seq"],r["actor"],r["object_id"]) for r in conn.execute(
        "SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok'",(EVENT,))}
    if len(expected)!=len(rows) or actual!=expected:
        raise access.AccessDenied("notification review audit mismatch")
    if conn.execute("SELECT coalesce(sum(length(payload)),0) FROM monitor_notification_reviews").fetchone()[0]>MAX_STORAGE:
        raise access.AccessDenied("notification review storage limit")


def _path(directory,ident):
    # Trusted IT provisions this fixed path. No URL, secret or caller-defined path.
    relay.require(relay.token(ident),"invalid monitor ID")
    return access._root(directory)/"notification-relays"/(ident+".sqlite3")


def _reviews(conn,ident,plan_digest):
    result={}
    for row in conn.execute("SELECT * FROM monitor_notification_reviews WHERE monitor=? ORDER BY audit_seq",(ident,)):
        doc=bridge.verify_record(json.loads(row["payload"]))
        if (doc["monitor_id"]!=ident or doc["plan_digest"]!=plan_digest or doc["attempt_id"]!=row["attempt"]
                or doc["payload_digest"]!=row["payload_digest"] or relay.digest(doc)!=row["digest"]):
            raise access.AccessDenied("notification review replay mismatch")
        result[row["attempt"]]={"record":doc,"digest":row["digest"],"actor":row["actor"],"audit_seq":row["audit_seq"]}
    return result


def _context(directory,conn,ident):
    _,p,_,_=monitor._get(conn,ident,monitor._today())
    return p,_path(directory,ident),_reviews(conn,ident,p["digest"])


def inbox(directory,actor,ident):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator")
        _inventory(conn)
        p,path,reviews=_context(directory,conn,ident)
        result={"monitor_id":ident,"plan_digest":p["digest"],"target":p["target"],
                "reviewed_count":len(reviews),"relay_state":"unavailable","snapshot":None,
                "human_delivery_verified":False,"deployment_authorized":False}
        try:
            snap=bridge.snapshot(path,ident,p["digest"],{k:r["record"]["payload_digest"] for k,r in reviews.items()})
            result.update(relay_state="available" if snap["source_consistent_with_review_metadata"] else "history_mismatch",snapshot=snap)
        except (sqlite3.Error,ValueError,KeyError,TypeError,OSError,AttributeError,RecursionError):
            pass
        return result


def detail(directory,actor,ident,attempt_id):
    relay.require(relay.hex64(attempt_id),"invalid attempt ID")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator")
        _inventory(conn)
        p,path,reviews=_context(directory,conn,ident)
        if attempt_id in reviews:
            return {"monitor_id":ident,"review":reviews[attempt_id],"item":None}
        try:
            item=bridge.attempt(path,ident,p["digest"],attempt_id)
        except (sqlite3.Error,ValueError,KeyError,TypeError,OSError,AttributeError,RecursionError) as exc:
            raise access.AccessDenied("relay attempt unavailable") from exc
        return {"monitor_id":ident,"review":None,"item":item}


def review(directory,actor,ident,attempt_id,payload_digest,external_record_digest,*,confirmed=False):
    relay.require(confirmed is True and all(relay.hex64(v) for v in (attempt_id,payload_digest,external_record_digest)),
                  "explicit exact notification review required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin","operator")
        _inventory(conn)
        p,path,reviews=_context(directory,conn,ident)
        if attempt_id in reviews:
            prior=reviews[attempt_id]
            record=prior["record"]
            if (prior["actor"]!=actor["user_id"] or record["payload_digest"]!=payload_digest
                    or record["external_record_digest"]!=external_record_digest):
                raise access.AccessDenied("review already locked")
            return prior["digest"]
        try:
            snap=bridge.snapshot(path,ident,p["digest"],{k:r["record"]["payload_digest"] for k,r in reviews.items()},limit=1)
            if not snap["source_consistent_with_review_metadata"]:
                raise access.AccessDenied("relay history mismatch; investigate before review")
            item=bridge.attempt(path,ident,p["digest"],attempt_id)
            doc=bridge.review_record(item,payload_digest,external_record_digest)
        except (sqlite3.Error,ValueError,KeyError,TypeError,OSError,AttributeError,RecursionError) as exc:
            raise access.AccessDenied("relay evidence unavailable or changed") from exc
        raw=relay.canonical(doc)
        count,total=conn.execute("SELECT count(*),coalesce(sum(length(payload)),0) FROM monitor_notification_reviews").fetchone()
        if count>=MAX_REVIEWS or total+len(raw)>MAX_STORAGE:
            raise access.AccessDenied("notification review storage limit reached")
        digest=relay.digest(doc)
        seq=u._audit(conn,actor,EVENT,ident+":"+attempt_id+":"+digest)
        conn.execute("INSERT INTO monitor_notification_reviews VALUES(?,?,?,?,?,?,?)",
                     (ident,attempt_id,payload_digest,raw,digest,actor["user_id"],seq))
        return digest
