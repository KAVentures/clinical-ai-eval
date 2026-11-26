"""Authenticated browser inbox for aggregate monitor transition notifications."""
from . import institution_monitor_notifications as service
from . import institution_imaging_web as forms


def inbox(directory,actor,ident):
    from .institution_web import page,esc
    doc=service.inbox(directory,actor,ident)
    body="<p><a href='/lifecycle-monitor-detail?id="+esc(ident)+"'>Back to monitor</a></p><p>Review of accepted transition notifications is separate from clinical incident resolution and relay CLI review. No human delivery or clinical safety is established.</p>"
    body+="<p>Product "+esc(doc["target"]["name"])+" · "+esc(doc["target"]["version"])+" · relay "+esc(doc["relay_state"])+" · "+esc(doc["reviewed_count"])+" institutional reviews locked.</p>"
    snap=doc["snapshot"]
    if snap is None:
        body+="<p>Relay unavailable or not provisioned. This is not an empty or healthy inbox.</p>"
    else:
        body+="<p>"+esc(snap["pending_count"])+" accepted batches without an institutional review. Showing the first "+esc(len(snap["items"]))+".</p>"
        if not snap["source_consistent_with_review_metadata"]:
            body+="<p>Relay history mismatch: "+esc(snap["missing_reviewed_count"])+" missing and "+esc(snap["changed_reviewed_count"])+" changed reviewed batch digests. Investigate before new reviews.</p>"
        for item in snap["items"]:
            e=item["envelope"]
            body+="<section><h2>"+esc(e["as_of"])+" · "+esc(len(e["events"]))+" transitions</h2><p>Attempt "+esc(e["attempt_digest"])+" · local CLI review recorded: "+esc(item["local_review_recorded"])+"</p>"
            body+="<a href='/monitor-notification-detail?id="+esc(ident)+"&amp;attempt="+esc(e["attempt_digest"])+"'>Inspect exact notification</a></section>"
    return page("Institutional monitoring notifications",body)


def detail(directory,actor,ident,attempt):
    from .institution_web import page,esc,_hidden
    doc=service.detail(directory,actor,ident,attempt)
    prior=doc["review"]
    item=doc["item"]
    e=prior["record"]["envelope"] if prior else item["envelope"]
    body="<p><a href='/monitor-notifications?id="+esc(ident)+"'>Back to notification inbox</a></p><p>Historical notification only. No clinical action or alert resolution.</p><p>"+esc(e["as_of"])+" · "+esc(e["attempt_digest"])+"</p>"
    body+="<table><tr><th>Transition</th><th>Kind</th><th>Severity</th><th>Opaque alert ID</th></tr>"
    for event in e["events"]:
        a=event["alert"]
        severity=(event["previous_severity"]+" → " if "previous_severity" in event else "")+a["severity"]
        body+="<tr>"+"".join("<td>"+esc(v)+"</td>" for v in (event["transition"],a["kind"],severity,a["id"]))+"</tr>"
    body+="</table>"
    if prior:
        body+="<p>Institutional review locked by "+esc(prior["actor"])+" · "+esc(prior["digest"])+"</p>"
    else:
        body+="<p>Payload SHA-256 "+esc(item["payload_digest"])+"</p>"
        body+=("<form method='post' action='/monitor-notification-review'>"+_hidden("csrf",actor["csrf"])+
               _hidden("id",ident)+_hidden("attempt",attempt)+_hidden("payload_digest",item["payload_digest"])+
               "<label>External review record SHA-256<input name='external_record_digest' required maxlength='64'></label>"+
               "<label><input type='checkbox' name='confirm' value='yes' required> Attest completion of the applicable external review procedure. No alert is resolved.</label><button>Lock institutional review</button></form>")
    return page("Exact monitoring notification",body)


def review(directory,actor,form):
    forms._exact(form,"id attempt payload_digest external_record_digest confirm")
    forms._confirm(form)
    service.review(directory,actor,form["id"],form["attempt"],form["payload_digest"],form["external_record_digest"],confirmed=True)
    return form["id"],form["attempt"]
