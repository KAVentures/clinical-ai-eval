"""Frozen institutional multi-module research programmes under a saved buyer scope.

Programmes coordinate existing planned studies. They do not confer canonical
suite execution, authorize collection, or provide a procurement decision.
"""
import json
import uuid

from . import (buyer_plan, buyer_manual as manual, institution_access as access,
               institution_research as studio, research_buyer_coverage as bridge,
               research_review_protocol as protocol, experimental_evidence as evidence,
               formal_review as formal)

SCHEMA = "institution-research-program/1"
EVENT = "research-program-frozen"
LIMIT = 100
LIMITATIONS = [
    "Programme scope is a buyer preview, not a validated canonical run project.",
    "Local recorded ordering does not prove external preregistration or blinding.",
    "Experimental reports do not satisfy canonical requirements.",
    "No overall clinical safety score, procurement recommendation or deployment approval.",
]


def _schema(conn):
    bridge._schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS research_programs(
        id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(id),
        plan_id TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,
        audit_seq INTEGER NOT NULL UNIQUE)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS research_program_members(
        program_id TEXT NOT NULL REFERENCES research_programs(id),
        study_id TEXT NOT NULL UNIQUE REFERENCES research_studies(id),
        kind TEXT NOT NULL, PRIMARY KEY(program_id,kind))""")


def _manager(conn,actor,row):
    studio._actor(conn,actor,"admin","operator")
    if actor["role"]!="admin" and row["owner"]!=actor["user_id"]:
        raise access.AccessDenied("programme owner or administrator required")


def _related(bp):
    required={x["suite"] for x in bp["plan"]["selection"]["required_suites"]}
    return {k:sorted(required.intersection(v)) for k,v in bridge.MODULE_SUITES.items()
            if required.intersection(v)}


def _link_record(conn, directory, bp, row, actor):
    existing=conn.execute("SELECT * FROM buyer_research_links WHERE study=?",(row["id"],)).fetchone()
    if existing:
        link=bridge._read_link(directory,conn,bp,existing,row)
        if not link["prospective_link"]:
            raise access.AccessDenied("programme requires a planned buyer association")
        return link["link_digest"]
    material=bridge._material(directory,row,bp)
    material.update(link_stage="planned",linked_by=actor["user_id"])
    digest=buyer_plan._digest(material)
    conn.execute("INSERT INTO buyer_research_links VALUES(?,?,?,?)",
                 (row["id"],bp["plan_id"],buyer_plan._canonical(material).decode("utf-8"),digest))
    access._audit(conn,actor["user_id"],"buyer-research-study-linked",row["id"])
    return digest


def freeze(directory,actor,plan_id,studies,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit programme scope approval required")
    if not isinstance(studies,dict) or not studies or set(studies)-set(bridge.MODULE_SUITES):
        raise access.AccessDenied("select at least one known study type")
    if any(not isinstance(v,str) or not v for v in studies.values()) or len(set(studies.values()))!=len(studies):
        raise access.AccessDenied("distinct study identifiers required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        studio._actor(conn,actor,"admin","operator")
        bp=bridge._buyer_plan(directory,plan_id)
        related=_related(bp)
        if set(studies)-set(related):
            raise access.AccessDenied("programme contains a module unrelated to the buyer scope")
        if conn.execute("SELECT count(*) FROM research_programs").fetchone()[0]>=LIMIT:
            raise access.AccessDenied("programme limit reached")
        members={}
        # All writes are in one SQLite transaction. Existing study files are read only.
        for kind,sid in sorted(studies.items()):
            row=studio._row(conn,sid)
            studio._manager(conn,actor,row)
            if (row["owner"]!=actor["user_id"] or row["kind"]!=kind or row["state"]!="planned"
                    or protocol._exposure(conn,sid)):
                raise access.AccessDenied("own matching planned study with no recorded candidate exposure required")
            if conn.execute("SELECT 1 FROM research_program_members WHERE study_id=?",(sid,)).fetchone():
                raise access.AccessDenied("study already belongs to a programme")
            material=bridge._material(directory,row,bp)
            _,_,plan=studio._plan(directory,row)
            registration=protocol.read(conn,row)
            members[kind]={"study_id":sid,"plan_hash":plan["plan_hash"],
                           "link_digest":_link_record(conn,directory,bp,row,actor),
                           "synthetic":material["synthetic"],"related_suites":related[kind],
                           "review_protocol_at_freeze":registration["digest"] if registration else None}
        ident="program-"+uuid.uuid4().hex
        payload={"schema":SCHEMA,"id":ident,"owner":actor["user_id"],"buyer_plan_id":plan_id,
                 "vendor_id":bp["vendor_id"],"target":{k:bp["plan"]["target"][k] for k in ("name","version")},
                 "members":members,"unplanned_related_modules":sorted(set(related)-set(members)),
                 "decision_grade":False}
        digest=buyer_plan._digest(payload)
        access._audit(conn,actor["user_id"],EVENT,ident)
        seq=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO research_programs VALUES(?,?,?,?,?,?)",
                     (ident,actor["user_id"],plan_id,buyer_plan._canonical(payload).decode(),digest,seq))
        for kind,m in members.items():
            conn.execute("INSERT INTO research_program_members VALUES(?,?,?)",(ident,m["study_id"],kind))
        return ident


def _load(directory,conn,actor,identifier):
    row=conn.execute("SELECT * FROM research_programs WHERE id=?",(manual._identifier(identifier),)).fetchone()
    if not row:
        raise access.AccessDenied("unknown research programme")
    _manager(conn,actor,row)
    payload=json.loads(row["payload"])
    if (not isinstance(payload,dict) or payload.get("schema")!=SCHEMA
            or payload.get("decision_grade") is not False or payload.get("id")!=identifier
            or payload.get("owner")!=row["owner"] or payload.get("buyer_plan_id")!=row["plan_id"]
            or buyer_plan._digest(payload)!=row["digest"]):
        raise access.AccessDenied("programme record changed")
    ev=conn.execute("SELECT seq,actor FROM audit WHERE object_id=? AND action=? AND outcome='ok'",
                    (identifier,EVENT)).fetchall()
    if len(ev)!=1 or ev[0]["seq"]!=row["audit_seq"] or ev[0]["actor"]!=row["owner"]:
        raise access.AccessDenied("programme freeze audit changed")
    bp=bridge._buyer_plan(directory,row["plan_id"])
    related=_related(bp)
    if (payload["vendor_id"]!=bp["vendor_id"] or payload["target"]!={k:bp["plan"]["target"][k] for k in ("name","version")}
            or not payload["members"] or set(payload["members"])-set(related)
            or payload["unplanned_related_modules"]!=sorted(set(related)-set(payload["members"]))):
        raise access.AccessDenied("programme scope changed")
    inventory={m["kind"]:m["study_id"] for m in conn.execute(
        "SELECT kind,study_id FROM research_program_members WHERE program_id=?",(identifier,))}
    if inventory!={k:m["study_id"] for k,m in payload["members"].items()}:
        raise access.AccessDenied("programme study inventory changed")
    return dict(row),payload,bp,related


def _member(directory,conn,record,payload,bp,related,kind,m,*,replay=False):
    row=studio._row(conn,m["study_id"])
    if (row["owner"]!=record["owner"] or row["kind"]!=kind or row["plan_hash"]!=m["plan_hash"]
            or m["related_suites"]!=related[kind] or protocol._exposure(conn,row["id"],record["audit_seq"])):
        raise access.AccessDenied("programme member or local ordering changed")
    association=conn.execute("SELECT * FROM buyer_research_links WHERE study=?",(row["id"],)).fetchone()
    if not association:
        raise access.AccessDenied("programme association missing")
    link=bridge._read_link(directory,conn,bp,association,row)
    if (link["link_digest"]!=m["link_digest"] or link["synthetic"] is not m["synthetic"]
            or not link["prospective_link"]):
        raise access.AccessDenied("programme association changed")
    registration=protocol.read(conn,row)
    if m["review_protocol_at_freeze"] is not None and (
            not registration or registration["digest"]!=m["review_protocol_at_freeze"]):
        raise access.AccessDenied("frozen review protocol changed")
    result={"study_id":row["id"],"kind":kind,"state":row["state"],"synthetic":m["synthetic"],
            "plan_hash":m["plan_hash"],"related_suites":related[kind],
            "review_protocol_status":protocol.status(conn,row),
            "report_digest":link["report_hash"],"replay_verified":False,"decision_grade":False}
    if link["report_hash"]:
        result["summary"]=link["summary"]
        if replay:
            root,mod,plan=studio._plan(directory,row)
            report=studio._report(directory,conn,row)
            responses=studio._verified_collection(directory,conn,row,plan,mod,studio._collected(root,row))
            binding,records=None,{}
            if mod is not studio.decision:
                _,_,_,(binding,_,issued)=studio._review(directory,conn,row)
                records={rid:formal._json(root/"review"/"submissions"/(rid+".json")) for rid in issued}
            package=evidence.build(kind,plan,responses,binding,records,report)
            result.update(replay_verified=True,package_digest=package["digest"])
    return result


def _status(directory,conn,actor,identifier,*,replay=False):
    record,payload,bp,related=_load(directory,conn,actor,identifier)
    members=[]
    for kind,m in sorted(payload["members"].items()):
        try:
            members.append(_member(directory,conn,record,payload,bp,related,kind,m,replay=replay))
        except (ValueError,RuntimeError,OSError,KeyError,TypeError,AttributeError,IndexError):
            members.append({"study_id":m["study_id"],"kind":kind,"state":"unavailable",
                            "report_digest":None,"replay_verified":False,"decision_grade":False})
    coverage=[]
    for s in bp["plan"]["selection"]["required_suites"]:
        sid=s["suite"]
        planned=[k for k,m in payload["members"].items() if sid in m["related_suites"]]
        coverage.append({"suite":sid,"canonical_runnable":s["implemented"],
                         "canonical_blocked_reason":s["blocked_reason"],
                         "canonical_requirement_satisfied":False,
                         "planned_modules":sorted(planned),
                         "integrity_checked_reports":[m["kind"] for m in members if m["kind"] in planned and m["report_digest"]],
                         "replayed_reports":[m["kind"] for m in members if m["kind"] in planned and m["replay_verified"]]})
    doc={"schema":"institution-research-program-status/1","program_id":identifier,
         "program_digest":record["digest"],"buyer_plan_id":record["plan_id"],"target":payload["target"],
         "members":members,"coverage":coverage,"unplanned_related_modules":payload["unplanned_related_modules"],
         "all_planned_reports_available":all(m["report_digest"] for m in members),
         "all_planned_reports_replayed":all(m["replay_verified"] for m in members),
         "decision_grade":False,"limitations":LIMITATIONS[:]}
    return {**doc,"digest":buyer_plan._digest(doc)}


def status(directory,actor,identifier):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        return _status(directory,conn,actor,identifier)


def dossier(directory,actor,identifier,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit approval for experimental summary dossier export required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        studio._actor(conn,actor,"admin")
        doc=_status(directory,conn,actor,identifier,replay=True)
        # A partial export remains useful, but never implies all planned studies replayed.
        evidence.encode(doc)
        access._audit(conn,actor["user_id"],"research-program-dossier-exported",identifier)
        return doc


def inventory(directory,actor,plan_id):
    with access.db(directory) as conn:
        _schema(conn)
        studio._actor(conn,actor,"admin","operator")
        bp=bridge._buyer_plan(directory,plan_id)
        programmes=[{"id":r["id"],"owner":r["owner"]} for r in conn.execute(
            "SELECT id,owner FROM research_programs WHERE plan_id=? ORDER BY rowid DESC",(plan_id,))
            if actor["role"]=="admin" or r["owner"]==actor["user_id"]]
        choices={k:[] for k in _related(bp)}
        for r in conn.execute("SELECT * FROM research_studies WHERE owner=? AND state='planned'",(actor["user_id"],)):
            row=dict(r)
            if row["kind"] not in choices or protocol._exposure(conn,row["id"]):
                continue
            if conn.execute("SELECT 1 FROM research_program_members WHERE study_id=?",(row["id"],)).fetchone():
                continue
            try:
                bridge._material(directory,row,bp)
                link=conn.execute("SELECT * FROM buyer_research_links WHERE study=?",(row["id"],)).fetchone()
                if link and not bridge._read_link(directory,conn,bp,link,row)["prospective_link"]:
                    continue
                choices[row["kind"]].append(row["id"])
            except (ValueError,RuntimeError,OSError,KeyError,TypeError,AttributeError):
                continue
        return {"plan_id":plan_id,"target":bp["plan"]["target"],"programmes":programmes,"choices":choices}
