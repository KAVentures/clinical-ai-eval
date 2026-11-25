"""Browser-only synthetic FHIR rehearsal. No arbitrary bundle upload."""
from . import institution_fhir as service, fhir_readiness as fhir, institution_access as access
from . import buyer_plan


def render(directory,actor,plan_id):
    from .institution_web import page,esc,_hidden
    doc=service.prepare(directory,actor,plan_id)
    body="<p><a href='/setup'>Assessment setup</a></p><p>Fixed synthetic FHIR R4 observation extraction only. Do not enter real patient data or connect to a production EHR.</p>"
    body+="<p>"+esc(doc["plan"]["target"]["name"])+" · "+esc(doc["plan"]["target"]["version"])+"</p>"
    for kind,label in (("exchange","Download synthetic input bundles"),("response_template","Download structured response template")):
        body+="<p><a href='/fhir-download?plan_id="+esc(plan_id)+"&amp;kind="+kind+"'>"+label+"</a></p>"
    body+="<p>Use the candidate's approved test environment to process these synthetic inputs. Paste only the structured response JSON, not source records, credentials or clinical notes. Missing outputs must be explicitly unavailable.</p>"
    body+="<form method='post' action='/fhir-submit'>"+_hidden("csrf",actor["csrf"])+_hidden("plan_id",plan_id)
    body+="<label>Response JSON<textarea name='responses' required maxlength='262144' rows='12'></textarea></label>"
    body+="<label><input type='checkbox' name='confirm' value='yes' required> I confirm this is exclusively the supplied synthetic fixture and contains no real patient information.</label><button>Evaluate and record synthetic responses</button></form>"
    body+="<h2>Saved synthetic rehearsals</h2>"
    for row in service.inventory(directory,actor):
        if row.get("plan_id")==plan_id:
            label=esc(row["id"][:12])
            if row["state"] in ("current","stale"):
                label="<a href='/fhir-report?id="+esc(row["id"])+"'>"+label+"</a>"
            body+="<p>"+label+" · "+esc(row["state"])+" · "+esc(row.get("summary",{}))+"</p>"
    return page("Synthetic FHIR interoperability rehearsal",body)


def report(directory,actor,identifier):
    from .institution_web import page,esc
    doc=service.detail(directory,actor,identifier)
    report=doc["package"]["report"]
    body="<p><a href='/fhir?plan_id="+esc(doc["plan_id"])+"'>Back to synthetic rehearsal</a></p>"
    body+="<p>"+esc(report["target"]["name"])+" · "+esc(report["target"]["version"])+" · "+("Current" if doc["scope_current"] else "Historical scope")+"</p>"
    body+="<p>Exact matches: "+esc(report["summary"]["exact_match"])+" / "+esc(report["summary"]["planned"])+" · Mismatches: "+esc(report["summary"]["mismatch"])+" · Unavailable: "+esc(report["summary"]["unavailable"])+"</p>"
    body+="<table><tr><th>Probe</th><th>Status</th><th>Missing source IDs</th><th>Unexpected source IDs</th><th>Changed observations</th></tr>"
    for row in report["rows"]:
        body+="<tr>"+"".join("<td>"+esc(row[k])+"</td>" for k in ("probe","status","missing","unexpected","changed"))+"</tr>"
    body+="</table><p><a href='/fhir-evidence?id="+esc(identifier)+"'>Download replayable synthetic evidence</a></p>"
    body+="<p>Evidence digest: "+esc(doc["package"]["digest"])+"</p><ul>"+"".join("<li>"+esc(x)+"</li>" for x in fhir.LIMITATIONS)+"</ul>"
    return page("Synthetic interoperability evidence",body)


def submit(directory,actor,form):
    if set(form)!={"plan_id","responses","confirm"} or form["confirm"]!="yes":
        raise access.AccessDenied("explicit synthetic response approval required")
    return service.submit(directory,actor,form["plan_id"],fhir.parse(form["responses"].encode("utf-8")),confirmed=True)
