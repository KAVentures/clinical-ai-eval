"""Browser preparation of a version-only reassessment; no execution."""
from . import version_retest as version, institution_access as access


def render(directory,actor):
    from .institution_web import page,esc,_hidden
    from . import buyer_jobs,buyer_connector
    access.require(actor,"admin")
    body="<p><a href='/monitoring'>Monitoring schedules</a></p><p>Prepare a new exact product version using a completed baseline's pack, panel and intended use. Staging does not authorize transmission.</p>"
    body+="<form method='post' action='/version-retest-preview'>"+_hidden("csrf",actor["csrf"])
    body+="<label>Completed baseline job<select name='baseline_job' required><option value=''>Choose</option>"
    for row in buyer_jobs.list_jobs(directory):
        if row["status"]=="completed":
            body+="<option>"+esc(row["id"])+"</option>"
    body+="</select></label><label>New exact version<input name='new_version' maxlength='300' required></label>"
    body+="<label>Approved new-version connector<select name='connector_id' required><option value=''>Choose</option>"
    for c in buyer_connector.connectors(directory):
        body+="<option value='"+esc(c["connector_id"])+"'>"+esc(c["connector_id"]+" · "+c["target_name"]+" "+c["target_version"])+"</option>"
    body+="</select></label><button>Preview retest preparation</button></form>"
    return page("Prepare version reassessment",body)


def preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    if set(form)!={"baseline_job","new_version","connector_id"}:
        raise access.AccessDenied("invalid version preview")
    d=version.preview(directory,actor,**form)
    body="<p>"+esc(d["target_name"])+" · "+esc(d["baseline_version"])+" → "+esc(d["target_version"])+"</p>"
    body+="<p>Frozen pack "+esc(d["pack_id"])+" · panel "+esc(d["panel_id"])+" · "+str(d["n_prompts"])+" prompts</p>"
    body+="<p>Families: "+esc(d["families"])+" · Required but not run: "+esc(d["required_but_not_run"])+"</p>"
    body+="<p>Procurement conditions: "+esc(d["conditions_hash"] or "Unbound research")+"</p><p>Digest: "+esc(d["digest"])+"</p>"
    body+="<form method='post' action='/version-retest-prepare'>"+_hidden("csrf",actor["csrf"])+"".join(_hidden(k,v) for k,v in form.items())+_hidden("digest",d["digest"])
    body+="<label><input type='checkbox' name='confirm' value='yes' required> Approve the exact version-only scope. No calls or schedule are authorized.</label><button>Prepare new-version exchange</button></form>"
    return page("Review version reassessment",body)


def prepare(directory,actor,form):
    from .institution_web import page,esc
    if set(form)!={"baseline_job","new_version","connector_id","digest","confirm"}:
        raise access.AccessDenied("invalid version preparation")
    d=version.prepare(directory,actor,form["baseline_job"],form["new_version"],form["connector_id"],form["digest"],confirmed=form["confirm"]=="yes")
    body="<p>Prepared exact new-version exchange. Copy these values into the monitoring schedule form; select an open pilot for the new version and choose the cadence.</p>"
    for k in ("baseline_job","exchange_id","connector_id","panel_id","governance"):
        body+="<p>"+esc(k)+": "+esc(d[k])+"</p>"
    body+="<p><a href='/monitoring'>Freeze monitoring schedule</a></p>"
    return page("Version reassessment prepared",body)
