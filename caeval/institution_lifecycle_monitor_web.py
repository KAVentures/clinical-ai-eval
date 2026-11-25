"""Authenticated no-install operational monitoring interface."""
import json
from . import lifecycle_monitor as m, institution_lifecycle_monitor as service, institution_imaging_web as forms


def home(directory,actor):
    from .institution_web import page,esc,_hidden
    body="<p><a href='/'>Dashboard</a></p><p>Aggregate-only monitoring of externally authorized product use. No live patient data, automated clinical decisions or operational controls.</p>"
    if actor["role"]=="admin":
        body+=("<section><h2>Register exact-version monitoring plan</h2><p>Use a frozen buyer programme and independent external authorization record. Specify fixed UTC date windows and local metric thresholds.</p><form method='post' action='/lifecycle-monitor-preview'>"+_hidden("csrf",actor["csrf"])+
               "<label>Plan specification JSON<textarea name='spec' rows='12' maxlength='65536' required></textarea></label><button>Preview plan</button></form></section>")
    for r in service.overview(directory,actor):
        body+="<section><h2>"+esc(r["id"])+"</h2><p>"+esc(r["vendor_id"])+" · "+esc(r["target"]["version"])+" · "+esc(r["n_missing_windows"])+" missing windows · "+esc(r["n_alerts"])+" alerts</p>"
        if r["attention_required"]:
            body+="<p>Attention required.</p>"
        if r["version_reassessment_required"]:
            body+="<p>Reported version change: reassessment required.</p>"
        if actor["role"] in ("admin","operator"):
            body+="<a href='/lifecycle-monitor-detail?id="+esc(r["id"])+"'>Open monitoring record</a>"
        body+="</section>"
    return page("Clinical AI lifecycle monitoring",body)


def preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    forms._exact(form,"spec")
    p=service.preview(directory,actor,forms._parse(form["spec"]))
    body="<p>Exact version and reporting plan. External authorization is not verified by this application.</p><pre>"+esc(json.dumps(p,indent=2))+"</pre>"
    body+=("<form method='post' action='/lifecycle-monitor-freeze'>"+_hidden("csrf",actor["csrf"])+_hidden("spec",json.dumps(p["spec"]))+_hidden("approval",p["digest"])+
           "<label><input type='checkbox' name='confirm' value='yes' required> Register externally authorized use and exact local thresholds. This registration does not authorize use.</label><button>Freeze monitoring plan</button></form>")
    return page("Approve monitoring plan",body)


def _json_form(actor,ident,path,title,doc):
    from .institution_web import esc,_hidden
    return ("<section><h2>"+esc(title)+"</h2><form method='post' action='"+path+"'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+
            "<label>Structured aggregate metadata<textarea name='document' rows='10' maxlength='65536' required>"+esc(json.dumps(doc,indent=2))+"</textarea></label>"+
            "<label><input type='checkbox' name='confirm' value='yes' required> Lock this externally attested record. No patient data or identifiers.</label><button>Lock record</button></form></section>")


def detail(directory,actor,ident):
    from .institution_web import page,esc,_hidden
    row=service.detail(directory,actor,ident)
    p,status=row["plan"],row["status"]
    body="<p><a href='/lifecycle-monitor'>Monitoring plans</a></p><p>Reports use fixed UTC calendar windows. Thresholds are descriptive, not clinical safety determinations. Alerts do not automatically trigger clinical action.</p>"
    body+="<pre>"+esc(json.dumps(row["coverage"],indent=2))+"</pre><h2>Reporting windows</h2>"
    for w in status["windows"]:
        body+="<section><h3>"+esc(w["start"])+" to "+esc(w["end_exclusive"])+" (exclusive)</h3><p>"+esc(w["state"])+"</p><pre>"+esc(json.dumps(w["metrics"],indent=2))+"</pre></section>"
    body+="<h2>Alerts and recorded escalation</h2>"
    for a in status["alerts"]:
        body+="<section><h3>"+esc(a["id"])+" · "+esc(a["severity"])+"</h3><p>"+esc(", ".join(a["recorded_actions"]) or "No action recorded")+"</p>"
        if actor["role"]=="admin":
            body+=("<form method='post' action='/lifecycle-monitor-action'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("plan_digest",p["digest"])+_hidden("alert_id",a["id"])+"<label>External action record<select name='action'>")
            for action in m.ACTIONS:
                body+="<option value='"+esc(action)+"'>"+esc(action)+"</option>"
            body+="</select></label><label>External record SHA-256<input name='external_record_digest' required maxlength='64'></label><label><input type='checkbox' name='confirm' value='yes' required> Record request or external action documentation only; no automatic intervention.</label><button>Record action</button></form>"
        body+="</section>"
    missing=[w["index"] for w in status["windows"] if w["state"]=="missing" and not w["version_overlap"]]
    if missing:
        body+=_json_form(actor,ident,"/lifecycle-monitor-report","Submit completed reporting window",{"plan_digest":p["digest"],"target":p["target"],"window_index":missing[0],
            "metrics":[{"id":r["id"],"numerator":0,"denominator":0} for r in p["spec"]["metrics"]],
            "source_digest":"","complete_aggregate_attested":False})
    body+=_json_form(actor,ident,"/lifecycle-monitor-incident","Record incident or product version change",{"plan_digest":p["digest"],"incident_id":"","date":status["as_of"],"severity":"moderate",
                "category":"clinical_safety","external_record_digest":"","observed_version":p["target"]["version"]})
    if actor["role"]=="admin":
        doc=service.export_preview(directory,actor,ident)
        body+=("<form method='post' action='/lifecycle-monitor-export'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("approval",doc["digest"])+
               "<label><input type='checkbox' name='confirm' value='yes' required> Export exact restricted historical monitoring evidence.</label><button>Download evidence</button></form>")
    return page("Version-specific monitoring record",body)


def dispatch(directory,actor,path,form):
    forms._confirm(form)
    if path=="/lifecycle-monitor-freeze":
        forms._exact(form,"spec approval confirm")
        return service.freeze(directory,actor,forms._parse(form["spec"]),form["approval"],confirmed=True)
    if path in ("/lifecycle-monitor-report","/lifecycle-monitor-incident"):
        forms._exact(form,"id document confirm")
        fn=service.submit_report if path.endswith("-report") else service.submit_incident
        fn(directory,actor,form["id"],forms._parse(form["document"]),confirmed=True)
    elif path=="/lifecycle-monitor-action":
        forms._exact(form,"id plan_digest alert_id action external_record_digest confirm")
        service.record_action(directory,actor,form["id"],{k:form[k] for k in ("plan_digest","alert_id","action","external_record_digest")},confirmed=True)
    elif path=="/lifecycle-monitor-export":
        forms._exact(form,"id approval confirm")
        return service.export(directory,actor,form["id"],form["approval"],confirmed=True)
    else:
        raise ValueError("unknown monitoring action")
    return form["id"]
