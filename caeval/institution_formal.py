"""Authenticated formal review coordination, separate from exploratory reviews.

Reviewers lock their own answers. A distinct coordinator explicitly attests
external credential/identity/independence verification before canonical import.
Only browser-created handoffs are visible here; offline CLI handoffs remain
separate. No network calls or changes to the scientific source package.
"""
from __future__ import annotations
import json

from . import formal_review as formal, institution_access as access
from . import buyer_plan, buyer_jobs as jobs, buyer_manual as manual

MAX_BROWSER_CELLS=400
MAX_HANDOFFS=100


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS formal_browser_handoffs(
        id TEXT PRIMARY KEY, job TEXT NOT NULL, family TEXT NOT NULL,
        binding_hash TEXT NOT NULL, coordinator TEXT NOT NULL,
        UNIQUE(job,family))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS formal_browser_roster(
        handoff TEXT NOT NULL REFERENCES formal_browser_handoffs(id),
        reviewer TEXT NOT NULL REFERENCES users(id),
        PRIMARY KEY(handoff,reviewer))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS formal_browser_pending(
        handoff TEXT NOT NULL REFERENCES formal_browser_handoffs(id),
        reviewer TEXT NOT NULL REFERENCES users(id), payload TEXT NOT NULL,
        digest TEXT NOT NULL, PRIMARY KEY(handoff,reviewer))""")


def _attestation_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS formal_browser_attestations(
        handoff TEXT NOT NULL, reviewer TEXT NOT NULL,
        pending_digest TEXT NOT NULL, canonical_digest TEXT NOT NULL,
        administrator TEXT NOT NULL REFERENCES users(id),
        PRIMARY KEY(handoff,reviewer),
        FOREIGN KEY(handoff,reviewer) REFERENCES formal_browser_pending(handoff,reviewer))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS formal_browser_consensus(
        handoff TEXT PRIMARY KEY REFERENCES formal_browser_handoffs(id),
        canonical_digest TEXT NOT NULL,
        administrator TEXT NOT NULL REFERENCES users(id))""")


def _actor(conn,actor,*roles):
    access.require(actor,*roles)
    row=conn.execute("SELECT role,enabled FROM users WHERE id=?",(actor["user_id"],)).fetchone()
    if not row or not row["enabled"] or row["role"]!=actor["role"]:
        raise access.AccessDenied("active authorized account required")


def _registry(conn,identifier):
    manual._identifier(identifier)
    row=conn.execute("SELECT * FROM formal_browser_handoffs WHERE id=?",(identifier,)).fetchone()
    if not row:
        raise access.AccessDenied("unknown formal browser handoff")
    return dict(row)


def _load(directory,conn,identifier):
    reg=_registry(conn,identifier)
    ws,binding,safety,validity,issued=formal._load(directory,identifier)
    if (binding["binding_hash"]!=reg["binding_hash"] or binding["job"]!=reg["job"]
            or binding["family"]!=reg["family"] or binding["issuing_operator"]!=reg["coordinator"]
            or len(safety)>MAX_BROWSER_CELLS):
        raise access.AccessDenied("formal browser binding changed")
    if {r[0] for r in conn.execute("SELECT reviewer FROM formal_browser_roster WHERE handoff=?",(identifier,))}!=set(issued):
        raise access.AccessDenied("registered reviewer roster changed")
    records=formal._submissions(ws,binding,safety,validity,issued)
    _attestation_schema(conn)
    ledger={r["reviewer"]:dict(r) for r in conn.execute(
        "SELECT * FROM formal_browser_attestations WHERE handoff=?",(identifier,))}
    if set(ledger)!=set(records):
        raise access.AccessDenied("browser attestation ledger differs from canonical review store")
    for rid,record in records.items():
        pending=_pending(conn,identifier,rid,binding,safety,validity,issued)
        entry=ledger[rid]
        if (not pending or pending[1]!=entry["pending_digest"]
                or pending[0]["submission"]["answers"]!=record["answers"]
                or record["digest"]!=entry["canonical_digest"]
                or record["operator"]!=entry["administrator"]):
            raise access.AccessDenied("browser review provenance differs from canonical submission")
    _verified_consensus(conn,identifier,ws,binding,safety,records,issued)
    return ws,binding,safety,validity,issued,records


def _verified_consensus(conn,identifier,ws,binding,safety,records,issued):
    record=formal._consensus_record(ws,binding,safety,records,issued)
    ledger=conn.execute("SELECT * FROM formal_browser_consensus WHERE handoff=?",(identifier,)).fetchone()
    if bool(record)!=bool(ledger) or (record and (
            record["digest"]!=ledger["canonical_digest"] or record["operator"]!=ledger["administrator"])):
        raise access.AccessDenied("browser consensus attestation differs from canonical store")
    return record["digest"] if record else None


def _eligible(conn,rid):
    row=conn.execute("SELECT role,enabled FROM users WHERE id=?",(rid,)).fetchone()
    return bool(row and row["enabled"] and row["role"]=="reviewer")


def prepare(directory,actor,job,family,reviewers,validity_reviewer,confirmed):
    if confirmed is not True:
        raise access.AccessDenied("external roster verification required")
    ids,vid=formal._roster(reviewers,validity_reviewer)
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,"admin")
        if not all(_eligible(conn,rid) for rid in ids+[vid]):
            raise access.AccessDenied("active preprovisioned reviewer accounts required")
        if conn.execute("SELECT 1 FROM formal_browser_handoffs WHERE job=? AND family=?",(job,family)).fetchone():
            raise access.AccessDenied("formal browser handoff already exists; use a fresh job")
        if conn.execute("SELECT count(*) FROM formal_browser_handoffs").fetchone()[0]>=MAX_HANDOFFS:
            raise access.AccessDenied("formal browser handoff limit")
        _,blobs=formal._source(directory,job,family)
        _,safety,_=formal._queues(blobs)
        if len(safety)>MAX_BROWSER_CELLS:
            raise access.AccessDenied("queue exceeds browser limit; use approved offline handoff")
        result=formal.prepare(directory,job,family,ids,vid,actor["user_id"],confirm_roster=True)
        conn.execute("INSERT INTO formal_browser_handoffs VALUES(?,?,?,?,?)",
                     (result["id"],job,family,result["binding_hash"],actor["user_id"]))
        conn.executemany("INSERT INTO formal_browser_roster VALUES(?,?)",[(result["id"],rid) for rid in ids+[vid]])
        access._audit(conn,actor["user_id"],"formal-browser-roster-frozen",result["id"])
    return result


def _pending(conn,identifier,rid,binding,safety,validity,issued):
    row=conn.execute("SELECT payload,digest FROM formal_browser_pending WHERE handoff=? AND reviewer=?",
                     (identifier,rid)).fetchone()
    if not row:
        return None
    payload=json.loads(row["payload"])
    if (buyer_plan._digest(payload)!=row["digest"] or payload.get("reviewer")!=rid
            or payload.get("binding_hash")!=binding["binding_hash"]
            or set(payload)!={"reviewer","binding_hash","answers"}):
        raise access.AccessDenied("pending review changed")
    expected=formal._template(binding,safety,validity,issued[rid],rid)
    doc={"request":expected["request"],"submission":{"answers":payload["answers"],"independent_review_confirmed":True}}
    if formal._validate_submission(doc,expected)!=payload["answers"]:
        raise access.AccessDenied("pending answers changed")
    return doc,row["digest"]


def overview(directory,actor):
    with access.db(directory) as conn:
        _schema(conn)
        _actor(conn,actor,"admin","reviewer")
        rows=[]
        if actor["role"]=="reviewer":
            registry=conn.execute("""SELECT h.* FROM formal_browser_handoffs h JOIN formal_browser_roster r
                ON h.id=r.handoff WHERE r.reviewer=? ORDER BY h.id LIMIT ?""",(actor["user_id"],MAX_HANDOFFS)).fetchall()
        else:
            registry=conn.execute("SELECT * FROM formal_browser_handoffs ORDER BY id LIMIT ?",(MAX_HANDOFFS,)).fetchall()
        for reg in registry:
            try:
                ws,binding,safety,validity,issued,records=_load(directory,conn,reg["id"])
            except (OSError,ValueError,RuntimeError,KeyError,TypeError):
                if actor["role"]=="admin":
                    rows.append({"id":reg["id"],"unavailable":True})
                continue
            rid=actor["user_id"]
            if actor["role"]=="reviewer":
                if rid not in issued:
                    continue
                pending=_pending(conn,reg["id"],rid,binding,safety,validity,issued)
                rows.append({"id":reg["id"],"kind":"validity" if rid==binding["validity_reviewer"] else "safety",
                             "submitted":bool(pending or rid in records)})
            else:
                roster=[]
                for reviewer in issued:
                    pending=_pending(conn,reg["id"],reviewer,binding,safety,validity,issued)
                    roster.append({"reviewer":reviewer,"kind":"validity" if reviewer==binding["validity_reviewer"] else "safety",
                                   "state":"attested" if reviewer in records else "pending_attestation" if pending else "awaiting",
                                   "digest":pending[1] if pending else None})
                rows.append({"id":reg["id"],"job":reg["job"],"family":reg["family"],
                             "synthetic":binding["synthetic"],"roster":roster,
                             "ready":set(records)==set(issued)})
        access._audit(conn,actor["user_id"],"formal-browser-overview")
    return rows


def reviewer_template(directory,actor,identifier):
    with access.db(directory) as conn:
        _schema(conn)
        _actor(conn,actor,"reviewer")
        if not conn.execute("SELECT 1 FROM formal_browser_roster WHERE handoff=? AND reviewer=?",(identifier,actor["user_id"])).fetchone():
            raise access.AccessDenied("assigned review required")
        _,binding,safety,validity,issued,records=_load(directory,conn,identifier)
        rid=actor["user_id"]
        if rid not in issued or rid in records or _pending(conn,identifier,rid,binding,safety,validity,issued):
            raise access.AccessDenied("open assigned unsubmitted review required")
        doc=formal._template(binding,safety,validity,issued[rid],rid)
        access._audit(conn,rid,"formal-browser-review-opened",identifier)
    return doc


def lock_review(directory,actor,identifier,binding_hash,answers,confirmed):
    if confirmed is not True:
        raise access.AccessDenied("independent review confirmation required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,"reviewer")
        if not conn.execute("SELECT 1 FROM formal_browser_roster WHERE handoff=? AND reviewer=?",(identifier,actor["user_id"])).fetchone():
            raise access.AccessDenied("assigned review required")
        _,binding,safety,validity,issued,records=_load(directory,conn,identifier)
        rid=actor["user_id"]
        if rid not in issued or rid in records or binding["binding_hash"]!=binding_hash:
            raise access.AccessDenied("assigned current unsubmitted review required")
        expected=formal._template(binding,safety,validity,issued[rid],rid)
        doc={"request":expected["request"],"submission":{"answers":answers,"independent_review_confirmed":True}}
        rows=formal._validate_submission(doc,expected)
        payload={"reviewer":rid,"binding_hash":binding_hash,"answers":rows}
        digest=buyer_plan._digest(payload)
        prior=_pending(conn,identifier,rid,binding,safety,validity,issued)
        if prior and prior[1]!=digest:
            raise access.AccessDenied("review already locked; no replacement")
        conn.execute("INSERT OR IGNORE INTO formal_browser_pending VALUES(?,?,?,?)",
                     (identifier,rid,json.dumps(payload,ensure_ascii=False),digest))
        access._audit(conn,rid,"formal-browser-review-locked",identifier)
    return {"digest":digest,"decision_grade":False}


def attest(directory,actor,identifier,rid,digest,confirmed):
    if confirmed is not True:
        raise access.AccessDenied("external identity, credentials and independence verification required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,"admin")
        _,binding,safety,validity,issued,records=_load(directory,conn,identifier)
        if rid not in issued or not _eligible(conn,rid):
            raise access.AccessDenied("active assigned reviewer required")
        pending=_pending(conn,identifier,rid,binding,safety,validity,issued)
        if not pending or pending[1]!=digest or rid in records:
            raise access.AccessDenied("exact pending independent review required")
        result=formal.submit(directory,identifier,rid,pending[0],actor["user_id"],confirm_identity=True)
        conn.execute("INSERT INTO formal_browser_attestations VALUES(?,?,?,?,?)",
                     (identifier,rid,digest,result["digest"],actor["user_id"]))
        access._audit(conn,actor["user_id"],"formal-browser-review-attested",identifier)
    return result


def analyze(directory,actor,identifier):
    with access.db(directory) as conn:
        _schema(conn)
        _actor(conn,actor,"admin")
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,"admin")
        ws,binding,safety,validity,issued,records=_load(directory,conn,identifier)
        consensus_digest=_verified_consensus(conn,identifier,ws,binding,safety,records,issued)
        result=formal.analyze(directory,identifier)
        # An offline write during computation cannot be presented as browser
        # evidence, even if the canonical engine validates it on its own.
        if (result["binding_hash"]!=binding["binding_hash"]
                or result["submission_digests"]!={rid:r["digest"] for rid,r in records.items()}
                or result["consensus_digest"]!=consensus_digest):
            raise access.AccessDenied("formal browser evidence changed during analysis")
        _load(directory,conn,identifier)
        access._audit(conn,actor["user_id"],"formal-browser-analysis",identifier)
    return result


def consensus_template(directory,actor,identifier):
    with access.db(directory) as conn:
        _schema(conn)
        _actor(conn,actor,"admin")
        _load(directory,conn,identifier)
    return formal.consensus_template(directory,identifier)


def consensus(directory,actor,identifier,request_digest,answers,confirmed):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,"admin")
        _load(directory,conn,identifier)
        doc=formal.consensus_template(directory,identifier)
        if buyer_plan._digest(doc["request"])!=request_digest:
            raise access.AccessDenied("stale consensus form")
        doc["submission"]={"answers":answers,"confirmed_by_original_reviewers":confirmed}
        result=formal.submit_consensus(directory,identifier,doc,actor["user_id"],confirm_consensus=confirmed)
        prior=conn.execute("SELECT canonical_digest,administrator FROM formal_browser_consensus WHERE handoff=?",(identifier,)).fetchone()
        if prior and (prior["canonical_digest"]!=result["digest"] or prior["administrator"]!=actor["user_id"]):
            raise access.AccessDenied("consensus already attested; no replacement")
        conn.execute("INSERT OR IGNORE INTO formal_browser_consensus VALUES(?,?,?)",
                     (identifier,result["digest"],actor["user_id"]))
        access._audit(conn,actor["user_id"],"formal-browser-consensus-attested",identifier)
    return result
