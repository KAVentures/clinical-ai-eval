"""Native browser handoff for an exact reported version change."""
from . import monitor_reassessment as service
from . import institution_imaging_web as forms


def render(directory,actor,ident):
    from .institution_web import page,esc,_hidden
    doc=service.view(directory,actor,ident)
    body="<p><a href='/lifecycle-monitor-detail?id="+esc(ident)+"'>Back to monitor</a></p><p>Administrative linkage only. A linked successor is not a completed reassessment, and the original version-change alert remains active. No authorization transfers.</p>"
    if not doc["incidents"]:
        body+="<p>No reported version changes in this monitor.</p>"
    for item in doc["incidents"]:
        body+="<section><h2>"+esc(item["incident_id"])+"</h2><p>"+esc(item["date"])+" · reported version "+esc(item["observed_version"])+" · "+esc(item["state"])+"</p>"
        if item["handoff"]:
            handoff=item["handoff"]
            body+="<p>Linked successor: <a href='/unified-assessment-detail?id="+esc(handoff["successor_id"])+"'>"+esc(handoff["successor_id"])+"</a> · "+esc(item["successor_state"])+"</p>"
            body+="<p>Exact handoff digest "+esc(handoff["digest"])+"</p>"
        elif actor["role"]=="admin":
            if item["eligible_successors"]:
                body+="<form method='post' action='/monitor-reassessment-preview'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("incident",item["incident_id"])
                body+="<label>Verified exact successor<select name='successor' required>"
                for sid in item["eligible_successors"]:
                    body+="<option value='"+esc(sid)+"'>"+esc(sid)+"</option>"
                body+="</select></label><button>Preview handoff</button></form>"
            else:
                body+="<p>No eligible current direct successor. Create an approved version reassessment and successor programme first.</p>"
                body+="<p><a href='/unified-assessment-lineage?id="+esc(doc["baseline_id"])+"'>Open programme lineage</a></p>"
        body+="</section>"
    return page("Version-change evaluation handoffs",body)


def preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    forms._exact(form,"id incident successor")
    doc=service.preview(directory,actor,form["id"],form["incident"],form["successor"])
    body="<p>Link exact reported version "+esc(doc["successor_target"]["version"])+" to the verified direct successor programme "+esc(doc["successor_id"])+". No evidence or authorization transfers. The alert remains active.</p>"
    body+="<p>Approval digest "+esc(doc["digest"])+"</p>"
    body+="<form method='post' action='/monitor-reassessment-lock'>"+_hidden("csrf",actor["csrf"])+_hidden("id",form["id"])+_hidden("incident",form["incident"])+_hidden("successor",form["successor"])+_hidden("approval",doc["digest"])
    body+="<label><input type='checkbox' name='confirm' value='yes' required> Confirm the exact administrative linkage.</label><button>Lock handoff</button></form>"
    return page("Approve version-change handoff",body)


def lock(directory,actor,form):
    forms._exact(form,"id incident successor approval confirm")
    forms._confirm(form)
    service.lock(directory,actor,form["id"],form["incident"],form["successor"],form["approval"],confirmed=True)
    return form["id"]
