"""Institution-controlled prospective RAG grounding research ledger."""
import json
from . import (rag_grounding as g, diagnostic_imaging as d, institution_rag_collection as collection,
               institution_access as access, unified_assessment as u, institution_rag_comparison as comparison)

FROZEN="rag-grounding-frozen"
TRACE="rag-grounding-trace-locked"
REVIEW="rag-grounding-review-locked"
SEAL="rag-grounding-sealed"
MAX_STUDIES=20


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS rag_grounding_studies(
        id TEXT PRIMARY KEY,payload TEXT NOT NULL,digest TEXT NOT NULL,
        actor TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE)""")
    for table in ("rag_grounding_traces","rag_grounding_reviews"):
        conn.execute("CREATE TABLE IF NOT EXISTS "+table+"""(
            study TEXT NOT NULL REFERENCES rag_grounding_studies(id),
            item TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL,
            actor TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE,PRIMARY KEY(study,item))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS rag_grounding_seals(
        study TEXT PRIMARY KEY REFERENCES rag_grounding_studies(id),digest TEXT NOT NULL,
        actor TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE)""")
    collection.schema(conn)
    comparison.schema(conn)


def _inventory(conn):
    for table,event,keys in (
        ("rag_grounding_studies",FROZEN,("id","digest")),
        ("rag_grounding_traces",TRACE,("study","item","digest")),
        ("rag_grounding_reviews",REVIEW,("study","item","digest")),
        ("rag_grounding_seals",SEAL,("study","digest"))):
        rows=conn.execute("SELECT audit_seq,actor,"+",".join(keys)+" FROM "+table).fetchall()
        expected={(r["audit_seq"],r["actor"],":".join(r[k] for k in keys)) for r in rows}
        actual={(r["seq"],r["actor"],r["object_id"]) for r in conn.execute(
            "SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok'",(event,))}
        if len(expected)!=len(rows) or actual!=expected:
            raise access.AccessDenied("RAG grounding audit inventory mismatch")
    collection.inventory(conn)
    comparison.inventory(conn)


def _roles(conn,plan):
    for uid in plan["spec"]["reviewers"]:
        u._actor(conn,{"user_id":uid,"role":"reviewer"},"reviewer")


def _get(conn,ident):
    d._require(d._token(ident),"invalid grounding study identifier")
    row=conn.execute("SELECT * FROM rag_grounding_studies WHERE id=?",(ident,)).fetchone()
    if not row:
        raise access.AccessDenied("unknown grounding study")
    plan=json.loads(row["payload"])
    g.verify_plan(plan)
    if plan["digest"]!=row["digest"] or plan["spec"]["study_id"]!=ident:
        raise access.AccessDenied("grounding study binding mismatch")
    docs=[]
    seq=row["audit_seq"]
    for r in conn.execute("SELECT * FROM rag_grounding_traces WHERE study=? ORDER BY item",(ident,)):
        doc=json.loads(r["payload"])
        if d._digest(doc)!=r["digest"] or doc["candidate"]["id"]!=r["item"] or r["audit_seq"]<=row["audit_seq"]:
            raise access.AccessDenied("grounding trace integrity mismatch")
        docs.append(doc)
        seq=max(seq,r["audit_seq"])
    docs=g.locked_traces(plan,docs)
    collection.check(conn,plan,docs)
    comparison.check(conn,plan)
    reviews={}
    packet=None
    last=seq
    for r in conn.execute("SELECT * FROM rag_grounding_reviews WHERE study=? ORDER BY item",(ident,)):
        if r["item"]!=r["actor"] or r["item"] not in plan["spec"]["reviewers"] or r["audit_seq"]<=seq:
            raise access.AccessDenied("grounding review role or order mismatch")
        if packet is None:
            packet=g.review_packet(plan,docs,r["item"])
        doc=json.loads(r["payload"])
        if d._digest(doc)!=r["digest"]:
            raise access.AccessDenied("grounding review digest mismatch")
        g._review(packet,doc)
        reviews[r["item"]]=doc
        last=max(last,r["audit_seq"])
    seal=conn.execute("SELECT * FROM rag_grounding_seals WHERE study=?",(ident,)).fetchone()
    evidence=None
    if seal:
        evidence=g.package(plan,docs,reviews)
        if evidence["digest"]!=seal["digest"] or seal["audit_seq"]<=last:
            raise access.AccessDenied("grounding seal integrity mismatch")
    return plan,docs,reviews,evidence


def _state(plan,docs,reviews,evidence):
    return {"id":plan["spec"]["study_id"],"n_questions":len(plan["spec"]["questions"]),
            "candidates":plan["spec"]["candidates"],"n_traces_locked":len(docs),
            "n_reviews_locked":len(reviews),"state":"sealed" if evidence else
            "ready" if len(reviews)==2 else "reviewing" if len(docs)==len(plan["spec"]["candidates"]) else "collecting",
            "evidence_digest":evidence["digest"] if evidence else None,"decision_grade":False}


def preview(directory,actor,spec):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        plan=g.freeze(spec,nonce="0"*64,confirmed=True)
        _roles(conn,plan)
        if conn.execute("SELECT 1 FROM rag_grounding_studies WHERE id=?",(spec["study_id"],)).fetchone():
            raise access.AccessDenied("study ID already frozen")
        return {"spec":plan["spec"],"approval":plan["digest"]}


def freeze(directory,actor,spec,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit prospective grounding freeze required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        p=g.freeze(spec,nonce="0"*64,confirmed=True)
        _roles(conn,p)
        ident=p["spec"]["study_id"]
        if p["digest"]!=approval or conn.execute("SELECT 1 FROM rag_grounding_studies WHERE id=?",(ident,)).fetchone():
            raise access.AccessDenied("grounding freeze differs from approval or ID already reserved")
        if conn.execute("SELECT count(*) FROM rag_grounding_studies").fetchone()[0]>=MAX_STUDIES:
            raise access.AccessDenied("grounding study limit reached")
        plan=g.freeze(spec,confirmed=True)
        seq=u._audit(conn,actor,FROZEN,ident+":"+plan["digest"])
        conn.execute("INSERT INTO rag_grounding_studies VALUES(?,?,?,?,?)",
                     (ident,json.dumps(plan),plan["digest"],actor["user_id"],seq))
        return ident


def overview(directory,actor):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator","observer","reviewer")
        _inventory(conn)
        out=[]
        for row in conn.execute("SELECT id FROM rag_grounding_studies ORDER BY audit_seq").fetchall():
            plan,docs,reviews,evidence=_get(conn,row["id"])
            if actor["role"]=="reviewer":
                uid=actor["user_id"]
                if uid in plan["spec"]["reviewers"] and len(docs)==len(plan["spec"]["candidates"]):
                    out.append({"id":row["id"],"state":"sealed" if evidence else "locked" if uid in reviews else "assigned"})
            else:
                state=_state(plan,docs,reviews,evidence)
                state["collection_receipts"]=collection.coverage(conn,row["id"])
                state["paired_comparison"]=comparison.coverage(conn,plan,evidence)
                out.append(state)
        return out


def detail(directory,actor,ident):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator")
        _inventory(conn)
        plan,docs,reviews,evidence=_get(conn,ident)
        state=_state(plan,docs,reviews,evidence)
        state["collection_receipts"]=collection.coverage(conn,ident)
        state["paired_comparison"]=comparison.coverage(conn,plan,evidence)
        state["submitted_candidates"]=[x["candidate"]["id"] for x in docs]
        if evidence and actor["role"]=="admin":
            state["results"]=evidence["results"]
        return state


def candidate_download(directory,actor,ident,candidate,kind):
    if kind not in ("exchange","template"):
        raise access.AccessDenied("invalid grounding candidate export")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin","operator")
        _inventory(conn)
        plan,docs,reviews,evidence=_get(conn,ident)
        if reviews or evidence or candidate in {x["candidate"]["id"] for x in docs}:
            raise access.AccessDenied("candidate already locked or review started")
        doc=(g.exchange if kind=="exchange" else g.template)(plan,candidate)
        u._audit(conn,actor,"rag-grounding-candidate-exported",ident+":"+candidate+":"+kind+":"+d._digest(doc))
        return doc


def _insert(conn,actor,table,event,ident,item,doc):
    digest=d._digest(doc)
    seq=u._audit(conn,actor,event,ident+":"+item+":"+digest)
    conn.execute("INSERT INTO "+table+" VALUES(?,?,?,?,?,?)",
                 (ident,item,json.dumps(doc),digest,actor["user_id"],seq))
    return digest


def submit(directory,actor,ident,doc,*,confirmed=False,receipt=None):
    if confirmed is not True:
        raise access.AccessDenied("explicit trace lock required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin","operator")
        _inventory(conn)
        plan,docs,reviews,evidence=_get(conn,ident)
        if reviews or evidence:
            raise access.AccessDenied("reference review already started")
        normalized=g.locked_traces(plan,[doc])[0]
        cid=normalized["candidate"]["id"]
        if receipt is not None:
            if collection.validate(plan,receipt)!=cid or receipt["trace"]!=normalized:
                raise access.AccessDenied("receipt does not match submitted trace")
        for old in docs:
            if old["candidate"]["id"]==cid:
                if old!=normalized:
                    raise access.AccessDenied("candidate trace cannot be replaced")
                prior=conn.execute("SELECT digest FROM rag_grounding_collection_receipts WHERE study=? AND item=?",(ident,cid)).fetchone()
                if (prior["digest"] if prior else None)!=(receipt["digest"] if receipt is not None else None):
                    raise access.AccessDenied("cannot change locked trace collection provenance")
                return d._digest(old)
        digest=_insert(conn,actor,"rag_grounding_traces",TRACE,ident,cid,normalized)
        if receipt is not None:
            collection.record(conn,actor,ident,receipt)
        return digest


def review_download(directory,actor,ident,kind):
    if kind not in ("packet","template"):
        raise access.AccessDenied("invalid grounding review export")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"reviewer")
        _inventory(conn)
        plan,docs,reviews,evidence=_get(conn,ident)
        uid=actor["user_id"]
        if evidence or uid in reviews:
            raise access.AccessDenied("review already locked")
        packet=g.review_packet(plan,docs,uid)
        result=packet if kind=="packet" else g.review_template(packet)
        u._audit(conn,actor,"rag-grounding-review-exported",ident+":"+kind+":"+d._digest(result))
        return result


def review(directory,actor,ident,doc,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit independent review lock required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"reviewer")
        _inventory(conn)
        plan,docs,reviews,evidence=_get(conn,ident)
        uid=actor["user_id"]
        if evidence or uid in reviews:
            raise access.AccessDenied("review already locked")
        packet=g.review_packet(plan,docs,uid)
        g._review(packet,doc)
        return _insert(conn,actor,"rag_grounding_reviews",REVIEW,ident,uid,doc)


def seal_preview(directory,actor,ident):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        plan,docs,reviews,evidence=_get(conn,ident)
        if evidence:
            raise access.AccessDenied("study already sealed")
        return {"approval":g.package(plan,docs,reviews)["digest"]}


def seal(directory,actor,ident,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit exact evidence seal required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        plan,docs,reviews,prior=_get(conn,ident)
        evidence=g.package(plan,docs,reviews)
        if prior or evidence["digest"]!=approval:
            raise access.AccessDenied("study already sealed or evidence changed")
        seq=u._audit(conn,actor,SEAL,ident+":"+approval)
        conn.execute("INSERT INTO rag_grounding_seals VALUES(?,?,?,?)",
                     (ident,approval,actor["user_id"],seq))
        return approval


def export(directory,actor,ident,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit private grounding export required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        _,_,_,evidence=_get(conn,ident)
        if not evidence or evidence["digest"]!=approval:
            raise access.AccessDenied("exact sealed grounding evidence required")
        u._audit(conn,actor,"rag-grounding-private-evidence-exported",ident+":"+approval)
        return evidence
