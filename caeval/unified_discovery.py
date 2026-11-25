"""Discovery and explicit batch association for unified assessment programmes.

Discovery never executes models, chooses a best result or modifies evidence.
Every displayed candidate source is reverified by the native source adapter.
A batch lock is atomic within the institutional access database.
"""
import json
from . import unified_assessment as u, diagnostic_imaging as d
from . import institution_access as access, buyer_jobs as jobs

MAX_SCAN=200
MAX_MATCHES=20
MAX_BATCH=40


def _candidates(directory,conn,req):
    kind=req["kind"]
    pid=req["plan_id"]
    if kind=="text":
        return sorted({j["id"] for j in jobs.list_jobs(directory) if j["status"]=="completed"})
    if kind=="conversation":
        from . import institution_conversation as module
        module._schema(conn)
        return [r["id"] for r in conn.execute("SELECT id FROM conversation_jobs WHERE status='completed' ORDER BY id LIMIT ?",(MAX_SCAN+1,))]
    if kind=="research":
        from . import research_program as module
        module._schema(conn)
        return [r["id"] for r in conn.execute("SELECT id FROM research_programs WHERE plan_id=? ORDER BY id LIMIT ?",(pid,MAX_SCAN+1))]
    if kind=="imaging":
        from . import institution_imaging as module
        module._schema(conn)
        module._inventory(conn)
        return [r["study"] for r in conn.execute("SELECT study FROM diagnostic_imaging_seals ORDER BY study LIMIT ?",(MAX_SCAN+1,))]
    from . import institution_fhir as fhir, institution_hl7 as hl7, institution_image_text as image
    module,table={"fhir":(fhir,"synthetic_fhir_reports"),"hl7":(hl7,"synthetic_hl7_reports"),
                  "image_text":(image,"synthetic_image_text_reports")}[kind]
    module._schema(conn)
    module._check_inventory(conn)
    return [r["id"] for r in conn.execute("SELECT id FROM "+table+" WHERE plan_id=? ORDER BY id LIMIT ?",(pid,MAX_SCAN+1))]


def discover(directory,actor,ident,rid):
    with access.db(directory) as conn:
        u._schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        u._inventory(conn)
        doc,seq=u._read(conn,ident)
        if not u._current(directory,doc):
            raise access.AccessDenied("programme scope no longer current")
        req,plan=u._requirement(doc,rid)
        if u._link(conn,doc,seq,rid):
            raise access.AccessDenied("requirement already locked")
        ids=_candidates(directory,conn,req)
        matches=[]
        scanned=0
        for source in ids[:MAX_SCAN]:
            scanned+=1
            try:
                p=u._preview_link(directory,conn,actor,ident,rid,source)
            except u.ERRORS:
                continue
            matches.append(p)
            if len(matches)>MAX_MATCHES:
                break
        return {"schema":"unified-source-discovery/1","programme_id":ident,
                "requirement_id":rid,"matches":matches[:MAX_MATCHES],
                "scan_truncated":len(ids)>scanned or len(matches)>MAX_MATCHES,
                "selection_authorized":False,"decision_grade":False}


def _batch(directory,conn,actor,ident,selections):
    d._require(isinstance(selections,list) and 1<=len(selections)<=MAX_BATCH,
               "select a bounded nonempty batch")
    seen=set()
    for row in selections:
        d._keys(row,"requirement_id source_id")
        rid=row["requirement_id"]
        d._require(d._token(rid) and rid not in seen and d._token(row["source_id"]),
                   "duplicate requirement or invalid source selection")
        seen.add(rid)
    doc,_=u._read(conn,ident)
    entries=[]
    for row in sorted(selections,key=lambda r:r["requirement_id"]):
        p=u._preview_link(directory,conn,actor,ident,row["requirement_id"],row["source_id"])
        entries.append(p)
    return d._sealed({"schema":"unified-source-batch/1","programme_id":ident,
                      "programme_digest":doc["digest"],"entries":entries,
                      "decision_grade":False})


def batch_preview(directory,actor,ident,selections):
    with access.db(directory) as conn:
        u._schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        u._inventory(conn)
        return _batch(directory,conn,actor,ident,selections)


def batch_lock(directory,actor,ident,selections,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit batch approval required")
    with access.db(directory) as conn:
        u._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        u._inventory(conn)
        doc=_batch(directory,conn,actor,ident,selections)
        if doc["digest"]!=approval:
            raise access.AccessDenied("batch differs from approved preview")
        # No writes until every source has been reverified. Any exception
        # rolls back every inserted source and its associated audit event.
        for p in doc["entries"]:
            binding=p["binding"]
            rid=binding["requirement_id"]
            digest=p["approval_digest"]
            seq=u._audit(conn,actor,u.LINK,ident+":"+rid+":"+digest)
            conn.execute("INSERT INTO unified_assessment_links VALUES(?,?,?,?,?,?,?)",
                         (ident,rid,binding["source"]["source_id"],json.dumps(binding),
                          digest,actor["user_id"],seq))
        u._audit(conn,actor,"unified-assessment-batch-locked",ident+":"+approval)
        return {"programme_id":ident,"batch_digest":approval,"n_locked":len(doc["entries"]),
                "decision_grade":False}
