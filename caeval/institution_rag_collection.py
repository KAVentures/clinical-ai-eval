"""Institutional RAG collection receipt links, separate from scientific verdicts."""
import json
from . import rag_collection as collection, rag_grounding as g, diagnostic_imaging as d
from . import institution_access as access, unified_assessment as u

EVENT="rag-grounding-collection-receipt-locked"


def schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS rag_grounding_collection_receipts(
        study TEXT NOT NULL,item TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL,
        actor TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE,PRIMARY KEY(study,item),
        FOREIGN KEY(study,item) REFERENCES rag_grounding_traces(study,item))""")


def inventory(conn):
    rows=conn.execute("SELECT study,item,digest,actor,audit_seq FROM rag_grounding_collection_receipts").fetchall()
    expected={(r["audit_seq"],r["actor"],r["study"]+":"+r["item"]+":"+r["digest"]) for r in rows}
    actual={(r["seq"],r["actor"],r["object_id"]) for r in conn.execute(
        "SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok'",(EVENT,))}
    if len(expected)!=len(rows) or actual!=expected:
        raise access.AccessDenied("RAG receipt audit inventory mismatch")


def validate(plan,receipt):
    cid=receipt["trace"]["candidate"]["id"]
    ex=g.exchange(plan,cid)
    collection.verify(ex,receipt)
    normalized=g.locked_traces(plan,[receipt["trace"]])[0]
    d._require(normalized==receipt["trace"],"receipt trace normalization mismatch")
    return cid


def check(conn,plan,docs):
    ident=plan["spec"]["study_id"]
    by={r["candidate"]["id"]:r for r in docs}
    results=[]
    # Every receipt must have been atomically locked with the candidate trace
    # and before any independent review, not attached retrospectively.
    first_review=conn.execute("SELECT min(audit_seq) FROM rag_grounding_reviews WHERE study=?",(ident,)).fetchone()[0]
    seal=conn.execute("SELECT audit_seq FROM rag_grounding_seals WHERE study=?",(ident,)).fetchone()
    for r in conn.execute("SELECT * FROM rag_grounding_collection_receipts WHERE study=? ORDER BY item",(ident,)):
        receipt=json.loads(r["payload"])
        cid=validate(plan,receipt)
        trace=conn.execute("SELECT actor,audit_seq FROM rag_grounding_traces WHERE study=? AND item=?",(ident,cid)).fetchone()
        if (cid!=r["item"] or cid not in by or by[cid]!=receipt["trace"]
                or receipt["digest"]!=r["digest"] or not trace or trace["actor"]!=r["actor"]
                or trace["audit_seq"]+1!=r["audit_seq"]
                or (first_review is not None and r["audit_seq"]>=first_review)
                or (seal and r["audit_seq"]>=seal["audit_seq"])):
            raise access.AccessDenied("RAG collection receipt binding or order mismatch")
        results.append(receipt)
    return results


def record(conn,actor,ident,receipt):
    cid=receipt["trace"]["candidate"]["id"]
    digest=receipt["digest"]
    seq=u._audit(conn,actor,EVENT,ident+":"+cid+":"+digest)
    conn.execute("INSERT INTO rag_grounding_collection_receipts VALUES(?,?,?,?,?,?)",
                 (ident,cid,json.dumps(receipt),digest,actor["user_id"],seq))


def coverage(conn,ident):
    return [{"candidate_id":r["item"],"receipt_digest":r["digest"],
             "provenance":"local_receipt_consistency_only"} for r in conn.execute(
                 "SELECT item,digest FROM rag_grounding_collection_receipts WHERE study=? ORDER BY item",(ident,))]


def _package(conn,plan,docs,evidence):
    receipts=check(conn,plan,docs)
    return d._sealed({"schema":"rag-grounding-collection-evidence/1","study_evidence_digest":evidence["digest"],
                      "receipts":receipts,"decision_grade":False,"execution_attested":False})


def export_preview(directory,actor,ident):
    from . import institution_rag_grounding as service
    with access.db(directory) as conn:
        service._schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        service._inventory(conn)
        plan,docs,_,evidence=service._get(conn,ident)
        if not evidence:
            raise access.AccessDenied("sealed grounding study required")
        package=_package(conn,plan,docs,evidence)
        return {"approval":package["digest"],"n_receipts":len(package["receipts"])}


def export(directory,actor,ident,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit restricted collection export required")
    from . import institution_rag_grounding as service
    with access.db(directory) as conn:
        service._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        service._inventory(conn)
        plan,docs,_,evidence=service._get(conn,ident)
        if not evidence:
            raise access.AccessDenied("sealed grounding study required")
        package=_package(conn,plan,docs,evidence)
        if package["digest"]!=approval:
            raise access.AccessDenied("collection export differs from approved preview")
        u._audit(conn,actor,"rag-grounding-collection-evidence-exported",ident+":"+approval)
        return package


def verify_package(package,evidence,expected_digest=None):
    g.verify(evidence)
    d._keys(package,"schema study_evidence_digest receipts decision_grade execution_attested digest")
    d._require(package["schema"]=="rag-grounding-collection-evidence/1" and package["decision_grade"] is False
               and package["execution_attested"] is False and package["study_evidence_digest"]==evidence["digest"]
               and d._digest({k:v for k,v in package.items() if k!="digest"})==package["digest"]
               and (expected_digest is None or expected_digest==package["digest"]),"collection package mismatch")
    by={doc["candidate"]["id"]:doc for doc in evidence["traces"]}
    rows=package["receipts"]
    d._require(isinstance(rows,list) and len(rows)<=len(by),"invalid receipt roster")
    seen=set()
    for r in rows:
        cid=r["trace"]["candidate"]["id"]
        d._require(cid in by and cid not in seen and by[cid]==r["trace"],"receipt differs from sealed trace")
        validate(evidence["plan"],r)
        seen.add(cid)
    d._require([r["trace"]["candidate"]["id"] for r in rows]==sorted(seen),"receipt order mismatch")
    return {"status":"REPLAY_VERIFIED","digest":package["digest"],"n_receipts":len(seen),
            "expected_digest_matched":expected_digest is not None,"execution_attested":False,"decision_grade":False}
