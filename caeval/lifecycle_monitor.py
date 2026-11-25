"""Aggregate-only post-deployment monitoring and escalation.

External operational authorization and counts are attestations, not verified
clinical facts. No automatic intervention or deployment approval.
"""
from datetime import date,timedelta
from . import diagnostic_imaging as d

MAX_METRICS=12
MAX_WINDOWS=104
MAX_INCIDENTS=100
MAX_ACTIONS=300
MAX_COUNT=10000000
SEVERITIES=("low","moderate","serious","critical")
ACTIONS=("acknowledge","investigate","request_reassessment","request_pause","request_rollback","document_external_resolution")
LIMITATIONS=[
    "External deployment authorization, aggregate counts and incident reports are operator attestations.",
    "Descriptive threshold monitoring only; no statistical drift test or clinical safety determination.",
    "Missing and low-volume windows are not evidence of safety.",
    "Escalation records requests and external actions; no actual pause, rollback, deployment or reassessment execution.",
    "No patient identifiers or individual case data are accepted by the structured protocol.",
]


def _date(x):
    d._require(isinstance(x,str) and len(x)==10,"ISO calendar date required")
    day=date.fromisoformat(x)
    d._require(day.isoformat()==x,"canonical ISO date required")
    return day


def _count(x):
    return type(x) is int and 0<=x<=MAX_COUNT


def plan(programme,spec):
    d._keys(spec,"monitor_id programme_id vendor_id intended_use_id start_date window_days windows metrics authorization_digest external_authorization_attested")
    d._require(d._token(spec["monitor_id"]) and d._token(spec["vendor_id"]) and d._token(spec["intended_use_id"])
               and spec["programme_id"]==programme["programme_id"] and d._hex(spec["authorization_digest"])
               and spec["external_authorization_attested"] is True,"external operational authorization attestation required")
    vendors=[p for p in programme["plans"] if p["vendor_id"]==spec["vendor_id"]]
    d._require(bool(vendors),"exact registered vendor required")
    target=vendors[0]["target"]
    start=_date(spec["start_date"])
    d._require(type(spec["window_days"]) is int and 1<=spec["window_days"]<=31
               and type(spec["windows"]) is int and 1<=spec["windows"]<=MAX_WINDOWS,"bounded fixed reporting schedule required")
    end=start+timedelta(days=spec["window_days"]*spec["windows"])
    metrics=spec["metrics"]
    d._require(isinstance(metrics,list) and 1<=len(metrics)<=MAX_METRICS,"bounded metrics required")
    seen=set()
    for m in metrics:
        d._keys(m,"id direction threshold_bp min_denominator definition_digest")
        d._require(d._token(m["id"]) and m["id"] not in seen and m["direction"] in ("higher_is_worse","lower_is_worse")
                   and type(m["threshold_bp"]) is int and 0<=m["threshold_bp"]<=10000
                   and _count(m["min_denominator"]) and m["min_denominator"]>0 and d._hex(m["definition_digest"]),
                   "invalid metric threshold or definition")
        seen.add(m["id"])
    return d._sealed({"schema":"lifecycle-monitor-plan/1","programme_digest":programme["digest"],
                      "target":dict(target),"spec":{**spec,"metrics":sorted([dict(m) for m in metrics],key=lambda m:m["id"])},
                      "end_date_exclusive":end.isoformat(),"authorization_independently_verified":False,
                      "deployment_authorized":False,"decision_grade":False,"limitations":LIMITATIONS[:]})


def window(p,index):
    s=p["spec"]
    d._require(type(index) is int and 0<=index<s["windows"],"invalid window index")
    start=_date(s["start_date"])+timedelta(days=index*s["window_days"])
    return start,start+timedelta(days=s["window_days"])


def report(p,doc):
    d._keys(doc,"plan_digest target window_index metrics source_digest complete_aggregate_attested")
    d._require(doc["plan_digest"]==p["digest"] and doc["target"]==p["target"] and d._hex(doc["source_digest"])
               and doc["complete_aggregate_attested"] is True,"exact aggregate report attestation required")
    window(p,doc["window_index"])
    rows=doc["metrics"]
    known={m["id"] for m in p["spec"]["metrics"]}
    d._require(isinstance(rows,list) and len(rows)==len(known),"complete metric inventory required")
    seen=set()
    for r in rows:
        d._keys(r,"id numerator denominator")
        d._require(d._token(r["id"]) and r["id"] in known and r["id"] not in seen
                   and _count(r["numerator"]) and _count(r["denominator"]) and r["numerator"]<=r["denominator"],
                   "invalid metric count")
        seen.add(r["id"])
    return d._sealed({"schema":"lifecycle-monitor-report/1","report":{**doc,"metrics":sorted([dict(r) for r in rows],key=lambda r:r["id"])}})


def incident(p,doc):
    d._keys(doc,"plan_digest incident_id date severity category external_record_digest observed_version")
    d._require(doc["plan_digest"]==p["digest"] and d._token(doc["incident_id"]) and doc["severity"] in SEVERITIES
               and doc["category"] in ("clinical_safety","data_quality","privacy_security","workflow","availability","version_change","other")
               and d._hex(doc["external_record_digest"]) and d._token(doc["observed_version"]),"invalid incident metadata")
    _date(doc["date"])
    if doc["category"]=="version_change":
        d._require(doc["observed_version"]!=p["target"]["version"],"distinct changed version required")
    else:
        d._require(doc["observed_version"]==p["target"]["version"],"wrong monitored version; register version change separately")
    return d._sealed({"schema":"lifecycle-monitor-incident/1","incident":dict(doc)})


def action(p,doc,valid_alerts):
    d._keys(doc,"plan_digest alert_id action external_record_digest")
    d._require(doc["plan_digest"]==p["digest"] and doc["alert_id"] in valid_alerts
               and doc["action"] in ACTIONS and d._hex(doc["external_record_digest"]),
               "known alert and external action record required")
    return d._sealed({"schema":"lifecycle-monitor-action/1","action":dict(doc),
                      "external_action_verified":False})


def _alert(kind,key,severity):
    return {"id":kind+":"+key,"kind":kind,"severity":severity}


def assess(p,reports,incidents,actions,as_of):
    today=_date(as_of)
    d._require(isinstance(reports,list) and len(reports)<=MAX_WINDOWS and isinstance(incidents,list)
               and len(incidents)<=MAX_INCIDENTS and isinstance(actions,list) and len(actions)<=MAX_ACTIONS,
               "monitor inventory limit")
    rows={}
    alerts=[]
    for r in reports:
        d._require(report(p,r["report"])==r,"aggregate report replay mismatch")
        doc=r["report"]
        idx=doc["window_index"]
        d._require(idx not in rows and window(p,idx)[1]<=today,"duplicate or unfinished reporting window")
        rows[idx]=doc
    changes=[_date(r["incident"]["date"]) for r in incidents if r["incident"]["category"]=="version_change"]
    windows=[]
    for idx in range(p["spec"]["windows"]):
        start,end=window(p,idx)
        if end>today:
            continue
        rec=rows.get(idx)
        metrics=[]
        if rec is None:
            alerts.append(_alert("missing_window",str(idx),"moderate"))
        else:
            by={r["id"]:r for r in rec["metrics"]}
            for m in p["spec"]["metrics"]:
                r=by[m["id"]]
                n,den=r["numerator"],r["denominator"]
                if den<m["min_denominator"]:
                    state="insufficient_volume"
                    alerts.append(_alert("low_volume",str(idx)+":"+m["id"],"moderate"))
                else:
                    # Inclusive exact integer threshold; no floating comparison.
                    exceeded=(n*10000>=m["threshold_bp"]*den if m["direction"]=="higher_is_worse"
                              else n*10000<=m["threshold_bp"]*den)
                    state="threshold_alert" if exceeded else "within_threshold"
                    if exceeded:
                        alerts.append(_alert("threshold",str(idx)+":"+m["id"],"serious"))
                metrics.append({"id":m["id"],"numerator":n,"denominator":den,"rate":n/den if den else None,"state":state})
        overlap=bool(changes and end>min(changes))
        if overlap:
            alerts.append(_alert("version_overlap",str(idx),"critical"))
        windows.append({"index":idx,"start":start.isoformat(),"end_exclusive":end.isoformat(),
                        "state":"reported_version_overlap" if rec and overlap else "reported" if rec else "missing",
                        "version_overlap":overlap,"metrics":metrics})
    seen=set()
    for r in incidents:
        d._require(incident(p,r["incident"])==r,"incident replay mismatch")
        doc=r["incident"]
        iid=doc["incident_id"]
        d._require(iid not in seen and _date(p["spec"]["start_date"])<=_date(doc["date"])<=today,"invalid incident time or duplicate")
        seen.add(iid)
        alerts.append(_alert("version_change" if doc["category"]=="version_change" else "incident",iid,
                             "critical" if doc["category"]=="version_change" else doc["severity"]))
    # A late report removes a missing-window condition, but prior actions
    # remain valid historical records. All due missing-window IDs are eligible.
    valid={a["id"] for a in alerts}|{"missing_window:"+str(w["index"]) for w in windows}
    actions_by={}
    for r in actions:
        d._require(action(p,r["action"],valid)==r,"action replay mismatch")
        doc=r["action"]
        actions_by.setdefault(doc["alert_id"],[]).append(doc["action"])
    for a in alerts:
        history=actions_by.get(a["id"],[])
        a["recorded_actions"]=history
        # Only an external resolution attestation changes the documentation
        # state. It cannot clear a version-change or threshold condition.
        a["external_resolution_documented"]="document_external_resolution" in history
        a["requires_attention"]=a["kind"]!="incident" or not a["external_resolution_documented"]
    return d._sealed({"schema":"lifecycle-monitor-status/1","plan_digest":p["digest"],"as_of":as_of,
                      "windows":windows,"alerts":alerts,
                      "schedule_ended":today>=_date(p["end_date_exclusive"]),
                      "version_reassessment_required":any(a["kind"]=="version_change" for a in alerts),
                      "attention_required":any(a["requires_attention"] for a in alerts),
                      "clinical_safety_established":False,"deployment_authorized":False,"decision_grade":False})


def package(programme,p,reports,incidents,actions,as_of):
    d._require(plan(programme,p["spec"])==p,"monitor plan replay mismatch")
    return d._sealed({"schema":"lifecycle-monitor-evidence/1","programme":programme,"plan":p,
                      "reports":reports,"incidents":incidents,"actions":actions,
                      "status":assess(p,reports,incidents,actions,as_of),"decision_grade":False})


def verify(doc,expected_digest=None):
    programme=doc["programme"]
    d._require(d._digest({k:v for k,v in programme.items() if k!="digest"})==programme["digest"],"programme digest mismatch")
    rebuilt=package(programme,doc["plan"],doc["reports"],doc["incidents"],doc["actions"],doc["status"]["as_of"])
    d._require(rebuilt==doc and (expected_digest is None or expected_digest==doc["digest"]),"monitor evidence replay mismatch")
    return {"status":"STRUCTURE_REPLAY_VERIFIED","digest":doc["digest"],"expected_digest_matched":expected_digest is not None,
            "source_records_verified":False,"clinical_safety_established":False,"deployment_authorized":False}


def configure(parser):
    parser.add_argument("evidence")
    parser.add_argument("--expected-digest")
    parser.set_defaults(func=cli)


def cli(args):
    import json
    try:
        print(json.dumps(verify(d._read(args.evidence),args.expected_digest),allow_nan=False))
    except (ValueError,RuntimeError,KeyError,TypeError,OSError,AttributeError,RecursionError,OverflowError):
        raise SystemExit("Lifecycle monitoring evidence verification refused.")
