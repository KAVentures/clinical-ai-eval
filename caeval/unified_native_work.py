"""Bounded native work projection: no execution or evidence association.

Only opaque administrative metadata leaves the source-specific adapters.
Imaging and grounding match vendor ID and version, not buyer-plan identity.
"""
import sqlite3
from . import unified_assessment as u

MAX_SCAN=200
ERRORS=u.ERRORS+(sqlite3.Error,RecursionError)
KINDS=("fhir","hl7","image_text","research","imaging","rag_grounding","rag")


def _record(ident,state,mode,scope):
    return {"id":ident,"state":state,"mode":mode,"scope":scope,
            "network_authorized":False,"association_authorized":False}


def _scan(ids,read,limit=MAX_SCAN):
    """Never label an incomplete or failed scan as an authoritative empty result."""
    if type(limit) is not int or not 1<=limit<=MAX_SCAN:
        raise ValueError("invalid scan limit")
    records=[]
    failed=0
    truncated=len(ids)>limit
    for ident in ids[:limit]:
        try:
            item=read(ident)
            if item is not None:
                records.append(item)
        except ERRORS:
            failed+=1
    return {"records":records,"state":"partial" if failed or truncated else "bounded_scan",
            "scan_truncated":truncated,"unverified_records":failed}


def _ids(conn,sql,args=()):
    return [r["id"] for r in conn.execute(sql,(*args,MAX_SCAN+1)).fetchall()]


def _synthetic(directory,conn,actor,req,plan):
    from . import institution_fhir as fhir, institution_hl7 as hl7, institution_image_text as image
    module,table={"fhir":(fhir,"synthetic_fhir_reports"),"hl7":(hl7,"synthetic_hl7_reports"),
                  "image_text":(image,"synthetic_image_text_reports")}[req["kind"]]
    module._schema(conn)
    module._check_inventory(conn)
    ids=_ids(conn,"SELECT id FROM "+table+" WHERE plan_id=? ORDER BY id LIMIT ?",(plan["plan_id"],))
    def read(ident):
        source=u._source(directory,conn,actor,req,plan,ident)
        return _record(ident,"report_available",source["mode"],source["scope"])
    return _scan(ids,read)


def _research(directory,conn,actor,req,plan):
    from . import research_program as research
    research._schema(conn)
    ids=_ids(conn,"SELECT id FROM research_programs WHERE plan_id=? ORDER BY id LIMIT ?",(plan["plan_id"],))
    def read(ident):
        row,payload,bp,_=research._load(directory,conn,actor,ident,committee=True)
        if row["plan_id"]!=plan["plan_id"] or bp["vendor_id"]!=plan["vendor_id"] or payload["target"]!=plan["target"]:
            raise ValueError("research programme scope changed")
        status=research._status(directory,conn,actor,ident,committee=True)
        unavailable=any(m["state"]=="unavailable" for m in status["members"])
        state=("source_unavailable" if unavailable else
               "reports_available" if status["all_planned_reports_available"] else "collecting")
        return _record(ident,state,"experimental_research","exact_buyer_plan")
    return _scan(ids,read)


def _candidate_matches(frozen,plan):
    return any(c["id"]==plan["vendor_id"] and c["version"]==plan["target"]["version"]
               for c in frozen["spec"]["candidates"])


def _candidate_studies(conn,plan,kind):
    if kind=="imaging":
        from . import institution_imaging as module
        table="diagnostic_imaging_studies"
    else:
        from . import institution_rag_grounding as module
        table="rag_grounding_studies"
    module._schema(conn)
    module._inventory(conn)
    ids=_ids(conn,"SELECT id FROM "+table+" ORDER BY id LIMIT ?")
    def read(ident):
        if kind=="imaging":
            frozen,docs,evidence=module._get(conn,ident)
            state="sealed" if evidence else "collecting"
            mode="experimental_imaging"
        else:
            frozen,docs,reviews,evidence=module._get(conn,ident)
            state=module._state(frozen,docs,reviews,evidence)["state"]
            mode="experimental_rag_grounding"
        if not _candidate_matches(frozen,plan):
            return None
        return _record(ident,state,mode,"vendor_id_and_version_only")
    return _scan(ids,read)


def _rag(directory,conn,req,plan):
    from . import institution_rag as rag
    rag._schema(conn)
    rag._inventory(conn)
    ids=_ids(conn,"SELECT id FROM institution_rag_studies WHERE plan_id=? ORDER BY id LIMIT ?",(plan["plan_id"],))
    def read(ident):
        p,e=rag._get(directory,conn,ident)
        if p["plan_id"]!=plan["plan_id"] or p["vendor_id"]!=plan["vendor_id"] or p["target"]!=plan["target"]:
            raise ValueError("RAG scope changed")
        if p["family"]!=req["family"]:
            return None
        return _record(ident,"sealed" if e else "collecting","demonstration","exact_buyer_plan")
    return _scan(ids,read)


def collect(directory,conn,actor,req,plan):
    kind=req["kind"]
    if kind not in KINDS:
        raise ValueError("unsupported work inventory kind")
    try:
        if kind in ("fhir","hl7","image_text"):
            return _synthetic(directory,conn,actor,req,plan)
        if kind=="research":
            return _research(directory,conn,actor,req,plan)
        if kind=="rag":
            return _rag(directory,conn,req,plan)
        return _candidate_studies(conn,plan,kind)
    except ERRORS:
        return {"records":[],"state":"unavailable","scan_truncated":False,"unverified_records":None}
