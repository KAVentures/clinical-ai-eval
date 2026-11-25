"""Institutional synthetic interoperability ledger, restricted to saved plans."""
import json
from . import (buyer_plan, buyer_manual as manual, institution_access as access,
               institution_setup as setup, research_buyer_coverage as bridge,
               fhir_readiness as fhir)

EVENT="synthetic-fhir-assessment-recorded"
MAX_RECORDS=200


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS synthetic_fhir_reports(
      id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,payload TEXT NOT NULL,
      audit_seq INTEGER NOT NULL UNIQUE)""")


def prepare(directory,actor,plan_id):
    with access.db(directory) as conn:
        setup._actor(conn,actor)
        p=bridge._buyer_plan(directory,plan_id)
        target={k:p["plan"]["target"][k] for k in ("name","version")}
        plan=fhir.build(target)
        return {"plan":plan,"exchange":fhir.exchange(plan),"response_template":fhir.template(plan)}


def _check_inventory(conn):
    from . import institution_retention as retention
    rows=conn.execute("SELECT id,audit_seq FROM synthetic_fhir_reports").fetchall()
    deleted=retention.tombstones(conn)
    live={r["id"] for r in rows}
    gone={r["id"] for r in deleted}
    events=conn.execute("SELECT object_id,seq FROM audit WHERE action=? AND outcome='ok'",(EVENT,)).fetchall()
    by_id={e["object_id"]:e["seq"] for e in events}
    if (len(by_id)!=len(events) or live & gone or live | gone != set(by_id)
            or any(by_id[r["id"]]!=r["audit_seq"] for r in rows)):
        raise access.AccessDenied("synthetic assessment inventory differs from audit history")
    return deleted


def submit(directory,actor,plan_id,responses,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("confirm synthetic input and no real patient data")
    with access.db(directory) as conn:
        setup._actor(conn,actor)
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        deleted=_check_inventory(conn)
        p=bridge._buyer_plan(directory,plan_id)
        plan=fhir.build({k:p["plan"]["target"][k] for k in ("name","version")})
        package=fhir.package(plan,responses)
        doc={"schema":"institution-synthetic-fhir/1","plan_id":plan_id,
             "vendor_id":p["vendor_id"],"submitted_by":actor["user_id"],
             "package":package,"decision_grade":False}
        ident=buyer_plan._digest(doc)
        if any(r["id"]==ident or (r["plan_id"]==plan_id and r["report_digest"]==package["report"]["digest"]) for r in deleted):
            raise access.AccessDenied("retention-deleted evidence cannot be silently restored")
        old=conn.execute("SELECT * FROM synthetic_fhir_reports WHERE id=?",(ident,)).fetchone()
        if old:
            _read(directory,conn,old)
            return ident
        if conn.execute("SELECT count(*) FROM synthetic_fhir_reports").fetchone()[0]>=MAX_RECORDS:
            raise access.AccessDenied("synthetic assessment record limit")
        access._audit(conn,actor["user_id"],EVENT,ident)
        seq=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO synthetic_fhir_reports VALUES(?,?,?,?)",
                     (ident,plan_id,buyer_plan._canonical(doc).decode("utf-8"),seq))
        return ident


def _read(directory,conn,row):
    doc=json.loads(row["payload"])
    if (doc.get("schema")!="institution-synthetic-fhir/1" or doc.get("decision_grade") is not False
            or buyer_plan._digest(doc)!=row["id"] or doc["plan_id"]!=row["plan_id"]):
        raise access.AccessDenied("synthetic report integrity mismatch")
    audit=conn.execute("SELECT seq,actor FROM audit WHERE action=? AND object_id=? AND outcome='ok'",
                      (EVENT,row["id"])).fetchall()
    if len(audit)!=1 or audit[0]["seq"]!=row["audit_seq"] or audit[0]["actor"]!=doc["submitted_by"]:
        raise access.AccessDenied("synthetic report audit mismatch")
    p=buyer_plan.load(directory,row["plan_id"],include_freshness=False)
    if (p["vendor_id"]!=doc["vendor_id"] or {k:p["plan"]["target"][k] for k in ("name","version")}!=doc["package"]["plan"]["target"]):
        raise access.AccessDenied("saved buyer plan mismatch")
    fhir.verify(doc["package"])
    try:
        bridge._buyer_plan(directory,row["plan_id"])
        current=True
    except (ValueError,RuntimeError,OSError,KeyError,TypeError):
        current=False
    return {**doc,"id":row["id"],"scope_current":current}


def detail(directory,actor,identifier):
    with access.db(directory) as conn:
        setup._actor(conn,actor)
        _schema(conn)
        _check_inventory(conn)
        manual._identifier(identifier,digest=True)
        row=conn.execute("SELECT * FROM synthetic_fhir_reports WHERE id=?",(identifier,)).fetchone()
        if not row:
            raise access.AccessDenied("unknown synthetic assessment")
        result=_read(directory,conn,row)
        access._audit(conn,actor["user_id"],"synthetic-fhir-assessment-opened",identifier)
        return result


def inventory(directory,actor):
    from . import institution_committee as committee
    with access.db(directory) as conn:
        committee._actor(conn,actor)
        _schema(conn)
        deleted=_check_inventory(conn)
        result=[]
        for row in conn.execute("SELECT * FROM synthetic_fhir_reports ORDER BY audit_seq").fetchall():
            item={"id":row["id"],"state":"unavailable","synthetic":True,"decision_grade":False}
            try:
                d=_read(directory,conn,row)
                report=d["package"]["report"]
                item.update(state="current" if d["scope_current"] else "stale",vendor_id=d["vendor_id"],
                            plan_id=d["plan_id"],target_version=report["target"]["version"],summary=report["summary"],
                            report_digest=report["digest"])
            except (ValueError,RuntimeError,OSError,KeyError,TypeError,AttributeError):
                pass
            result.append(item)
        result.extend(deleted)
        return result
