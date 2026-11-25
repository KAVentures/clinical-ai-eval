"""Institutional browser review of external evaluation requests.

Machine grants cannot access this route. Browser administrators select only
existing canonical jobs; no job creation or candidate/model calls occur here.
"""
from . import evaluator_service as service, buyer_jobs as jobs, institution_access as access


def render(directory,actor):
    from .institution_web import page,esc,_hidden
    rows=service.inventory(directory,actor)
    eligible=jobs.list_jobs(directory)
    linked={r["job_id"] for r in rows if r["job_id"] is not None}
    body="<p><a href='/'>Workspace</a> · <a href='/setup'>Prepare assessments and queue jobs</a></p>"
    body+="<p>External applications can request a preapproved assessment. Only administrators may associate an existing exact assessment job. Linking never authorizes model calls or executes a job.</p>"
    if not rows:
        body+="<p>No external evaluation requests.</p>"
    for row in rows:
        body+="<section><h2>Request "+esc(row["request_id"])+"</h2><p>Grant owner: "+esc(row["owner"])+" · "+("Active grant" if row["grant_active"] else "Inactive grant")+"</p>"
        body+="<p>Assessment: "+esc(row["assessment_id"])+"</p><p>"+esc(row["status"])+"</p>"
        if row["job_id"]:
            body+="<p>Linked job: "+esc(row["job_id"])+"</p>"
        elif row["grant_active"]:
            matching=[j for j in eligible if j["assessment_id"]==row["assessment_id"] and
                      j["id"] not in linked and j["status"] in ("queued","running","completed")]
            if matching:
                body+="<form method='post' action='/evaluator-request-link'>"+_hidden("csrf",actor["csrf"])+_hidden("request_id",row["request_id"])
                body+="<label>Existing matching job<select name='job_id' required><option value=''>Choose</option>"
                body+="".join("<option value='"+esc(j["id"])+"'>"+esc(j["id"]+" · "+j["status"])+"</option>" for j in matching)
                body+="</select></label><label><input type='checkbox' name='confirm' value='yes' required> Approve this exact request-to-job association. Execution is governed separately.</label><button>Link existing job</button></form>"
            else:
                body+="<p>No matching unlinked job. Use the existing assessment setup workflow.</p>"
        if row["grant_active"]:
            body+="<form method='post' action='/evaluator-request-revoke'>"+_hidden("csrf",actor["csrf"])+_hidden("grant_id",row["grant_id"])
            body+="<label><input type='checkbox' name='confirm' value='yes' required> Revoke this application's grant and all associated request access.</label><button>Revoke grant</button></form>"
        body+="</section>"
    return page("External evaluation requests",body)


def dispatch(directory,actor,path,form):
    access.require(actor,"admin")
    if path=="/evaluator-request-link" and set(form)=={"request_id","job_id","confirm"} and form["confirm"]=="yes":
        return service.link(directory,form["request_id"],form["job_id"],confirmed=True,actor=actor)
    if path=="/evaluator-request-revoke" and set(form)=={"grant_id","confirm"} and form["confirm"]=="yes":
        service.revoke(directory,form["grant_id"],actor=actor)
        return
    raise access.AccessDenied("invalid external request form")
