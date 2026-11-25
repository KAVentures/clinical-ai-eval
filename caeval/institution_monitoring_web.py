"""Browser administration for frozen monitoring schedules; never runs jobs."""
from __future__ import annotations
import datetime as dt

from . import institution_monitoring as monitor, institution_access as access, project


def render(directory,actor):
    from .institution_web import page,esc,_hidden
    def stamp(n):
        return dt.datetime.fromtimestamp(n,dt.timezone.utc).isoformat()
    def form(action,fields,button):
        return "<form action='/monitoring-"+action+"' method='post'>"+_hidden("csrf",actor["csrf"])+fields+"<button>"+esc(button)+"</button></form>"
    def field(key,title,kind="text"):
        return "<label>"+esc(title)+"<input name='"+key+"' type='"+kind+"' required maxlength='300'></label>"
    body="<p><a href='/'>Workspace</a></p><p>Repeated synthetic or approved non-identifiable regression probes. Not patient surveillance, clinical safety certification or deployment approval. Network execution requires the separately approved local worker.</p>"
    if actor["role"]=="admin":
        body+="<p><a href='/version-reassessment'>Prepare a new-version reassessment from a verified baseline</a></p>"
        fields="".join(field(k,t) for k,t in (("pilot_id","Lifecycle pilot ID"),("baseline_job","Verified baseline job ID"),("exchange_id","Approved exchange ID"),("connector_id","Approved local connector"),("panel_id","Approved judge panel")))
        for q in project.MANDATORY_GOVERNANCE:
            fields+=field(q,q.replace("_"," "))
        fields+=field("interval_hours","Interval in whole hours","number")+field("first_due","First due date/time in UTC (YYYY-MM-DDTHH:MM:SS+00:00)")+field("alert_delta","Absolute increase threshold (fraction, e.g. 0.1)")
        fields+="<label><input type='checkbox' name='confirmed' value='yes' required> I approve the exact repeat-testing scope, case pack, product version and descriptive alert rule.</label>"
        body+="<section><h2>Freeze monitoring schedule</h2>"+form("create",fields,"Freeze schedule without running")+"</section>"
    for row in monitor.overview(directory,actor):
        body+="<section><h2>"+esc(row["id"])+"</h2><p>Pilot: "+esc(row["pilot_id"])+" · Baseline version "+esc(row["baseline_version"])+" → tested version "+esc(row["target_version"])+"</p>"
        body+="<p>"+("Enabled" if row["enabled"] else "Disabled")+" · Next due "+esc(stamp(row["next_due"]))+" · Interval "+str(row["interval_seconds"]//3600)+" hours</p>"
        if actor["role"]=="admin":
            body+="<p>Worker approval binding: "+esc(row["binding_hash"])+"</p>"
            if row["enabled"]:
                body+=form("disable",_hidden("id",row["id"]),"Disable future occurrences")
        for occ in row["occurrences"]:
            body+="<h3>"+esc(stamp(occ["due"]))+" · "+esc(occ["state"])+"</h3><p>Skipped intervals: "+str(occ["skipped_intervals"])+"</p>"
            report=occ["report"]
            if occ["state"]!="completed" or not report:
                continue
            body+="<p>Descriptive alert threshold: "+esc(report["alert_delta"])+"</p>"
            for family,metrics in report["families"].items():
                body+="<h4>"+esc(family)+"</h4><table><tr><th>Measure</th><th>Matched / eligible</th><th>Baseline rate</th><th>Retest rate</th><th>Delta</th><th>Status</th></tr>"
                for metric,data in metrics.items():
                    def pct(x):
                        return "Not estimable" if x is None else f"{100*x:.1f}%"
                    values=(metric,str(data["matched_observed"])+"/"+str(data["eligible"]),pct(data["baseline_matched_rate"]),pct(data["retest_matched_rate"]),pct(data["delta"]),data["status"])
                    body+="<tr>"+"".join("<td>"+esc(v)+"</td>" for v in values)+"</tr>"
                body+="</table>"
                for metric,data in metrics.items():
                    body+="<p>"+esc(metric)+": missing quorum "+str(data["missing_quorum_pairs"])+", judge participation mismatch "+str(data["judge_participation_mismatch_pairs"])+", missing metric "+str(data["missing_metric_pairs"])+"</p>"
            body+="<p>"+esc(report["interpretation"])+"</p>"
        body+="</section>"
    return page("Longitudinal regression monitoring",body)


def dispatch(directory,actor,path,fields):
    access.require(actor,"admin")
    if path=="/monitoring-disable" and set(fields)=={"id"}:
        monitor.disable(directory,actor,fields["id"])
        return
    required={"pilot_id","baseline_job","exchange_id","connector_id","panel_id",
              "interval_hours","first_due","alert_delta","confirmed",*project.MANDATORY_GOVERNANCE}
    if path!="/monitoring-create" or set(fields)!=required:
        raise access.AccessDenied("invalid monitoring form")
    first=dt.datetime.fromisoformat(fields["first_due"])
    if first.utcoffset()!=dt.timedelta(0):
        raise access.AccessDenied("first due must use explicit UTC")
    spec={k:fields[k] for k in ("pilot_id","baseline_job","exchange_id","connector_id","panel_id")}
    spec.update(governance={q:fields[q] for q in project.MANDATORY_GOVERNANCE},
                interval_seconds=int(fields["interval_hours"])*3600,first_due=int(first.timestamp()),
                alert_delta=float(fields["alert_delta"]))
    monitor.create(directory,actor,spec,confirmed=fields["confirmed"]=="yes")
