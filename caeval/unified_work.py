"""Read-only plan-aware work coordination; native workflow owns execution."""
from . import unified_assessment as u
from . import institution_access as access, institution_setup as setup
from . import buyer_assessment as assessment, buyer_jobs as jobs
from . import buyer_manual as manual

MAX_RECORDS=200


def _text_work(directory,conn,plan):
    pid=plan["plan_id"]
    prepared=[]
    for ident in setup._ids(directory,"prepared-assessments",""):
        try:
            # Pre-filter without interpreting unverified metadata as evidence.
            b=manual._read(directory / "prepared-assessments" / ident / "binding.json")
            if b["plan_id"]!=pid:
                continue
            checked=assessment.check(directory,ident,conn=conn)
            if checked!=b:
                continue
            prepared.append({"id":ident,"mode":b["mode"],"state":"prepared_not_executed",
                             "assessment_id":ident,"network_authorized":False})
        except u.ERRORS:
            continue
    found=[]
    for row in jobs.list_jobs(directory):
        try:
            state=row["status"]
            if state=="completed":
                verified=jobs.status(directory,row["id"])
                if verified["status"]!="completed":
                    continue
                b=manual._read(jobs._job_path(directory,row["id"])/"binding.json")
            else:
                # Native prepared project remains the scope anchor even when
                # the job has not yet made a workspace.
                b=assessment.check(directory,row["assessment_id"],conn=conn)
            if b["plan_id"]!=pid or b["assessment_id"]!=row["assessment_id"]:
                continue
            found.append({"id":row["id"],"assessment_id":row["assessment_id"],
                          "state":state,"mode":row["mode"],"real_judges":row["real_judges"],
                          "network_authorized":False})
        except u.ERRORS:
            continue
    used={r["assessment_id"] for r in found}
    return [r for r in prepared if r["id"] not in used]+found


def _conversation_work(directory,conn,plan):
    from . import institution_conversation as conversation
    conversation._schema(conn)
    out=[]
    for item in conn.execute("SELECT id FROM conversation_jobs ORDER BY id LIMIT ?",(MAX_RECORDS,)):
        try:
            row=conversation._row(conn,item["id"])
            b=row["binding"]
            if b["plan_id"]!=plan["plan_id"] or b["vendor_id"]!=plan["vendor_id"]:
                continue
            current,_=conversation._material(directory,**{k:b[k] for k in
                   ("plan_id","connector_id","pack_id","panel_id","governance")})
            if current!=b:
                continue
            if row["status"]=="completed" and conversation._verify(directory,row)!=row["result"]:
                continue
            out.append({"id":row["id"],"state":row["status"],"mode":b["mode"],
                        "real_judges":b["real_judges"],"network_authorized":False})
        except u.ERRORS:
            continue
    return out


def work(directory,actor,ident):
    from pathlib import Path
    root=Path(directory)
    with access.db(root) as conn:
        u._schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator")
        u._inventory(conn)
        status=u._status(root,conn,actor,ident)
        cache={}
        rows=[]
        for row in status["requirements"]:
            req=row["requirement"]
            rid=req["id"]
            _,plan=u._requirement(status["programme"],rid)
            key=(req["kind"],req["plan_id"])
            if key not in cache:
                records=[]
                if status["state"]=="current":
                    if req["kind"]=="text":
                        records=_text_work(root,conn,plan)
                    elif req["kind"]=="conversation":
                        records=_conversation_work(root,conn,plan)
                    elif req["kind"]=="rag":
                        from . import institution_rag as rag
                        rag._schema(conn)
                        rag._inventory(conn)
                        for item in conn.execute("SELECT id FROM institution_rag_studies WHERE plan_id=? ORDER BY id",(req["plan_id"],)):
                            try:
                                p,e=rag._get(root,conn,item["id"])
                                records.append({"id":p["digest"],"state":"sealed" if e else "collecting",
                                                "mode":"demonstration","network_authorized":False})
                            except u.ERRORS:
                                continue
                cache[key]=records
            rows.append({"requirement_id":rid,"kind":req["kind"],"plan_id":req["plan_id"],
                         "vendor_id":row["vendor_id"],"target":row["target"],"family":req["family"],
                         "coverage_state":row["state"],"native_work_records":cache[key],
                         "execution_authorized":False})
        return {"schema":"unified-assessment-work/1","programme_id":ident,
                "programme_digest":status["programme"]["digest"],"programme_state":status["state"],
                "requirements":rows,"decision_grade":False,"execution_authorized":False,
                "limitations":["Read-only coordination. Native workflows own preparation, queueing, cancellation and execution.",
                               "Only verified exact-plan text, conversation and synthetic RAG work is shown; absent entries do not prove no work exists.",
                               "The same multi-family job can be shown in several requirements. Completed work is not automatically locked."]}
