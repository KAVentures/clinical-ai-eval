"""Prospective version reassessment, without implicit equivalence or deployment claims.

A successor copies the original scope and intake, but gets a new content-addressed
plan under current scientific definitions. Historical plans remain immutable.
No model calls, risk activation, package transfer, or scoring occur here.
"""
import json
from . import (buyer_plan as plans, buyer_agent as buyer, buyer_manual as manual,
               buyer_connector as connector, institution_access as access,
               institution_setup as setup, mixed_evaluation as mixed,
               research_buyer_coverage as research)

EVENT="version-reassessment-frozen"
SCHEMA="version-reassessment/1"
MAX_SUCCESSORS=100


def _deny(message):
    raise access.AccessDenied(message)


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS version_reassessments(
        baseline_id TEXT NOT NULL, id TEXT NOT NULL PRIMARY KEY,
        successor_id TEXT NOT NULL, payload TEXT NOT NULL,
        digest TEXT NOT NULL UNIQUE, audit_seq INTEGER NOT NULL UNIQUE,
        UNIQUE(baseline_id,successor_id))""")


def _baseline(directory,identifier):
    row=plans.load(directory,identifier)
    state=buyer.load(directory)
    if (row["buyer_scope"]!={k:state.get(k,"") for k in research.SCOPE_KEYS}
            or row["vendor_id"] not in {v["vendor_id"] for v in state["vendors"]}):
        _deny("baseline belongs to a different institutional scope")
    return row


def _spec(baseline,version):
    p=baseline["plan"]
    version=plans._text(version,"target_version",300)
    if version==p["target"]["version"]:
        _deny("successor version must differ from baseline")
    return {"vendor_id":baseline["vendor_id"],"target_name":p["target"]["name"],
            "target_version":version,"profiles":p["target_profile"]["types"],
            "answers":p["intake"]}


def _definition_delta(old,new):
    before=old["family_definition_hashes"]
    after=new["family_definition_hashes"]
    return {"tool_version_changed":old["tool_version"]!=new["tool_version"],
            "selection_rules_changed":old["selection_rules_hash"]!=new["selection_rules_hash"],
            "added_families":sorted(set(after)-set(before)),
            "removed_families":sorted(set(before)-set(after)),
            "changed_families":sorted(k for k in set(before)&set(after) if before[k]!=after[k]),
            "selection_changed":old["plan"]["selection"]!=new["plan"]["selection"]}


def _material(directory,baseline_id,version):
    old=_baseline(directory,baseline_id)
    spec=_spec(old,version)
    new=plans.preview(directory,spec,save=False)
    if new["buyer_scope"]!=old["buyer_scope"]:
        _deny("institutional scope changed during planning")
    delta=_definition_delta(old,new)
    payload={"schema":SCHEMA,"baseline_id":baseline_id,"successor_id":new["plan_id"],
             "id":plans._digest([baseline_id,spec["target_version"]]),
             "vendor_id":old["vendor_id"],"target_name":spec["target_name"],
             "baseline_version":old["plan"]["target"]["version"],
             "successor_version":spec["target_version"],
             "profiles":spec["profiles"],"intake_hash":plans._digest(spec["answers"]),
             "definition_delta":delta,
             "baseline_selection":old["plan"]["selection"],
             "successor_selection":new["plan"]["selection"],
             "direct_comparison_authorized":False,"prior_evidence_transferred":False,
             "network_authorized":False,"decision_grade":False}
    return old,spec,{**payload,"digest":plans._digest(payload)}


def _event(doc):
    return doc["baseline_id"]+":"+doc["id"]+":"+doc["digest"]


def _history(conn,baseline_id):
    rows=conn.execute("SELECT * FROM version_reassessments WHERE baseline_id=? ORDER BY id",
                      (baseline_id,)).fetchall()
    events=conn.execute("SELECT seq,object_id FROM audit WHERE action=? AND outcome='ok' AND substr(object_id,1,65)=?",
                        (EVENT,baseline_id+":")).fetchall()
    if len(rows)!=len(events) or len(rows)>MAX_SUCCESSORS:
        _deny("reassessment inventory differs from audit history")
    seen=set()
    result=[]
    for row in rows:
        doc=manual._parse(row["payload"].encode("utf-8"))
        if (not isinstance(doc,dict) or doc.get("schema")!=SCHEMA or
                any(doc.get(k)!=row[k] for k in ("id","baseline_id","successor_id","digest")) or
                plans._digest({k:v for k,v in doc.items() if k!="digest"})!=doc["digest"] or
                doc["id"]!=plans._digest([baseline_id,doc["successor_version"]]) or
                any(doc[k] is not False for k in ("direct_comparison_authorized","prior_evidence_transferred","network_authorized","decision_grade"))):
            _deny("reassessment record integrity mismatch")
        seen.add((row["audit_seq"],_event(doc)))
        result.append(doc)
    if seen!={(r["seq"],r["object_id"]) for r in events}:
        _deny("reassessment audit mismatch")
    return result


def _validate_successor(directory,old,doc):
    new=_baseline(directory,doc["successor_id"])
    p=new["plan"]
    baseline=old["plan"]
    if (doc["baseline_id"]!=old["plan_id"] or doc["vendor_id"]!=old["vendor_id"]
            or doc["target_name"]!=baseline["target"]["name"]
            or doc["baseline_version"]!=baseline["target"]["version"]
            or doc["baseline_selection"]!=baseline["selection"]
            or new["vendor_id"]!=old["vendor_id"]
            or p["target"]["name"]!=doc["target_name"] or p["target"]["version"]!=doc["successor_version"]
            or p["target_profile"]["types"]!=doc["profiles"]
            or p["target_profile"]["types"]!=baseline["target_profile"]["types"]
            or p["intake"]!=baseline["intake"] or plans._digest(p["intake"])!=doc["intake_hash"]
            or p["selection"]!=doc["successor_selection"]
            or _definition_delta(old,new)!=doc["definition_delta"]):
        _deny("frozen successor no longer matches reassessment scope")
    return new


def preview(directory,actor,baseline_id,version):
    with access.db(directory) as conn:
        _schema(conn)
        setup._actor(conn,actor)
        old,_,doc=_material(directory,baseline_id,version)
        prior=next((r for r in _history(conn,baseline_id) if r["id"]==doc["id"]),None)
        if prior is not None:
            _validate_successor(directory,old,prior)
            if prior!=doc:
                _deny("successor already frozen under different definitions; create a new explicit scope")
        return {**doc,"frozen":prior is not None}


def freeze(directory,actor,baseline_id,version,digest,*,confirmed=False):
    if confirmed is not True:
        _deny("explicit reassessment approval required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        setup._actor(conn,actor)
        old,spec,doc=_material(directory,baseline_id,version)
        if doc["digest"]!=digest:
            _deny("reassessment differs from approved preview")
        records=_history(conn,baseline_id)
        prior=next((r for r in records if r["id"]==doc["id"]),None)
        if prior is not None:
            if prior!=doc:
                _deny("previously frozen reassessment differs")
            _validate_successor(directory,old,prior)
        else:
            if len(records)>=MAX_SUCCESSORS:
                _deny("reassessment history limit reached")
            new=plans.preview(directory,spec)
            if new["plan_id"]!=doc["successor_id"]:
                _deny("successor plan changed during preparation")
            _validate_successor(directory,old,doc)
            access._audit(conn,actor["user_id"],EVENT,_event(doc))
            seq=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("INSERT INTO version_reassessments VALUES(?,?,?,?,?,?)",
                         (baseline_id,doc["id"],doc["successor_id"],plans._canonical(doc).decode("utf-8"),doc["digest"],seq))
        return {**doc,"frozen":True}


def _coverage(directory,records):
    lanes={r["plan_id"]:{"runnable":r["plan"]["selection"]["runnable_suites"]}
           for r in records if r["definitions_current"]}
    generic=mixed._generic(directory,lanes) if lanes else {}
    conversations=mixed._conversations(directory,lanes) if lanes else {}
    result={}
    for r in records:
        pid=r["plan_id"]
        selected=r["plan"]["selection"]
        receipts=generic.get(pid,[])+conversations.get(pid,[])
        result[pid]={"definitions_current":r["definitions_current"],
             "suites":[{"suite":s["suite"],
                        "state":"historical_definitions" if not r["definitions_current"] else
                           "verified_scoped_package" if any(x["suite"]==s["suite"] for x in receipts) else
                           "not_executed" if s["implemented"] else "required_but_not_run",
                        "packages":[x for x in receipts if x["suite"]==s["suite"]]}
                       for s in selected["required_suites"]],
             "decision_grade":False}
    return result


def history(directory,actor,baseline_id):
    with access.db(directory) as conn:
        _schema(conn)
        setup._actor(conn,actor)
        old=_baseline(directory,baseline_id)
        records=_history(conn,baseline_id)
        successors=[_validate_successor(directory,old,d) for d in records]
    coverage=_coverage(directory,[old,*successors])
    staged=connector.connectors(directory)
    rows=[]
    for d,new in zip(records,successors):
        matches=[{"connector_id":c["connector_id"],"protocol":c["protocol"]} for c in staged
                 if c["vendor_id"]==d["vendor_id"] and c["target_name"]==d["target_name"]
                 and c["target_version"]==d["successor_version"]]
        rows.append({"reassessment":d,"coverage":coverage[new["plan_id"]],
                     "matching_staged_connectors":matches})
    with access.db(directory) as conn:
        _schema(conn)
        setup._actor(conn,actor)
        if _history(conn,baseline_id)!=records:
            _deny("reassessment history changed during verification")
        access._audit(conn,actor["user_id"],"version-reassessment-history-viewed",baseline_id)
    return {"schema":"version-reassessment-history/1","baseline_id":baseline_id,
            "target_name":old["plan"]["target"]["name"],"baseline_version":old["plan"]["target"]["version"],
            "baseline_coverage":coverage[baseline_id],"successors":rows,
            "comparative_verdict":None,"decision_grade":False}
