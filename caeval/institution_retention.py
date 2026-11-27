"""Governed retention policy and offline, explicitly approved maintenance.

Only expired authentication artifacts and unreferenced, unexported synthetic
FHIR rehearsals are eligible for deletion. No clinical raw evidence, physician
records, vendor evidence, committee snapshots, audit, or backups are removed.
"""
import json
import time
from . import (buyer_agent as buyer, buyer_plan, buyer_manual as manual,
               institution_access as access, institution_backup as backup,
               institution_fhir as fhir, institution_committee as committee)

SCHEMA="institution-retention-policy/1"
EVENT="retention-policy-approved"
MAX_POLICIES=200
MAX_HOLDS=200
LIMITATIONS=[
    "Clinical evidence, vendor records, clinical review, committee snapshots and audit are protected from automated deletion.",
    "Only expired authentication records and eligible synthetic rehearsals can be removed by this maintenance tool.",
    "Historical backups, exports, filesystem remnants and copies are not erased by database row deletion.",
    "Institutional legal obligations, hold decisions, data-subject requests and secure erasure require separate governance.",
    "Trusted IT must stop the browser, workers and all CLI writers before maintenance.",
]


def _fail(message):
    raise access.AccessDenied(message)


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS retention_policies(
      id TEXT PRIMARY KEY,payload TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS synthetic_fhir_retention_tombstones(
      id TEXT PRIMARY KEY,policy_id TEXT NOT NULL,plan_id TEXT NOT NULL,
      report_digest TEXT NOT NULL,deleted_at INTEGER NOT NULL,audit_seq INTEGER NOT NULL UNIQUE)""")
    fhir._schema(conn)
    committee._schema(conn)


def _admin(conn,actor):
    committee._actor(conn,actor,True)
    access.require(actor,"admin")


def _days(value,minimum,optional=False):
    if optional and value is None:
        return None
    if type(value) is not int or not minimum<=value<=36500:
        _fail("retention days outside supported range")
    return value


def _material(spec,actor,previous):
    if not isinstance(spec,dict) or set(spec)!={"expired_session_grace_days","failure_retention_days",
            "synthetic_fhir_retention_days","hold_all","synthetic_holds","rationale"}:
        _fail("complete explicit retention policy required")
    if type(spec["hold_all"]) is not bool or not isinstance(spec["synthetic_holds"],list) or len(spec["synthetic_holds"])>MAX_HOLDS:
        _fail("explicit bounded legal hold configuration required")
    holds=[manual._identifier(x,digest=True) for x in spec["synthetic_holds"]]
    if len(holds)!=len(set(holds)):
        _fail("duplicate legal hold")
    note=spec["rationale"]
    if not isinstance(note,str) or not note.strip() or len(note)>2000:
        _fail("document the local governance rationale")
    return {"schema":SCHEMA,"previous":previous,"approved_by":actor,
        "expired_session_grace_days":_days(spec["expired_session_grace_days"],0),
        "failure_retention_days":_days(spec["failure_retention_days"],1),
        "synthetic_fhir_retention_days":_days(spec["synthetic_fhir_retention_days"],1,True),
        "hold_all":spec["hold_all"],"synthetic_holds":sorted(holds),"rationale":note.strip(),
        "limitations":LIMITATIONS[:]}


def _policies(conn):
    rows=conn.execute("SELECT * FROM retention_policies ORDER BY audit_seq").fetchall()
    audits=conn.execute("SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok' ORDER BY seq",(EVENT,)).fetchall()
    if len(rows)!=len(audits) or len(rows)>MAX_POLICIES:
        _fail("retention policy inventory differs from audit")
    previous=None
    result=[]
    for row,ev in zip(rows,audits):
        doc=json.loads(row["payload"])
        spec={k:doc[k] for k in ("expired_session_grace_days","failure_retention_days",
                "synthetic_fhir_retention_days","hold_all","synthetic_holds","rationale")}
        if (doc!=_material(spec,doc["approved_by"],previous) or
                buyer_plan._digest(doc)!=row["id"] or ev["seq"]!=row["audit_seq"] or
                ev["actor"]!=doc["approved_by"] or ev["object_id"]!=row["id"]):
            _fail("retention policy history integrity mismatch")
        result.append({"id":row["id"],"policy":doc})
        previous=row["id"]
    return result


def approve(directory,actor,spec,previous,*,confirmed=False):
    if confirmed is not True:
        _fail("explicit governance approval required")
    with access.db(directory) as conn:
        _admin(conn,actor)
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        history=_policies(conn)
        actual=history[-1]["id"] if history else None
        if (previous or None)!=actual or len(history)>=MAX_POLICIES:
            _fail("retention policy changed or limit reached")
        doc=_material(spec,actor["user_id"],actual)
        for ident in doc["synthetic_holds"]:
            if not conn.execute("SELECT 1 FROM synthetic_fhir_reports WHERE id=?",(ident,)).fetchone():
                _fail("hold must name an existing synthetic rehearsal")
        ident=buyer_plan._digest(doc)
        access._audit(conn,actor["user_id"],EVENT,ident)
        seq=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO retention_policies VALUES(?,?,?)",
            (ident,buyer_plan._canonical(doc).decode("utf-8"),seq))
        return ident


def view(directory,actor):
    with access.db(directory) as conn:
        _admin(conn,actor)
        _schema(conn)
        history=_policies(conn)
        return {"current":history[-1] if history else None,"history":history,
            "synthetic_reports":[{"id":r["id"],"plan_id":r["plan_id"]} for r in conn.execute(
                "SELECT id,plan_id FROM synthetic_fhir_reports ORDER BY id")],
            "limitations":LIMITATIONS[:]}


def _protected(conn):
    refs=set()
    rows=conn.execute("SELECT id,payload FROM committee_snapshots").fetchall()
    frozen={r[0] for r in conn.execute("SELECT object_id FROM audit WHERE action='committee-snapshot-frozen' AND outcome='ok'")}
    if {r["id"] for r in rows}!=frozen:
        _fail("committee snapshot inventory differs from audit; refuse deletion")
    for row in rows:
        payload=json.loads(row["payload"])
        if buyer_plan._digest(payload)!=row["id"]:
            _fail("committee snapshot integrity mismatch; refuse deletion")
        for item in payload.get("lifecycle_inventory",{}).get("synthetic_fhir_assessments",[]):
            refs.add(manual._identifier(item["id"],digest=True))
    # Exported evidence may have circulated outside the workspace. Do not
    # silently erase the local original, even without a committee snapshot.
    refs.update(r[0] for r in conn.execute("SELECT object_id FROM audit WHERE action='synthetic-fhir-evidence-exported' AND outcome='ok'"))
    return refs


def _preview(directory,conn,as_of):
    fhir._check_inventory(conn)
    history=_policies(conn)
    if not history:
        _fail("approve a retention policy before maintenance")
    current=history[-1]
    policy=current["policy"]
    if type(as_of) is not int or as_of<0 or as_of>int(time.time()) or int(time.time())-as_of>86400:
        _fail("preview timestamp must be within the preceding 24 hours")
    sessions=[]; failures=[]; eligible=[]; protected=[]; held=[]
    if not policy["hold_all"]:
        sessions=[r[0] for r in conn.execute("SELECT digest FROM sessions WHERE expires<=? ORDER BY digest",
            (as_of-policy["expired_session_grace_days"]*86400,))]
        failures=[list(r) for r in conn.execute("SELECT rowid,user_key,at FROM failures WHERE at<? ORDER BY rowid",
            (as_of-max(access.WINDOW_SECONDS,policy["failure_retention_days"]*86400),))]
    refs=_protected(conn)
    for row in conn.execute("SELECT * FROM synthetic_fhir_reports ORDER BY id"):
        doc=fhir._read(directory,conn,row)
        at=conn.execute("SELECT at FROM audit WHERE seq=?",(row["audit_seq"],)).fetchone()[0]
        ident=row["id"]
        if policy["hold_all"] or ident in policy["synthetic_holds"]:
            held.append(ident)
        elif ident in refs:
            protected.append(ident)
        elif policy["synthetic_fhir_retention_days"] is not None and at<=as_of-policy["synthetic_fhir_retention_days"]*86400:
            eligible.append({"id":ident,"plan_id":row["plan_id"],"report_digest":doc["package"]["report"]["digest"]})
    material={"schema":"retention-maintenance-preview/1","as_of":as_of,"policy_id":current["id"],
        "expired_sessions":len(sessions),"obsolete_login_failures":len(failures),
        "authentication_inventory_digest":buyer_plan._digest([sessions,failures]),
        "synthetic_rehearsals_to_delete":eligible,"protected_rehearsals":protected,
        "held_rehearsals":held,"hold_all":policy["hold_all"],"limitations":LIMITATIONS[:]}
    return {**material,"digest":buyer_plan._digest(material)},sessions,failures


def _offline(directory,services_stopped):
    if services_stopped is not True:
        _fail("explicit confirmation that all services, workers and CLI writers are stopped required")
    root=buyer._root(directory)
    backup._inventory(root)
    backup._sqlite_check(root)


def preview(directory,*,services_stopped=False,as_of=None):
    _offline(directory,services_stopped)
    with access.db(directory) as conn:
        _schema(conn)
        return _preview(directory,conn,int(time.time()) if as_of is None else as_of)[0]


def apply(directory,as_of,approved_digest,*,services_stopped=False,confirmed=False):
    if confirmed is not True:
        _fail("explicit deletion approval required")
    _offline(directory,services_stopped)
    manual._identifier(approved_digest,digest=True)
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        doc,sessions,failures=_preview(directory,conn,as_of)
        if doc["digest"]!=approved_digest:
            _fail("maintenance inventory or policy changed; create a new preview")
        conn.execute("PRAGMA secure_delete=ON")
        for digest in sessions:
            conn.execute("DELETE FROM sessions WHERE digest=?",(digest,))
        for rowid,_,_ in failures:
            conn.execute("DELETE FROM failures WHERE rowid=?",(rowid,))
        for row in doc["synthetic_rehearsals_to_delete"]:
            ident=row["id"]
            access._audit(conn,"local-admin","synthetic-fhir-retention-deleted",ident)
            seq=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("INSERT INTO synthetic_fhir_retention_tombstones VALUES(?,?,?,?,?,?)",
                (ident,doc["policy_id"],row["plan_id"],row["report_digest"],int(time.time()),seq))
            if conn.execute("DELETE FROM synthetic_fhir_reports WHERE id=?",(ident,)).rowcount!=1:
                _fail("synthetic record changed during maintenance")
        fhir._check_inventory(conn)
        access._audit(conn,"local-admin","retention-maintenance-applied",doc["digest"])
        return {"status":"maintenance_applied","preview_digest":doc["digest"],
            "expired_sessions_removed":len(sessions),"obsolete_login_failures_removed":len(failures),
            "synthetic_rehearsals_removed":[r["id"] for r in doc["synthetic_rehearsals_to_delete"]],
            "limitations":LIMITATIONS[:]}


def tombstones(conn):
    """Restricted metadata preserves explicit deletion history, not raw evidence."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='synthetic_fhir_retention_tombstones'").fetchone():
        if conn.execute("SELECT 1 FROM audit WHERE action='synthetic-fhir-retention-deleted' AND outcome='ok' LIMIT 1").fetchone():
            _fail("synthetic deletion ledger missing")
        return []
    rows=conn.execute("SELECT * FROM synthetic_fhir_retention_tombstones ORDER BY id").fetchall()
    events=conn.execute("SELECT seq,object_id FROM audit WHERE action='synthetic-fhir-retention-deleted' AND outcome='ok'").fetchall()
    if {r["audit_seq"]:r["id"] for r in rows}!={e["seq"]:e["object_id"] for e in events}:
        _fail("synthetic deletion ledger integrity mismatch")
    return [{"id":r["id"],"plan_id":r["plan_id"],"report_digest":r["report_digest"],
             "state":"deleted_under_retention_policy","synthetic":True,"decision_grade":False} for r in rows]
