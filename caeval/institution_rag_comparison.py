"""Optional prospectively frozen within-study RAG comparison protocol."""
import json
from . import rag_paired_analysis as analysis, diagnostic_imaging as d
from . import institution_access as access, unified_assessment as u

EVENT="rag-paired-analysis-frozen"


def schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS rag_paired_analysis_plans(
        study TEXT PRIMARY KEY REFERENCES rag_grounding_studies(id),
        payload TEXT NOT NULL,digest TEXT NOT NULL,actor TEXT NOT NULL,
        audit_seq INTEGER NOT NULL UNIQUE)""")


def inventory(conn):
    rows=conn.execute("SELECT * FROM rag_paired_analysis_plans").fetchall()
    expected={(r["audit_seq"],r["actor"],r["study"]+":"+r["digest"]) for r in rows}
    actual={(r["seq"],r["actor"],r["object_id"]) for r in conn.execute(
        "SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok'",(EVENT,))}
    if len(expected)!=len(rows) or expected!=actual:
        raise access.AccessDenied("paired analysis audit inventory mismatch")


def _first_exposure(conn,ident):
    # The candidate export audit object uses study ID followed by a colon.
    # Use exact prefix rather than SQL LIKE (underscores are legal IDs).
    prefix=ident+":"
    export=conn.execute("SELECT min(seq) FROM audit WHERE action='rag-grounding-candidate-exported' AND outcome='ok' AND substr(object_id,1,?)=?",
                        (len(prefix),prefix)).fetchone()[0]
    trace=conn.execute("SELECT min(audit_seq) FROM rag_grounding_traces WHERE study=?",(ident,)).fetchone()[0]
    return min(x for x in (export,trace) if x is not None) if export is not None or trace is not None else None


def check(conn,plan):
    ident=plan["spec"]["study_id"]
    row=conn.execute("SELECT * FROM rag_paired_analysis_plans WHERE study=?",(ident,)).fetchone()
    if not row:
        return None
    doc=json.loads(row["payload"])
    analysis._verify_protocol(plan,doc)
    study=conn.execute("SELECT audit_seq FROM rag_grounding_studies WHERE id=?",(ident,)).fetchone()
    first=_first_exposure(conn,ident)
    if (doc["digest"]!=row["digest"] or row["audit_seq"]<=study["audit_seq"]
            or (first is not None and row["audit_seq"]>=first)):
        raise access.AccessDenied("paired protocol ordering or digest mismatch")
    return doc


def _preview(conn,plan,spec):
    ident=plan["spec"]["study_id"]
    if check(conn,plan) or _first_exposure(conn,ident) is not None:
        raise access.AccessDenied("comparison must be frozen before any recorded candidate export or trace submission")
    return analysis.protocol(plan,spec)


def preview(directory,actor,ident,spec):
    from . import institution_rag_grounding as studies
    with access.db(directory) as conn:
        studies._schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        studies._inventory(conn)
        plan,_,_,_=studies._get(conn,ident)
        return _preview(conn,plan,spec)


def freeze(directory,actor,ident,spec,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit paired research protocol approval required")
    from . import institution_rag_grounding as studies
    with access.db(directory) as conn:
        studies._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        studies._inventory(conn)
        plan,_,_,_=studies._get(conn,ident)
        doc=_preview(conn,plan,spec)
        if doc["digest"]!=approval:
            raise access.AccessDenied("paired analysis differs from approval")
        seq=u._audit(conn,actor,EVENT,ident+":"+approval)
        conn.execute("INSERT INTO rag_paired_analysis_plans VALUES(?,?,?,?,?)",
                     (ident,json.dumps(doc),approval,actor["user_id"],seq))
        return approval


def coverage(conn,plan,evidence):
    p=check(conn,plan)
    if p is None:
        return {"state":"not_prespecified" if _first_exposure(conn,plan["spec"]["study_id"]) is None else "unavailable_after_exposure"}
    result={"state":"frozen" if evidence is None else "sealed_research_analysis",
            "analysis_plan_digest":p["digest"],"baseline":p["baseline"],"candidate":p["candidate"],
            "primary_endpoint":p["parameters"]["primary_endpoint"],"decision_grade":False}
    if evidence:
        result["analysis_digest"]=analysis.analyse(evidence,p)["digest"]
    return result


def result(directory,actor,ident):
    from . import institution_rag_grounding as studies
    with access.db(directory) as conn:
        studies._schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        studies._inventory(conn)
        plan,_,_,evidence=studies._get(conn,ident)
        p=check(conn,plan)
        if not p or not evidence:
            raise access.AccessDenied("sealed study with frozen analysis protocol required")
        return analysis.analyse(evidence,p)


def export(directory,actor,ident,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit restricted paired analysis export required")
    from . import institution_rag_grounding as studies
    with access.db(directory) as conn:
        studies._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        studies._inventory(conn)
        plan,_,_,evidence=studies._get(conn,ident)
        p=check(conn,plan)
        if not p or not evidence:
            raise access.AccessDenied("sealed study with frozen analysis protocol required")
        doc=analysis.analyse(evidence,p)
        if doc["digest"]!=approval:
            raise access.AccessDenied("paired analysis differs from approved export")
        u._audit(conn,actor,"rag-paired-analysis-exported",ident+":"+approval)
        return doc
