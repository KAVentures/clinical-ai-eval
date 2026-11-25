"""Frozen profile-specific evaluation programme for mixed clinical products.

Separate audience scopes are explicit child plans, not a relaxation of the
parent's canonical audience gate. This coordinator never executes, scores,
or silently satisfies any parent-level requirement.
"""
from . import (buyer_plan, buyer_manual as manual, buyer_agent as buyer,
               institution_access as access, institution_setup as setup,
               research_buyer_coverage as bridge, buyer_jobs as jobs,
               institution_conversation as conversation, executors)

EVENT="mixed-evaluation-programme-frozen"
MAX_PROGRAMMES=100
ERRORS=(OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError)


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS mixed_evaluation_programmes(
        parent_id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
        payload TEXT NOT NULL, audit_seq INTEGER NOT NULL UNIQUE)""")


def _spec(parent,profile):
    p=parent["plan"]
    return {"vendor_id":parent["vendor_id"],"target_name":p["target"]["name"],
            "target_version":p["target"]["version"],"profiles":[profile],
            "answers":p["intake"]}


def _material(directory,parent_id):
    parent=bridge._buyer_plan(directory,parent_id)
    p=parent["plan"]
    profiles=p["target_profile"]["types"]
    if len(profiles)<2:
        raise buyer.BuyerError("mixed programme requires at least two explicit profiles")
    lanes=[]
    for profile in sorted(profiles):
        child=buyer_plan.preview(directory,_spec(parent,profile),save=False)
        selected=child["plan"]["selection"]
        runnable=selected["runnable_suites"]
        kinds=sorted({executors.resolve(f).executor_id for f in runnable})
        lanes.append({"profile":profile,"plan_id":child["plan_id"],
                      "audience":child["plan"]["target_profile"]["audience"],
                      "runnable":runnable,"required_but_not_run":selected["required_but_not_run"],
                      "executors":kinds,
                      "required_suites":[r["suite"] for r in selected["required_suites"]]})
    doc={"schema":"mixed-evaluation-programme/1","parent_id":parent_id,
         "vendor_id":parent["vendor_id"],"target":{k:p["target"][k] for k in ("name","version")},
         "parent_required_suites":p["selection"]["required_suites"],
         "parent_required_but_not_run":p["selection"]["required_but_not_run"],
         "lanes":lanes,"parent_canonical_requirements_satisfied":False,
         "decision_grade":False,"network_authorized":False}
    return parent,{**doc,"digest":buyer_plan._digest(doc)}


def _event(doc):
    return doc["parent_id"]+":"+doc["digest"]


def _stored(conn,parent_id):
    import json
    row=conn.execute("SELECT * FROM mixed_evaluation_programmes WHERE parent_id=?",(parent_id,)).fetchone()
    events=conn.execute("SELECT seq,object_id FROM audit WHERE action=? AND outcome='ok' AND substr(object_id,1,65)=?",
                        (EVENT,parent_id+":")).fetchall()
    if row is None:
        if events:
            raise access.AccessDenied("audited mixed programme is missing")
        return None
    doc=json.loads(row["payload"])
    if (not isinstance(doc,dict) or doc["parent_id"]!=parent_id or doc["digest"]!=row["digest"]
            or buyer_plan._digest({k:v for k,v in doc.items() if k!="digest"})!=row["digest"]
            or len(events)!=1 or events[0]["seq"]!=row["audit_seq"] or events[0]["object_id"]!=_event(doc)):
        raise access.AccessDenied("mixed programme approval or binding changed")
    return doc


def preview(directory,actor,parent_id):
    with access.db(directory) as conn:
        _schema(conn)
        setup._actor(conn,actor)
        _,doc=_material(directory,parent_id)
        prior=_stored(conn,parent_id)
        if prior is not None and prior!=doc:
            raise access.AccessDenied("frozen programme no longer current")
        return {**doc,"frozen":prior is not None}


def freeze(directory,actor,parent_id,digest,*,confirmed=False):
    import json
    if confirmed is not True:
        raise access.AccessDenied("explicit programme approval required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        setup._actor(conn,actor)
        parent,doc=_material(directory,parent_id)
        prior=_stored(conn,parent_id)
        if doc["digest"]!=digest or (prior is not None and prior!=doc):
            raise access.AccessDenied("programme differs from approved preview")
        for lane in doc["lanes"]:
            if prior is None:
                child=buyer_plan.preview(directory,_spec(parent,lane["profile"]))
            else:
                # An already approved child may not be silently recreated.
                child=bridge._buyer_plan(directory,lane["plan_id"])
            if child["plan_id"]!=lane["plan_id"]:
                raise access.AccessDenied("child plan differs from approved programme")
        if prior is None:
            if conn.execute("SELECT count(*) FROM mixed_evaluation_programmes").fetchone()[0]>=MAX_PROGRAMMES:
                raise access.AccessDenied("mixed programme limit")
            access._audit(conn,actor["user_id"],EVENT,_event(doc))
            seq=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("INSERT INTO mixed_evaluation_programmes VALUES(?,?,?,?)",
                         (parent_id,digest,json.dumps(doc),seq))
        return {**doc,"frozen":True}


def _generic(directory,lanes):
    result={p:[] for p in lanes}
    for stored in jobs.list_jobs(directory):
        if stored["status"]!="completed":
            continue
        try:
            job=jobs.status(directory,stored["id"])
            if job["status"]!="completed":
                continue
            b=manual._read(jobs._job_path(directory,job["id"])/"binding.json")
            pid=b["plan_id"]
            if pid not in lanes:
                continue
            if (b["assessment_id"]!=job["assessment_id"] or
                    buyer_plan._digest({k:v for k,v in b.items() if k!="assessment_id"})!=job["assessment_id"]):
                raise buyer.BuyerError("job binding changed")
            exchange=manual._load(directory,b["exchange_id"])
            if exchange["plan_id"]!=pid:
                raise buyer.BuyerError("exchange plan mismatch")
            for p in job["result"]["packages"]:
                if p["family_id"] not in lanes[pid]["runnable"]:
                    raise buyer.BuyerError("unexpected family in lane")
            for p in job["result"]["packages"]:
                result[pid].append({"job_id":job["id"],"suite":p["family_id"],
                    "manifest_hash":p["manifest_hash"],"claim":p["claim"]})
        except ERRORS:
            continue
    return result


def _conversations(directory,lanes):
    result={p:[] for p in lanes}
    with access.db(directory) as conn:
        conversation._schema(conn)
        rows=[]
        for row in conn.execute("SELECT id FROM conversation_jobs WHERE status='completed' ORDER BY id").fetchall():
            try:
                rows.append(conversation._row(conn,row["id"]))
            except ERRORS:
                continue
    for row in rows:
        try:
            b=row["binding"]
            pid=b["plan_id"]
            if pid not in lanes or conversation.FAMILY not in lanes[pid]["runnable"]:
                continue
            current,_=conversation._material(directory,**{k:b[k] for k in
                    ("plan_id","connector_id","pack_id","panel_id","governance")})
            if current!=b or conversation._verify(directory,row)!=row["result"]:
                continue
            result[pid].append({"job_id":row["id"],"suite":conversation.FAMILY,
                "manifest_hash":row["result"]["manifest_hash"],"claim":row["result"]["claim"]})
        except ERRORS:
            continue
    return result


def status(directory,actor,parent_id):
    doc=preview(directory,actor,parent_id)
    if not doc["frozen"]:
        return doc
    lanes={l["plan_id"]:l for l in doc["lanes"]}
    for pid in lanes:
        bridge._buyer_plan(directory,pid)
    generic=_generic(directory,lanes)
    conversations=_conversations(directory,lanes)
    outputs=[]
    for pid,lane in lanes.items():
        receipts=generic[pid]+conversations[pid]
        research=bridge.coverage(directory,actor,pid)
        evidence=[]
        for suite in lane["required_suites"]:
            verified=[r for r in receipts if r["suite"]==suite]
            related=next(r for r in research["suites"] if r["suite"]==suite)
            evidence.append({"suite":suite,"state":("verified_scoped_package" if verified else
                "not_executed" if suite in lane["runnable"] else "required_but_not_run"),
                "packages":verified,"related_experimental_reports":related["verified_experimental_reports"],
                "decision_grade":False})
        outputs.append({**lane,"evidence":evidence})
    # Keep the parent's original unresolved requirements visible, even if a
    # narrower scope could run the same family. No pooling or pass inference.
    return {**doc,"lanes":outputs,"all_runnable_lane_suites_have_packages":all(
        e["state"]=="verified_scoped_package" for l in outputs for e in l["evidence"] if e["suite"] in l["runnable"])
        and any(l["runnable"] for l in outputs)}


def committee_inventory(directory,actor,known,assessments):
    """Read verified, aggregate lane coverage without disclosing study material.

    An audited programme missing from the live table remains visible as
    unavailable. Canonical references must also be present in the committee's
    independently captured assessment inventory. No research result is promoted
    to a canonical package.
    """
    from . import institution_committee as committee
    docs={}
    invalid=set()
    with access.db(directory) as conn:
        _schema(conn)
        committee._actor(conn,actor)
        ids={r["parent_id"] for r in conn.execute("SELECT parent_id FROM mixed_evaluation_programmes")}
        for row in conn.execute("SELECT object_id FROM audit WHERE action=? AND outcome='ok'",(EVENT,)):
            prefix=row["object_id"].split(":",1)[0]
            manual._identifier(prefix,digest=True)
            ids.add(prefix)
        if len(ids)>MAX_PROGRAMMES:
            raise access.AccessDenied("mixed programme inventory exceeds limit")
        for pid in sorted(ids):
            try:
                prior=_stored(conn,pid)
                _,current=_material(directory,pid)
                if prior!=current or current["vendor_id"] not in known:
                    raise access.AccessDenied("programme no longer current")
                for lane in current["lanes"]:
                    bridge._buyer_plan(directory,lane["plan_id"])
                docs[pid]=current
            except ERRORS:
                invalid.add(pid)
    lanes={l["plan_id"]:l for doc in docs.values() for l in doc["lanes"]}
    generic=_generic(directory,lanes)
    conversations=_conversations(directory,lanes)
    canonical={(job["id"],pkg["family_id"],pkg["manifest_hash"],pkg["claim"],
                job["vendor_id"],job["target_version"])
               for job in assessments if job["status"]=="completed" for pkg in job["packages"]}
    result=[{"id":pid,"state":"unavailable","decision_grade":False} for pid in sorted(invalid)]
    with access.db(directory) as conn:
        committee._actor(conn,actor)
        bridge._schema(conn)
        for pid,doc in sorted(docs.items()):
            try:
                if _stored(conn,pid)!=doc or _material(directory,pid)[1]!=doc:
                    raise access.AccessDenied("programme changed during capture")
                outputs=[]
                for lane in doc["lanes"]:
                    child=bridge._buyer_plan(directory,lane["plan_id"])
                    counts={s:0 for s in lane["required_suites"]}
                    unavailable=0
                    records=conn.execute("SELECT * FROM buyer_research_links WHERE plan_id=? ORDER BY study",
                                         (lane["plan_id"],)).fetchall()
                    for record in records:
                        try:
                            row=bridge.studio._row(conn,record["study"])
                            link=bridge._read_link(directory,conn,child,record,row)
                            if link["evidence_state"]=="verified_experimental_report":
                                for suite in link["related_suites"]:
                                    if suite in counts:
                                        counts[suite]+=1
                        except ERRORS:
                            unavailable+=1
                    receipts=generic[lane["plan_id"]]+conversations[lane["plan_id"]]
                    coverage=[]
                    for suite in lane["required_suites"]:
                        packages=[r for r in receipts if r["suite"]==suite and
                            (r["job_id"],suite,r["manifest_hash"],r["claim"],doc["vendor_id"],doc["target"]["version"]) in canonical]
                        coverage.append({"suite":suite,"state":"verified_scoped_package" if packages else
                            "not_executed" if suite in lane["runnable"] else "required_but_not_run",
                            "packages":packages,"related_experimental_reports":counts[suite],"decision_grade":False})
                    outputs.append({"profile":lane["profile"],"plan_id":lane["plan_id"],
                                    "audience":lane["audience"],"coverage":coverage,
                                    "unavailable_research_associations":unavailable})
                result.append({"id":pid,"state":"current","vendor_id":doc["vendor_id"],
                    "target_version":doc["target"]["version"],"programme_digest":doc["digest"],
                    "parent_required_but_not_run":[r["suite"] for r in doc["parent_required_but_not_run"]],
                    "lanes":outputs,"parent_canonical_requirements_satisfied":False,"decision_grade":False})
            except ERRORS:
                result.append({"id":pid,"state":"unavailable","decision_grade":False})
    return sorted(result,key=lambda r:r["id"])
