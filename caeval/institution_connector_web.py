"""Institutional collection queue; the browser never executes network calls."""
from . import buyer_connector as connector, institution_access as access


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
