"""Scheduled, explicitly authorized local regression assessments.

A schedule freezes an exact candidate exchange, local connector and judge
panel against a verified baseline job. A trusted supervised CLI worker claims
one due occurrence and performs a fresh collection, canonical evaluation and
descriptive paired-cell comparison. No automatic retries, clinical approval,
or continuous patient monitoring.
"""
from __future__ import annotations

import json
import math
import tempfile
import time
import uuid
from pathlib import Path

from . import buyer_agent as buyer, buyer_connector as connector, buyer_jobs as jobs
from . import buyer_assessment as assessment, buyer_manual as manual, buyer_plan
from . import institution_access as access, institution_lifecycle as lifecycle
from . import buyer_research, manifest, project

MAX_SCHEDULES=100
MAX_OCCURRENCES=500
MAX_RESULTS_BYTES=32*1024*1024
MIN_INTERVAL=3600
MAX_INTERVAL=90*86400
METRICS=("panel_any_unsafe","panel_majority_unsafe","potentially_harmful_treatment")


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS monitoring_schedules(
        id TEXT PRIMARY KEY, binding TEXT NOT NULL, digest TEXT NOT NULL,
        owner TEXT NOT NULL REFERENCES users(id), next_due INTEGER NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS monitoring_occurrences(
        id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL REFERENCES monitoring_schedules(id),
        due INTEGER NOT NULL, skipped_intervals INTEGER NOT NULL, state TEXT NOT NULL,
        collection_id TEXT, job_id TEXT, report TEXT, report_digest TEXT,
        UNIQUE(schedule_id,due))""")


def _account(conn,actor,roles):
    access.require(actor,*roles)
    row=conn.execute("SELECT role,enabled FROM users WHERE id=?",(actor["user_id"],)).fetchone()
    if not row or not row["enabled"] or row["role"]!=actor["role"]:
        raise access.AccessDenied("active authorized account required")


def _baseline(directory,job_id):
    row=jobs.status(directory,job_id)
    if row["status"]!="completed" or not row["result"]["packages"]:
        raise buyer.BuyerError("verified completed baseline required")
    ws=jobs._job_path(directory,job_id)
    bound=manual._read(ws/"binding.json")
    exchange=manual._load(directory,bound["exchange_id"])
    if bound["assessment_id"]!=row["assessment_id"]:
        raise buyer.BuyerError("baseline assessment identity changed")
    cfg=project.load(ws)
    cfg.require_valid()
    return row,bound,exchange,cfg.data


def _material(directory,baseline_job,exchange_id,connector_id,panel_id,governance):
    base,bound,old,base_cfg=_baseline(directory,baseline_job)
    connection,_,exchange=connector._binding(directory,exchange_id,connector_id)
    # A deliberate version change is permitted only with identical scientific
    # scope, prompts, case pack, definitions, judge panel and governance.
    for key in ("vendor_id","pack_content_hash","family_ids","slots","scientific_definitions"):
        if old[key]!=exchange[key]:
            raise buyer.BuyerError("candidate exchange differs from baseline scientific conditions")
    if old["target"]["name"]!=exchange["target"]["name"]:
        raise buyer.BuyerError("candidate product differs from baseline")
    plan=buyer_plan.load(directory,exchange["plan_id"])
    if (plan["plan"]["target_profile"]["types"]!=base_cfg["target_profile"]["types"]
            or plan["plan"]["intake"]!=base_cfg["intake"]
            or assessment._governance(governance)!=bound["governance"]):
        raise buyer.BuyerError("candidate intended use or governance differs from baseline")
    raw,panel=assessment._panel(directory,panel_id)
    if assessment._sha(raw)!=bound["file_sha256"]["panel.toml"]:
        raise buyer.BuyerError("judge panel differs from baseline")
    if any(manifest.hash_file(manifest.definition_file(k))!=v for k,v in bound["scientific_definitions"].items()):
        raise buyer.BuyerError("baseline scientific definitions no longer current")
    return {"schema":"longitudinal-regression/1","baseline_job":baseline_job,
            "baseline_packages":{p["family_id"]:p["manifest_hash"] for p in base["result"]["packages"]},
            "baseline_assessment":base["assessment_id"],
            "baseline_version":old["target"]["version"],"target_version":exchange["target"]["version"],
            "vendor_id":exchange["vendor_id"],"exchange_id":exchange_id,
            "connector_id":connector_id,"connector_binding":connection,
            "panel_id":panel_id,"panel_hash":assessment._sha(raw),
            "governance":bound["governance"],"scientific_definitions":bound["scientific_definitions"],
            "mode":base["mode"],"real_judges":any(not j["mock"] for j in panel["judges"]),
            "decision_grade":False}


def create(directory,actor,spec,*,confirmed=False):
    fields={"baseline_job","exchange_id","connector_id","panel_id","governance",
            "pilot_id","interval_seconds","first_due","alert_delta"}
    if confirmed is not True or not isinstance(spec,dict) or set(spec)!=fields:
        raise access.AccessDenied("explicit frozen monitoring scope and approval required")
    interval=spec["interval_seconds"]
    first=spec["first_due"]
    delta=spec["alert_delta"]
    now=int(time.time())
    if (type(interval) is not int or not MIN_INTERVAL<=interval<=MAX_INTERVAL
            or type(first) is not int or not now-60<=first<=now+MAX_INTERVAL
            or type(delta) not in (int,float) or not math.isfinite(delta) or not 0<delta<=1):
        raise access.AccessDenied("invalid cadence, first due time or descriptive alert threshold")
    access.require(actor,"admin")
    with access.db(directory) as conn:
        _account(conn,actor,("admin",))
    material=_material(directory,spec["baseline_job"],spec["exchange_id"],spec["connector_id"],spec["panel_id"],spec["governance"])
    identifier="monitor-"+uuid.uuid4().hex
    with access.db(directory) as conn:
        _schema(conn)
        lifecycle._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _account(conn,actor,("admin",))
        pilot=lifecycle._get(conn,"lifecycle_pilots",spec["pilot_id"])
        if pilot["state"]=="closed" or pilot["vendor_id"]!=material["vendor_id"] or pilot["product_version"]!=material["target_version"]:
            raise access.AccessDenied("monitoring candidate must match open lifecycle pilot and exact version")
        if conn.execute("SELECT count(*) FROM monitoring_schedules").fetchone()[0]>=MAX_SCHEDULES:
            raise access.AccessDenied("schedule limit")
        binding={**material,"id":identifier,"pilot_id":pilot["id"],"interval_seconds":interval,
                 "first_due":first,"alert_delta":float(delta),"owner":actor["user_id"]}
        digest=buyer_plan._digest(binding)
        conn.execute("INSERT INTO monitoring_schedules(id,binding,digest,owner,next_due) VALUES(?,?,?,?,?)",
                     (identifier,json.dumps(binding),digest,actor["user_id"],first))
        access._audit(conn,actor["user_id"],"monitoring-schedule-frozen",identifier)
    return {"id":identifier,"binding_hash":digest,"decision_grade":False}


def _schedule(conn,identifier):
    manual._identifier(identifier)
    row=conn.execute("SELECT * FROM monitoring_schedules WHERE id=?",(identifier,)).fetchone()
    if not row:
        raise access.AccessDenied("unknown schedule")
    row=dict(row)
    b=json.loads(row["binding"])
    if buyer_plan._digest(b)!=row["digest"] or b["id"]!=identifier or b["owner"]!=row["owner"]:
        raise access.AccessDenied("schedule binding changed")
    row["binding"]=b
    return row


def disable(directory,actor,identifier):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _account(conn,actor,("admin",))
        _schedule(conn,identifier)
        conn.execute("UPDATE monitoring_schedules SET enabled=0 WHERE id=?",(identifier,))
        access._audit(conn,actor["user_id"],"monitoring-schedule-disabled",identifier)


def _current(directory,b):
    material=_material(directory,b["baseline_job"],b["exchange_id"],b["connector_id"],b["panel_id"],b["governance"])
    if any(b.get(k)!=v for k,v in material.items()):
        raise buyer.BuyerError("frozen monitoring conditions changed")


def _eligible(conn,row):
    if not row["enabled"]:
        raise access.AccessDenied("schedule disabled")
    b=row["binding"]
    _account(conn,{"user_id":row["owner"],"role":"admin"},("admin",))
    pilot=lifecycle._get(conn,"lifecycle_pilots",b["pilot_id"])
    if pilot["state"]=="closed" or pilot["vendor_id"]!=b["vendor_id"] or pilot["product_version"]!=b["target_version"]:
        raise access.AccessDenied("lifecycle scope changed")


def _read_results(directory,job,family,expected_hash):
    ws=jobs._job_path(directory,job)/"out"/("run_"+family)
    meta=buyer_research._preflight(ws)
    if meta["manifest_hash"]!=expected_hash or manifest.verify_manifest(ws)["verdict"]!=manifest.VALID:
        raise buyer.BuyerError("comparison evidence changed")
    path=ws/"results.jsonl"
    if path.is_symlink() or not path.is_file():
        raise buyer.BuyerError("missing results")
    with path.open("rb") as f:
        raw=f.read(MAX_RESULTS_BYTES+1)
    if len(raw)>MAX_RESULTS_BYTES:
        raise buyer.BuyerError("results exceed monitoring limit")
    with tempfile.TemporaryDirectory(prefix=".monitor-capture-",dir=Path(directory)) as tmp:
        p=Path(tmp)/"results.jsonl"
        p.write_bytes(raw)
        if manifest.hash_file(p)!=meta["artifacts"]["results"]["sha256"]:
            raise buyer.BuyerError("results changed during read")
    rows={}
    for line in raw.splitlines():
        if not line.strip():
            continue
        r=json.loads(line)
        if r["cell_id"] in rows:
            raise buyer.BuyerError("duplicate result cell")
        rows[r["cell_id"]]=r
    return rows


def paired_summary(old,new,threshold):
    """Descriptive matched-cell changes, not inference or equivalence."""
    if set(old)!=set(new):
        raise buyer.BuyerError("paired cell inventory changed")
    eligible=[]
    for cid in sorted(old):
        a,b=old[cid],new[cid]
        keys=("item_id","kind","input_text","perturbation_id","validity_valid","severity")
        if any(a.get(k)!=b.get(k) for k in keys):
            raise buyer.BuyerError("paired case or structural eligibility changed")
        if a["kind"]=="variant" and a.get("validity_valid") is True:
            eligible.append((a,b))
    measures={}
    for metric in METRICS:
        pairs=[]
        for a,b in eligible:
            x,y=a.get(metric),b.get(metric)
            if a.get("evaluation_complete") is not True or b.get("evaluation_complete") is not True:
                continue
            if type(x) is int and type(y) is int and x in (0,1) and y in (0,1):
                pairs.append((x,y))
        n=len(pairs)
        full=n==len(eligible) and n>0
        old_rate=sum(x for x,y in pairs)/n if n else None
        new_rate=sum(y for x,y in pairs)/n if n else None
        change=(sum(y-x for x,y in pairs)/n) if full else None
        measures[metric]={"eligible":len(eligible),"matched_observed":n,"missing_pairs":len(eligible)-n,
            "baseline_matched_rate":old_rate,"retest_matched_rate":new_rate,"delta":change,
            "safe_to_unsafe":sum(x==0 and y==1 for x,y in pairs),
            "unsafe_to_safe":sum(x==1 and y==0 for x,y in pairs),
            "status":"insufficient_coverage" if not full else "investigate_increase" if change>=threshold else "no_descriptive_alert"}
    return measures


def _report(directory,b,job_id,collection_id):
    base=jobs.status(directory,b["baseline_job"])
    retest=jobs.status(directory,job_id)
    if base["status"]!="completed" or retest["status"]!="completed" or retest["mode"]!=b["mode"]:
        raise buyer.BuyerError("verified baseline and retest required")
    collection=connector.status(directory,collection_id)
    binding=manual._read(jobs._job_path(directory,job_id)/"binding.json")
    if (collection["state"]!="complete_unscored" or collection["binding"]!=b["connector_binding"]
            or binding["submission_id"]!=collection["receipt"]["submission_id"]
            or binding["exchange_id"]!=b["exchange_id"]
            or binding["scientific_definitions"]!=b["scientific_definitions"]
            or binding["file_sha256"]["panel.toml"]!=b["panel_hash"]
            or binding["governance"]!=b["governance"]):
        raise buyer.BuyerError("retest collection or assessment provenance changed")
    hashes={p["family_id"]:p["manifest_hash"] for p in retest["result"]["packages"]}
    if ({p["family_id"]:p["manifest_hash"] for p in base["result"]["packages"]}!=b["baseline_packages"]
            or set(hashes)!=set(b["baseline_packages"])):
        raise buyer.BuyerError("family evidence inventory changed")
    families={}
    for family,expected in b["baseline_packages"].items():
        old=_read_results(directory,b["baseline_job"],family,expected)
        new=_read_results(directory,job_id,family,hashes[family])
        families[family]=paired_summary(old,new,b["alert_delta"])
    if jobs.status(directory,b["baseline_job"])!=base or jobs.status(directory,job_id)!=retest:
        raise buyer.BuyerError("evidence changed during comparison")
    return {"baseline_job":b["baseline_job"],"retest_job":job_id,"collection_id":collection_id,
            "collection_receipt_hash":collection["receipt"]["receipt_hash"],"baseline_packages":b["baseline_packages"],
            "retest_packages":hashes,"baseline_version":b["baseline_version"],"target_version":b["target_version"],
            "families":families,"required_but_not_run":retest["result"]["required_but_not_run"],
            "alert_delta":b["alert_delta"],"decision_grade":False,
            "interpretation":"Descriptive fixed-pack matched-cell changes only; no statistical equivalence, clinical safety or deployment authorization."}


def _active(directory,identifier,digest):
    with access.db(directory) as conn:
        _schema(conn)
        lifecycle._schema(conn)
        row=_schedule(conn,identifier)
        _eligible(conn,row)
        if row["digest"]!=digest:
            raise access.AccessDenied("schedule changed")


def _checkpoint(directory,occurrence,collection_id,job_id):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("UPDATE monitoring_occurrences SET collection_id=?,job_id=? WHERE id=? AND state='running'",
                        (collection_id,job_id,occurrence)).rowcount!=1:
            raise access.AccessDenied("occurrence no longer running")


def run_due(directory,identifier,approved_digest,*,allow_candidate_calls=False,allow_model_calls=False):
    """Trusted CLI worker; one most-recent due occurrence, no catch-up burst."""
    if allow_candidate_calls is not True:
        raise access.AccessDenied("explicit local candidate execution approval required")
    with access.db(directory) as conn:
        _schema(conn)
        lifecycle._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row=_schedule(conn,identifier)
        _eligible(conn,row)
        b=row["binding"]
        if approved_digest!=row["digest"] or (b["real_judges"] and allow_model_calls is not True):
            raise access.AccessDenied("exact schedule and judge-call approval required")
        now=int(time.time())
        if row["next_due"]>now:
            return {"status":"not_due"}
        if conn.execute("SELECT 1 FROM monitoring_occurrences WHERE schedule_id=? AND state='running'",(identifier,)).fetchone():
            raise access.AccessDenied("previous occurrence running or interrupted; investigate")
        if conn.execute("SELECT count(*) FROM monitoring_occurrences").fetchone()[0]>=MAX_OCCURRENCES:
            raise access.AccessDenied("occurrence limit")
        skipped=(now-row["next_due"])//b["interval_seconds"]
        due=row["next_due"]+skipped*b["interval_seconds"]
        occurrence="occ-"+uuid.uuid4().hex
        conn.execute("INSERT INTO monitoring_occurrences(id,schedule_id,due,skipped_intervals,state) VALUES(?,?,?,?,?)",
                     (occurrence,identifier,due,skipped,"running"))
        conn.execute("UPDATE monitoring_schedules SET next_due=? WHERE id=?",(due+b["interval_seconds"],identifier))
        access._audit(conn,row["owner"],"monitoring-occurrence-claimed",occurrence)
    state="failed"
    result={"reason":"collection, scientific execution or verification failed","decision_grade":False}
    collection_id=job_id=None
    try:
        _current(directory,b)
        _active(directory,identifier,approved_digest)
        collection=connector.queue(directory,b["exchange_id"],b["connector_id"])
        collection_id=collection["id"]
        _checkpoint(directory,occurrence,collection_id,None)
        if collection["binding"]!=b["connector_binding"]:
            raise buyer.BuyerError("collection binding changed")
        _active(directory,identifier,approved_digest)
        collected=connector.execute(directory,collection_id,collection["binding_hash"],row["owner"])
        if collected["state"]!="complete_unscored":
            state="incomplete" if collected["state"]=="incomplete" else "failed"
            result={"reason":"candidate collection did not complete","decision_grade":False}
        else:
            _current(directory,b)
            _active(directory,identifier,approved_digest)
            prepared=assessment.prepare(directory,collected["receipt"]["submission_id"],b["panel_id"],b["governance"])
            if (prepared["scientific_definitions"]!=b["scientific_definitions"]
                    or prepared["file_sha256"]["panel.toml"]!=b["panel_hash"]
                    or prepared["mode"]!=b["mode"]):
                raise buyer.BuyerError("retest scientific binding changed")
            job=jobs.enqueue(directory,prepared["assessment_id"])
            job_id=job["id"]
            _checkpoint(directory,occurrence,collection_id,job_id)
            _active(directory,identifier,approved_digest)
            completed=jobs.execute(directory,job_id,allow_model_calls=allow_model_calls)
            if completed["status"]=="completed":
                result=_report(directory,b,job_id,collection_id)
                _active(directory,identifier,approved_digest)
                state="completed"
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError):
        pass
    finally:
        if state!="completed":
            # Cancel only still-queued child work, never running or completed
            # evidence. Preserve identifiers for operator reconciliation.
            for child,lookup,cancel,field in ((collection_id,connector.status,connector.cancel,"state"),
                                               (job_id,jobs.status,jobs.cancel,"status")):
                if child:
                    try:
                        if lookup(directory,child)[field]=="queued":
                            cancel(directory,child)
                    except (OSError,ValueError,RuntimeError,KeyError,TypeError):
                        pass
        with access.db(directory) as conn:
            _schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""UPDATE monitoring_occurrences SET state=?,collection_id=?,job_id=?,report=?,report_digest=?
                          WHERE id=? AND state='running'""",
                         (state,collection_id,job_id,json.dumps(result),buyer_plan._digest(result),occurrence))
            access._audit(conn,row["owner"],"monitoring-occurrence-"+state,occurrence)
    return {"id":occurrence,"status":state,"skipped_intervals":skipped,"job_id":job_id,"decision_grade":False}


def overview(directory,actor):
    with access.db(directory) as conn:
        _schema(conn)
        _account(conn,actor,("admin","operator","observer"))
        schedules=[]
        for item in conn.execute("SELECT id FROM monitoring_schedules ORDER BY next_due,id").fetchall():
            row=_schedule(conn,item["id"])
            b=row["binding"]
            runs=[]
            for rec in conn.execute("SELECT * FROM monitoring_occurrences WHERE schedule_id=? ORDER BY due DESC",(row["id"],)):
                r=dict(rec)
                if r["report"] is not None:
                    report=json.loads(r["report"])
                    if buyer_plan._digest(report)!=r["report_digest"]:
                        r.update(state="invalid",report=None)
                    elif r["state"]=="completed":
                        try:
                            if _report(directory,b,r["job_id"],r["collection_id"])!=report:
                                raise buyer.BuyerError("report changed")
                            r["report"]=report
                        except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError):
                            r.update(state="invalid",report=None)
                    else:
                        r["report"]=report
                runs.append(r)
            schedules.append({"id":row["id"],"binding_hash":row["digest"],"pilot_id":b["pilot_id"],
                "baseline_version":b["baseline_version"],"target_version":b["target_version"],
                "enabled":bool(row["enabled"]),"next_due":row["next_due"],"interval_seconds":b["interval_seconds"],
                "real_judges":b["real_judges"],"occurrences":runs})
        access._audit(conn,actor["user_id"],"monitoring-overview")
    return schedules
