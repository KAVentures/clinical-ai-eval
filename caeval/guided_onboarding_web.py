"""Plan-specific guided collection setup."""
from . import guided_onboarding as guide, institution_access as access


def render(directory,actor,plan_id):
    from .institution_web import page,esc,_hidden
    d=guide.options(directory,actor,plan_id)
    body="<p><a href='/setup'>Assessment setup</a></p><p>"+esc(d["target"]["name"])+" · "+esc(d["target"]["version"])+"</p>"
    body+="<p>Runnable suites: "+esc(d["runnable_suites"])+"</p><p>Required but not runnable: "+esc(d["required_but_not_run"])+"</p>"
    if not d["single_turn_supported"]:
        body+="<p>This plan is not a pure single-turn evaluation. Use the conversation or research workflows; no suites will be silently omitted.</p>"
    elif not d["connectors"] or not d["packs"]:
        body+="<p>IT must stage an exact product/version single-turn connector and approved case pack before collection. Existing offline response exchange remains available.</p>"
    else:
        body+="<form method='post' action='/guided-collection-preview'>"+_hidden("csrf",actor["csrf"])+_hidden("plan_id",plan_id)
        for key,label,rows in (("connector_id","Approved matching connector",[(r["connector_id"],r["connector_id"]+" · "+r["protocol"]) for r in d["connectors"]]),
                                ("pack_id","Approved case pack",[(x,x) for x in d["packs"]])):
            body+="<label>"+label+"<select name='"+key+"' required><option value=''>Choose</option>"
            body+="".join("<option value='"+esc(v)+"'>"+esc(t)+"</option>" for v,t in rows)+"</select></label>"
        body+="<button>Preview exact collection</button></form>"
    return page("Guided candidate onboarding",body)


def preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    if set(form)!={"plan_id","pack_id","connector_id"}:
        raise access.AccessDenied("invalid guided preview")
    d=guide.preview(directory,actor,**form)
    body="<p>Candidate "+esc(d["target"]["name"])+" · "+esc(d["target"]["version"])+"</p>"
    body+="<p>Pack "+esc(d["pack_id"])+" · Connector "+esc(d["connector_id"])+" · "+esc(d["n_expected"])+" prompts</p>"
    body+="<p>Runnable: "+esc(d["runnable_suites"])+"</p><p>Required but not runnable: "+esc(d["required_but_not_run"])+"</p><p>Approval digest: "+esc(d["digest"])+"</p>"
    body+="<form method='post' action='/guided-collection-approve'>"+_hidden("csrf",actor["csrf"])+"".join(_hidden(k,v) for k,v in form.items())+_hidden("digest",d["digest"])
    body+="<label><input type='checkbox' name='confirm' value='yes' required> Approve this exact test inventory and connector for queueing. No prompts are transmitted now.</label><button>Prepare exchange and queue</button></form>"
    return page("Review guided collection",body)


def approve(directory,actor,form):
    if set(form)!={"plan_id","pack_id","connector_id","digest","confirm"}:
        raise access.AccessDenied("invalid guided approval")
    return guide.approve(directory,actor,form["plan_id"],form["pack_id"],form["connector_id"],form["digest"],confirmed=form["confirm"]=="yes")
