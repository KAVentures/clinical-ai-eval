"""Optional locally collected imaging receipt linkage.

Receipt replay checks self-consistency, not a signed attestation of network
execution. Imported plain prediction documents remain a separate evidence path.
"""
import json
from . import diagnostic_imaging as d, imaging_transport as transport, institution_access as access

EVENT = "diagnostic-imaging-collection-receipt-locked"


def schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS diagnostic_imaging_collection_receipts(
        study TEXT NOT NULL, candidate TEXT NOT NULL, payload TEXT NOT NULL,
        digest TEXT NOT NULL, actor TEXT NOT NULL, audit_seq INTEGER NOT NULL UNIQUE,
        PRIMARY KEY(study,candidate),
        FOREIGN KEY(study,candidate) REFERENCES diagnostic_imaging_submissions(study,candidate))""")


def inventory(conn):
    rows=conn.execute("SELECT study,candidate,digest,actor,audit_seq FROM diagnostic_imaging_collection_receipts").fetchall()
    expected={(r["audit_seq"],r["actor"],r["study"]+":"+r["candidate"]+":"+r["digest"]) for r in rows}
    actual={(r["seq"],r["actor"],r["object_id"]) for r in conn.execute(
        "SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok'",(EVENT,))}
    if len(expected)!=len(rows) or expected!=actual:
        raise access.AccessDenied("imaging collection receipt audit inventory mismatch")


def validate(plan,receipt):
    candidate=receipt["predictions"]["candidate"]["id"]
    ex=d.exchange(plan,candidate)
    transport.verify(ex,receipt)
    d._predictions(plan,receipt["predictions"])
    return candidate


def check(conn,plan,docs):
    ident=plan["spec"]["study_id"]
    by={doc["candidate"]["id"]:doc for doc in docs}
    result=[]
    for row in conn.execute("SELECT * FROM diagnostic_imaging_collection_receipts WHERE study=? ORDER BY candidate",(ident,)):
        receipt=json.loads(row["payload"])
        cid=validate(plan,receipt)
        sub=conn.execute("SELECT actor,audit_seq FROM diagnostic_imaging_submissions WHERE study=? AND candidate=?",(ident,cid)).fetchone()
        if (cid!=row["candidate"] or cid not in by or d._digest(by[cid])!=d._digest(receipt["predictions"])
                or receipt["digest"]!=row["digest"] or not sub or sub["actor"]!=row["actor"]
                or sub["audit_seq"]>=row["audit_seq"]):
            raise access.AccessDenied("imaging receipt differs from locked submission")
        result.append(receipt)
    return result


def record(conn,actor,ident,receipt):
    from . import institution_imaging as studies
    cid=receipt["predictions"]["candidate"]["id"]
    seq=studies._audit(conn,actor,EVENT,ident+":"+cid+":"+receipt["digest"])
    conn.execute("INSERT INTO diagnostic_imaging_collection_receipts VALUES(?,?,?,?,?,?)",
                 (ident,cid,studies._json(receipt),receipt["digest"],actor["user_id"],seq))


def status(conn,ident):
    return [{"candidate_id":row["candidate"],"receipt_digest":row["digest"],"provenance":"local_receipt_consistency_only"}
            for row in conn.execute("SELECT candidate,digest FROM diagnostic_imaging_collection_receipts WHERE study=? ORDER BY candidate",(ident,))]


def export(directory,actor,ident,expected_digest,*,confirmed=False):
    from . import institution_imaging as studies
    if confirmed is not True:
        raise access.AccessDenied("explicit restricted transport evidence export required")
    with access.db(directory) as conn:
        studies._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        studies._actor(conn,actor,"admin")
        studies._inventory(conn)
        plan,docs,evidence=studies._get(conn,ident)
        if not evidence or evidence["digest"]!=expected_digest:
            raise access.AccessDenied("exact sealed imaging evidence required")
        receipts=check(conn,plan,docs)
        package=d._sealed({"schema":"institution-diagnostic-image-transport-evidence/1","study_evidence_digest":expected_digest,
                           "exchanges":[d.exchange(plan,r["predictions"]["candidate"]["id"]) for r in receipts],
                           "receipts":receipts,"decision_grade":False})
        studies._audit(conn,actor,"diagnostic-imaging-collection-evidence-exported",ident+":"+package["digest"])
        return package


def verify_package(package,study_evidence,expected_digest=None):
    d.verify(study_evidence)
    d._keys(package,"schema study_evidence_digest exchanges receipts decision_grade digest")
    d._require(package["schema"]=="institution-diagnostic-image-transport-evidence/1" and package["decision_grade"] is False
               and package["study_evidence_digest"]==study_evidence["digest"]
               and d._digest({k:v for k,v in package.items() if k!="digest"})==package["digest"]
               and (expected_digest is None or expected_digest==package["digest"]),"transport package digest mismatch")
    exs,receipts=package["exchanges"],package["receipts"]
    d._require(isinstance(exs,list) and isinstance(receipts,list) and len(exs)==len(receipts)<=d.MAX_CANDIDATES,
               "transport package inventory mismatch")
    by={doc["candidate"]["id"]:doc for doc in study_evidence["submissions"]}
    seen=set()
    for ex,r in zip(exs,receipts):
        cid=r["predictions"]["candidate"]["id"]
        d._require(cid in by and cid not in seen and d._digest(by[cid])==d._digest(r["predictions"])
                   and ex==d.exchange(study_evidence["plan"],cid),"transport receipt differs from sealed study")
        transport.verify(ex,r)
        seen.add(cid)
    return {"status":"REPLAY_VERIFIED","digest":package["digest"],"n_receipts":len(seen),
            "expected_digest_matched":expected_digest is not None,"decision_grade":False}
