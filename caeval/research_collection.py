"""Explicitly approved single-turn research collection through a staged local connector.

One execution per study. The worker records exact raw output and every outcome.
It does not atomize clinical claims or perform clinical review. Browser requests
only queue/cancel and import, never call the candidate endpoint.
"""
from __future__ import annotations

import json
import time

from . import (institution_research as studio, institution_access as access,
               buyer_connector as connector, buyer_manual as manual, buyer_plan,
               formal_review as formal, adapters)

MAX_CALLS=200
MAX_PROMPT_BYTES=64*1024
MAX_TOTAL_BYTES=1024*1024
MAX_RUNTIME=900
STATES=("queued","running","raw_collected","cancelled","interrupted")


def _schema(conn):
    studio._schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS research_collections(
        study TEXT PRIMARY KEY REFERENCES research_studies(id),
        binding TEXT NOT NULL, binding_hash TEXT NOT NULL,
        state TEXT NOT NULL, receipt_hash TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS research_collection_outcomes(
        study TEXT NOT NULL REFERENCES research_collections(study),
        slot TEXT NOT NULL, ordinal INTEGER NOT NULL,
        status TEXT NOT NULL, answer TEXT NOT NULL, digest TEXT NOT NULL,
        PRIMARY KEY(study,slot), UNIQUE(study,ordinal))""")


def _prompts(mod,plan):
    if mod is studio.citation:
        rows=[(q["query_id"],q["query"]) for q in mod.prompts(plan)["queries"]]
    elif mod is studio.robustness:
        rows=[(i["slot"],i["prompt"]) for i in mod.exchange(plan)["items"]]
    elif mod is studio.scribe:
        ex=mod.exchange(plan)
        rows=[(i["slot"],ex["instructions"]+"\n\nRequested format: "+ex["note_format"]+
               "\nConsultation transcript (JSON):\n"+json.dumps(i["transcript"],ensure_ascii=False))
              for i in ex["items"]]
    elif mod is studio.decision:
        ex=mod.exchange(plan)
        rows=[]
        for item in ex["items"]:
            data={k:v for k,v in item.items() if k!="slot"}
            rows.append((item["slot"],ex["instructions"]+
                         "\nReturn only JSON with keys verdict, certificate, requested_facts. "
                         "Use the approved native certificate contract.\n"+
                         json.dumps(data,ensure_ascii=False,sort_keys=True)))
    else:
        raise access.AccessDenied("unsupported research collector")
    rows=sorted(rows)
    if (not 1<=len(rows)<=MAX_CALLS or len({s for s,_ in rows})!=len(rows)
            or any(len(p.encode("utf-8"))>MAX_PROMPT_BYTES for _,p in rows)):
        raise access.AccessDenied("collection prompt budget exceeded")
    return rows


def _material(directory,row,connector_id):
    root,mod,plan=studio._plan(directory,row)
    cfg=connector._config(directory,connector_id)
    target=plan["spec"]["target"]
    if (cfg["target_name"],cfg["target_version"])!=(target["name"],target["version"]):
        raise access.AccessDenied("connector target/version does not match research plan")
    prompts=_prompts(mod,plan)
    binding={"schema":"research-local-collection/1","study":row["id"],
             "plan_hash":row["plan_hash"],"kind":row["kind"],"owner":row["owner"],
             "connector_id":connector_id,"connector_hash":buyer_plan._digest(cfg),
             "target":target,"n_expected":len(prompts),
             "prompt_inventory":[{"slot":s,"hash":buyer_plan._digest(p)} for s,p in prompts],
             "transport":"approved_literal_loopback_http","decision_grade":False}
    return root,mod,plan,cfg,prompts,binding


def _job(conn,identifier):
    job=conn.execute("SELECT * FROM research_collections WHERE study=?",(identifier,)).fetchone()
    if not job:
        raise access.AccessDenied("no research collection")
    job=dict(job)
    job["binding"]=json.loads(job["binding"])
    if buyer_plan._digest(job["binding"])!=job["binding_hash"] or job["state"] not in STATES:
        raise access.AccessDenied("collection binding changed")
    return job


def queue(directory,actor,identifier,connector_id,plan_hash,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit exact plan and local collection approval required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row=studio._row(conn,identifier)
        studio._manager(conn,actor,row)
        if row["state"]!="planned" or plan_hash!=row["plan_hash"]:
            raise access.AccessDenied("fresh approved study required")
        if conn.execute("SELECT 1 FROM research_collections WHERE study=?",(identifier,)).fetchone():
            raise access.AccessDenied("one collection per study")
        _,_,_,_,_,binding=_material(directory,row,connector_id)
        digest=buyer_plan._digest(binding)
        conn.execute("INSERT INTO research_collections(study,binding,binding_hash,state) VALUES(?,?,?,?)",
                     (identifier,json.dumps(binding),digest,"queued"))
        conn.execute("UPDATE research_studies SET state='collecting' WHERE id=?",(identifier,))
        access._audit(conn,actor["user_id"],"research-collection-queued",identifier)
        return digest


def _record(conn,identifier,index,slot,status,answer):
    payload={"slot":slot,"status":status,"answer":answer}
    conn.execute("INSERT INTO research_collection_outcomes VALUES(?,?,?,?,?,?)",
                 (identifier,slot,index,status,answer,buyer_plan._digest(payload)))


def _outcomes(conn,identifier,binding,*,complete=True):
    rows=conn.execute("SELECT * FROM research_collection_outcomes WHERE study=? ORDER BY ordinal",
                      (identifier,)).fetchall()
    slots=[i["slot"] for i in binding["prompt_inventory"]]
    if len(rows)>len(slots) or (complete and len(rows)!=len(slots)):
        raise access.AccessDenied("collection outcome inventory differs")
    result=[]
    for index,row in enumerate(rows):
        p={k:row[k] for k in ("slot","status","answer")}
        if (row["ordinal"]!=index or p["slot"]!=slots[index] or
                p["status"] not in ("complete","provider_error","not_attempted") or
                (p["status"]=="complete")!=bool(p["answer"].strip()) or
                (p["status"]!="complete" and p["answer"]!="") or
                buyer_plan._digest(p)!=row["digest"]):
            raise access.AccessDenied("recorded outcome changed")
        result.append(p)
    return result


def execute(directory,identifier,approved_binding,*,allow_candidate_calls=False):
    """Trusted worker entry point. No automatic retries or interrupted resume."""
    if allow_candidate_calls is not True:
        raise access.AccessDenied("explicit candidate-call authorization required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row=studio._row(conn,identifier)
        job=_job(conn,identifier)
        if row["state"]!="collecting" or job["state"]!="queued" or job["binding_hash"]!=approved_binding:
            raise access.AccessDenied("queued collection and exact binding approval required")
        owner=conn.execute("SELECT role,enabled FROM users WHERE id=?",(row["owner"],)).fetchone()
        if not owner or not owner["enabled"] or owner["role"] not in ("admin","operator"):
            raise access.AccessDenied("active study owner required")
        root,mod,plan,cfg,prompts,binding=_material(directory,row,job["binding"]["connector_id"])
        if binding!=job["binding"] or _outcomes(conn,identifier,binding,complete=False):
            raise access.AccessDenied("collection drift or prior outcomes")
        headers=adapters.expand_env(cfg["headers"])
        if any(not isinstance(v,str) or len(v)>8192 or "\r" in v or "\n" in v for v in headers.values()):
            raise access.AccessDenied("invalid credential header")
        conn.execute("UPDATE research_collections SET state='running' WHERE study=?",(identifier,))
        access._audit(conn,"local-admin","research-collection-started",identifier)
    deadline=time.monotonic()+MAX_RUNTIME
    used,failures=0,0
    stopped=False
    for index,(slot,prompt) in enumerate(prompts):
        # Recheck approval and active owner before each new request. An in-flight
        # call cannot be revoked; no DB transaction spans network I/O.
        with access.db(directory) as conn:
            current=_job(conn,identifier)
            owner=conn.execute("SELECT role,enabled FROM users WHERE id=?",(row["owner"],)).fetchone()
            if current["state"]!="running" or current["binding_hash"]!=approved_binding:
                raise access.AccessDenied("collection interrupted")
            if not owner or not owner["enabled"] or owner["role"] not in ("admin","operator"):
                stopped=True
        if time.monotonic()>=deadline:
            stopped=True
        if not stopped:
            try:
                if buyer_plan._digest(connector._config(directory,binding["connector_id"]))!=binding["connector_hash"]:
                    stopped=True
            except (ValueError,RuntimeError,OSError,TypeError,KeyError):
                stopped=True
        status,answer="not_attempted",""
        if not stopped:
            try:
                answer=connector._call(cfg,prompt,headers)
                if used+len(answer.encode("utf-8"))>MAX_TOTAL_BYTES:
                    stopped=True
                    raise adapters.AdapterError("response budget exceeded")
                used+=len(answer.encode("utf-8"))
                status="complete"
            except (OSError,ValueError,RuntimeError,KeyError,TypeError):
                status,answer="provider_error",""
                failures+=1
                if failures>=3:
                    stopped=True
        with access.db(directory) as conn:
            conn.execute("BEGIN IMMEDIATE")
            current=_job(conn,identifier)
            if current["state"]!="running" or current["binding_hash"]!=approved_binding:
                raise access.AccessDenied("collection interrupted before checkpoint")
            _record(conn,identifier,index,slot,status,answer)
    with access.db(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        current=_job(conn,identifier)
        if current["state"]!="running" or current["binding_hash"]!=approved_binding:
            raise access.AccessDenied("collection interrupted")
        rows=_outcomes(conn,identifier,binding)
        receipt={"schema":"research-collection-receipt/1","binding_hash":approved_binding,
                 "outcomes":rows,"counts":{s:sum(r["status"]==s for r in rows)
                                           for s in ("complete","provider_error","not_attempted")},
                 "decision_grade":False}
        receipt["receipt_hash"]=buyer_plan._digest(receipt)
        formal._publish_once(root/"collection-receipt.json",receipt)
        conn.execute("UPDATE research_collections SET state='raw_collected',receipt_hash=? WHERE study=?",
                     (receipt["receipt_hash"],identifier))
        conn.execute("UPDATE research_studies SET state='raw_collected' WHERE id=?",(identifier,))
        access._audit(conn,"local-admin","research-collection-finished",identifier)
        return {"receipt_hash":receipt["receipt_hash"],"counts":receipt["counts"]}


def cancel(directory,actor,identifier):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row=studio._row(conn,identifier)
        studio._manager(conn,actor,row)
        job=_job(conn,identifier)
        if row["state"]!="collecting" or job["state"]!="queued":
            raise access.AccessDenied("only queued work can be cancelled")
        conn.execute("UPDATE research_collections SET state='cancelled' WHERE study=?",(identifier,))
        conn.execute("UPDATE research_studies SET state='collection_cancelled' WHERE id=?",(identifier,))
        access._audit(conn,actor["user_id"],"research-collection-cancelled",identifier)


def abandon(directory,identifier,*,worker_stopped=False):
    if worker_stopped is not True:
        raise access.AccessDenied("stop and investigate worker first")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row=studio._row(conn,identifier)
        job=_job(conn,identifier)
        if row["state"]!="collecting" or job["state"]!="running":
            raise access.AccessDenied("only interrupted running collection can be abandoned")
        conn.execute("UPDATE research_collections SET state='interrupted' WHERE study=?",(identifier,))
        conn.execute("UPDATE research_studies SET state='collection_interrupted' WHERE id=?",(identifier,))
        access._audit(conn,"local-admin","research-collection-abandoned",identifier)


def receipt(directory,conn,row):
    job=_job(conn,row["id"])
    if job["state"]!="raw_collected":
        raise access.AccessDenied("complete raw collection receipt required")
    root,mod,plan=studio._plan(directory,row)
    b=job["binding"]
    if (b["study"]!=row["id"] or b["plan_hash"]!=row["plan_hash"] or
            b["prompt_inventory"]!=[{"slot":s,"hash":buyer_plan._digest(p)} for s,p in _prompts(mod,plan)]):
        raise access.AccessDenied("collection no longer matches study")
    rows=_outcomes(conn,row["id"],b)
    doc=formal._json(root/"collection-receipt.json")
    if (doc.get("receipt_hash")!=job["receipt_hash"] or doc.get("binding_hash")!=job["binding_hash"]
            or doc.get("outcomes")!=rows or doc.get("decision_grade") is not False
            or buyer_plan._digest({k:v for k,v in doc.items() if k!="receipt_hash"})!=job["receipt_hash"]):
        raise access.AccessDenied("collection receipt changed")
    return doc


def draft(directory,conn,row,plan,mod):
    """Project exact recorded strings into the existing scientific import schema.

    Claim arrays remain empty: only separately reviewed annotation is allowed.
    Unparseable native certificates stay explicit extraction failures.
    """
    rec=receipt(directory,conn,row)
    by={r["slot"]:r for r in rec["outcomes"]}
    doc=studio._citation_template(plan) if mod is studio.citation else mod.response_template(plan)
    if mod is studio.citation:
        for r in doc["traces"]:
            result=by[r["query_id"]]
            r.update(model_status="complete" if result["status"]=="complete" else "provider_error",
                     answer=result["answer"],atomic_claims=[])
    else:
        for r in doc["responses"]:
            result=by[r["slot"]]
            status="complete" if result["status"]=="complete" else "provider_error"
            r["status"]=status
            answer=result["answer"]
            if mod is studio.scribe:
                r.update(note=answer,claims=[])
            elif mod is studio.robustness:
                r["answer"]=answer
            elif mod is studio.decision and status=="complete":
                r["raw_response"]=answer
                try:
                    parsed=manual._parse(answer.encode("utf-8"))
                    mod._strict(parsed,("verdict","certificate","requested_facts"))
                    if (parsed["verdict"] not in mod.VERDICTS or not isinstance(parsed["requested_facts"],list)
                            or any(not isinstance(x,str) or x not in {f["id"] for f in plan["spec"]["rule_bundle"]["facts"]}
                                   for x in parsed["requested_facts"])
                            or len(set(parsed["requested_facts"]))!=len(parsed["requested_facts"])):
                        raise ValueError("invalid native response")
                    r.update(parsed)
                except (ValueError,RuntimeError,TypeError,KeyError):
                    r["status"]="extraction_failure"
    return doc


def verify_import(directory,conn,row,plan,mod,responses):
    expected=draft(directory,conn,row,plan,mod)
    # Permit only the existing modules' independently reviewed claim arrays.
    # Everything else, including outcomes, raw answers and source context, must
    # remain byte-equivalent in canonical JSON, not manually repaired.
    if mod in (studio.citation,studio.scribe):
        import copy
        projected=copy.deepcopy(responses)
        key,claims=("traces","atomic_claims") if mod is studio.citation else ("responses","claims")
        for item in projected[key]:
            item[claims]=[]
    else:
        projected=responses
    if buyer_plan._canonical(projected)!=buyer_plan._canonical(expected):
        raise access.AccessDenied("import changes recorded candidate outcomes")
