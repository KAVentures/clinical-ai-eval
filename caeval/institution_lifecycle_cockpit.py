"""Read-only institutional lifecycle cockpit.

Join verified administrative inventories by exact programme, vendor and
declared target. Independent source reads are not a transaction across the
whole institution. No workflow is executed and no evidence is promoted.
"""
from . import diagnostic_imaging as d, unified_assessment as u
from . import institution_access as access

SECTIONS=("programmes","policies","monitors","successors")
STATES=("unlinked","stale_plan","source_available","source_changed","source_unavailable")
LIMITATIONS=[
    "Independent source reads are not an atomic institutional snapshot.",
    "Source coverage, referral dispositions and monitoring thresholds are administrative indicators, not clinical acceptance.",
    "Absent records and unavailable inventories do not establish safety or absence of work.",
    "Version-specific records never transfer across programme or vendor boundaries.",
    "No clinical deployment, procurement or workflow execution is authorized.",
]


def _key(row):
    return row["programme_id"],row["vendor_id"]


def _map(rows):
    out={}
    for row in rows:
        k=_key(row)
        d._require(k not in out,"duplicate lifecycle scope")
        out[k]=row
    return out


def _task(code,priority,route,role,subject=None):
    return {"code":code,"priority":priority,"route":route,"role":role,"subject":subject}


def _vendor(status,vid,policies,monitors,successors,availability):
    p=status["programme"]
    ident=p["programme_id"]
    plans=[r for r in p["plans"] if r["vendor_id"]==vid]
    target=plans[0]["target"]
    d._require(all(x["target"]==target for x in plans),"inconsistent vendor target")
    ids={r["plan_id"] for r in plans}
    rows=[r for r in status["requirements"] if r["requirement"]["plan_id"] in ids]
    gaps=[r for r in p["unplanned_canonical_suites"] if r["plan_id"] in ids]
    states={k:0 for k in STATES}
    reqs=[]
    tasks=[]
    route="/unified-assessment-detail?id="+ident
    for row in rows:
        d._require(row["vendor_id"]==vid and row["target"]==target and row["state"] in states,
                   "requirement scope mismatch")
        states[row["state"]]+=1
        req=row["requirement"]
        source=row["locked_source"]
        reqs.append({"id":req["id"],"kind":req["kind"],"family":req["family"],
                     "state":row["state"],"mode":source.get("mode") if source else None,
                     "claim":source.get("claim") if source else None})
    if status["state"]!="current":
        tasks.append(_task("programme_scope_stale","high",route,"admin"))
    if gaps:
        tasks.append(_task("canonical_suite_gaps","high",route,"admin"))
    if states["source_changed"] or states["source_unavailable"]:
        tasks.append(_task("locked_evidence_requires_investigation","high",route,"admin"))
    if states["unlinked"]:
        tasks.append(_task("associate_verified_sources","normal",route,"admin"))
        tasks.append(_task("collect_native_evidence","normal","/unified-assessment-work?id="+ident,"operator"))
    policy=policies.get((ident,vid))
    if policy is not None:
        d._require(policy["target"]==target,"policy target mismatch")
    if availability["policies"]!="available":
        tasks.append(_task("policy_inventory_unavailable","high","/hospital-referral","admin"))
    elif policy is None:
        tasks.append(_task("consider_local_referral_policy","normal","/hospital-referral","admin"))
    elif policy["state"]=="registered":
        tasks.append(_task("prepare_policy_evidence_review","normal","/hospital-referral-detail?id="+policy["id"],"admin"))
    elif not policy["snapshot_current"]:
        tasks.append(_task("policy_snapshot_stale","high","/hospital-referral-detail?id="+policy["id"],"admin"))
    elif policy["state"]=="reviewing":
        if policy["n_reviewed"]<policy["n_gates"]:
            tasks.append(_task("await_assigned_gate_reviews","normal","/hospital-referral-detail?id="+policy["id"],"admin"))
        else:
            tasks.append(_task("review_administrative_disposition","normal","/hospital-referral-detail?id="+policy["id"],"admin"))
    mon=monitors.get((ident,vid))
    if mon is not None:
        d._require(mon["target"]==target,"monitor target mismatch")
    if availability["monitors"]!="available":
        tasks.append(_task("monitor_inventory_unavailable","high","/lifecycle-monitor","admin"))
    elif mon is not None:
        dest="/lifecycle-monitor-detail?id="+mon["id"]
        if mon["version_reassessment_required"]:
            tasks.append(_task("reported_version_change","critical",dest,"operator"))
        if mon["attention_required"]:
            tasks.append(_task("monitor_attention_required","high",dest,"operator"))
        if mon["n_missing_windows"]:
            tasks.append(_task("missing_reporting_windows","high",dest,"operator"))
        if mon["schedule_ended"]:
            tasks.append(_task("monitor_schedule_ended","normal",dest,"admin"))
    # A monitor is optional until external authorization. Absence is never
    # interpreted as a missing deployment or as a safety blocker.
    lineage=[{"baseline_id":r["baseline_id"],"successor_id":r["successor_id"],
              "digest":r["digest"]} for r in successors if ident in (r["baseline_id"],r["successor_id"])]
    if availability["successors"]!="available":
        tasks.append(_task("version_lineage_unavailable","high",route,"admin"))
    order={"critical":0,"high":1,"normal":2}
    tasks.sort(key=lambda x:(order[x["priority"]],x["code"]))
    return {"programme_id":ident,"vendor_id":vid,"target":target,"programme_state":status["state"],
            "requirements":reqs,"source_counts":states,"canonical_gaps":gaps,
            "policy":policy,"monitor":mon,"programme_lineage":lineage,"tasks":tasks,
            "decision_grade":False,"deployment_authorized":False}


def project(inventories):
    d._keys(inventories,"programmes policies monitors successors")
    availability={}
    for name,section in inventories.items():
        d._keys(section,"state records")
        d._require(section["state"] in ("available","unavailable") and isinstance(section["records"],list)
                   and (section["state"]=="available" or not section["records"]),"invalid source inventory state")
        availability[name]=section["state"]
    programmes=inventories["programmes"]["records"]
    policies=_map(inventories["policies"]["records"])
    monitors=_map(inventories["monitors"]["records"])
    successors=inventories["successors"]["records"]
    out=[]
    seen=set()
    programme_ids=set()
    for status in programmes:
        p=status["programme"]
        d._require(p["programme_id"] not in programme_ids,"duplicate programme")
        programme_ids.add(p["programme_id"])
        for vid in sorted({r["vendor_id"] for r in p["plans"]}):
            row=_vendor(status,vid,policies,monitors,successors,availability)
            seen.add(_key(row))
            out.append(row)
    unmatched={}
    for name,by in (("policies",policies),("monitors",monitors)):
        unmatched[name]=len(set(by)-seen)
    out.sort(key=lambda r:(r["programme_id"],r["vendor_id"]))
    return d._sealed({"schema":"institution-lifecycle-cockpit/1","source_availability":availability,
                      "unmatched_record_counts":unmatched,"vendors":out,
                      "partial":any(v!="available" for v in availability.values()) or any(unmatched.values()),
                      "decision_grade":False,"deployment_authorized":False,"limitations":LIMITATIONS[:]})


def overview(directory,actor):
    from . import institution_hospital_referral as referral, institution_lifecycle_monitor as monitor
    from . import unified_successor as successor
    # Authenticate before any potentially failing source call. A source error
    # must not turn an unauthorized caller into an authorized empty response.
    with access.db(directory) as conn:
        u._actor(conn,actor,"admin","operator","observer")
    inventories={}
    for name,fn in (("programmes",u.overview),("policies",referral.overview),
                    ("monitors",monitor.overview),("successors",successor.inventory)):
        try:
            records=fn(directory,actor)
            inventories[name]={"state":"available","records":records}
        except u.ERRORS:
            inventories[name]={"state":"unavailable","records":[]}
    return project(inventories)
