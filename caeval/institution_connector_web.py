"""Institutional collection queue; the browser never executes network calls."""
from . import buyer_connector as connector, institution_access as access
from . import collection_assessment as handoff, buyer_assessment as assessment, project


def render(directory,actor):
    from .institution_web import page,esc,_hidden
    access.require(actor,"admin","operator")
    body="<p><a href='/'>Workspace</a></p><p>Administrator-staged, literal loopback candidate endpoints only. Queueing does not send data. A trusted local operator must explicitly execute each binding through the CLI.</p>"
    options=""
    for cfg in connector.connectors(directory):
        options+="<option value='"+esc(cfg["connector_id"])+"'>"+esc(cfg["connector_id"]+" — "+cfg["target_name"]+" "+cfg["target_version"])+"</option>"
    if options:
        body+="<section><h2>Queue collection</h2><form action='/collection-queue' method='post'>"+_hidden("csrf",actor["csrf"])
        body+="<label>Prepared exchange ID<input name='exchange_id' required maxlength='64'></label><label>Approved connector<select name='connector_id'>"+options+"</select></label><button>Queue without executing</button></form></section>"
    for row in connector.list_collections(directory):
        binding=row["binding"]
        body+="<section><h2>"+esc(binding["target_name"])+" "+esc(binding["target_version"])+"</h2><p>"+esc(row["state"])+" · "+str(binding["n_expected"])+" expected responses</p>"
        body+="<p>Collection ID: "+esc(row["id"])+"</p><p>Approval binding: "+esc(row["binding_hash"])+"</p>"
        receipt=row["receipt"]
        if receipt.get("submission_id"):
            body+="<p>Submission ID: "+esc(receipt["submission_id"])+" · Provided: "+esc(receipt["n_provided"])+"</p>"
        if row["state"]=="complete_unscored":
            body+="<form method='post' action='/collection-assessment-preview'>"+_hidden("csrf",actor["csrf"])+_hidden("collection_id",row["id"])
            body+="<label>Approved judge panel<select name='panel_id' required><option value=''>Choose</option>"+"".join("<option>"+esc(p)+"</option>" for p in assessment.panels(directory))+"</select></label>"
            for k in project.MANDATORY_GOVERNANCE:
                body+="<label>"+esc(k.replace("_"," "))+"<input name='"+esc(k)+"' required maxlength='500'></label>"
            body+="<p>For diagnosis/treatment and clinician review, use yes or no; device status yes, no or unsure.</p><label>Activated procurement conditions hash (optional; leave empty only for unbound research)<input name='conditions_hash' maxlength='64'></label><button>Preview assessment handoff</button></form>"
        if row["state"]=="queued":
            body+="<form action='/collection-cancel' method='post'>"+_hidden("csrf",actor["csrf"])+_hidden("collection_id",row["id"])+"<button>Cancel queued collection</button></form>"
        body+="</section>"
    access.event(directory,actor["user_id"],"candidate-collections-opened")
    return page("Candidate response collections",body)


def dispatch(directory,actor,path,form):
    access.require(actor,"admin","operator")
    if path=="/collection-queue" and set(form)=={"exchange_id","connector_id"}:
        result=connector.queue(directory,form["exchange_id"],form["connector_id"])
        action="candidate-collection-queued"
    elif path=="/collection-cancel" and set(form)=={"collection_id"}:
        result=connector.cancel(directory,form["collection_id"])
        action="candidate-collection-cancelled"
    else:
        raise access.AccessDenied("invalid collection form")
    access.event(directory,actor["user_id"],action,result["id"])
    return result


def _handoff_inputs(form,*,approve=False):
    expected={"collection_id","panel_id","conditions_hash",*project.MANDATORY_GOVERNANCE}
    if approve:
        expected|={"approved_digest","confirm"}
    if set(form)!=expected:
        raise access.AccessDenied("invalid assessment handoff form")
    return (form["collection_id"],form["panel_id"],{k:form[k] for k in project.MANDATORY_GOVERNANCE},
            form["conditions_hash"] or None)


def preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    cid,panel,governance,conditions=_handoff_inputs(form)
    doc=handoff.preview(directory,actor,cid,panel,governance,conditions_hash=conditions)
    body="<p><a href='/collections'>Back to collections</a></p><p>Product "+esc(doc["target"]["name"])+" · "+esc(doc["target"]["version"])+"</p>"
    body+="<p>"+esc(doc["n_responses"])+" complete responses · "+esc(doc["mode"])+" · panel "+esc(panel)+"</p><p>Conditions: "+esc(conditions or "Unbound research")+"</p>"
    body+="<p>Assessment "+esc(doc["assessment_id"])+"</p><p>Approval digest "+esc(doc["digest"])+"</p>"
    for k,v in doc["governance"].items():
        body+="<p>"+esc(k.replace("_"," "))+": "+esc(v)+"</p>"
    body+="<form method='post' action='/collection-assessment-prepare'>"+_hidden("csrf",actor["csrf"])+"".join(_hidden(k,v) for k,v in form.items())+_hidden("approved_digest",doc["digest"])
    body+="<label><input type='checkbox' name='confirm' value='yes' required> Approve these exact collected responses and prepared assessment inputs. This does not queue or execute evaluation.</label><button>Prepare assessment</button></form>"
    return page("Review collection assessment handoff",body)


def prepare(directory,actor,form):
    cid,panel,governance,conditions=_handoff_inputs(form,approve=True)
    return handoff.prepare(directory,actor,cid,panel,governance,form["approved_digest"],
                           conditions_hash=conditions,confirmed=form["confirm"]=="yes")
