"""Administrator-only retention policy view; destructive maintenance stays offline."""
from . import institution_retention as retention, institution_access as access


def render(directory,actor):
    from .institution_web import page,esc,_hidden
    data=retention.view(directory,actor)
    current=data["current"]
    p=current["policy"] if current else {}
    body="<p><a href='/'>Workspace</a></p><p>Configure retention and legal holds. No data is deleted from this browser. Trusted IT must preview and approve offline maintenance separately.</p>"
    if current:
        body+="<p>Current policy digest "+esc(current["id"])+"</p>"
    body+="<form method='post' action='/retention-policy'>"+_hidden("csrf",actor["csrf"])+_hidden("previous",current["id"] if current else "")
    for key,label,minimum in (("expired_session_grace_days","Expired session grace days",0),("failure_retention_days","Obsolete login failure retention days",1),("synthetic_fhir_retention_days","Synthetic FHIR retention days (blank = retain)",1)):
        value=p.get(key)
        body+="<label>"+label+"<input type='number' min='"+str(minimum)+"' max='36500' name='"+key+"' value='"+esc("" if value is None else value)+"'"+(" required" if key!="synthetic_fhir_retention_days" else "")+"></label>"
    body+="<label><input type='checkbox' name='hold_all' value='yes'"+(" checked" if p.get("hold_all") else "")+"> Hold all maintenance deletions</label>"
    body+="<h2>Individual synthetic rehearsal holds</h2>"
    for row in data["synthetic_reports"]:
        ident=row["id"]
        body+="<label><input type='checkbox' name='hold_"+esc(ident)+"' value='yes'"+(" checked" if ident in p.get("synthetic_holds",[]) else "")+"> "+esc(ident)+"</label>"
    body+="<label>Governance rationale<textarea name='rationale' required maxlength='2000'></textarea></label>"
    body+="<label><input type='checkbox' name='confirm' value='yes' required> Approve this explicit policy revision and hold inventory.</label><button>Approve retention policy</button></form>"
    body+="<h2>Policy history</h2>"
    for row in reversed(data["history"]):
        body+="<p>"+esc(row["id"])+" · "+esc(row["policy"]["approved_by"])+"</p>"
    body+="<ul>"+"".join("<li>"+esc(x)+"</li>" for x in data["limitations"])+"</ul>"
    return page("Governed retention and legal holds",body)


def dispatch(directory,actor,form):
    required={"previous","expired_session_grace_days","failure_retention_days","synthetic_fhir_retention_days","rationale","confirm"}
    if not required<=set(form) or any(k!="hold_all" and not k.startswith("hold_") for k in set(form)-required):
        raise access.AccessDenied("invalid retention policy fields")
    if any(v!="yes" for k,v in form.items() if k.startswith("hold_")):
        raise access.AccessDenied("invalid hold selection")
    spec={k:int(form[k]) for k in ("expired_session_grace_days","failure_retention_days")}
    spec["synthetic_fhir_retention_days"]=int(form["synthetic_fhir_retention_days"]) if form["synthetic_fhir_retention_days"] else None
    spec.update(hold_all="hold_all" in form,synthetic_holds=sorted(k[5:] for k in form if k.startswith("hold_") and k!="hold_all"),
                rationale=form["rationale"])
    return retention.approve(directory,actor,spec,form["previous"],confirmed=form["confirm"]=="yes")
