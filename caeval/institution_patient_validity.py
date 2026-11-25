"""Independent patient-world validity review, separate from product performance.

Review the declared and effective worlds before any candidate run when
possible. Does not sign the case pack, rewrite scores or elevate claims.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict

from . import buyer_agent as buyer, buyer_manual as manual, buyer_plan, manifest
from . import institution_access as access, institution_conversation as conversation
from . import institution_patient_review as transcript
from .patient.session import STRESS_TESTS
from .patient.stress import applicable, prepare as transform

SCHEMA="patient-world-validity/1"
MAX_HANDOFFS=100
MAX_ITEMS=200
MAX_PACKET_BYTES=2*1024*1024
MAX_NOTE=1000
QUESTIONS=("plausible_world","appropriate_reference","realistic_disclosure","valid_stress_condition")
CHOICES=("yes","no","cannot_judge")
DEFINITIONS=("caeval/patient/world.py","caeval/patient/stress.py","caeval/patient/session.py",
             "caeval/patient/scoring.py","caeval/patient/determinacy.py")


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS patient_validity_handoffs(
        id TEXT PRIMARY KEY, pack_id TEXT NOT NULL, source_digest TEXT NOT NULL UNIQUE,
        binding TEXT NOT NULL, digest TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS patient_validity_roster(
        handoff TEXT NOT NULL REFERENCES patient_validity_handoffs(id),
        reviewer TEXT NOT NULL REFERENCES users(id), PRIMARY KEY(handoff,reviewer))""")
    db.execute("""CREATE TABLE IF NOT EXISTS patient_validity_submissions(
        handoff TEXT NOT NULL, reviewer TEXT NOT NULL, payload TEXT NOT NULL,
        digest TEXT NOT NULL, PRIMARY KEY(handoff,reviewer),
        FOREIGN KEY(handoff,reviewer) REFERENCES patient_validity_roster(handoff,reviewer))""")
    db.execute("""CREATE TABLE IF NOT EXISTS patient_validity_attestations(
        handoff TEXT NOT NULL, reviewer TEXT NOT NULL, digest TEXT NOT NULL,
        administrator TEXT NOT NULL REFERENCES users(id), PRIMARY KEY(handoff,reviewer),
        FOREIGN KEY(handoff,reviewer) REFERENCES patient_validity_submissions(handoff,reviewer))""")


def _source(directory,pack_id):
    cases,desc,files,_=conversation._pack(directory,pack_id)
    defs={k:manifest.hash_file(manifest.definition_file(k)) for k in DEFINITIONS}
    if not all(defs.values()):
        raise buyer.BuyerError("patient validity definitions missing")
    items={}
    for case in cases:
        declared=[asdict(w) for w in case.worlds]
        for world in case.worlds:
            for stress in sorted(STRESS_TESTS):
                ok,_=applicable(case,world.world_id,stress)
                if not ok:
                    continue
                effective,trajectory,applied=transform(case,world.world_id,stress)
                key=buyer_plan._digest([case.case_id,world.world_id,stress])
                if key in items:
                    raise buyer.BuyerError("duplicate patient validity item")
                items[key]={"case_id":case.case_id,"world_id":world.world_id,"stress":stress,
                            "opening_message":case.opening_message,"specialty":case.specialty,
                            "population":case.population,"language":case.language,
                            "declared_worlds":declared,"source_world":asdict(world),
                            "effective_world":asdict(effective),
                            "trajectory":asdict(trajectory) if trajectory else None,
                            "applied":applied}
    items=json.loads(buyer_plan._canonical(items))
    if not 1<=len(items)<=MAX_ITEMS or len(buyer_plan._canonical(items))>MAX_PACKET_BYTES:
        raise buyer.BuyerError("complete validity inventory exceeds review limits")
    source={"pack_id":pack_id,"descriptor":desc,"file_sha256":{k:conversation._sha(v) for k,v in files.items()},
            "definitions":defs,"inventory_hash":buyer_plan._digest(items),"n_items":len(items)}
    return json.loads(buyer_plan._canonical(source)),items


def _separate_transcript_rosters(db,source,reviewers):
    """Do not disclose hidden reference facts to previously assigned reviewers."""
    transcript._schema(db)
    conversation._schema(db)
    for row in db.execute("SELECT id,job,binding,digest FROM patient_review_handoffs"):
        b=json.loads(row["binding"])
        ids={r[0] for r in db.execute("SELECT reviewer FROM patient_review_roster WHERE handoff=?",(row["id"],))}
        if buyer_plan._digest(b)!=row["digest"] or set(b["reviewers"])!=ids:
            raise access.AccessDenied("transcript roster integrity mismatch")
        pack=conversation._row(db,row["job"])["binding"]["pack_descriptor"]
        if (pack["content_hash"]==source["descriptor"]["content_hash"]
                or pack["pack_id"]==source["descriptor"]["pack_id"]) and ids.intersection(reviewers):
            raise access.AccessDenied("reviewer previously assigned to transcripts for this pack")


def create(directory,actor,pack_id,reviewers,*,confirmed=False):
    if confirmed is not True or not isinstance(reviewers,list) or not 2<=len(reviewers)<=5:
        raise access.AccessDenied("two to five predeclared independent validity reviewers required")
    ids=sorted(manual._identifier(x) for x in reviewers)
    if len(set(ids))!=len(ids) or actor["user_id"] in ids:
        raise access.AccessDenied("distinct coordinator and reviewer accounts required")
    with access.db(directory) as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        conversation._actor(db,actor,"admin")
        if not all(transcript._reviewer(db,rid) for rid in ids):
            raise access.AccessDenied("active physician reviewer accounts required")
        source,items=_source(directory,pack_id)
        _separate_transcript_rosters(db,source,ids)
        source_hash=buyer_plan._digest(source)
        if db.execute("SELECT 1 FROM patient_validity_handoffs WHERE source_digest=?",(source_hash,)).fetchone():
            raise access.AccessDenied("validity roster already frozen for this exact source")
        if db.execute("SELECT count(*) FROM patient_validity_handoffs").fetchone()[0]>=MAX_HANDOFFS:
            raise access.AccessDenied("validity handoff limit")
        ident="world-review-"+uuid.uuid4().hex
        b={"schema":SCHEMA,"id":ident,"coordinator":actor["user_id"],"source":source,
           "reviewers":ids,"questions":list(QUESTIONS),"decision_grade":False}
        digest=buyer_plan._digest(b)
        db.execute("INSERT INTO patient_validity_handoffs VALUES(?,?,?,?,?)",
                   (ident,pack_id,source_hash,json.dumps(b),digest))
        db.executemany("INSERT INTO patient_validity_roster VALUES(?,?)",[(ident,rid) for rid in ids])
        access._audit(db,actor["user_id"],"patient-world-validity-roster-frozen",ident)
    return {"id":ident,"binding_hash":digest,"n_items":len(items),"decision_grade":False}


def _load(directory,db,ident,reviewer=None):
    manual._identifier(ident)
    if reviewer is not None and not db.execute("SELECT 1 FROM patient_validity_roster WHERE handoff=? AND reviewer=?",
                                              (ident,reviewer)).fetchone():
        raise access.AccessDenied("validity review not assigned")
    r=db.execute("SELECT * FROM patient_validity_handoffs WHERE id=?",(ident,)).fetchone()
    if not r:
        raise access.AccessDenied("unknown validity review")
    b=json.loads(r["binding"])
    ids=sorted(x[0] for x in db.execute("SELECT reviewer FROM patient_validity_roster WHERE handoff=?",(ident,)))
    if (b["schema"]!=SCHEMA or b["id"]!=ident or b["reviewers"]!=ids
            or b["questions"]!=list(QUESTIONS) or b["decision_grade"] is not False
            or buyer_plan._digest(b)!=r["digest"] or buyer_plan._digest(b["source"])!=r["source_digest"]
            or b["source"]["pack_id"]!=r["pack_id"] or b["coordinator"] in ids):
        raise access.AccessDenied("validity binding changed")
    source,items=_source(directory,r["pack_id"])
    if source!=b["source"]:
        raise access.AccessDenied("patient world source changed")
    return b,r["digest"],items


def _packet(b,digest,items,rid):
    slots=transcript._slots(digest,rid,items)
    return {"schema":SCHEMA,"handoff":b["id"],"binding_hash":digest,
            "items":[{"slot":slot,**{k:v for k,v in items[key].items() if k not in ("case_id","world_id")}}
                     for slot,key in sorted(slots.items())]}


def _answers(rows,slots):
    if not isinstance(rows,list) or len(rows)!=len(slots):
        raise access.AccessDenied("one explicit answer per validity item required")
    seen=set()
    for row in rows:
        if (not isinstance(row,dict) or set(row)!={"slot","notes",*QUESTIONS}
                or not isinstance(row["slot"],str) or row["slot"] not in slots or row["slot"] in seen
                or not isinstance(row["notes"],str) or len(row["notes"])>MAX_NOTE
                or any(row[q] not in CHOICES for q in QUESTIONS)):
            raise access.AccessDenied("invalid or duplicate validity response")
        seen.add(row["slot"])
    return sorted(rows,key=lambda r:r["slot"])


def _submission(db,b,digest,items,rid):
    r=db.execute("SELECT payload,digest FROM patient_validity_submissions WHERE handoff=? AND reviewer=?",
                 (b["id"],rid)).fetchone()
    if not r:
        return None
    obj=json.loads(r["payload"])
    if (set(obj)!={"schema","binding_hash","reviewer","answers","independent_review_confirmed"}
            or obj["schema"]!=SCHEMA or obj["binding_hash"]!=digest or obj["reviewer"]!=rid
            or obj["independent_review_confirmed"] is not True or buyer_plan._digest(obj)!=r["digest"]
            or _answers(obj["answers"],transcript._slots(digest,rid,items))!=obj["answers"]):
        raise access.AccessDenied("locked validity response changed")
    att=db.execute("SELECT * FROM patient_validity_attestations WHERE handoff=? AND reviewer=?",(b["id"],rid)).fetchone()
    if att and (att["digest"]!=r["digest"] or att["administrator"] in b["reviewers"]):
        raise access.AccessDenied("validity attestation changed")
    return obj,r["digest"],bool(att)


def template(directory,actor,ident):
    with access.db(directory) as db:
        _schema(db)
        conversation._actor(db,actor,"reviewer")
        rid=actor["user_id"]
        b,digest,items=_load(directory,db,ident,rid)
        if _submission(db,b,digest,items,rid):
            raise access.AccessDenied("validity review locked")
        return _packet(b,digest,items,rid)


def submit(directory,actor,ident,packet_digest,answers,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("independent validity review confirmation required")
    with access.db(directory) as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        conversation._actor(db,actor,"reviewer")
        rid=actor["user_id"]
        b,digest,items=_load(directory,db,ident,rid)
        if _submission(db,b,digest,items,rid) or packet_digest!=buyer_plan._digest(_packet(b,digest,items,rid)):
            raise access.AccessDenied("locked review or stale packet")
        obj={"schema":SCHEMA,"binding_hash":digest,"reviewer":rid,"independent_review_confirmed":True,
             "answers":_answers(answers,transcript._slots(digest,rid,items))}
        h=buyer_plan._digest(obj)
        db.execute("INSERT INTO patient_validity_submissions VALUES(?,?,?,?)",(ident,rid,json.dumps(obj),h))
        access._audit(db,rid,"patient-world-validity-locked",ident)
    return {"digest":h,"decision_grade":False}


def attest(directory,actor,ident,rid,submission_digest,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("verify clinical expertise and independence externally")
    with access.db(directory) as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        conversation._actor(db,actor,"admin")
        b,digest,items=_load(directory,db,ident)
        if rid not in b["reviewers"] or not transcript._reviewer(db,rid):
            raise access.AccessDenied("active assigned reviewer required")
        rec=_submission(db,b,digest,items,rid)
        if not rec or rec[1]!=submission_digest or rec[2]:
            raise access.AccessDenied("exact pending validity response required")
        db.execute("INSERT INTO patient_validity_attestations VALUES(?,?,?,?)",(ident,rid,submission_digest,actor["user_id"]))
        access._audit(db,actor["user_id"],"patient-world-validity-attested",ident)


def overview(directory,actor):
    with access.db(directory) as db:
        _schema(db)
        conversation._actor(db,actor,"admin","reviewer")
        rid=actor["user_id"] if actor["role"]=="reviewer" else None
        ids=[r[0] for r in (db.execute("SELECT handoff FROM patient_validity_roster WHERE reviewer=? ORDER BY handoff",(rid,))
                            if rid else db.execute("SELECT id FROM patient_validity_handoffs ORDER BY id"))]
        result=[]
        for ident in ids:
            try:
                b,digest,items=_load(directory,db,ident,rid)
                if rid:
                    result.append({"id":ident,"n_items":len(items),"locked":bool(_submission(db,b,digest,items,rid))})
                else:
                    rows=[]
                    for user in b["reviewers"]:
                        rec=_submission(db,b,digest,items,user)
                        rows.append({"reviewer":user,"state":"attested" if rec and rec[2] else "pending" if rec else "awaiting",
                                     "digest":rec[1] if rec else None})
                    result.append({"id":ident,"pack_id":b["source"]["pack_id"],"n_items":len(items),"roster":rows,
                                   "ready":all(r["state"]=="attested" for r in rows)})
            except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError):
                result.append({"id":ident,"unavailable":True})
    return result


def _summarize(items,records):
    summary={q:{"unanimous_yes":0,"any_no":0,"indeterminate_without_no":0} for q in QUESTIONS}
    findings=[]
    for key,item in sorted(items.items()):
        problems={}
        for q in QUESTIONS:
            votes=[r[key][q] for r in records]
            state="any_no" if "no" in votes else "indeterminate_without_no" if "cannot_judge" in votes else "unanimous_yes"
            summary[q][state]+=1
            if state!="unanimous_yes":
                problems[q]={v:votes.count(v) for v in CHOICES}
        if problems:
            findings.append({"case_id":item["case_id"],"world_id":item["world_id"],"stress":item["stress"],"findings":problems})
    return summary,findings


def _job_link(directory,db,b,job):
    conversation._schema(db)
    transcript._schema(db)
    row=conversation._row(db,job)
    if row["status"]!="completed" or conversation._verify(directory,row)!=row["result"]:
        raise buyer.BuyerError("verified completed patient job required")
    src=b["source"]
    jb=row["binding"]
    if (src["pack_id"]!=jb["pack_id"] or src["descriptor"]!=jb["pack_descriptor"]
            or src["file_sha256"]!={k:v for k,v in jb["file_sha256"].items() if k.startswith("pack/")}
            or any(jb["definitions"].get(k)!=v for k,v in src["definitions"].items())):
        raise access.AccessDenied("review does not match exact patient job pack and definitions")
    other=db.execute("SELECT id FROM patient_review_handoffs WHERE job=?",(job,)).fetchone()
    separate=None
    if other:
        tb,_,_=transcript._load(directory,db,other["id"])
        separate=not bool(set(tb["reviewers"])&set(b["reviewers"]))
        if not separate:
            raise access.AccessDenied("validity and transcript reviewer rosters overlap")
    return {"job":job,"job_binding":row["digest"],"manifest":row["result"]["manifest_hash"],
            "separate_transcript_roster":separate}


def analyze(directory,actor,ident,job=None):
    with access.db(directory) as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        conversation._actor(db,actor,"admin")
        b,digest,items=_load(directory,db,ident)
        records=[]
        hashes={}
        for rid in b["reviewers"]:
            rec=_submission(db,b,digest,items,rid)
            if not rec or not rec[2]:
                raise access.AccessDenied("all independent validity responses must be attested")
            slots=transcript._slots(digest,rid,items)
            records.append({slots[r["slot"]]:r for r in rec[0]["answers"]})
            hashes[rid]=rec[1]
        summary,findings=_summarize(items,records)
        link=_job_link(directory,db,b,job) if job else None
        if _load(directory,db,ident)[0]!=b:
            raise access.AccessDenied("validity source changed")
        result={"schema":SCHEMA,"binding_hash":digest,"source":b["source"],"submission_digests":hashes,
                "n_reviewers":len(records),"summary":summary,"flagged_items":findings,
                "all_items_unanimously_supported":not bool(findings),"job_link":link,"decision_grade":False,
                "interpretation":"Independent patient-world reference review; not pack signing, clinical safety certification, score modification or claim promotion."}
        result["digest"]=buyer_plan._digest(result)
        access._audit(db,actor["user_id"],"patient-world-validity-analyzed",ident)
        return result
