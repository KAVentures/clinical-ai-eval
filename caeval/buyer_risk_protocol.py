"""Buyer-owned frozen clinical hazard planning, separate from scoring.

No default numerical safety thresholds. Required suites remain visible even when
a buyer has not specified a hazard. This is a planning record, not execution
authorization, procurement criteria activation or proof of preregistration.
"""
import json
import math
import re
from . import buyer_plan, buyer_agent as buyer, buyer_manual as manual
from . import institution_access as access, institution_setup as setup
from . import research_buyer_coverage as bridge

SCHEMA="buyer-risk-protocol/1"
EVENT="buyer-risk-protocol-frozen"
SEVERITIES=("critical","high","moderate","low")
OPERATORS=("<=","<",">=",">")
MAX_HAZARDS=40
MAX_PROTOCOLS=200
# Guidance only: no numerical threshold or claim that the suite measures it.
GUIDANCE={
    "missing_information":"Unsafe commitment when determining facts are missing",
    "conflicting_evidence":"Unsafe advice despite contradictory evidence",
    "patient_red_flag":"Failure to escalate a time-sensitive patient red flag",
    "citation_verification":"Unsupported, nonexistent or outdated citation",
    "retrieval_failure":"Missing clinically relevant source information",
    "decision_certifiability":"Clinical action without sufficient verified premises",
    "omission":"Omission of clinically important encounter information",
    "fabrication":"Unsupported assertion in the generated clinical note",
    "unsupported_inference":"Clinical conclusion unsupported by available facts",
    "rewording_robustness":"Clinically consequential degradation under equivalent rewording",
}
LIMITATIONS=[
    "Buyer-authored planning thresholds are not automatically applied to canonical scoring.",
    "An endpoint description does not establish measurement feasibility or clinical validity.",
    "No local or external proof that the buyer has not previously seen candidate results.",
    "Missing hazard coverage, unsupported suites and uncertainty remain explicit.",
    "No overall score, purchasing recommendation or deployment authorization.",
]


def _fail(msg):
    raise access.AccessDenied(msg)


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS buyer_risk_protocols(
        id TEXT PRIMARY KEY, plan_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
        audit_seq INTEGER NOT NULL UNIQUE)""")


def _text(value,limit):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        _fail("explicit bounded buyer input required")
    return value.strip()


def _suite_rows(bp):
    return {x["suite"]:x for x in bp["plan"]["selection"]["required_suites"]}


def _hazards(values,bp):
    if not isinstance(values,list) or not 1<=len(values)<=MAX_HAZARDS:
        _fail("one or more bounded buyer-authored hazards required")
    suites=_suite_rows(bp)
    seen=set()
    result=[]
    for h in values:
        if not isinstance(h,dict) or set(h)!={"hazard_id","suite","description","severity","endpoint","denominator","operator","threshold","rationale","mitigation"}:
            _fail("unexpected hazard fields")
        hid=manual._identifier(h["hazard_id"])
        if hid in seen or h["suite"] not in suites or h["severity"] not in SEVERITIES or h["operator"] not in OPERATORS:
            _fail("invalid or duplicate hazard, severity, suite or operator")
        seen.add(hid)
        threshold=h["threshold"]
        if type(threshold) not in (int,float) or not math.isfinite(threshold) or not 0<=threshold<=1:
            _fail("buyer threshold must be an explicit proportion between zero and one")
        result.append({"hazard_id":hid,"suite":h["suite"],"description":_text(h["description"],1000),
                       "severity":h["severity"],"endpoint":_text(h["endpoint"],300),
                       "denominator":_text(h["denominator"],1000),"operator":h["operator"],
                       "threshold":threshold,"rationale":_text(h["rationale"],2000),
                       "mitigation":_text(h["mitigation"],2000)})
    return sorted(result,key=lambda h:h["hazard_id"])


def _material(bp,hazards,governance):
    if not isinstance(governance,dict) or set(governance)!={"population","setting_boundaries","oversight","data_governance","jurisdiction_review","stop_rules"}:
        _fail("complete explicit governance planning required")
    gov={k:_text(v,2000) for k,v in governance.items()}
    normalized=_hazards(hazards,bp)
    suites=_suite_rows(bp)
    return {"schema":SCHEMA,"plan_id":bp["plan_id"],"vendor_id":bp["vendor_id"],
            "target":{k:bp["plan"]["target"][k] for k in ("name","version")},
            "buyer_scope":bp["buyer_scope"],"selection_rules_hash":bp["selection_rules_hash"],
            "family_definition_hashes":bp["family_definition_hashes"],"hazards":normalized,
            "governance":gov,"coverage":[{"suite":sid,"canonical_runnable":s["implemented"],
                "blocked_reason":s["blocked_reason"],
                "hazard_ids":[h["hazard_id"] for h in normalized if h["suite"]==sid],
                "measurement_verified":False} for sid,s in sorted(suites.items())],
            "decision_grade":False,"execution_authorized":False,"deployment_authorized":False,
            "limitations":LIMITATIONS[:]}


def _check_inventory(conn):
    rows=conn.execute("SELECT id,audit_seq FROM buyer_risk_protocols").fetchall()
    events=conn.execute("SELECT object_id,seq FROM audit WHERE action=? AND outcome='ok'",(EVENT,)).fetchall()
    if len(rows)!=len(events) or {(r["id"],r["audit_seq"]) for r in rows}!={(e["object_id"],e["seq"]) for e in events}:
        _fail("risk protocol inventory differs from audit history")


def freeze(directory,actor,plan_id,hazards,governance,*,confirmed=False):
    if confirmed is not True:
        _fail("explicit buyer threshold and governance approval required")
    with access.db(directory) as conn:
        setup._actor(conn,actor)
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _check_inventory(conn)
        bp=bridge._buyer_plan(directory,plan_id)
        doc=_material(bp,hazards,governance)
        prior=conn.execute("SELECT id FROM buyer_risk_protocols WHERE plan_id=?",(plan_id,)).fetchone()
        if prior:
            _fail("risk protocol already frozen; create a distinct plan for changed scope")
        if conn.execute("SELECT count(*) FROM buyer_risk_protocols").fetchone()[0]>=MAX_PROTOCOLS:
            _fail("risk protocol limit reached")
        doc["approved_by"]=actor["user_id"]
        ident=buyer_plan._digest(doc)
        access._audit(conn,actor["user_id"],EVENT,ident)
        seq=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO buyer_risk_protocols VALUES(?,?,?,?)",
                     (ident,plan_id,buyer_plan._canonical(doc).decode("utf-8"),seq))
        return ident


def _read(conn,row,directory):
    doc=json.loads(row["payload"])
    if buyer_plan._digest(doc)!=row["id"] or doc["plan_id"]!=row["plan_id"]:
        _fail("frozen risk protocol changed")
    audits=conn.execute("SELECT seq,actor FROM audit WHERE action=? AND object_id=? AND outcome='ok'",
                        (EVENT,row["id"])).fetchall()
    if len(audits)!=1 or audits[0]["seq"]!=row["audit_seq"] or audits[0]["actor"]!=doc["approved_by"]:
        _fail("risk protocol audit changed")
    # Reconstruct from original plan even if scientific definitions have since
    # changed. Currentness is checked separately and never silently updated.
    bp=buyer_plan.load(directory,row["plan_id"],include_freshness=False)
    expected=_material(bp,doc["hazards"],doc["governance"])
    if {k:v for k,v in doc.items() if k!="approved_by"}!=expected:
        _fail("risk protocol differs from frozen plan")
    try:
        bridge._buyer_plan(directory,row["plan_id"])
        current=True
    except (ValueError,RuntimeError,OSError,KeyError,TypeError):
        current=False
    return {"id":row["id"],"protocol":doc,"scope_current":current}


def view(directory,actor,plan_id):
    with access.db(directory) as conn:
        setup._actor(conn,actor)
        _schema(conn)
        _check_inventory(conn)
        manual._identifier(plan_id,digest=True)
        row=conn.execute("SELECT * FROM buyer_risk_protocols WHERE plan_id=?",(plan_id,)).fetchone()
        if row:
            result=_read(conn,row,directory)
        else:
            bp=bridge._buyer_plan(directory,plan_id)
            result={"id":None,"plan_id":plan_id,"target":bp["plan"]["target"],
                    "buyer_scope":bp["buyer_scope"],"suites":[{"suite":sid,"runnable":s["implemented"],
                      "guidance":GUIDANCE.get(sid,"Describe the clinical harm this suite should investigate")}
                      for sid,s in sorted(_suite_rows(bp).items())]}
        access._audit(conn,actor["user_id"],"buyer-risk-protocol-opened",plan_id)
        return result


def committee_inventory(directory,actor):
    with access.db(directory) as conn:
        from . import institution_committee as committee
        committee._actor(conn,actor)
        _schema(conn)
        _check_inventory(conn)
        result=[]
        for row in conn.execute("SELECT * FROM buyer_risk_protocols ORDER BY id").fetchall():
            item={"id":row["id"],"state":"unavailable","decision_grade":False}
            try:
                saved=_read(conn,row,directory)
                p=saved["protocol"]
                item.update(state="current" if saved["scope_current"] else "stale",
                    vendor_id=p["vendor_id"],target_version=p["target"]["version"],
                    plan_id=p["plan_id"],hazard_count=len(p["hazards"]),
                    uncovered_suites=[s["suite"] for s in p["coverage"] if not s["hazard_ids"]],
                    blocked_suites=[s["suite"] for s in p["coverage"] if not s["canonical_runnable"]],
                    critical_hazards=sum(h["severity"]=="critical" for h in p["hazards"]),
                    measurement_verified=False)
            except (ValueError,RuntimeError,OSError,KeyError,TypeError,AttributeError,IndexError):
                pass
            result.append(item)
        return result
