"""Browser preview and explicit administrator activation of canonical conditions."""
from . import buyer_risk_activation as activation, buyer_risk_protocol as risk
from . import institution_access as access, buyer_manual as manual, buyer_assessment as assessment
from . import project, buyer_plan


def render(directory,actor,plan_id):
    from .institution_web import page,esc,_hidden
    saved=risk.view(directory,actor,plan_id)
    body="<p><a href='/buyer-risk?plan_id="+esc(plan_id)+"'>Frozen risk protocol</a> · <a href='/setup'>Assessment setup</a></p>"
    if not saved["id"] or not saved["scope_current"]:
        return page("Activate risk conditions",body+"<p>A current frozen risk protocol is required.</p>")
    body+="<p>Map each runnable single-turn family to a real canonical result metric. The denominator is every generated cell, including originals and variants. Invalid and incomplete cells remain missing. Do not interpret a free-text endpoint as a validated measure.</p>"
    body+="<form method='post' action='/buyer-risk-activation-preview'>"+_hidden("csrf",actor["csrf"])+_hidden("plan_id",plan_id)
    for name,label,options in (("pack_id","Approved case pack",manual.packs(directory)),("panel_id","Approved judge panel",assessment.panels(directory))):
        body+="<label>"+label+"<select name='"+name+"' required><option value=''>Select</option>"+"".join("<option>"+esc(x)+"</option>" for x in options)+"</select></label>"
    for h in saved["protocol"]["hazards"]:
        body+="<label>"+esc(h["hazard_id"])+" · "+esc(h["suite"])+" · "+esc(h["endpoint"])+"<select name='map_"+esc(h["hazard_id"])+"'><option value=''>Not mapped</option>"+"".join("<option>"+esc(m)+"</option>" for m in activation.METRICS)+"</select></label>"
    from . import institution_setup as setup
    body+="<h2>Additional candidates (optional)</h2><p>Select saved plans for distinct products with exactly matching intake, scope and test selection. The primary risk protocol applies to every candidate.</p>"
    for other in setup.overview(directory,actor)["plans"]:
        if other["id"]!=plan_id and not other.get("invalid") and other["current"]:
            body+="<label><input type='checkbox' name='candidate_"+esc(other["id"])+"' value='yes'> "+esc(other["target"]["name"])+" · "+esc(other["target"]["version"])+" · "+esc(other["id"][:12])+"</label>"
    body+="<button>Preview compiled conditions</button></form>"
    body+="<p>Only an administrator can activate a preview. Existing procurement studies cannot be replaced.</p>"
    return page("Activate buyer risk conditions",body)


def _inputs(form,*,activate=False):
    allowed={"plan_id","pack_id","panel_id"}
    if activate:
        allowed|={"approved_digest","confirm"}
    if not allowed<=set(form) or any(not k.startswith(("map_","candidate_")) for k in set(form)-allowed):
        raise access.AccessDenied("invalid conditions form")
    if any(v!="yes" for k,v in form.items() if k.startswith("candidate_")):
        raise access.AccessDenied("invalid selected candidate")
    return (form["plan_id"],form["pack_id"],form["panel_id"],{k[4:]:v for k,v in form.items() if k.startswith("map_") and v},
            sorted(k[len("candidate_"):] for k in form if k.startswith("candidate_")))


def preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    args=_inputs(form)
    doc=activation.preview(directory,actor,*args[:4],additional_plans=args[4])
    body="<p><a href='/buyer-risk-activation?plan_id="+esc(args[0])+"'>Change selections</a></p>"
    body+="<p>Compiled digest "+esc(doc["digest"])+"</p><p>Case pack "+esc(doc["pack_id"])+" · Judge panel "+esc(doc["panel_id"])+"</p>"
    for vid,entry in sorted(activation.candidates(doc).items()):
        body+="<p>Candidate "+esc(vid)+" · "+esc(entry["target"]["name"])+" · "+esc(entry["target"]["version"])+"</p>"
    body+="<p>Unmapped hazards: "+esc(", ".join(doc["unmapped_hazards"]) or "None")+" · Required but not run: "+esc(", ".join(doc["required_but_not_run"]) or "None")+"</p>"
    for h in doc["hazards"]:
        criterion=h["acceptance_criterion"]
        body+="<p>"+esc(h["hazard_id"])+" · "+esc(h["family"])+" · "+esc(criterion["metric"])+" "+esc(criterion["operator"])+" "+esc(criterion["threshold"])+" · "+esc(len(doc["cell_inventory"][h["family"]]))+" planned cells</p>"
    body+="<p>All generated cells; invalid and incomplete observations remain missing. No clinical validity or deployment authorization.</p>"
    if actor["role"]=="admin":
        body+="<form method='post' action='/buyer-risk-activation-freeze'>"+_hidden("csrf",actor["csrf"])
        body+="".join(_hidden(k,v) for k,v in form.items())+_hidden("approved_digest",doc["digest"])
        body+="<label><input type='checkbox' name='confirm' value='yes' required> I approve these exact mappings, denominator, panel and pack, and confirm this is a new study.</label><button>Activate canonical conditions</button></form>"
    return page("Review compiled risk conditions",body)


def freeze(directory,actor,form):
    args=_inputs(form,activate=True)
    return activation.activate(directory,actor,*args[:4],form["approved_digest"],confirmed=form["confirm"]=="yes",additional_plans=args[4])


def prepared(directory,actor,form):
    from . import institution_setup as setup
    expected={"submission_id","panel_id","conditions_hash",*project.MANDATORY_GOVERNANCE,"confirm"}
    if set(form)!=expected or form["confirm"]!="yes":
        raise access.AccessDenied("explicit bound preparation approval required")
    with access.db(directory) as conn:
        setup._actor(conn,actor)
    result=assessment.prepare(directory,form["submission_id"],form["panel_id"],
        {k:form[k] for k in project.MANDATORY_GOVERNANCE},conditions_hash=form["conditions_hash"])
    access.event(directory,actor["user_id"],"risk-bound-assessment-prepared",result["assessment_id"])
    return result


def active(directory,actor):
    from .institution_web import page,esc,_hidden
    from . import institution_setup as setup
    with access.db(directory) as conn:
        setup._actor(conn,actor)
        _,_,state,doc=activation.read_active(directory,conn=conn)
    body="<p><a href='/setup'>Assessment setup</a> · <a href='/committee'>Committee</a></p><p>Active conditions "+esc(state["conditions_hash"])+" · "+esc(doc["digest"])+"</p>"
    body+="<p>Required but not run: "+esc(", ".join(doc["required_but_not_run"]) or "None")+"</p>"
    for vid,entry in sorted(activation.candidates(doc).items()):
        body+="<p>Candidate "+esc(vid)+" · "+esc(entry["target"]["name"])+" · "+esc(entry["target"]["version"])+" · plan "+esc(entry["plan_id"][:12])+"</p>"
    body+="<p>Use an imported complete response submission from any frozen candidate plan and the exact pack. No execution occurs here.</p>"
    body+="<form method='post' action='/buyer-risk-bound-prepare'>"+_hidden("csrf",actor["csrf"])+_hidden("conditions_hash",state["conditions_hash"])+_hidden("panel_id",doc["panel_id"])
    body+="<label>Complete submission ID<input name='submission_id' required maxlength='64'></label>"
    for k in project.MANDATORY_GOVERNANCE:
        body+="<label>"+esc(k.replace("_"," "))+"<input name='"+esc(k)+"' required maxlength='500'></label>"
    body+="<p>For diagnosis/treatment and clinician review use yes or no. For medical device status use yes, no or unsure.</p>"
    body+="<label><input type='checkbox' name='confirm' value='yes' required> Prepare an assessment under these exact activated conditions.</label><button>Prepare bound assessment</button></form>"
    from . import buyer_risk_handoff as handoff
    body+="<h2>Completed assessment handoff</h2><p>Only verified jobs under these exact conditions are eligible. An administrator must approve copying the confidential packages into the institutional evidence store.</p>"
    for row in handoff.inventory(directory,actor):
        body+="<section><p>"+esc(row["job_id"])+" · "+esc(row["vendor_id"])+" · "+esc(row["status"])+"</p>"
        for pkg in row["packages"]:
            body+="<p>"+esc(pkg["family_id"])+" · "+esc(pkg["manifest_hash"])+"</p>"
        if row["status"]=="ready" and actor["role"]=="admin":
            body+="<form method='post' action='/buyer-risk-ingest'>"+_hidden("csrf",actor["csrf"])+_hidden("job_id",row["job_id"])+_hidden("conditions_hash",state["conditions_hash"])
            body+="<label><input type='checkbox' name='confirm' value='yes' required> Approve internal evidence handoff</label><button>Stage and ingest verified job</button></form>"
        body+="</section>"
    return page("Active buyer risk study",body)


def ingest(directory,actor,form):
    from . import buyer_risk_handoff as handoff
    if set(form)!={"job_id","conditions_hash","confirm"}:
        raise access.AccessDenied("unexpected evidence handoff fields")
    return handoff.ingest(directory,actor,form["job_id"],form["conditions_hash"],confirmed=form["confirm"]=="yes")
