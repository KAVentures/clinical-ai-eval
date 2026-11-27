"""No-install hazard planning form. No prefilled acceptance thresholds."""
from . import buyer_risk_protocol as risk, institution_access as access


def render(directory,actor,plan_id):
    from .institution_web import page,esc,_hidden
    doc=risk.view(directory,actor,plan_id)
    body="<p><a href='/setup'>Assessment setup</a></p><p>Buyer planning only. No clinical validation, automatic scoring or deployment approval.</p>"
    if doc["id"]:
        p=doc["protocol"]
        body+="<p>Frozen protocol "+esc(doc["id"])+" · "+("Current scope" if doc["scope_current"] else "Historical or stale scope")+"</p>"
        body+="<h2>Clinical hazards</h2>"
        for h in p["hazards"]:
            body+="<section><h3>"+esc(h["hazard_id"])+" · "+esc(h["severity"])+"</h3>"
            for k in ("suite","description","endpoint","denominator","operator","threshold","rationale","mitigation"):
                body+="<p>"+esc(k.replace("_"," ").capitalize())+": "+esc(h[k])+"</p>"
            body+="</section>"
        body+="<h2>Governance</h2>"
        for key,value in p["governance"].items():
            body+="<p>"+esc(key.replace("_"," "))+": "+esc(value)+"</p>"
        body+="<h2>Required suite coverage</h2>"
        for s in p["coverage"]:
            body+="<p>"+esc(s["suite"])+" · "+("Runnable" if s["canonical_runnable"] else "Required but not runnable")+" · "+esc(", ".join(s["hazard_ids"]) or "No hazard defined")+" · Measurement not verified</p>"
        body+="<p><a href='/buyer-risk-download?plan_id="+esc(plan_id)+"'>Download frozen protocol JSON</a></p>"
        if doc["scope_current"]:
            body+="<p><a href='/buyer-risk-activation?plan_id="+esc(plan_id)+"'>Compile and activate canonical evaluation conditions</a></p>"
    else:
        body+="<p>"+esc(doc["target"]["name"])+" · "+esc(doc["target"]["version"])+"</p>"
        body+="<p>Define up to one hazard per suite in this browser form. Unselected suites remain explicit gaps. All criteria are buyer-authored planning inputs, not activated scoring thresholds.</p>"
        body+="<form method='post' action='/buyer-risk-freeze'>"+_hidden("csrf",actor["csrf"])+_hidden("plan_id",plan_id)
        for s in doc["suites"]:
            sid=s["suite"]
            body+="<section><h3>"+esc(sid)+"</h3><p>"+esc(s["guidance"])+" · "+("Runnable" if s["runnable"] else "Not runnable")+"</p>"
            body+="<label><input type='checkbox' name='use_"+esc(sid)+"' value='yes'> Include hazard</label>"
            for k,label,limit in (("description","Clinical harm",1000),("endpoint","Rate or proportion endpoint",300),("denominator","Eligible denominator and missingness",1000),("rationale","Threshold rationale",2000),("mitigation","Mitigation and escalation",2000)):
                body+="<label>"+label+"<textarea name='"+k+"_"+esc(sid)+"' maxlength='"+str(limit)+"'></textarea></label>"
            body+="<label>Severity<select name='severity_"+esc(sid)+"'><option value=''>Select</option>"+"".join("<option>"+esc(v)+"</option>" for v in risk.SEVERITIES)+"</select></label>"
            body+="<label>Criterion<select name='operator_"+esc(sid)+"'><option value=''>Select</option>"+"".join("<option value='"+esc(v)+"'>"+esc(v)+"</option>" for v in risk.OPERATORS)+"</select></label>"
            body+="<label>Threshold (proportion from 0 to 1)<input type='number' name='threshold_"+esc(sid)+"' min='0' max='1' step='any'></label></section>"
        body+="<h2>Governance and clinical scope</h2>"
        for k in ("population","setting_boundaries","oversight","data_governance","jurisdiction_review","stop_rules"):
            body+="<label>"+esc(k.replace("_"," ").capitalize())+"<textarea name='"+k+"' maxlength='2000' required></textarea></label>"
        body+="<label><input type='checkbox' name='confirm' value='yes' required> I approve these buyer-authored planning criteria and acknowledge they do not activate scoring or authorize deployment.</label><button>Freeze risk protocol</button></form>"
    body+="<h2>Limitations</h2><ul>"+"".join("<li>"+esc(x)+"</li>" for x in risk.LIMITATIONS)+"</ul>"
    return page("Buyer clinical risk protocol",body)


def dispatch(directory,actor,form):
    plan_id=form.get("plan_id","")
    draft=risk.view(directory,actor,plan_id)
    if draft["id"]:
        raise access.AccessDenied("protocol already frozen")
    govkeys=("population","setting_boundaries","oversight","data_governance","jurisdiction_review","stop_rules")
    fields=("description","endpoint","denominator","rationale","mitigation","severity","operator","threshold")
    expected={"plan_id","confirm",*govkeys}
    optional=set()
    hazards=[]
    for s in draft["suites"]:
        sid=s["suite"]
        use="use_"+sid
        optional.add(use)
        keys={k:k+"_"+sid for k in fields}
        expected.update(keys.values())
        if use in form:
            if form[use]!="yes" or any(v not in form for v in keys.values()):
                raise access.AccessDenied("invalid selected hazard")
            h={k:form[v] for k,v in keys.items()}
            h["threshold"]=float(h["threshold"])
            h.update(hazard_id="hazard-"+sid,suite=sid)
            hazards.append(h)
    if not expected<=set(form) or set(form)-expected-optional:
        raise access.AccessDenied("unexpected risk protocol fields")
    return risk.freeze(directory,actor,plan_id,hazards,{k:form[k] for k in govkeys},confirmed=form["confirm"]=="yes")
