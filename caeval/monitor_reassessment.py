"""Audited handoff from reported operational version changes to evaluation.

The linkage is administrative traceability only. It does not clear an alert,
authorize a new version, establish clinical safety, or transfer old evidence.
"""
import json
from . import diagnostic_imaging as d, unified_assessment as u, unified_successor as successor
from . import institution_access as access, institution_lifecycle_monitor as monitor

EVENT="monitor-version-reassessment-linked"
MAX_LINKS=6000


def _schema(conn):
    monitor._schema(conn)
    successor._schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS monitor_version_handoffs(
        monitor TEXT NOT NULL REFERENCES lifecycle_monitors(id),
        incident TEXT NOT NULL,
        successor TEXT NOT NULL REFERENCES unified_assessment_programmes(id),
        payload TEXT NOT NULL,digest TEXT NOT NULL,actor TEXT NOT NULL,
        audit_seq INTEGER NOT NULL UNIQUE,
        PRIMARY KEY(monitor,incident))""")


def _inventory(conn):
    monitor._inventory(conn)
    successor._inventory(conn)
    rows=conn.execute("SELECT monitor,incident,digest,actor,audit_seq FROM monitor_version_handoffs").fetchall()
    expected={(r["audit_seq"],r["actor"],r["monitor"]+":"+r["incident"]+":"+r["digest"]) for r in rows}
    actual={(r["seq"],r["actor"],r["object_id"]) for r in conn.execute(
        "SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok'",(EVENT,))}
    if len(expected)!=len(rows) or expected!=actual:
        raise access.AccessDenied("version handoff audit inventory mismatch")


def _material(conn,programme,p,incident,new_id):
    old_id=programme["programme_id"]
    doc=incident["incident"]
    if doc["category"]!="version_change" or doc["plan_digest"]!=p["digest"]:
        raise access.AccessDenied("exact version-change incident required")
    row=conn.execute("SELECT * FROM unified_assessment_successors WHERE successor=? AND baseline=?",
                     (new_id,old_id)).fetchone()
    if not row:
        raise access.AccessDenied("verified direct programme successor required")
    lineage=successor._read(conn,row)
    new,_=u._read(conn,new_id)
    vid=p["spec"]["vendor_id"]
    old_plans=[r for r in programme["plans"] if r["vendor_id"]==vid]
    new_plans=[r for r in new["plans"] if r["vendor_id"]==vid]
    expected={"name":p["target"]["name"],"version":doc["observed_version"]}
    if not old_plans or not new_plans or any(r["target"]!=expected for r in new_plans):
        raise access.AccessDenied("successor does not match observed product version")
    old_ids={r["plan_id"] for r in old_plans}
    new_ids={r["plan_id"] for r in new_plans}
    pairs=[r for r in lineage["plans"] if r["baseline_plan_id"] in old_ids]
    if ({r["baseline_plan_id"] for r in pairs}!=old_ids or
        {r["successor_plan_id"] for r in pairs}!=new_ids or
        any(r["native_reassessment_digest"] is None or
            r["baseline_version"]!=p["target"]["version"] or
            r["successor_version"]!=doc["observed_version"] for r in pairs)):
        raise access.AccessDenied("vendor-specific native reassessment is incomplete")
    return d._sealed({"schema":"monitor-version-handoff/1","monitor_id":p["spec"]["monitor_id"],
                      "monitor_digest":p["digest"],"incident_id":doc["incident_id"],
                      "incident_digest":incident["digest"],"vendor_id":vid,
                      "baseline_id":old_id,"baseline_digest":programme["digest"],
                      "baseline_target":p["target"],"successor_id":new_id,
                      "successor_digest":new["digest"],"successor_target":expected,
                      "lineage_digest":lineage["digest"],"plans":pairs,
                      "alert_resolved":False,"prior_evidence_transferred":False,
                      "decision_grade":False,"deployment_authorized":False})


def _context(conn,ident,incident_id):
    programme,p,records,status=monitor._get(conn,ident,monitor._today())
    matches=[r for r in records["incident"] if r["incident"]["incident_id"]==incident_id]
    if len(matches)!=1 or matches[0]["incident"]["category"]!="version_change":
        raise access.AccessDenied("reported version change required")
    return programme,p,matches[0]


def _read(conn,row,programme,p,incident):
    doc=json.loads(row["payload"])
    rebuilt=_material(conn,programme,p,incident,row["successor"])
    if doc!=rebuilt or doc["digest"]!=row["digest"] or doc["monitor_id"]!=row["monitor"] or doc["incident_id"]!=row["incident"]:
        raise access.AccessDenied("monitor handoff integrity mismatch")
    event=conn.execute("SELECT audit_seq FROM lifecycle_monitor_events WHERE monitor=? AND kind='incident' AND item=?",
                       (row["monitor"],row["incident"])).fetchone()
    lineage=conn.execute("SELECT audit_seq FROM unified_assessment_successors WHERE successor=?",(row["successor"],)).fetchone()
    if not event or not lineage or row["audit_seq"]<=max(event["audit_seq"],lineage["audit_seq"]):
        raise access.AccessDenied("monitor handoff ordering mismatch")
    return doc


def _preview(directory,conn,ident,incident_id,new_id):
    programme,p,incident=_context(conn,ident,incident_id)
    if conn.execute("SELECT 1 FROM monitor_version_handoffs WHERE monitor=? AND incident=?",(ident,incident_id)).fetchone():
        raise access.AccessDenied("version-change handoff already locked")
    doc=_material(conn,programme,p,incident,new_id)
    new,_=u._read(conn,new_id)
    if not u._current(directory,new):
        raise access.AccessDenied("current successor programme required")
    return doc


def preview(directory,actor,ident,incident_id,new_id):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        return _preview(directory,conn,ident,incident_id,new_id)


def lock(directory,actor,ident,incident_id,new_id,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit version-change handoff approval required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        doc=_preview(directory,conn,ident,incident_id,new_id)
        if doc["digest"]!=approval:
            raise access.AccessDenied("handoff differs from preview")
        if conn.execute("SELECT count(*) FROM monitor_version_handoffs").fetchone()[0]>=MAX_LINKS:
            raise access.AccessDenied("handoff limit")
        seq=u._audit(conn,actor,EVENT,ident+":"+incident_id+":"+approval)
        conn.execute("INSERT INTO monitor_version_handoffs VALUES(?,?,?,?,?,?,?)",
                     (ident,incident_id,new_id,json.dumps(doc),approval,actor["user_id"],seq))
        return approval


def _view(directory,conn,actor,ident,*,choices=False):
    programme,p,records,status=monitor._get(conn,ident,monitor._today())
    incidents={r["incident"]["incident_id"]:r for r in records["incident"] if r["incident"]["category"]=="version_change"}
    locked={r["incident"]:r for r in conn.execute("SELECT * FROM monitor_version_handoffs WHERE monitor=?",(ident,))}
    if set(locked)-set(incidents):
        raise access.AccessDenied("handoff refers to missing version incident")
    out=[]
    for iid,incident in sorted(incidents.items()):
        item={"incident_id":iid,"observed_version":incident["incident"]["observed_version"],
              "date":incident["incident"]["date"],"state":"unlinked","handoff":None,
              "successor_state":None,"eligible_successors":[],"alert_resolved":False}
        if iid in locked:
            doc=_read(conn,locked[iid],programme,p,incident)
            item.update(state="linked",handoff=doc)
            new,_=u._read(conn,doc["successor_id"])
            try:
                item["successor_state"]="current" if u._current(directory,new) else "stale_plan"
            except u.ERRORS:
                item["successor_state"]="unavailable"
        elif choices:
            for row in conn.execute("SELECT successor FROM unified_assessment_successors WHERE baseline=? ORDER BY successor",
                                    (programme["programme_id"],)):
                try:
                    doc=_material(conn,programme,p,incident,row["successor"])
                    new,_=u._read(conn,row["successor"])
                    if u._current(directory,new):
                        item["eligible_successors"].append(doc["successor_id"])
                except u.ERRORS:
                    continue
        out.append(item)
    return {"monitor_id":ident,"baseline_id":programme["programme_id"],
            "vendor_id":p["spec"]["vendor_id"],"incidents":out,
            "version_reassessment_required":status["version_reassessment_required"],
            "decision_grade":False,"deployment_authorized":False}


def view(directory,actor,ident):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator")
        _inventory(conn)
        return _view(directory,conn,actor,ident,choices=actor["role"]=="admin")


def overview(directory,actor):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator","observer")
        _inventory(conn)
        result=[]
        for r in conn.execute("SELECT id FROM lifecycle_monitors ORDER BY id").fetchall():
            doc=_view(directory,conn,actor,r["id"])
            items=doc["incidents"]
            result.append({"monitor_id":doc["monitor_id"],"baseline_id":doc["baseline_id"],
                           "vendor_id":doc["vendor_id"],"n_reported_changes":len(items),
                           "n_linked":sum(i["state"]=="linked" for i in items),
                           "n_current_successors":sum(i["successor_state"]=="current" for i in items),
                           "successor_programmes":sorted({i["handoff"]["successor_id"] for i in items if i["handoff"]}),
                           "version_reassessment_required":doc["version_reassessment_required"],
                           "decision_grade":False,"deployment_authorized":False})
        return result
