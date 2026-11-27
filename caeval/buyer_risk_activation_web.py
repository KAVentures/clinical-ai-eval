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
    body+="<button>Preview compiled conditions</button></form>"
    body+="<p>Only an administrator can activate a preview. Existing procurement studies cannot be replaced.</p>"
    return page("Activate buyer risk conditions",body)


def _inputs(form,*,activate=False):
    allowed={"plan_id","pack_id","panel_id"}
    if activate:
        allowed|={"approved_digest","confirm"}
    if not allowed<=set(form) or any(not k.startswith("map_") for k in set(form)-allowed):
        raise access.AccessDenied("invalid conditions form")
    return form["plan_id"],form["pack_id"],form["panel_id"],{k[4:]:v for k,v in form.items() if k.startswith("map_") and v}


def preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    args=_inputs(form)
    doc=activation.preview(directory,actor,*args)
    body="<p><a href='/buyer-risk-activation?plan_id="+esc(args[0])+"'>Change selections</a></p>"
    body+="<p>Compiled digest "+esc(doc["digest"])+"</p><p>Case pack "+esc(doc["pack_id"])+" · Judge panel "+esc(doc["panel_id"])+"</p>"
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
    return activation.activate(directory,actor,*_inputs(form,activate=True),form["approved_digest"],confirmed=form["confirm"]=="yes")


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
    from . import procurement_workflow as pw, buyer_agent as buyer, institution_setup as setup
    with access.db(directory) as conn:
        setup._actor(conn,actor)
    state=pw.load(buyer._root(directory)/"assessment")
    doc=state["conditions"].get("risk_activation")
    if not doc:
        raise access.AccessDenied("risk-bound study is not active")
    activation._check_conditions(state,doc)
    body="<p><a href='/setup'>Assessment setup</a> · <a href='/committee'>Committee</a></p><p>Active conditions "+esc(state["conditions_hash"])+" · "+esc(doc["digest"])+"</p>"
    body+="<p>Required but not run: "+esc(", ".join(doc["required_but_not_run"]) or "None")+"</p>"
    body+="<p>Use an imported complete response submission from this exact plan and pack. No execution occurs here.</p>"
    body+="<form method='post' action='/buyer-risk-bound-prepare'>"+_hidden("csrf",actor["csrf"])+_hidden("conditions_hash",state["conditions_hash"])+_hidden("panel_id",doc["panel_id"])
    body+="<label>Complete submission ID<input name='submission_id' required maxlength='64'></label>"
    for k in project.MANDATORY_GOVERNANCE:
        body+="<label>"+esc(k.replace("_"," "))+"<input name='"+esc(k)+"' required maxlength='500'></label>"
    body+="<p>For diagnosis/treatment and clinician review use yes or no. For medical device status use yes, no or unsure.</p>"
    body+="<label><input type='checkbox' name='confirm' value='yes' required> Prepare an assessment under these exact activated conditions.</label><button>Prepare bound assessment</button></form>"
    return page("Active buyer risk study",body)
