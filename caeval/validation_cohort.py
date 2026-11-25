"""Prospective full-variant clinician validation for a fixed staged pack.

A separate experimental derivative, not canonical adjudication. Freeze before
local exchange creation; review every generated variant in the chosen family.
No claim of population representativeness or external non-exposure.
"""
import json
from . import (buyer_plan as plans,buyer_manual as manual,buyer_jobs as jobs,
               buyer_research as research,buyer_agent as buyer,
               institution_access as access,institution_setup as setup,
               formal_review as formal,manifest,pipeline,review_calibration)

EVENT="validation-cohort-frozen"
LINK="validation-cohort-job-linked"
SUBMIT="validation-cohort-review-locked"
ATTEST="validation-cohort-review-attested"
MAX_COHORTS=100
MAX_ITEMS=1000


def _schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS validation_cohorts(
        id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL,
        audit_seq INTEGER NOT NULL UNIQUE)""")
    c.execute("""CREATE TABLE IF NOT EXISTS validation_cohort_jobs(
        cohort TEXT PRIMARY KEY REFERENCES validation_cohorts(id), job TEXT NOT NULL,
        manifest TEXT NOT NULL, audit_seq INTEGER NOT NULL UNIQUE)""")
    c.execute("""CREATE TABLE IF NOT EXISTS validation_cohort_reviews(
        cohort TEXT NOT NULL REFERENCES validation_cohorts(id), reviewer TEXT NOT NULL REFERENCES users(id),
        payload TEXT NOT NULL,digest TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE,
        PRIMARY KEY(cohort,reviewer))""")
    c.execute("""CREATE TABLE IF NOT EXISTS validation_cohort_attestations(
        cohort TEXT NOT NULL, reviewer TEXT NOT NULL, digest TEXT NOT NULL,
        audit_seq INTEGER NOT NULL UNIQUE,PRIMARY KEY(cohort,reviewer),
        FOREIGN KEY(cohort,reviewer) REFERENCES validation_cohort_reviews(cohort,reviewer))""")


def _actor(c,actor,*roles):
    access.require(actor,*roles)
    u=c.execute("SELECT role,enabled FROM users WHERE id=?",(actor["user_id"],)).fetchone()
    if not u or not u["enabled"] or u["role"]!=actor["role"]:
        raise access.AccessDenied("active validation account required")


def _event(c,action,prefix,expected=None):
    rows=c.execute("SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok' AND substr(object_id,1,?)=?",
                   (action,len(prefix),prefix)).fetchall()
    if expected is None:
        if rows:raise access.AccessDenied("audited validation record missing")
    elif len(rows)!=1 or rows[0]["seq"]!=expected[0] or rows[0]["object_id"]!=prefix+expected[1] or (len(expected)==3 and rows[0]["actor"]!=expected[2]):
        raise access.AccessDenied("validation audit mismatch")


def _write_audit(c,actor,action,obj):
    access._audit(c,actor,action,obj)
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def _material(directory,plan_id,pack_id,family,reviewers,validity_reviewer):
    exchange=manual._build(directory,plan_id,pack_id)
    if family not in exchange["family_ids"]:
        raise buyer.BuyerError("choose a runnable canonical text family")
    reviewers,validity_reviewer=formal._roster(reviewers,validity_reviewer)
    cases,_=manual._pack(directory,pack_id)
    rows=pipeline.generate_responses(lambda _: "(placeholder)",pipeline.load_family(family),cases)
    ids=sorted(r["cell_id"] for r in rows if r["kind"]=="variant")
    if not 1<=len(ids)<=MAX_ITEMS or len(ids)!=len(set(ids)):
        raise buyer.BuyerError("invalid or oversized full variant cohort")
    if set(r["cell_id"] for r in rows)!={k for k,v in exchange["slots"].items() if family in v["families"]}:
        raise buyer.BuyerError("generated family differs from exchange")
    ident=plans._digest([plan_id,pack_id,family])
    d={"schema":"prospective-validation-cohort/1","id":ident,"plan_id":plan_id,
       "pack_id":pack_id,"family_id":family,"exchange_id":exchange["exchange_id"],
       "pack_content_hash":exchange["pack_content_hash"],"reviewers":reviewers,
       "validity_reviewer":validity_reviewer,"variant_ids":ids,
       "sampling":"all_generated_variants_in_fixed_family_and_pack",
       "external_nonexposure_attested":True,"decision_grade":False}
    return d


def _read(c,ident):
    manual._identifier(ident,digest=True)
    r=c.execute("SELECT * FROM validation_cohorts WHERE id=?",(ident,)).fetchone()
    if not r:
        _event(c,EVENT,ident+":")
        return None
    d=manual._parse(r["payload"].encode())
    if d["id"]!=ident or plans._digest(d)!=r["digest"]:
        raise access.AccessDenied("cohort content changed")
    _event(c,EVENT,ident+":",(r["audit_seq"],r["digest"]))
    return d


def _roster(d):
    return d["reviewers"]+[d["validity_reviewer"]]


def preview(directory,actor,plan_id,pack_id,family,reviewers,validity_reviewer):
    with access.db(directory) as c:
        _schema(c)
        _actor(c,actor,"admin")
        d=_material(directory,plan_id,pack_id,family,reviewers,validity_reviewer)
        for rid in _roster(d):
            _actor(c,{"role":"reviewer","user_id":rid},"reviewer")
        return {"cohort":d,"digest":plans._digest(d)}


def freeze(directory,actor,plan_id,pack_id,family,reviewers,validity_reviewer,digest,*,confirmed=False):
    if confirmed is not True:raise access.AccessDenied("explicit prospective cohort confirmation required")
    with access.db(directory) as c:
        _schema(c)
        c.execute("BEGIN IMMEDIATE")
        _actor(c,actor,"admin")
        d=_material(directory,plan_id,pack_id,family,reviewers,validity_reviewer)
        if plans._digest(d)!=digest:raise access.AccessDenied("cohort differs from preview")
        for rid in _roster(d):
            _actor(c,{"role":"reviewer","user_id":rid},"reviewer")
        prior=_read(c,d["id"])
        if prior is not None:
            if prior!=d:raise access.AccessDenied("cannot replace frozen cohort")
            return d["id"]
        folder=manual._directory(directory,"manual-exchanges")
        # Refuse any earlier exchange for this exact plan/pack, not merely the
        # current digest; a changed pack cannot erase prior local exposure.
        if folder.exists():
            for p in folder.iterdir():
                if p.suffix==".json":
                    old=manual._read(p)
                    if old.get("plan_id")==plan_id and old.get("pack_id")==pack_id:
                        raise access.AccessDenied("freeze before local exchange preparation")
        if c.execute("SELECT count(*) FROM validation_cohorts").fetchone()[0]>=MAX_COHORTS:
            raise access.AccessDenied("cohort limit reached")
        seq=_write_audit(c,actor["user_id"],EVENT,d["id"]+":"+digest)
        c.execute("INSERT INTO validation_cohorts VALUES(?,?,?,?)",
                  (d["id"],json.dumps(d),digest,seq))
        return d["id"]


def _source(directory,d,job_id):
    manual._identifier(job_id)
    job=jobs.status(directory,job_id)
    if job["status"]!="completed":raise access.AccessDenied("verified completed job required")
    folder=jobs._job_path(directory,job_id)
    b=manual._read(folder/"binding.json")
    if b["plan_id"]!=d["plan_id"] or b["exchange_id"]!=d["exchange_id"] or b["pack_content_hash"]!=d["pack_content_hash"]:
        raise access.AccessDenied("job differs from prospective cohort")
    packages=[p for p in job["result"]["packages"] if p["family_id"]==d["family_id"]]
    if len(packages)!=1:raise access.AccessDenied("exact family package required")
    pkg=packages[0]
    ws=folder/"out"/("run_"+d["family_id"])
    meta=research._preflight(ws)
    if meta["manifest_hash"]!=pkg["manifest_hash"] or manifest.verify_manifest(ws)["verdict"]!=manifest.VALID:
        raise access.AccessDenied("source package changed")
    def rows(key):
        items=[json.loads(x) for x in research.verified_bytes(ws,meta,key).splitlines() if x.strip()]
        out={r["cell_id"]:r for r in items}
        if len(items)!=len(out):raise access.AccessDenied("duplicate source cells")
        return out
    responses,results=rows("responses"),rows("results")
    if set(responses)!=set(results) or sorted(k for k,r in responses.items() if r["kind"]=="variant")!=d["variant_ids"]:
        raise access.AccessDenied("full variant inventory changed")
    for cid,r in responses.items():
        if any(r[k]!=results[cid][k] for k in ("cell_id","item_id","kind","severity","input_text","response_text")):
            raise access.AccessDenied("scored content differs from candidate response")
    panel=json.loads(research.verified_bytes(ws,meta,"run_meta"))["panel"]
    if jobs.status(directory,job_id)!=job:raise access.AccessDenied("source changed during capture")
    return pkg["manifest_hash"],responses,results,panel


def _link(c,d):
    ident=d["id"]
    r=c.execute("SELECT * FROM validation_cohort_jobs WHERE cohort=?",(ident,)).fetchone()
    if not r:
        _event(c,LINK,ident+":")
        return None
    _event(c,LINK,ident+":",(r["audit_seq"],plans._digest([r["job"],r["manifest"]])))
    return dict(r)


def attach(directory,actor,ident,job_id,*,confirmed=False):
    if confirmed is not True:raise access.AccessDenied("explicit source confirmation required")
    with access.db(directory) as c:
        _schema(c)
        c.execute("BEGIN IMMEDIATE")
        _actor(c,actor,"admin")
        d=_read(c,ident)
        if not d:raise access.AccessDenied("unknown cohort")
        digest,*_=_source(directory,d,job_id)
        prior=_link(c,d)
        if prior:
            if (prior["job"],prior["manifest"])!=(job_id,digest):raise access.AccessDenied("cannot replace validation source")
        else:
            seq=_write_audit(c,actor["user_id"],LINK,ident+":"+plans._digest([job_id,digest]))
            c.execute("INSERT INTO validation_cohort_jobs VALUES(?,?,?,?)",(ident,job_id,digest,seq))
        return digest


def _context(directory,c,ident):
    d=_read(c,ident)
    if not d:raise access.AccessDenied("unknown cohort")
    link=_link(c,d)
    if not link:raise access.AccessDenied("no verified source attached")
    digest,responses,results,panel=_source(directory,d,link["job"])
    if digest!=link["manifest"]:raise access.AccessDenied("linked manifest changed")
    return d,link,responses,results,panel


def _packet(d,link,responses,rid):
    is_validity=rid==d["validity_reviewer"]
    slots={plans._digest([d["id"],link["manifest"],rid,cid]):cid for cid in d["variant_ids"]}
    items=[]
    for slot,cid in sorted(slots.items()):
        r=responses[cid]
        item={"slot":slot}
        if is_validity:
            item.update(original_case=responses[r["paired_original_cell_id"]]["input_text"],perturbed_case=r["input_text"])
        else:
            item.update(case=r["input_text"],response=r["response_text"])
        items.append(item)
    req={"schema":"prospective-cohort-review/1","cohort":d["id"],"kind":"validity" if is_validity else "safety","items":items}
    return req,slots


def packet(directory,actor,ident):
    with access.db(directory) as c:
        _schema(c)
        _actor(c,actor,"reviewer")
        d=_read(c,ident)
        rid=actor["user_id"]
        if not d or rid not in _roster(d):raise access.AccessDenied("not assigned")
        d,link,responses,_,_=_context(directory,c,ident)
        return _packet(d,link,responses,rid)[0]


def _answers(req,rows):
    fields=review_calibration.VALIDITY_FIELDS if req["kind"]=="validity" else ("verdict",)
    allowed=("yes","no","cannot_judge") if req["kind"]=="validity" else ("safe","unsafe","cannot_judge")
    if not isinstance(rows,list) or len(rows)!=len(req["items"]):raise access.AccessDenied("complete review required")
    expected={r["slot"] for r in req["items"]}
    seen=set()
    for r in rows:
        if not isinstance(r,dict) or set(r)!={"slot",*fields} or r["slot"] not in expected or r["slot"] in seen or any(r[k] not in allowed for k in fields):
            raise access.AccessDenied("invalid review answer")
        seen.add(r["slot"])
    return sorted(rows,key=lambda r:r["slot"])


def _review(c,d,link,responses,rid):
    prefix=d["id"]+":"+rid+":"
    r=c.execute("SELECT * FROM validation_cohort_reviews WHERE cohort=? AND reviewer=?",(d["id"],rid)).fetchone()
    if not r:
        _event(c,SUBMIT,prefix)
        return None
    payload=manual._parse(r["payload"].encode())
    req,_=_packet(d,link,responses,rid)
    if (payload["request_digest"]!=plans._digest(req) or payload["answers"]!=_answers(req,payload["answers"]) or plans._digest(payload)!=r["digest"]):
        raise access.AccessDenied("review binding changed")
    _event(c,SUBMIT,prefix,(r["audit_seq"],r["digest"],rid))
    return payload,r["digest"]


def submit(directory,actor,ident,request_digest,answers,*,confirmed=False):
    if confirmed is not True:raise access.AccessDenied("confirm independent review")
    with access.db(directory) as c:
        _schema(c)
        c.execute("BEGIN IMMEDIATE")
        _actor(c,actor,"reviewer")
        d=_read(c,ident)
        rid=actor["user_id"]
        if not d or rid not in _roster(d):raise access.AccessDenied("not assigned")
        d,link,responses,_,_=_context(directory,c,ident)
        req,_=_packet(d,link,responses,rid)
        if plans._digest(req)!=request_digest:raise access.AccessDenied("review packet changed")
        payload={"request_digest":request_digest,"answers":_answers(req,answers)}
        digest=plans._digest(payload)
        prior=_review(c,d,link,responses,rid)
        if prior:
            if prior!=(payload,digest):raise access.AccessDenied("cannot replace locked review")
        else:
            seq=_write_audit(c,rid,SUBMIT,ident+":"+rid+":"+digest)
            c.execute("INSERT INTO validation_cohort_reviews VALUES(?,?,?,?,?)",(ident,rid,json.dumps(payload),digest,seq))
        return digest


def attest(directory,actor,ident,rid,digest,*,confirmed=False):
    if confirmed is not True:raise access.AccessDenied("confirm verified identity, expertise and independence outside software")
    with access.db(directory) as c:
        _schema(c)
        c.execute("BEGIN IMMEDIATE")
        _actor(c,actor,"admin")
        d,link,responses,_,_=_context(directory,c,ident)
        if rid not in _roster(d):raise access.AccessDenied("unknown reviewer")
        prior=_review(c,d,link,responses,rid)
        if not prior or prior[1]!=digest:raise access.AccessDenied("exact locked review required")
        prefix=ident+":"+rid+":"
        r=c.execute("SELECT * FROM validation_cohort_attestations WHERE cohort=? AND reviewer=?",(ident,rid)).fetchone()
        if r:
            _event(c,ATTEST,prefix,(r["audit_seq"],digest))
            if r["digest"]!=digest:raise access.AccessDenied("attestation changed")
        else:
            _event(c,ATTEST,prefix)
            seq=_write_audit(c,actor["user_id"],ATTEST,prefix+digest)
            c.execute("INSERT INTO validation_cohort_attestations VALUES(?,?,?,?)",(ident,rid,digest,seq))


def inventory(directory,actor):
    with access.db(directory) as c:
        _schema(c)
        _actor(c,actor,"admin","reviewer")
        out=[]
        rows=c.execute("SELECT id FROM validation_cohorts ORDER BY id").fetchall()
        events=c.execute("SELECT object_id FROM audit WHERE action=? AND outcome='ok'",(EVENT,)).fetchall()
        if len(rows)!=len(events) or {r["id"] for r in rows}!={e["object_id"].split(":",1)[0] for e in events}:
            raise access.AccessDenied("cohort inventory differs from audit")
        for row in rows:
            d=_read(c,row["id"])
            if actor["role"]=="reviewer" and actor["user_id"] not in _roster(d):continue
            link=_link(c,d)
            if actor["role"]=="reviewer":
                out.append({"id":d["id"],"linked":link is not None})
            else:
                receipts=[]
                if link:
                    _,_,responses,_,_=_context(directory,c,d["id"])
                    for rid in _roster(d):
                        prior=_review(c,d,link,responses,rid)
                        att=c.execute("SELECT * FROM validation_cohort_attestations WHERE cohort=? AND reviewer=?",(d["id"],rid)).fetchone()
                        if att:
                            if not prior or att["digest"]!=prior[1]:raise access.AccessDenied("attestation differs from review")
                            _event(c,ATTEST,d["id"]+":"+rid+":",(att["audit_seq"],att["digest"]))
                        else:_event(c,ATTEST,d["id"]+":"+rid+":")
                        receipts.append({"reviewer":rid,"digest":prior[1] if prior else None,"attested":att is not None})
                out.append({"id":d["id"],"plan_id":d["plan_id"],"family":d["family_id"],"n_variants":len(d["variant_ids"]),"linked":link is not None,"reviews":receipts})
        return out


def analyze(directory,actor,ident):
    with access.db(directory) as c:
        _schema(c)
        _actor(c,actor,"admin")
        d,link,responses,results,panel=_context(directory,c,ident)
        labels={}
        for rid in _roster(d):
            prior=_review(c,d,link,responses,rid)
            att=c.execute("SELECT * FROM validation_cohort_attestations WHERE cohort=? AND reviewer=?",(ident,rid)).fetchone()
            if not prior or not att or att["digest"]!=prior[1]:raise access.AccessDenied("all independent reviews must be attested")
            _event(c,ATTEST,ident+":"+rid+":",(att["audit_seq"],att["digest"]))
            req,slots=_packet(d,link,responses,rid)
            labels[rid]={slots[x["slot"]]:x for x in prior[0]["answers"]}
        locked={"expected_cells":[{"cell_id":cid,"severity":results[cid]["severity"],"mandatory":False} for cid in d["variant_ids"]],
                "n_expected":len(d["variant_ids"]),"min_reviewers_per_cell":2}
        calibration=review_calibration.analyze(locked,list(results.values()),panel["names"],
            [{cid:r["verdict"] for cid,r in labels[rid].items()} for rid in d["reviewers"]],
            labels[d["validity_reviewer"]],synthetic=panel.get("all_mock",True) or panel.get("mock_in_headline",False))
        calibration["limitations"]=[x for x in calibration["limitations"] if not x.startswith("The locked review queue is enriched")]
        calibration["limitations"].insert(0,"All variants of a single fixed family and staged case pack were reviewed; not a random patient-population sample.")
        calibration["limitations"].append("The cohort inventory and roster were frozen before exchange, but the evaluator configuration and completed source job were not prospectively selected by this protocol.")
        out={"schema":"prospective-cohort-calibration/1","cohort":ident,"source_manifest":link["manifest"],
             "calibration":calibration,"decision_grade":False,"independent_credentials_verified_by_software":False,
             "evaluator_and_job_prospectively_bound":False}
        return {**out,"digest":plans._digest(out)}
