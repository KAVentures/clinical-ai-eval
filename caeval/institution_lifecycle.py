"""Institutional implementation and surveillance register.

Human-recorded workflow, not a deployment control, clinical decision or
automatic incident detector. No external services or patient identifiers.
All edits are versioned events and authorized by institutional account role.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid

from . import institution_access as access, buyer_agent as buyer, buyer_jobs, buyer_manual

KINDS = ("risk", "incident", "version_change", "monitoring")
SEVERITIES = ("low", "moderate", "high", "critical")
PILOT_STATES = ("planned", "in_review", "paused", "closed")
MAX_RECORDS = 1000


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS lifecycle_pilots(
      id TEXT PRIMARY KEY, vendor_id TEXT NOT NULL, product_version TEXT NOT NULL,
      scope TEXT NOT NULL, owner TEXT NOT NULL, review_date TEXT NOT NULL,
      rollback_plan TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS lifecycle_items(
      id TEXT PRIMARY KEY, pilot_id TEXT NOT NULL REFERENCES lifecycle_pilots(id),
      kind TEXT NOT NULL, severity TEXT NOT NULL, title TEXT NOT NULL,
      action TEXT NOT NULL, owner TEXT NOT NULL, due TEXT NOT NULL,
      state TEXT NOT NULL, revision INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS lifecycle_evidence(
      pilot_id TEXT NOT NULL REFERENCES lifecycle_pilots(id), job_id TEXT NOT NULL,
      purpose TEXT NOT NULL, package_hashes TEXT NOT NULL,
      PRIMARY KEY(pilot_id,job_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS lifecycle_events(
      seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT DEFAULT CURRENT_TIMESTAMP,
      actor TEXT NOT NULL, object_id TEXT NOT NULL, action TEXT NOT NULL,
      revision INTEGER NOT NULL, snapshot TEXT NOT NULL)""")


def _actor(conn, actor, write=False):
    roles = ("admin", "operator") if write else ("admin", "operator", "observer")
    access.require(actor, *roles)
    row = conn.execute("SELECT role,enabled FROM users WHERE id=?", (actor["user_id"],)).fetchone()
    if not row or not row["enabled"] or row["role"] != actor["role"]:
        raise access.AccessDenied("active account required")


def _text(value, limit=2000):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise access.AccessDenied("complete required field within length limit")
    return value.strip()


def _date(value):
    try:
        date=dt.date.fromisoformat(value)
        if date.isoformat()!=value:
            raise ValueError()
        return value
    except (ValueError,TypeError):
        raise access.AccessDenied("date must be YYYY-MM-DD")


def _owner(conn, owner):
    buyer_manual._identifier(owner)
    row=conn.execute("SELECT role,enabled FROM users WHERE id=?", (owner,)).fetchone()
    if not row or not row["enabled"] or row["role"] not in ("admin","operator"):
        raise access.AccessDenied("owner must be an active administrator or operator")
    return owner


def _event(conn,actor,record,action):
    conn.execute("INSERT INTO lifecycle_events(actor,object_id,action,revision,snapshot) VALUES(?,?,?,?,?)",
                 (actor["user_id"],record["id"],action,record["revision"],json.dumps(record,ensure_ascii=False,sort_keys=True)))
    access._audit(conn,actor["user_id"],"lifecycle-"+action,record["id"])


def _get(conn, table, identifier):
    buyer_manual._identifier(identifier)
    if table not in ("lifecycle_pilots","lifecycle_items"):
        raise ValueError("unknown table")
    row=conn.execute("SELECT * FROM "+table+" WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise access.AccessDenied("record not found")
    return dict(row)


def _revision(record, expected):
    if type(expected) is not int or expected!=record["revision"]:
        raise access.AccessDenied("record changed; reload before submitting")


def create_pilot(directory, actor, spec):
    required={"vendor_id","product_version","scope","owner","review_date","rollback_plan"}
    if not isinstance(spec,dict) or set(spec)!=required:
        raise access.AccessDenied("complete all pilot fields")
    access.require(actor,"admin","operator")
    vendors=buyer.load(directory)["vendors"]
    if spec["vendor_id"] not in {v["vendor_id"] for v in vendors}:
        raise access.AccessDenied("register the candidate in the buyer workspace first")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,True)
        if conn.execute("SELECT count(*) FROM lifecycle_pilots").fetchone()[0]>=MAX_RECORDS:
            raise access.AccessDenied("pilot register limit reached")
        r={"id":"pilot-"+uuid.uuid4().hex,"vendor_id":spec["vendor_id"],
           "product_version":_text(spec["product_version"],300),"scope":_text(spec["scope"]),
           "owner":_owner(conn,spec["owner"]),"review_date":_date(spec["review_date"]),
           "rollback_plan":_text(spec["rollback_plan"]),"state":"planned","revision":1}
        conn.execute("INSERT INTO lifecycle_pilots VALUES(:id,:vendor_id,:product_version,:scope,:owner,:review_date,:rollback_plan,:state,:revision)",r)
        _event(conn,actor,r,"pilot-created")
    return r


def add_item(directory,actor,pilot_id,spec,expected_revision):
    if not isinstance(spec,dict) or set(spec)!={"kind","severity","title","action","owner","due"}:
        raise access.AccessDenied("complete all register fields")
    if spec["kind"] not in KINDS or spec["severity"] not in SEVERITIES:
        raise access.AccessDenied("unknown record category or severity")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,True)
        pilot=_get(conn,"lifecycle_pilots",pilot_id)
        _revision(pilot,expected_revision)
        if pilot["state"]=="closed":
            raise access.AccessDenied("closed pilot is immutable")
        if conn.execute("SELECT count(*) FROM lifecycle_items").fetchone()[0]>=MAX_RECORDS:
            raise access.AccessDenied("register limit reached")
        item={"id":"item-"+uuid.uuid4().hex,"pilot_id":pilot_id,"kind":spec["kind"],
              "severity":spec["severity"],"title":_text(spec["title"],300),"action":_text(spec["action"]),
              "owner":_owner(conn,spec["owner"]),"due":_date(spec["due"]),"state":"open","revision":1}
        conn.execute("INSERT INTO lifecycle_items VALUES(:id,:pilot_id,:kind,:severity,:title,:action,:owner,:due,:state,:revision)",item)
        _event(conn,actor,item,"item-created")
        # A serious incident or version change forces administrative pause;
        # this does not send a stop command to any clinical system.
        pilot["revision"]+=1
        if item["severity"] in ("high","critical") or item["kind"]=="version_change":
            pilot["state"]="paused"
        conn.execute("UPDATE lifecycle_pilots SET state=?,revision=? WHERE id=?",
                     (pilot["state"],pilot["revision"],pilot_id))
        _event(conn,actor,pilot,"pilot-register-updated")
    return item


def resolve_item(directory,actor,item_id,expected_revision,reason):
    reason=_text(reason)
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,True)
        item=_get(conn,"lifecycle_items",item_id)
        _revision(item,expected_revision)
        pilot=_get(conn,"lifecycle_pilots",item["pilot_id"])
        if item["state"]!="open" or pilot["state"]=="closed":
            raise access.AccessDenied("record is not open")
        if item["severity"] in ("high","critical") or item["kind"]=="version_change":
            access.require(actor,"admin")
        item["state"]="resolved"
        item["revision"]+=1
        conn.execute("UPDATE lifecycle_items SET state=?,revision=? WHERE id=?",(item["state"],item["revision"],item_id))
        _event(conn,actor,{**item,"resolution_reason":reason},"item-resolved")
        pilot["revision"]+=1
        conn.execute("UPDATE lifecycle_pilots SET revision=? WHERE id=?",(pilot["revision"],pilot["id"]))
        _event(conn,actor,pilot,"pilot-register-updated")
    return item


def transition(directory,actor,pilot_id,expected_revision,state,reason):
    if state not in PILOT_STATES or state=="planned":
        raise access.AccessDenied("invalid transition")
    reason=_text(reason)
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,True)
        pilot=_get(conn,"lifecycle_pilots",pilot_id)
        _revision(pilot,expected_revision)
        if pilot["state"]=="closed" or state==pilot["state"]:
            raise access.AccessDenied("invalid transition")
        if state in ("in_review","closed"):
            access.require(actor,"admin")
            unresolved=conn.execute("SELECT count(*) FROM lifecycle_items WHERE pilot_id=? AND state='open' AND (severity IN ('high','critical') OR kind='version_change' OR ?='closed')",(pilot_id,state)).fetchone()[0]
            if unresolved:
                raise access.AccessDenied("resolve blocking register items first")
        pilot.update(state=state,revision=pilot["revision"]+1)
        conn.execute("UPDATE lifecycle_pilots SET state=?,revision=? WHERE id=?",(state,pilot["revision"],pilot_id))
        _event(conn,actor,{**pilot,"reason":reason},"pilot-transition")
    return pilot


def _evidence(directory, job_id, pilot):
    buyer_manual._identifier(job_id)
    if job_id.startswith("conversation-"):
        from . import institution_conversation as conversation, buyer_plan
        with access.db(directory) as db:
            conversation._schema(db)
            row=conversation._row(db,job_id)
            b=row["binding"]
            if (b["vendor_id"]!=pilot["vendor_id"] or b["target"]["version"]!=pilot["product_version"]
                    or row["status"]!="completed" or conversation._verify(directory,row)!=row["result"]):
                raise access.AccessDenied("verified conversation assessment for exact pilot vendor and version required")
            # The canonical manifest alone does not cover every episode
            # auxiliary. Preserve both it and the complete verified receipt.
            result=row["result"]
            return {conversation.FAMILY:{"manifest_hash":result["manifest_hash"],
                    "job_binding":row["digest"],"receipt_hash":buyer_plan._digest(result)}}
    row=buyer_jobs.status(directory,job_id)
    if row["status"]!="completed" or not row["result"].get("packages"):
        raise access.AccessDenied("completed verified assessment required")
    bound=buyer_manual._read(buyer_jobs._job_path(directory,job_id)/"binding.json")
    exchange=buyer_manual._load(directory,bound["exchange_id"])
    if exchange["vendor_id"]!=pilot["vendor_id"] or exchange["target"]["version"]!=pilot["product_version"]:
        raise access.AccessDenied("assessment vendor or version differs from pilot")
    hashes={p["family_id"]:p["manifest_hash"] for p in row["result"]["packages"]}
    return hashes


def link_evidence(directory,actor,pilot_id,expected_revision,job_id,purpose):
    if purpose not in ("baseline","retest"):
        raise access.AccessDenied("select baseline or retest")
    with access.db(directory) as conn:
        _schema(conn)
        _actor(conn,actor,True)
        pilot=_get(conn,"lifecycle_pilots",pilot_id)
    _revision(pilot,expected_revision)
    hashes=_evidence(directory,job_id,pilot)
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,True)
        pilot=_get(conn,"lifecycle_pilots",pilot_id)
        _revision(pilot,expected_revision)
        if pilot["state"]=="closed":
            raise access.AccessDenied("closed pilot is immutable")
        if conn.execute("SELECT 1 FROM lifecycle_evidence WHERE pilot_id=? AND job_id=?",(pilot_id,job_id)).fetchone():
            raise access.AccessDenied("job is already linked")
        conn.execute("INSERT INTO lifecycle_evidence VALUES(?,?,?,?)",(pilot_id,job_id,purpose,json.dumps(hashes,sort_keys=True)))
        pilot["revision"]+=1
        conn.execute("UPDATE lifecycle_pilots SET revision=? WHERE id=?",(pilot["revision"],pilot_id))
        _event(conn,actor,{**pilot,"job_id":job_id,"purpose":purpose,"package_hashes":hashes},"evidence-linked")
    return {"job_id":job_id,"package_hashes":hashes,"decision_grade":False}


def overview(directory,actor):
    with access.db(directory) as conn:
        _schema(conn)
        _actor(conn,actor)
        rows=[dict(r) for r in conn.execute("SELECT * FROM lifecycle_pilots ORDER BY review_date,id LIMIT ?",(MAX_RECORDS,))]
        for r in rows:
            items=[dict(i) for i in conn.execute("SELECT * FROM lifecycle_items WHERE pilot_id=? ORDER BY due,id",(r["id"],))]
            r["items"]=items
            r["evidence"]=[dict(e) for e in conn.execute("SELECT * FROM lifecycle_evidence WHERE pilot_id=? ORDER BY job_id",(r["id"],))]
            for e in r["evidence"]:
                recorded=json.loads(e.pop("package_hashes"))
                try:
                    e["integrity"]="matches" if _evidence(directory,e["job_id"],r)==recorded else "changed"
                except (OSError,ValueError,RuntimeError,KeyError,TypeError):
                    e["integrity"]="unavailable"
                e["family_count"]=len(recorded)
                e["assessment_type"]="patient_conversation" if e["job_id"].startswith("conversation-") else "single_turn"
            today=dt.date.today().isoformat()
            r["overdue"]=r["state"]!="closed" and (r["review_date"]<today or any(i["state"]=="open" and i["due"]<today for i in items))
            r["blocking_open"]=sum(i["state"]=="open" and (i["severity"] in ("high","critical") or i["kind"]=="version_change") for i in items)
        access._audit(conn,actor["user_id"],"lifecycle-overview")
    return {"pilots":rows,"decision_grade":False,"deployment_authorized":False}


def history(directory,actor,pilot_id):
    with access.db(directory) as conn:
        _schema(conn)
        _actor(conn,actor)
        access.require(actor,"admin")
        _get(conn,"lifecycle_pilots",pilot_id)
        rows=conn.execute("""SELECT * FROM lifecycle_events WHERE object_id=? OR object_id IN
            (SELECT id FROM lifecycle_items WHERE pilot_id=?) ORDER BY seq""",(pilot_id,pilot_id)).fetchall()
        access._audit(conn,actor["user_id"],"lifecycle-history-opened",pilot_id)
        return [dict(r) for r in rows]
