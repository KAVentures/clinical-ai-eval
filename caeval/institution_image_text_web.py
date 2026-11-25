"""Browser-only fixed synthetic image-text rehearsal. No arbitrary image upload."""
from . import institution_image_text as service, image_text_readiness as image, institution_access as access
from . import buyer_plan


def render(directory,actor,plan_id):
    from .institution_web import page,esc,_hidden
    doc=service.prepare(directory,actor,plan_id)
    body="<p><a href='/setup'>Assessment setup</a></p><p>Fixed synthetic monitor-display images and text only. No real patient data, radiographs or diagnostic interpretation.</p>"
    body+="<p>"+esc(doc["plan"]["target"]["name"])+" · "+esc(doc["plan"]["target"]["version"])+"</p>"
    for kind,label in (("exchange","Download synthetic image-text inputs"),("response_template","Download structured response template")):
        body+="<p><a href='/image-text-download?plan_id="+esc(plan_id)+"&amp;kind="+kind+"'>"+label+"</a></p>"
    body+="<p>Use the candidate's approved test environment to process these synthetic inputs. Paste only the structured response JSON, not source images, credentials or clinical notes. Missing outputs must be explicitly unavailable.</p>"
    body+="<form method='post' action='/image-text-submit'>"+_hidden("csrf",actor["csrf"])+_hidden("plan_id",plan_id)
    body+="<label>Response JSON<textarea name='responses' required maxlength='262144' rows='12'></textarea></label>"
    body+="<label><input type='checkbox' name='confirm' value='yes' required> I confirm this is exclusively the supplied synthetic fixture and contains no real patient information.</label><button>Evaluate and record synthetic responses</button></form>"
    body+="<h2>Saved synthetic rehearsals</h2>"
    for row in service.inventory(directory,actor):
        if row.get("plan_id")==plan_id:
            label=esc(row["id"][:12])
            if row["state"] in ("current","stale"):
                label="<a href='/image-text-report?id="+esc(row["id"])+"'>"+label+"</a>"
            body+="<p>"+label+" · "+esc(row["state"])+" · "+esc(row.get("summary",{}))+"</p>"
    return page("Synthetic image-text readiness rehearsal",body)


def report(directory,actor,identifier):
    from .institution_web import page,esc
    doc=service.detail(directory,actor,identifier)
    report=doc["package"]["report"]
    body="<p><a href='/image-text?plan_id="+esc(doc["plan_id"])+"'>Back to synthetic rehearsal</a></p>"
    body+="<p>"+esc(report["target"]["name"])+" · "+esc(report["target"]["version"])+" · "+("Current" if doc["scope_current"] else "Historical scope")+"</p>"
    body+="<p>Exact matches: "+esc(report["summary"]["exact_match"])+" / "+esc(report["summary"]["planned"])+" · Mismatches: "+esc(report["summary"]["mismatch"])+" · Unavailable: "+esc(report["summary"]["unavailable"])+"</p>"
    body+="<table><tr><th>Probe</th><th>Status</th><th>Expected outcome</th><th>Expected value</th><th>Reported outcome</th><th>Reported value</th></tr>"
    for row in report["rows"]:
        body+="<tr>"+"".join("<td>"+esc(row[k])+"</td>" for k in ("probe","status","expected_status","expected_value","reported_status","reported_value"))+"</tr>"
    body+="</table><p><a href='/image-text-evidence?id="+esc(identifier)+"'>Download replayable synthetic evidence</a></p>"
    body+="<p>Evidence digest: "+esc(doc["package"]["digest"])+"</p><ul>"+"".join("<li>"+esc(x)+"</li>" for x in image.LIMITATIONS)+"</ul>"
    return page("Synthetic multimodal evidence",body)


def submit(directory,actor,form):
    if set(form)!={"plan_id","responses","confirm"} or form["confirm"]!="yes":
        raise access.AccessDenied("explicit synthetic response approval required")
    from . import buyer_manual
    raw=form["responses"].encode("utf-8")
    if len(raw)>65536:
        raise access.AccessDenied("synthetic image response exceeds limit")
    return service.submit(directory,actor,form["plan_id"],buyer_manual._parse(raw),confirmed=True)
