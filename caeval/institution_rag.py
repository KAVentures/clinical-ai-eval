"""Institutional frozen RAG rehearsal, response lock and private replay export."""
import json
from . import (institution_rag_trace as trace, diagnostic_imaging as d,
               institution_access as access, unified_assessment as u,
               research_buyer_coverage as bridge)

FROZEN="institution-rag-trace-frozen"
LOCKED="institution-rag-trace-responses-locked"
MAX_STUDIES=60


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS institution_rag_studies(
        id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,payload TEXT NOT NULL,
        actor TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS institution_rag_reports(
        study TEXT PRIMARY KEY REFERENCES institution_rag_studies(id),
        digest TEXT NOT NULL,payload TEXT NOT NULL,
        actor TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE)""")


def _inventory(conn):
    for table,event,key in (("institution_rag_studies",FROZEN,"id"),
                            ("institution_rag_reports",LOCKED,"study")):
        rows=conn.execute("SELECT * FROM "+table).fetchall()
        expected={(r["audit_seq"],r["actor"],r[key]+(":"+r["digest"] if event==LOCKED else "")) for r in rows}
        actual={(r["seq"],r["actor"],r["object_id"]) for r in conn.execute(
            "SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok'",(event,))}
        if len(expected)!=len(rows) or actual!=expected:
            raise access.AccessDenied("RAG rehearsal audit inventory mismatch")


def _scope(directory,pid):
    bp=bridge._buyer_plan(directory,pid)
    if trace.FAMILY not in bp["plan"]["selection"]["runnable_suites"]:
        raise access.AccessDenied("current runnable retrieval failure scope required")
    target={k:bp["plan"]["target"][k] for k in ("name","version")}
    return bp["vendor_id"],target


def preview(directory,actor,pid):
    with access.db(directory) as conn:
        u._actor(conn,actor,"admin")
        _schema(conn)
        _inventory(conn)
        vid,target=_scope(directory,pid)
        return trace.freeze(pid,vid,target,nonce="0"*64)


def freeze(directory,actor,pid,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit synthetic RAG rehearsal approval required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        vid,target=_scope(directory,pid)
        p=trace.freeze(pid,vid,target,nonce="0"*64)
        if p["digest"]!=approval:
            raise access.AccessDenied("RAG scope changed since preview")
        if conn.execute("SELECT count(*) FROM institution_rag_studies").fetchone()[0]>=MAX_STUDIES:
            raise access.AccessDenied("RAG study limit reached")
        plan=trace.freeze(pid,vid,target)
        ident=plan["digest"]
        seq=u._audit(conn,actor,FROZEN,ident)
        conn.execute("INSERT INTO institution_rag_studies VALUES(?,?,?,?,?)",
                     (ident,pid,json.dumps(plan),actor["user_id"],seq))
        return ident


def _get(directory,conn,ident):
    d._require(d._hex(ident),"invalid RAG study identifier")
    row=conn.execute("SELECT * FROM institution_rag_studies WHERE id=?",(ident,)).fetchone()
    if not row:
        raise access.AccessDenied("unknown RAG rehearsal")
    plan=json.loads(row["payload"])
    trace.verify_plan(plan)
    if plan["digest"]!=ident or plan["plan_id"]!=row["plan_id"]:
        raise access.AccessDenied("RAG study binding changed")
    # Currentness is deliberately strict, including the buyer definition gate.
    vid,target=_scope(directory,row["plan_id"])
    if (vid,target)!=(plan["vendor_id"],plan["target"]):
        raise access.AccessDenied("RAG buyer identity changed")
    report=conn.execute("SELECT * FROM institution_rag_reports WHERE study=?",(ident,)).fetchone()
    evidence=None
    if report:
        evidence=json.loads(report["payload"])
        trace.verify(evidence,report["digest"])
        if evidence["plan"]!=plan or report["audit_seq"]<=row["audit_seq"]:
            raise access.AccessDenied("RAG evidence binding or ordering changed")
    return plan,evidence


def _status(plan,evidence):
    return {"id":plan["digest"],"plan_id":plan["plan_id"],"vendor_id":plan["vendor_id"],
            "target_version":plan["target"]["version"],"n_slots":len(plan["slots"]),
            "state":"sealed" if evidence else "collecting","synthetic":True,
            "evidence_digest":evidence["digest"] if evidence else None,
            "mode":"demonstration","decision_grade":False}


def detail(directory,actor,ident):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator")
        _inventory(conn)
        plan,evidence=_get(directory,conn,ident)
        result=_status(plan,evidence)
        if evidence and actor["role"]=="admin":
            result["summary"]=evidence["native_run"]["summary"]
        return result


def overview(directory,actor):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator","observer")
        _inventory(conn)
        result=[]
        for row in conn.execute("SELECT id,plan_id FROM institution_rag_studies ORDER BY audit_seq").fetchall():
            try:
                result.append(_status(*_get(directory,conn,row["id"])))
            except u.ERRORS:
                result.append({"id":row["id"],"plan_id":row["plan_id"],"state":"unavailable",
                               "synthetic":True,"decision_grade":False})
        return result


def download(directory,actor,ident,kind):
    if kind not in ("exchange","template"):
        raise access.AccessDenied("invalid RAG download")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin","operator")
        _inventory(conn)
        plan,evidence=_get(directory,conn,ident)
        if evidence:
            raise access.AccessDenied("RAG rehearsal already sealed")
        doc=(trace.exchange if kind=="exchange" else trace.template)(plan)
        u._audit(conn,actor,"institution-rag-trace-exchange-opened",ident+":"+kind+":"+d._digest(doc))
        return doc


def submit(directory,actor,ident,responses,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit synthetic response lock required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin","operator")
        _inventory(conn)
        plan,prior=_get(directory,conn,ident)
        evidence=trace.package(plan,responses)
        if prior:
            if prior!=evidence:
                raise access.AccessDenied("cannot replace sealed RAG responses")
            return prior["digest"]
        seq=u._audit(conn,actor,LOCKED,ident+":"+evidence["digest"])
        conn.execute("INSERT INTO institution_rag_reports VALUES(?,?,?,?,?)",
                     (ident,evidence["digest"],json.dumps(evidence),actor["user_id"],seq))
        return evidence["digest"]


def export(directory,actor,ident,expected_digest,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit restricted RAG export required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        _,evidence=_get(directory,conn,ident)
        if not evidence or evidence["digest"]!=expected_digest:
            raise access.AccessDenied("exact sealed RAG evidence required")
        u._audit(conn,actor,"institution-rag-trace-evidence-exported",ident+":"+expected_digest)
        return evidence
