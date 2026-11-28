"""Institutional browser configuration for patient conversation assessments."""
from . import institution_conversation as conversation, institution_access as access, project


def render(directory,actor,plan_id=""):
    from .institution_web import page,esc,_hidden
    def form(action,fields,button):
        return "<form method='post' action='/conversation-"+action+"'>"+_hidden("csrf",actor["csrf"])+fields+"<button>"+esc(button)+"</button></form>"
    def select(key,label,options):
        return "<label>"+esc(label)+"<select name='"+key+"' required><option value=''>Choose</option>"+"".join("<option>"+esc(x)+"</option>" for x in options)+"</select></label>"
    body="<p><a href='/'>Workspace</a></p><p>Experimental patient-world testing. Use approved synthetic or non-identifiable cases. This browser queues work; it never makes candidate or judge calls.</p>"
    if actor["role"] in ("admin","operator"):
        choices=conversation.options(directory)
        if plan_id:
            from . import buyer_manual
            buyer_manual._identifier(plan_id,digest=True)
        fields="<label>Saved patient evaluation plan ID<input name='plan_id' maxlength='64' value='"+esc(plan_id)+"' required></label>"
        fields+=select("connector_id","Approved local conversation connector",choices["connectors"])
        fields+=select("pack_id","Patient-world case pack",choices["packs"])
        fields+=select("panel_id","Approved judge panel",choices["panels"])
        for q in project.MANDATORY_GOVERNANCE:
            fields+="<label>"+esc(q.replace("_"," "))+"<input name='"+esc(q)+"' required maxlength='500'></label>"
        body+="<section><h2>Prepare multi-turn assessment</h2>"+form("queue",fields,"Freeze and queue")+"</section>"
    for row in conversation.overview(directory,actor):
        body+="<section><h2>"+esc(row["id"])+"</h2><p>"+esc(row["status"])+" · "+esc(row["mode"])+" · version "+esc(row["target_version"])+"</p>"
        if row["binding_hash"]:
            body+="<p>Worker approval hash: "+esc(row["binding_hash"])+"</p>"
        if row["status"]=="queued" and (actor["role"]=="admin" or actor["user_id"]==row["owner"]):
            body+=form("cancel",_hidden("id",row["id"]),"Cancel queued job")
        if row["status"]=="completed":
            result=row["result"]
            body+="<p>Verified package "+esc(result["manifest_hash"])+"</p><p>Episodes: "+str(result["n_episodes"])+" · Review units: "+str(result["n_review_items"])+" · Claim ceiling: "+esc(result["claim"])+"</p>"
        if row["required_but_not_run"]:
            body+="<p>Required but unavailable: "+esc(", ".join(r["suite"] for r in row["required_but_not_run"]))+"</p>"
        body+="</section>"
    return page("Patient conversation assessments",body)


def dispatch(directory,actor,path,fields):
    access.require(actor,"admin","operator")
    if path=="/conversation-cancel" and set(fields)=={"id"}:
        conversation.cancel(directory,actor,fields["id"])
        return
    keys={"plan_id","connector_id","pack_id","panel_id"}
    if path!="/conversation-queue" or set(fields)!=keys|set(project.MANDATORY_GOVERNANCE):
        raise access.AccessDenied("invalid conversation form")
    spec={k:fields[k] for k in keys}
    spec["governance"]={q:fields[q] for q in project.MANDATORY_GOVERNANCE}
    conversation.prepare(directory,actor,spec)
