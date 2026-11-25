"""Audited aggregate monitoring store. All operations are local."""
import json
from datetime import datetime,timezone
from . import lifecycle_monitor as m, diagnostic_imaging as d
from . import institution_access as access, unified_assessment as u

MAX_MONITORS=60
FROZEN="lifecycle-monitor-frozen"
EVENT="lifecycle-monitor-event-locked"


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _schema(conn):
    u._schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS lifecycle_monitors(
        id TEXT PRIMARY KEY,programme TEXT NOT NULL,vendor TEXT NOT NULL,payload TEXT NOT NULL,
        digest TEXT NOT NULL,actor TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE,
        UNIQUE(programme,vendor))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS lifecycle_monitor_events(
        monitor TEXT NOT NULL REFERENCES lifecycle_monitors(id),kind TEXT NOT NULL,item TEXT NOT NULL,
        payload TEXT NOT NULL,digest TEXT NOT NULL,actor TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE,
        PRIMARY KEY(monitor,kind,item))""")


def _inventory(conn):
    u._inventory(conn)
    for table,event,keys in (
        ("lifecycle_monitors",FROZEN,("id","digest")),
        ("lifecycle_monitor_events",EVENT,("monitor","kind","item","digest"))):
        rows=conn.execute("SELECT audit_seq,actor,"+",".join(keys)+" FROM "+table).fetchall()
        expected={(r["audit_seq"],r["actor"],":".join(r[k] for k in keys)) for r in rows}
        actual={(r["seq"],r["actor"],r["object_id"]) for r in conn.execute(
            "SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok'",(event,))}
        if len(expected)!=len(rows) or expected!=actual:
            raise access.AccessDenied("monitor audit inventory mismatch")


def _preview(directory,conn,spec):
    programme,_=u._read(conn,spec["programme_id"])
    if not u._current(directory,programme):
        raise access.AccessDenied("current buyer programme required for registration")
    p=m.plan(programme,spec)
    if conn.execute("SELECT 1 FROM lifecycle_monitors WHERE id=? OR (programme=? AND vendor=?)",
                    (spec["monitor_id"],spec["programme_id"],spec["vendor_id"])).fetchone():
        raise access.AccessDenied("monitor or vendor programme already registered")
    return p


def preview(directory,actor,spec):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        return _preview(directory,conn,spec)


def freeze(directory,actor,spec,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit externally authorized monitoring registration required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        p=_preview(directory,conn,spec)
        if p["digest"]!=approval or conn.execute("SELECT count(*) FROM lifecycle_monitors").fetchone()[0]>=MAX_MONITORS:
            raise access.AccessDenied("registration differs from approval or limit reached")
        ident=spec["monitor_id"]
        seq=u._audit(conn,actor,FROZEN,ident+":"+approval)
        conn.execute("INSERT INTO lifecycle_monitors VALUES(?,?,?,?,?,?,?)",
                     (ident,spec["programme_id"],spec["vendor_id"],json.dumps(p),approval,actor["user_id"],seq))
        return ident


def _get(conn,ident,today):
    d._require(d._token(ident),"invalid monitor ID")
    row=conn.execute("SELECT * FROM lifecycle_monitors WHERE id=?",(ident,)).fetchone()
    if not row:
        raise access.AccessDenied("unknown monitor")
    p=json.loads(row["payload"])
    programme,seq=u._read(conn,row["programme"])
    if (m.plan(programme,p["spec"])!=p or p["digest"]!=row["digest"] or p["spec"]["monitor_id"]!=ident
            or p["spec"]["vendor_id"]!=row["vendor"] or row["audit_seq"]<=seq):
        raise access.AccessDenied("monitor binding mismatch")
    records={"report":[],"incident":[],"action":[]}
    # Reconstruct availability at event time using the audit timestamp,
    # not the current date; prevents retrospective action on a future alert.
    for r in conn.execute("SELECT e.*,a.at FROM lifecycle_monitor_events e JOIN audit a ON a.seq=e.audit_seq WHERE monitor=? ORDER BY e.audit_seq",(ident,)):
        doc=json.loads(r["payload"])
        kind=r["kind"]
        event_day=datetime.fromtimestamp(r["at"],timezone.utc).date().isoformat()
        if event_day>today or r["audit_seq"]<=row["audit_seq"] or kind not in records:
            raise access.AccessDenied("monitor event order mismatch")
        if kind=="report":
            rebuilt=m.report(p,doc["report"])
            item=str(doc["report"]["window_index"])
            changes=[m._date(i["incident"]["date"]) for i in records["incident"] if i["incident"]["category"]=="version_change"]
            if changes and m.window(p,doc["report"]["window_index"])[1]>min(changes):
                raise access.AccessDenied("report locked after known version overlap")
        elif kind=="incident":
            rebuilt=m.incident(p,doc["incident"])
            item=doc["incident"]["incident_id"]
        else:
            before=m.assess(p,records["report"],records["incident"],records["action"],event_day)
            rebuilt=m.action(p,doc["action"],{a["id"] for a in before["alerts"]})
            item=doc["digest"]
        if rebuilt!=doc or item!=r["item"] or doc["digest"]!=r["digest"]:
            raise access.AccessDenied("monitor event integrity mismatch")
        records[kind].append(doc)
        if kind!="action":
            m.assess(p,records["report"],records["incident"],records["action"],event_day)
    status=m.assess(p,records["report"],records["incident"],records["action"],today)
    return programme,p,records,status


def _event(directory,actor,ident,kind,doc,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit aggregate or external action attestation required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,*(("admin",) if kind=="action" else ("admin","operator")))
        _inventory(conn)
        today=_today()
        _,p,records,status=_get(conn,ident,today)
        if kind=="report":
            sealed=m.report(p,doc)
            item=str(doc["window_index"])
            # No version-mixed aggregate after a reported version change.
            changes=[m._date(r["incident"]["date"]) for r in records["incident"] if r["incident"]["category"]=="version_change"]
            if changes and m.window(p,doc["window_index"])[1]>min(changes):
                raise access.AccessDenied("window overlaps reported version change; use new version monitor")
        elif kind=="incident":
            sealed=m.incident(p,doc)
            item=doc["incident_id"]
            if len(records[kind])>=m.MAX_INCIDENTS:
                raise access.AccessDenied("incident limit")
        else:
            sealed=m.action(p,doc,{a["id"] for a in status["alerts"]})
            item=sealed["digest"]
            if len(records[kind])>=m.MAX_ACTIONS:
                raise access.AccessDenied("action limit")
        prior=conn.execute("SELECT digest FROM lifecycle_monitor_events WHERE monitor=? AND kind=? AND item=?",(ident,kind,item)).fetchone()
        if prior:
            if prior["digest"]!=sealed["digest"]:
                raise access.AccessDenied("immutable monitor record")
            return sealed["digest"]
        records[kind].append(sealed)
        m.assess(p,records["report"],records["incident"],records["action"],today)
        digest=sealed["digest"]
        seq=u._audit(conn,actor,EVENT,ident+":"+kind+":"+item+":"+digest)
        conn.execute("INSERT INTO lifecycle_monitor_events VALUES(?,?,?,?,?,?,?)",
                     (ident,kind,item,json.dumps(sealed),digest,actor["user_id"],seq))
        return digest


def submit_report(directory,actor,ident,doc,*,confirmed=False):
    return _event(directory,actor,ident,"report",doc,confirmed=confirmed)


def submit_incident(directory,actor,ident,doc,*,confirmed=False):
    return _event(directory,actor,ident,"incident",doc,confirmed=confirmed)


def record_action(directory,actor,ident,doc,*,confirmed=False):
    return _event(directory,actor,ident,"action",doc,confirmed=confirmed)


def _coverage(directory,programme,p,status):
    try:
        current=u._current(directory,programme)
    except u.ERRORS:
        current=False
    return {"id":p["spec"]["monitor_id"],"programme_id":programme["programme_id"],
            "vendor_id":p["spec"]["vendor_id"],"target":p["target"],"plan_digest":p["digest"],
            "buyer_scope_current":current,"as_of":status["as_of"],"n_due_windows":len(status["windows"]),
            "n_missing_windows":sum(w["state"]=="missing" for w in status["windows"]),
            "n_alerts":len(status["alerts"]),"attention_required":status["attention_required"] or not current,
            "version_reassessment_required":status["version_reassessment_required"],
            "schedule_ended":status["schedule_ended"],"deployment_authorized":False,"decision_grade":False}


def overview(directory,actor):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator","observer")
        _inventory(conn)
        today=_today()
        out=[]
        for r in conn.execute("SELECT id FROM lifecycle_monitors ORDER BY audit_seq"):
            programme,p,_,status=_get(conn,r["id"],today)
            out.append(_coverage(directory,programme,p,status))
        return out


def detail(directory,actor,ident):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator")
        _inventory(conn)
        programme,p,records,status=_get(conn,ident,_today())
        return {"coverage":_coverage(directory,programme,p,status),"plan":p,"status":status,
                "records":records if actor["role"]=="admin" else None}


def export_preview(directory,actor,ident):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        programme,p,r,status=_get(conn,ident,_today())
        return m.package(programme,p,r["report"],r["incident"],r["action"],status["as_of"])


def export(directory,actor,ident,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit restricted monitor export required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        programme,p,r,status=_get(conn,ident,_today())
        doc=m.package(programme,p,r["report"],r["incident"],r["action"],status["as_of"])
        if doc["digest"]!=approval:
            raise access.AccessDenied("monitor evidence changed since preview")
        u._audit(conn,actor,"lifecycle-monitor-evidence-exported",ident+":"+approval)
        return doc
