"""Read-only plan-aware work coordination; native workflow owns execution."""
from . import unified_assessment as u
from . import institution_access as access
from . import buyer_assessment as assessment, buyer_jobs as jobs
from . import buyer_manual as manual, unified_native_work as native
from . import unified_legacy_work as legacy

MAX_RECORDS=200


def _text_work(directory,conn,plan,family):
    base=manual._directory(directory,"prepared-assessments")
    def prepared_record(ident):
        path=base/ident
        if path.is_symlink() or not path.is_dir():
            raise ValueError("invalid prepared assessment record")
        b=manual._read(path/"binding.json")
        if b["plan_id"]!=plan["plan_id"]:
            return None
        checked=assessment.check(directory,ident,conn=conn)
        if checked!=b:
            raise ValueError("prepared binding changed")
        exchange=manual._load(directory,b["exchange_id"])
        if not legacy.text_scope(exchange,plan,family):
            return None
        return {"id":ident,"mode":b["mode"],"state":"prepared_not_executed",
                "assessment_id":ident,"scope":"exact_buyer_plan","network_authorized":False}
    prepared=native._scan(legacy.prepared_ids(base,MAX_RECORDS),prepared_record)
    with jobs._db(directory) as db:
        queued=db.execute("SELECT * FROM jobs ORDER BY created_at DESC,id DESC LIMIT ?",(MAX_RECORDS+1,)).fetchall()
    def job_record(raw):
        row=jobs._public(raw)
        state=row["status"]
        if state=="completed":
            verified=jobs.status(directory,row["id"])
            if verified["status"]!="completed" or verified!=row:
                raise ValueError("completed job verification failed")
            b=manual._read(jobs._job_path(directory,row["id"])/"binding.json")
        else:
            b=assessment.check(directory,row["assessment_id"],conn=conn)
        if b["plan_id"]!=plan["plan_id"]:
            return None
        if b["assessment_id"]!=row["assessment_id"] or b["mode"]!=row["mode"]:
            raise ValueError("job scope or mode mismatch")
        exchange=manual._load(directory,b["exchange_id"])
        if not legacy.text_scope(exchange,plan,family):
            return None
        if state=="completed" and len([p for p in verified["result"]["packages"] if p["family_id"]==family])!=1:
            raise ValueError("completed job family mismatch")
        return {"id":row["id"],"assessment_id":row["assessment_id"],"state":state,
                "mode":row["mode"],"real_judges":row["real_judges"],"scope":"exact_buyer_plan",
                "network_authorized":False}
    return legacy.merge_text(prepared,native._scan(queued,job_record))


def _conversation_work(directory,conn,plan,family):
    from . import institution_conversation as conversation
    if family!=conversation.FAMILY:
        raise ValueError("conversation family mismatch")
    conversation._schema(conn)
    ids=native._ids(conn,"SELECT id FROM conversation_jobs ORDER BY id LIMIT ?")
    def read(ident):
        row=conversation._row(conn,ident)
        b=row["binding"]
        if b["plan_id"]!=plan["plan_id"]:
            return None
        if b["vendor_id"]!=plan["vendor_id"] or {k:b["target"][k] for k in ("name","version")}!=plan["target"]:
            raise ValueError("conversation candidate scope mismatch")
        current,_=conversation._material(directory,**{k:b[k] for k in
               ("plan_id","connector_id","pack_id","panel_id","governance")})
        if current!=b:
            raise ValueError("conversation binding changed")
        if row["status"]=="completed" and conversation._verify(directory,row)!=row["result"]:
            raise ValueError("conversation verification failed")
        return {"id":row["id"],"state":row["status"],"mode":b["mode"],
                "real_judges":b["real_judges"],"scope":"exact_buyer_plan","network_authorized":False}
    return native._scan(ids,read)


def work(directory,actor,ident):
    from pathlib import Path
    root=Path(directory)
    with access.db(root) as conn:
        u._schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator")
        u._inventory(conn)
        status=u._status(root,conn,actor,ident)
        return _project(root,conn,actor,status)


def gaps(directory,actor,ident):
    from pathlib import Path
    from . import unified_evidence_gaps as report
    root=Path(directory)
    with access.db(root) as conn:
        u._schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator")
        u._inventory(conn)
        status=u._status(root,conn,actor,ident)
        return report.project(status,_project(root,conn,actor,status))


def _project(root,conn,actor,status):
    ident=status["programme"]["programme_id"]
    cache={}
    rows=[]
    for row in status["requirements"]:
        req=row["requirement"]
        rid=req["id"]
        _,plan=u._requirement(status["programme"],rid)
        key=(req["kind"],req["plan_id"],req["family"])
        if key not in cache:
            result={"records":[],"state":"stale_plan","scan_truncated":False,"unverified_records":None}
            if status["state"]=="current":
                if req["kind"] in native.KINDS:
                    result=native.collect(root,conn,actor,req,plan)
                else:
                    try:
                        result=(_text_work(root,conn,plan,req["family"]) if req["kind"]=="text" else
                                _conversation_work(root,conn,plan,req["family"]))
                    except native.ERRORS:
                        result={"records":[],"state":"unavailable","scan_truncated":False,"unverified_records":None}
            cache[key]=result
        result=cache[key]
        rows.append({"requirement_id":rid,"kind":req["kind"],"plan_id":req["plan_id"],
                     "vendor_id":row["vendor_id"],"target":row["target"],"family":req["family"],
                     "coverage_state":row["state"],"native_work_records":result["records"],
                     "work_inventory_state":result["state"],"scan_truncated":result["scan_truncated"],
                     "unverified_records":result["unverified_records"],
                     "execution_authorized":False})
    return {"schema":"unified-assessment-work/1","programme_id":ident,
            "programme_digest":status["programme"]["digest"],"programme_state":status["state"],
            "requirements":rows,"decision_grade":False,"execution_authorized":False,
            "limitations":["Read-only coordination. Native workflows own preparation, queueing, cancellation and execution.",
                           "Bounded scans show exact-plan work or explicitly labelled vendor/version-only imaging and grounding studies; absence is not proof of no work.",
                           "The same multi-family job can be shown in several requirements. Completed work is not automatically locked."]}
