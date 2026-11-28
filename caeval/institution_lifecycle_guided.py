"""Guided registration of local policy and monitor using native preview gates."""
import json
from . import hospital_referral as h, institution_access as access
from . import unified_assessment as u


def _scope(directory,actor,query):
    if set(query)!={"programme","vendor"}:
        raise access.AccessDenied("select exact programme and vendor")
    status=u.status(directory,actor,query["programme"])
    plans=[p for p in status["programme"]["plans"] if p["vendor_id"]==query["vendor"]]
    if not plans or status["state"]!="current":
        raise access.AccessDenied("current exact vendor scope required")
    return status,plans


def policy_form(directory,actor,query):
    from .institution_web import page,esc,_hidden
    from . import institution_hospital_referral as service
    status,plans=_scope(directory,actor,query)
    if any(r["vendor_id"]==query["vendor"] and r["locked_source"] is not None for r in status["requirements"]):
        raise access.AccessDenied("policy must precede source association")
    if any(r["programme_id"]==query["programme"] and r["vendor_id"]==query["vendor"] for r in service.overview(directory,actor)):
        raise access.AccessDenied("vendor policy already registered")
    with access.db(directory) as conn:
        u._actor(conn,actor,"admin")
        reviewers=[r["id"] for r in conn.execute("SELECT id FROM users WHERE enabled=1 AND role='reviewer' ORDER BY id")]
    body="<p>Define criteria with the institution's qualified reviewers. No default clinical acceptance criteria are supplied.</p><p>"+esc(plans[0]["target"]["name"])+" · "+esc(plans[0]["target"]["version"])+"</p>"
    if len(reviewers)<2:
        return page("Register hospital policy",body+"<p>At least two active reviewer accounts must be provisioned before registration.</p>")
    body+="<form method='post' action='/cockpit-policy-preview'>"+_hidden("csrf",actor["csrf"])+_hidden("programme_id",query["programme"])+_hidden("vendor_id",query["vendor"])
    body+="<label>Policy identifier<input name='policy_id' required maxlength='80'></label><label>Exact intended use<textarea name='intended_use' required maxlength='2000'></textarea></label>"
    for gate in h.MANDATORY:
        body+="<section><h3>"+esc(gate.replace("_"," "))+"</h3><label>Reviewer<select name='reviewer_"+gate+"' required><option value=''>Choose reviewer</option>"
        for reviewer in reviewers:
            body+="<option value='"+esc(reviewer)+"'>"+esc(reviewer)+"</option>"
        body+="</select></label><label>Local criterion<textarea name='criterion_"+gate+"' maxlength='1500' required></textarea></label></section>"
    body+="<button>Preview mandatory policy</button></form><p>Use the native advanced JSON builder for additional local gates.</p>"
    return page("Register hospital policy",body)


def policy_preview(directory,actor,form):
    from . import institution_hospital_referral_web as native
    expected={"policy_id","programme_id","vendor_id","intended_use"}|{p+g for g in h.MANDATORY for p in ("reviewer_","criterion_")}
    if set(form)!=expected:
        raise access.AccessDenied("invalid guided policy form")
    spec={k:form[k] for k in ("policy_id","programme_id","vendor_id","intended_use")}
    spec["gates"]=[{"id":g,"reviewer":form["reviewer_"+g],"criterion":form["criterion_"+g]} for g in h.MANDATORY]
    return native.preview(directory,actor,{"spec":json.dumps(spec)})


def monitor_form(directory,actor,query):
    from .institution_web import page,esc,_hidden
    from . import institution_lifecycle_monitor as service,lifecycle_monitor as m
    _,plans=_scope(directory,actor,query)
    if any(r["programme_id"]==query["programme"] and r["vendor_id"]==query["vendor"] for r in service.overview(directory,actor)):
        raise access.AccessDenied("vendor monitor already registered")
    body="<p>Register only externally authorized operational use. Choose locally justified aggregate definitions and thresholds; none are supplied by the application.</p><p>"+esc(plans[0]["target"]["name"])+" · "+esc(plans[0]["target"]["version"])+"</p>"
    body+="<form method='post' action='/cockpit-monitor-preview'>"+_hidden("csrf",actor["csrf"])+_hidden("programme_id",query["programme"])+_hidden("vendor_id",query["vendor"])
    body+="<label>Monitor identifier<input name='monitor_id' maxlength='80' required></label><label>Intended use identifier<input name='intended_use_id' maxlength='80' required></label><label>Start date (UTC)<input type='date' name='start_date' required></label><label>Days per window<input type='number' name='window_days' min='1' max='31' required></label><label>Number of windows<input type='number' name='windows' min='1' max='104' required></label><label>External authorization SHA-256<input name='authorization_digest' maxlength='64' required></label><label><input type='checkbox' name='authorized' value='yes' required> External authorization is attested; this application does not independently verify it.</label>"
    body+="<h2>Aggregate metrics</h2><p>Fill one or more complete rows. Leave unused rows entirely blank. Basis points: 100 = 1 percentage point.</p>"
    for i in range(m.MAX_METRICS):
        prefix="metric_"+str(i)+"_"
        body+="<section><h3>Metric "+str(i+1)+"</h3><label>ID<input name='"+prefix+"id' maxlength='80'></label><label>Direction<select name='"+prefix+"direction'><option value=''>Choose</option><option value='higher_is_worse'>Higher is worse</option><option value='lower_is_worse'>Lower is worse</option></select></label><label>Alert threshold (basis points)<input type='number' name='"+prefix+"threshold_bp' min='0' max='10000'></label><label>Minimum denominator<input type='number' name='"+prefix+"min_denominator' min='1' max='10000000'></label><label>Approved metric definition SHA-256<input name='"+prefix+"definition_digest' maxlength='64'></label></section>"
    body+="<button>Preview monitoring plan</button></form>"
    return page("Register operational monitoring",body)


def monitor_preview(directory,actor,form):
    from . import institution_lifecycle_monitor_web as native,lifecycle_monitor as m
    base=("monitor_id","programme_id","vendor_id","intended_use_id","start_date","window_days","windows","authorization_digest")
    fields=("id","direction","threshold_bp","min_denominator","definition_digest")
    expected=set(base)|{"authorized"}|{"metric_"+str(i)+"_"+k for i in range(m.MAX_METRICS) for k in fields}
    if set(form)!=expected or form["authorized"]!="yes":
        raise access.AccessDenied("invalid guided monitoring form")
    spec={k:form[k] for k in base}
    spec.update(window_days=int(form["window_days"]),windows=int(form["windows"]),external_authorization_attested=True)
    spec["metrics"]=[]
    for i in range(m.MAX_METRICS):
        row={k:form["metric_"+str(i)+"_"+k] for k in fields}
        if not any(row.values()):
            continue
        if not all(row.values()):
            raise ValueError("incomplete metric row")
        row.update(threshold_bp=int(row["threshold_bp"]),min_denominator=int(row["min_denominator"]))
        spec["metrics"].append(row)
    return native.preview(directory,actor,{"spec":json.dumps(spec)})
