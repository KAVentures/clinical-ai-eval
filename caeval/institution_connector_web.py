"""Institutional collection queue; the browser never executes network calls."""
from . import buyer_connector as connector, institution_access as access
from . import buyer_assessment as assessment, collection_assessment as handoff, project


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
            body+="<form action='/collection-assessment-preview' method='post'>"+_hidden("csrf",actor["csrf"])+_hidden("collection_id",row["id"])
            body+="<label>Approved judge panel<select name='panel_id' required><option value=''>Choose</option>"
            body+="".join("<option value='"+esc(p)+"'>"+esc(p)+"</option>" for p in assessment.panels(directory))+"</select></label>"
            body+="<label>Activated procurement conditions hash (required for risk-bound study)<input name='conditions_hash' maxlength='64'></label>"
            for key in project.MANDATORY_GOVERNANCE:
                body+="<label>"+esc(key.replace("_"," "))+"<input name='"+esc(key)+"' required maxlength='500'></label>"
            body+="<p>Use yes/no for diagnosis and oversight, yes/no/unsure for device status, and jurisdiction text.</p><button>Preview prepared assessment</button></form>"
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


def assessment_dispatch(directory,actor,path,form):
    from .institution_web import page,esc,_hidden
    keys={"collection_id","panel_id","conditions_hash",*project.MANDATORY_GOVERNANCE}
    if path=="/collection-assessment-preview" and set(form)==keys:
        doc=handoff.preview(directory,form["collection_id"],form["panel_id"],
                            {k:form[k] for k in project.MANDATORY_GOVERNANCE},
                            form["conditions_hash"] or None,actor=actor)
        body="<p><a href='/collections'>Back to collections</a></p><p>Exact candidate "+esc(doc["target_name"])+" · "+esc(doc["target_version"])+"</p>"
        body+="<p>Panel "+esc(doc["panel_id"])+" · "+esc(doc["n_responses"])+" responses · "+esc(doc["mode"])+"</p>"
        body+="<p>Conditions "+esc(doc["conditions_hash"] or "not risk-bound")+"</p><p>Assessment "+esc(doc["assessment_id"])+"</p><p>Approval digest "+esc(doc["digest"])+"</p>"
        body+="<p>This prepares a project only. No scoring, network calls or clinical deployment authorization.</p>"
        if actor["role"]=="admin":
            body+="<form action='/collection-assessment-prepare' method='post'>"+_hidden("csrf",actor["csrf"])
            body+="".join(_hidden(k,form[k]) for k in sorted(keys))+_hidden("approved_digest",doc["digest"])
            body+="<label><input type='checkbox' name='confirm' value='yes' required> Approve exact collection and prepared inputs</label><button>Prepare assessment</button></form>"
        return page("Review collected assessment",body)
    if path=="/collection-assessment-prepare" and set(form)==keys|{"approved_digest","confirm"} and form["confirm"]=="yes":
        return handoff.prepare(directory,form["collection_id"],form["panel_id"],
                              {k:form[k] for k in project.MANDATORY_GOVERNANCE},
                              form["conditions_hash"] or None,form["approved_digest"],confirmed=True,actor=actor)
    raise access.AccessDenied("invalid collection assessment form")
