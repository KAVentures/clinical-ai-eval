"""HTML views and strict form dispatcher for institutional lifecycle governance."""
from __future__ import annotations

from . import institution_lifecycle as lifecycle, institution_access as access


def render(directory,actor):
    from .institution_web import esc,page,_hidden
    data=lifecycle.overview(directory,actor)
    write=actor["role"] in ("admin","operator")
    def form(action,fields,button,hidden=()):
        return ("<form action='/lifecycle' method='post'>"+_hidden("csrf",actor["csrf"])
                +_hidden("action",action)+"".join(_hidden(k,v) for k,v in hidden)
                +fields+"<button>"+esc(button)+"</button></form>")
    def field(key,title,typ="text"):
        return "<label>"+esc(title)+" <input type='"+typ+"' name='"+key+"' required maxlength='2000'></label>"
    def select(key,title,values):
        return "<label>"+esc(title)+" <select name='"+key+"'>"+"".join("<option>"+esc(v)+"</option>" for v in values)+"</select></label>"
    body="<p><a href='/'>Workspace</a></p><p>Administrative register only. No deployment authorization. A pause is a record, not an automatic clinical-system shutdown. Do not enter patient identifiers.</p>"
    if write:
        body+="<section><h2>Plan a pilot</h2>"+form("create",field("vendor_id","Registered candidate ID")+field("product_version","Exact version")+field("scope","Scope and intended use")+field("owner","Account ID of owner")+field("review_date","Next review","date")+field("rollback_plan","Rollback plan"),"Create pilot")+"</section>"
    for pilot in data["pilots"]:
        h=(("pilot_id",pilot["id"]),("revision",pilot["revision"]))
        body+="<section><h2>"+esc(pilot["vendor_id"])+" · "+esc(pilot["product_version"])+"</h2>"
        body+="<p>"+esc(pilot["scope"])+"</p><p>State: "+esc(pilot["state"])+" · Owner: "+esc(pilot["owner"])+" · Review: "+esc(pilot["review_date"])+"</p>"
        body+="<p>Rollback: "+esc(pilot["rollback_plan"])+"</p><p>Blocking open records: "+str(pilot["blocking_open"])+(" · Overdue" if pilot["overdue"] else "")+"</p>"
        if actor["role"]=="admin":
            body+="<p><a href='/lifecycle-history?id="+esc(pilot["id"])+"'>Record history</a></p>"
        for item in pilot["items"]:
            body+="<section><h3>"+esc(item["title"])+"</h3><p>"+esc(item["kind"])+" · "+esc(item["severity"])+" · "+esc(item["state"])+"</p><p>"+esc(item["action"])+"</p><p>Owner: "+esc(item["owner"])+" · Due: "+esc(item["due"])+"</p>"
            if write and item["state"]=="open" and pilot["state"]!="closed":
                body+=form("resolve",field("reason","Resolution and evidence"),"Record resolution",(("item_id",item["id"]),("revision",item["revision"])))
            body+="</section>"
        if write and pilot["state"]!="closed":
            body+=form("add",select("kind","Record type",lifecycle.KINDS)+select("severity","Severity",lifecycle.SEVERITIES)+field("title","Title")+field("action","Action / mitigation")+field("owner","Owner account ID")+field("due","Due date","date"),"Add register item",h)
            allowed=("paused","in_review","closed") if actor["role"]=="admin" else ("paused",)
            body+=form("transition",select("state","Administrative state",allowed)+field("reason","Reason"),"Record state change",h)
        body+="</section>"
    return page("Implementation and monitoring register",body)


def render_history(directory,actor,pilot_id):
    from .institution_web import esc,page
    import json
    body="<p><a href='/lifecycle'>Register</a></p><p>Administrative history; not clinical evidence.</p>"
    for event in lifecycle.history(directory,actor,pilot_id):
        body+="<section><h3>"+esc(event["action"])+" · "+esc(event["at"])+"</h3><p>"+esc(event["actor"])+"</p><pre style='white-space:pre-wrap'>"+esc(json.dumps(json.loads(event["snapshot"]),ensure_ascii=False,indent=2))+"</pre></section>"
    return page("Pilot event history",body)


def dispatch(directory,actor,form):
    access.require(actor,"admin","operator")
    action=form.pop("action","")
    if action=="create":
        return lifecycle.create_pilot(directory,actor,form)
    try:
        rev=int(form.pop("revision"))
    except (ValueError,KeyError):
        raise access.AccessDenied("record revision required")
    if action=="add":
        pilot=form.pop("pilot_id","")
        return lifecycle.add_item(directory,actor,pilot,form,rev)
    if action=="resolve" and set(form)=={"item_id","reason"}:
        return lifecycle.resolve_item(directory,actor,form["item_id"],rev,form["reason"])
    if action=="transition" and set(form)=={"pilot_id","state","reason"}:
        return lifecycle.transition(directory,actor,form["pilot_id"],rev,form["state"],form["reason"])
    raise access.AccessDenied("invalid lifecycle form")
