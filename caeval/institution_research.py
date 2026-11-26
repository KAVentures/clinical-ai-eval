"""Account-bound browser coordination of experimental research assessments.

A closed set of adapters delegates scientific work to the existing assessment
modules. Browser users never choose filesystem paths or execute candidates.
SQLite binds approval, assignment, reviewer submission and report digests.
Trusted filesystem administration is assumed; interrupted cross-store writes
fail closed rather than silently reconciling or dropping clinical evidence.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from . import (buyer_agent as buyer, buyer_manual as manual, buyer_plan,
               buyer_research as research, formal_review as formal,
               institution_access as access, citation_assessment as citation,
               scribe_assessment as scribe, decision_assessment as decision,
               robustness_assessment as robustness)

MODULES = {"citation":citation,"scribe":scribe,"decision":decision,"robustness":robustness}
LABELS = {"citation":"Citation and guideline grounding","scribe":"Medical scribe documentation",
          "decision":"Rule-bound decision certifiability","robustness":"Paired clinical robustness"}
MAX_STUDIES=100


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS research_studies(
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner TEXT NOT NULL REFERENCES users(id),
        plan_hash TEXT NOT NULL, response_hash TEXT, binding_hash TEXT,
        report_hash TEXT, state TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS research_reviewers(
        study TEXT NOT NULL REFERENCES research_studies(id), reviewer TEXT NOT NULL REFERENCES users(id),
        role TEXT NOT NULL, pending_hash TEXT, canonical_hash TEXT,
        PRIMARY KEY(study,reviewer))""")


def _actor(conn,actor,*roles):
    access.require(actor,*roles)
    u=conn.execute("SELECT role,enabled FROM users WHERE id=?",(actor["user_id"],)).fetchone()
    if not u or not u["enabled"] or u["role"]!=actor["role"]:
        raise access.AccessDenied("active account with current role required")


def _root(directory):
    buyer.load(directory)
    path=manual._directory(directory,"research-studies")
    path.mkdir(mode=0o700,exist_ok=True)
    if not path.is_dir() or path.is_symlink():
        raise access.AccessDenied("invalid research store")
    return path


def _path(directory,identifier):
    p=_root(directory)/manual._identifier(identifier)
    if p.is_symlink() or not p.is_dir():
        raise access.AccessDenied("research study unavailable")
    return p


def _row(conn,identifier):
    row=conn.execute("SELECT * FROM research_studies WHERE id=?",(manual._identifier(identifier),)).fetchone()
    if not row or row["kind"] not in MODULES:
        raise access.AccessDenied("unknown research study")
    return dict(row)


def _manager(conn,actor,row):
    _actor(conn,actor,"admin","operator")
    if actor["role"]!="admin" and actor["user_id"]!=row["owner"]:
        raise access.AccessDenied("study owner or administrator required")


def _assigned(conn,actor,row):
    _actor(conn,actor,"reviewer")
    record=conn.execute("SELECT * FROM research_reviewers WHERE study=? AND reviewer=?",
                        (row["id"],actor["user_id"])).fetchone()
    if not record:
        raise access.AccessDenied("assigned review required")
    return dict(record)


def _plan(directory,row):
    root=_path(directory,row["id"])
    research._safe_tree(root)
    mod=MODULES[row["kind"]]
    plan=mod.verify_plan(formal._json(root/"plan.json"))
    if plan["plan_hash"]!=row["plan_hash"]:
        raise access.AccessDenied("study plan differs from approved record")
    return root,mod,plan


def create(directory,actor,kind,spec):
    if kind not in MODULES:
        raise access.AccessDenied("unknown assessment type")
    with access.db(directory) as conn:
        _schema(conn)
        _actor(conn,actor,"admin","operator")
    plan=MODULES[kind].build_plan(spec)
    identifier="study-"+uuid.uuid4().hex
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,"admin","operator")
        if conn.execute("SELECT count(*) FROM research_studies").fetchone()[0]>=MAX_STUDIES:
            raise access.AccessDenied("study limit reached")
        root=_root(directory)/identifier
        root.mkdir(mode=0o700)
        (root/"pending").mkdir(mode=0o700)
        formal._publish_once(root/"plan.json",plan)
        conn.execute("INSERT INTO research_studies(id,kind,owner,plan_hash,state) VALUES(?,?,?,?,?)",
                     (identifier,kind,actor["user_id"],plan["plan_hash"],"planned"))
        access._audit(conn,actor["user_id"],"research-plan-created",identifier)
    return identifier


def _citation_template(plan):
    catalog={s["source_id"]:s for s in plan["spec"]["sources"]}
    return {"schema":"observed_rag_trace_v1","target":plan["spec"]["target"],
            "sources":plan["spec"]["sources"],"traces":[
                {"query_id":q["query_id"],"query":q["query"],"retrieval_query":q["query"],
                 "reference_supporting_source_ids":q["reference_supporting_source_ids"],
                 "retrieved_chunks":[{"source_id":sid,"source_version":catalog[sid]["version"],
                    "text":catalog[sid]["text"],"rank":n+1} for n,sid in enumerate(q["source_ids"])],
                 "model_status":"","answer":"","atomic_claims":[]} for q in plan["queries"]]}


def download(directory,actor,identifier,kind):
    with access.db(directory) as conn:
        _schema(conn)
        row=_row(conn,identifier)
        if kind=="review":
            assignment=_assigned(conn,actor,row)
            if row["state"]!="reviewing" or assignment["pending_hash"] is not None:
                raise access.AccessDenied("review no longer open")
            root,mod,plan,loaded=_review(directory,conn,row)
            doc=mod.template(root/"review",actor["user_id"])
        else:
            _manager(conn,actor,row)
            root,mod,plan=_plan(directory,row)
            if kind=="exchange":
                doc=mod.prompts(plan) if mod is citation else mod.exchange(plan)
            elif kind=="responses":
                if row["state"]=="raw_collected":
                    from . import research_collection as collection
                    doc=collection.draft(directory,conn,row,plan,mod)
                else:
                    doc=_citation_template(plan) if mod is citation else mod.response_template(plan)
            elif kind=="collection-receipt":
                from . import research_collection as collection
                doc=collection.receipt(directory,conn,row)
            elif kind=="review-provenance":
                _actor(conn,actor,"admin")
                from . import research_review_protocol as protocol
                doc=protocol.receipt(directory,conn,row)
            elif kind=="plan":
                doc=plan
            elif kind=="report":
                doc=_report(directory,conn,row)
            else:
                raise access.AccessDenied("unknown research export")
        access._audit(conn,actor["user_id"],"research-export-"+kind,identifier)
        return doc


def collect(directory,actor,identifier,approved_plan,responses,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit collection provenance confirmation required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row=_row(conn,identifier)
        _manager(conn,actor,row)
        root,mod,plan=_plan(directory,row)
        if row["state"] not in ("planned","raw_collected") or approved_plan!=row["plan_hash"]:
            raise access.AccessDenied("exact approved uncollected plan required")
        from . import research_review_protocol as protocol
        protocol.read(conn,row)
        if row["state"]=="raw_collected":
            from . import research_collection as collection
            collection.verify_import(directory,conn,row,plan,mod,responses)
        if mod is decision:
            mod.assess(plan,responses)
        else:
            mod.capture(plan,responses)
        formal._publish_once(root/"responses.json",responses)
        conn.execute("UPDATE research_studies SET response_hash=?,state='collected' WHERE id=?",
                     (buyer_plan._digest(responses),identifier))
        access._audit(conn,actor["user_id"],"research-responses-collected",identifier)


def _collected(root,row):
    responses=formal._json(root/"responses.json")
    if buyer_plan._digest(responses)!=row["response_hash"]:
        raise access.AccessDenied("collected responses changed")
    return responses


def _verified_collection(directory,conn,row,plan,mod,responses):
    from . import research_collection as collection
    collection._schema(conn)
    job=conn.execute("SELECT state FROM research_collections WHERE study=?",(row["id"],)).fetchone()
    if job:
        collection.verify_import(directory,conn,row,plan,mod,responses)
    return responses


def assign(directory,actor,identifier,reviewers,validity_reviewers,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("external credentials, conflicts and reference approval required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,"admin")
        row=_row(conn,identifier)
        root,mod,plan=_plan(directory,row)
        if row["state"]!="collected" or mod is decision:
            raise access.AccessDenied("collected human-reviewed assessment required")
        responses=_verified_collection(directory,conn,row,plan,mod,_collected(root,row))
        if mod is robustness:
            v,a,_=mod._roster(validity_reviewers,reviewers,actor["user_id"])
            roster=[(rid,"reference_validity") for rid in v]+[(rid,"isolated_answer") for rid in a]
        else:
            if validity_reviewers:
                raise access.AccessDenied("unexpected separate validity roster")
            a,_=mod._roster(reviewers,actor["user_id"])
            roster=[(rid,"clinical") for rid in a]
        for rid,_ in roster:
            user=conn.execute("SELECT role,enabled FROM users WHERE id=?",(rid,)).fetchone()
            if not user or user["role"]!="reviewer" or not user["enabled"] or rid==row["owner"]:
                raise access.AccessDenied("independent enabled reviewer account required")
        from . import research_review_protocol as protocol
        protocol.verify_assignment(conn,row,sorted(
            [{"reviewer":rid,"role":role} for rid,role in roster],
            key=lambda item:(item["role"],item["reviewer"])))
        if mod is robustness:
            binding=mod.prepare(plan,responses,root/"review",v,a,actor["user_id"],confirmed=True)
        else:
            binding=mod.prepare(plan,responses,root/"review",a,actor["user_id"],confirmed=True)
        for rid,role in roster:
            conn.execute("INSERT INTO research_reviewers(study,reviewer,role) VALUES(?,?,?)",
                         (identifier,rid,role))
        conn.execute("UPDATE research_studies SET binding_hash=?,state='reviewing' WHERE id=?",
                     (binding["binding_hash"],identifier))
        access._audit(conn,actor["user_id"],"research-review-assigned",identifier)


def _review(directory,conn,row):
    root,mod,plan=_plan(directory,row)
    responses=_verified_collection(directory,conn,row,plan,mod,_collected(root,row))
    if mod is decision or not row["binding_hash"]:
        raise access.AccessDenied("human review unavailable")
    loaded=mod._load(root/"review")
    _,frozen,stored,b,evidence,issued=loaded if mod in (scribe,citation) else (
        loaded[0],loaded[1],formal._json(root/"review"/"responses.json"),loaded[2],loaded[3],loaded[4])
    if frozen!=plan or stored!=responses or b["binding_hash"]!=row["binding_hash"]:
        raise access.AccessDenied("canonical review differs from study")
    roster={r["reviewer"]:dict(r) for r in conn.execute(
        "SELECT * FROM research_reviewers WHERE study=?",(row["id"],))}
    from . import research_review_protocol as protocol
    protocol.verify_assignment(conn,row)
    if set(roster)!=set(issued):
        raise access.AccessDenied("review roster changed")
    expected={rid+".json" for rid,r in roster.items() if r["canonical_hash"]}
    actual={p.name for p in (root/"review"/"submissions").iterdir()}
    if expected!=actual:
        raise access.AccessDenied("canonical submissions differ from authenticated ledger")
    pending_expected={rid+".json" for rid,r in roster.items() if r["pending_hash"]}
    if {p.name for p in (root/"pending").iterdir()}!=pending_expected:
        raise access.AccessDenied("pending submission inventory differs from ledger")
    for rid,r in roster.items():
        expected_role=issued[rid]["reviewer_role"]
        if r["role"]!=("clinical" if mod is not robustness else expected_role):
            raise access.AccessDenied("review role changed")
        if r["pending_hash"]:
            pending=formal._json(root/"pending"/(rid+".json"))
            if buyer_plan._digest(pending)!=r["pending_hash"]:
                raise access.AccessDenied("pending review changed")
        if r["canonical_hash"]:
            record=formal._json(root/"review"/"submissions"/(rid+".json"))
            if (record.get("digest")!=r["canonical_hash"] or
                buyer_plan._digest({k:v for k,v in record.items() if k!="digest"})!=r["canonical_hash"]):
                raise access.AccessDenied("canonical submission changed")
    return root,mod,plan,(b,evidence,issued)


def lock_review(directory,actor,identifier,document):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row=_row(conn,identifier)
        assignment=_assigned(conn,actor,row)
        if row["state"]!="reviewing" or assignment["pending_hash"] is not None:
            raise access.AccessDenied("review already locked or closed")
        root,mod,_,(b,evidence,issued)=_review(directory,conn,row)
        rid=actor["user_id"]
        expected=mod.template(root/"review",rid)
        if not isinstance(document,dict) or set(document)!={"request","submission"} or document["request"]!=expected["request"]:
            raise access.AccessDenied("exact account-bound packet required")
        if mod is robustness:
            mod._validate(document["submission"],evidence,issued[rid]["reviewer_role"])
        else:
            mod._validate_submission(document["submission"],evidence)
        formal._publish_once(root/"pending"/(rid+".json"),document)
        digest=buyer_plan._digest(document)
        conn.execute("UPDATE research_reviewers SET pending_hash=? WHERE study=? AND reviewer=?",
                     (digest,identifier,rid))
        access._audit(conn,rid,"research-review-locked",identifier)
        return digest


def attest(directory,actor,identifier,reviewer,digest,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("external identity and independent review attestation required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,"admin")
        row=_row(conn,identifier)
        if row["state"]!="reviewing":
            raise access.AccessDenied("open review required")
        assignment=conn.execute("SELECT * FROM research_reviewers WHERE study=? AND reviewer=?",
                                (identifier,manual._identifier(reviewer))).fetchone()
        if not assignment or not assignment["pending_hash"] or assignment["canonical_hash"] or assignment["pending_hash"]!=digest:
            raise access.AccessDenied("exact unattested submission required")
        root,mod,_,_=_review(directory,conn,row)
        pending=formal._json(root/"pending"/(reviewer+".json"))
        result=mod.submit(root/"review",reviewer,pending,actor["user_id"],confirmed=True)
        conn.execute("UPDATE research_reviewers SET canonical_hash=? WHERE study=? AND reviewer=?",
                     (result["digest"],identifier,reviewer))
        access._audit(conn,actor["user_id"],"research-review-attested",identifier)


def analyze(directory,actor,identifier,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit research analysis approval required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,"admin")
        row=_row(conn,identifier)
        root,mod,plan=_plan(directory,row)
        if mod is decision and row["state"]=="collected":
            report=mod.assess(plan,_verified_collection(directory,conn,row,plan,mod,_collected(root,row)))
        elif mod is not decision and row["state"]=="reviewing":
            _review(directory,conn,row)
            if conn.execute("SELECT count(*) FROM research_reviewers WHERE study=? AND canonical_hash IS NULL",
                            (identifier,)).fetchone()[0]:
                raise access.AccessDenied("all independent submissions must be attested")
            report=mod.analyze(root/"review")
        else:
            raise access.AccessDenied("study not ready for analysis")
        if report["decision_grade"] is not False:
            raise access.AccessDenied("experimental assessment cannot be decision grade")
        formal._publish_once(root/"report.json",report)
        conn.execute("UPDATE research_studies SET report_hash=?,state='analyzed' WHERE id=?",
                     (report["digest"],identifier))
        access._audit(conn,actor["user_id"],"research-analyzed",identifier)
        return report["digest"]


def _report(directory,conn,row):
    if row["state"]!="analyzed":
        raise access.AccessDenied("analysis not available")
    root,mod,plan=_plan(directory,row)
    _verified_collection(directory,conn,row,plan,mod,_collected(root,row))
    if mod is not decision:
        _review(directory,conn,row)
    report=formal._json(root/"report.json")
    if (report.get("digest")!=row["report_hash"] or report.get("decision_grade") is not False or
            buyer_plan._digest({k:v for k,v in report.items() if k!="digest"})!=row["report_hash"]):
        raise access.AccessDenied("research report changed")
    return report


def overview(directory,actor):
    with access.db(directory) as conn:
        _schema(conn)
        _actor(conn,actor,"admin","operator","observer","reviewer")
        if actor["role"]=="reviewer":
            rows=conn.execute("""SELECT s.id,s.kind,s.state,r.pending_hash,r.canonical_hash,r.role
                FROM research_reviewers r JOIN research_studies s ON r.study=s.id WHERE r.reviewer=?""",
                (actor["user_id"],)).fetchall()
            return [dict(r) for r in rows]
        result=[]
        for item in conn.execute("SELECT * FROM research_studies ORDER BY rowid DESC LIMIT ?",(MAX_STUDIES,)):
            row=dict(item)
            if actor["role"]=="operator" and row["owner"]!=actor["user_id"]:
                continue
            public={k:row[k] for k in ("id","kind","state")}
            public["decision_grade"]=False
            if actor["role"]!="observer":
                public["plan_hash"]=row["plan_hash"]
                public["owner"]=row["owner"]
                from . import research_review_protocol as protocol
                public["review_protocol_status"]=protocol.status(conn,row)
                if actor["role"]=="admin":
                    record=protocol.read(conn,row)
                    if record:
                        public["review_protocol"]=record
                from . import research_collection as collection
                collection._schema(conn)
                if conn.execute("SELECT 1 FROM research_collections WHERE study=?",(row["id"],)).fetchone():
                    job=collection._job(conn,row["id"])
                    public["collection"]={"state":job["state"],"binding_hash":job["binding_hash"],
                        "n_expected":job["binding"]["n_expected"],"n_recorded":conn.execute(
                            "SELECT count(*) FROM research_collection_outcomes WHERE study=?",(row["id"],)).fetchone()[0]}
                if row["state"]=="planned":
                    from . import buyer_connector as connectors
                    _,_,plan=_plan(directory,row)
                    target=plan["spec"]["target"]
                    public["connectors"]=[x["connector_id"] for x in connectors.connectors(directory)
                        if (x["target_name"],x["target_version"])==(target["name"],target["version"])]
            if actor["role"]=="admin":
                public["reviews"]=[dict(r) for r in conn.execute(
                    "SELECT reviewer,role,pending_hash,canonical_hash FROM research_reviewers WHERE study=? ORDER BY reviewer",(row["id"],))]
            if row["state"]=="analyzed":
                try:
                    report=_report(directory,conn,row)
                    public["summary"]=report["summary"]
                except (ValueError,RuntimeError,OSError,KeyError,TypeError):
                    public["state"]="unavailable"
            result.append(public)
        return result
