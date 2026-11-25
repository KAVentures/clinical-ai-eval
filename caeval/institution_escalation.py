"""Local institutional escalation inbox derived from current evidence.

Acknowledging a notice never resolves its source, alters clinical evidence,
changes pilot state, or sends external notifications. No patient text is copied.
"""
from __future__ import annotations

import datetime as dt
import time

from . import buyer_plan, institution_access as access
from . import institution_lifecycle as lifecycle, institution_monitoring as monitoring

MAX_ACKNOWLEDGEMENTS = 20000
PRIORITY = {"critical": 0, "high": 1, "moderate": 2, "low": 3}


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS escalation_acknowledgements(
      alert_id TEXT NOT NULL, actor TEXT NOT NULL REFERENCES users(id),
      at INTEGER NOT NULL, PRIMARY KEY(alert_id,actor))""")


def _notice(kind, source, version, owner, priority, title, destination):
    ident = buyer_plan._digest([kind, source, version])
    return {"id":ident,"kind":kind,"source":source,"owner":owner,
            "priority":priority,"title":title,"destination":destination}


def _derive(directory, actor):
    """Use the existing source verification, never trust an unchecked report."""
    pilots=lifecycle.overview(directory,actor)["pilots"]
    schedules=monitoring.overview(directory,actor)
    today=dt.date.today().isoformat()
    now=int(time.time())
    out=[]
    for p in pilots:
        if p["state"]=="closed":
            continue
        if p["review_date"]<today:
            out.append(_notice("pilot_review_overdue",p["id"],p["revision"],p["owner"],
                               "moderate","Pilot review date overdue","/lifecycle"))
        for item in p["items"]:
            if item["state"]!="open":
                continue
            if item["severity"] in ("high","critical") or item["kind"]=="version_change":
                out.append(_notice("blocking_register_item",item["id"],item["revision"],item["owner"],
                                   item["severity"] if item["severity"] in ("high","critical") else "high",
                                   "Blocking implementation register item","/lifecycle"))
            if item["due"]<today:
                out.append(_notice("register_item_overdue",item["id"],item["revision"],item["owner"],
                                   item["severity"],"Implementation action overdue","/lifecycle"))
        for e in p["evidence"]:
            if e["integrity"]!="matches":
                out.append(_notice("linked_evidence_unavailable",p["id"]+":"+e["job_id"],
                                   [p["revision"],e["integrity"]],p["owner"],
                                   "high","Linked assessment no longer verifies","/lifecycle"))
    by_pilot={p["id"]:p for p in pilots}
    for s in schedules:
        p=by_pilot.get(s["pilot_id"])
        if not p or p["state"]=="closed":
            continue
        owner=p["owner"]
        if s["enabled"] and now>=s["next_due"]:
            out.append(_notice("monitoring_due",s["id"],s["next_due"],owner,"moderate",
                               "Scheduled regression assessment due","/monitoring"))
        for occurrence in s["occurrences"]:
            ident=occurrence["id"]
            state=occurrence["state"]
            version=[state,occurrence.get("report_digest"),occurrence["due"]]
            if state in ("failed","incomplete","invalid"):
                out.append(_notice("monitoring_"+state,ident,version,owner,"high",
                                   "Regression assessment "+state,"/monitoring"))
            if state=="running" and now>=occurrence["due"]+s["interval_seconds"]:
                out.append(_notice("monitoring_still_running",ident,version,owner,"high",
                                   "Regression assessment still running beyond its interval","/monitoring"))
            if occurrence["skipped_intervals"]:
                out.append(_notice("monitoring_skipped",ident,occurrence["skipped_intervals"],owner,
                                   "moderate","One or more scheduled regression intervals missed","/monitoring"))
            if state=="completed":
                report=occurrence["report"]
                if not isinstance(report,dict) or not report.get("families"):
                    out.append(_notice("monitoring_invalid",ident,version,owner,"high",
                                       "Completed regression assessment lacks verified results","/monitoring"))
                    continue
                statuses={metric["status"] for metrics in report["families"].values() for metric in metrics.values()}
                if "investigate_increase" in statuses:
                    out.append(_notice("monitoring_deterioration",ident,version,owner,"high",
                                       "Descriptive safety deterioration requires investigation","/monitoring"))
                if "insufficient_coverage" in statuses:
                    out.append(_notice("monitoring_insufficient_coverage",ident,version,owner,"high",
                                       "Regression comparison has insufficient matched coverage","/monitoring"))
    # Operators see only their owned notices. Administrators retain complete
    # visibility, including notices whose owner is no longer active.
    if actor["role"]=="operator":
        out=[a for a in out if a["owner"]==actor["user_id"]]
    return sorted(out,key=lambda a:(PRIORITY[a["priority"]],a["kind"],a["source"]))


def overview(directory,actor):
    with access.db(directory) as db:
        _schema(db)
        lifecycle._actor(db,actor)
    rows=_derive(directory,actor)
    with access.db(directory) as db:
        _schema(db)
        lifecycle._actor(db,actor)
        seen={r["alert_id"]:r["at"] for r in db.execute(
            "SELECT alert_id,at FROM escalation_acknowledgements WHERE actor=?",(actor["user_id"],))}
        for row in rows:
            row["acknowledged_at"]=seen.get(row["id"])
        access._audit(db,actor["user_id"],"escalation-inbox-opened")
    return {"alerts":rows,"decision_grade":False,"deployment_authorized":False}


def acknowledge(directory,actor,alert_id):
    if not isinstance(alert_id,str) or len(alert_id)!=64 or any(c not in "0123456789abcdef" for c in alert_id):
        raise access.AccessDenied("invalid notice identity")
    access.require(actor,"admin","operator")
    if alert_id not in {a["id"] for a in overview(directory,actor)["alerts"]}:
        raise access.AccessDenied("current accessible notice required")
    with access.db(directory) as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        lifecycle._actor(db,actor,True)
        if db.execute("SELECT 1 FROM escalation_acknowledgements WHERE alert_id=? AND actor=?",
                      (alert_id,actor["user_id"])).fetchone():
            return
        if db.execute("SELECT count(*) FROM escalation_acknowledgements").fetchone()[0]>=MAX_ACKNOWLEDGEMENTS:
            raise access.AccessDenied("acknowledgement storage limit; contact administrator")
        db.execute("INSERT INTO escalation_acknowledgements VALUES(?,?,?)",(alert_id,actor["user_id"],int(time.time())))
        access._audit(db,actor["user_id"],"escalation-acknowledged",alert_id)
