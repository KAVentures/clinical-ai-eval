"""Institutional browser view of mixed-profile evaluation programmes."""
from . import mixed_evaluation as mixed, institution_access as access


def render(directory,actor,parent_id):
    from .institution_web import page,esc,_hidden
    doc=mixed.status(directory,actor,parent_id)
    body="<p><a href='/setup'>Assessment setup</a></p><p>"+esc(doc["target"]["name"])+" · "+esc(doc["target"]["version"])+"</p>"
    body+="<p>Each profile is a separate scientific scope. No blocked parent requirement is satisfied by splitting it, and no pooled score or clinical approval is inferred.</p>"
    body+="<h2>Original parent requirements</h2>"
    for row in doc["parent_required_suites"]:
        body+="<p>"+esc(row["suite"])+" · "+("Runnable under parent rules" if row["implemented"] else "Required, not runnable: "+esc(row["blocked_reason"]))+"</p>"
    for lane in doc["lanes"]:
        body+="<section><h2>"+esc(lane["profile"])+"</h2><p>Audience: "+esc(lane["audience"])+"</p>"
        body+="<p>Scoped plan: "+esc(lane["plan_id"])+"</p>"
        body+="<p>Runnable: "+esc(lane["runnable"])+" · Required but not run: "+esc(lane["required_but_not_run"])+"</p>"
        if doc["frozen"]:
            pid=esc(lane["plan_id"])
            from . import executors, institution_conversation
            body+="<p>"
            if lane["executors"]==[executors.GENERIC_PAIRED_TEXT]:
                body+="<a href='/guided-collection?plan_id="+pid+"'>Single-turn collection</a> · "
            if lane["runnable"]==[institution_conversation.FAMILY]:
                body+="<a href='/conversation?plan_id="+pid+"'>Patient conversation setup</a> · "
            body+="<a href='/research-buyer-coverage?plan_id="+pid+"'>Related research coverage</a></p>"
            for e in lane["evidence"]:
                body+="<p>"+esc(e["suite"])+" · "+esc(e["state"])+" · "+str(len(e["packages"]))+" packages · "+str(e["related_experimental_reports"])+" related research reports</p>"
        body+="</section>"
    if not doc["frozen"]:
        body+="<p>Approval digest: "+esc(doc["digest"])+"</p><form method='post' action='/mixed-evaluation-freeze'>"+_hidden("csrf",actor["csrf"])+_hidden("parent_id",parent_id)+_hidden("digest",doc["digest"])
        body+="<label><input type='checkbox' name='confirm' value='yes' required> Freeze the exact profile-specific plans. No execution or data transmission is approved.</label><button>Freeze mixed evaluation programme</button></form>"
    return page("Mixed-profile evaluation programme",body)


def freeze(directory,actor,form):
    if set(form)!={"parent_id","digest","confirm"}:
        raise access.AccessDenied("invalid mixed programme form")
    return mixed.freeze(directory,actor,form["parent_id"],form["digest"],confirmed=form["confirm"]=="yes")
