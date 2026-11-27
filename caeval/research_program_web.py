"""Institutional research programme browser pages."""
from . import research_program as programs, institution_access as access
from . import institution_research as studio


def overview(directory,actor,plan_id):
    from .institution_web import page,esc,_hidden
    doc=programs.inventory(directory,actor,plan_id)
    body="<p><a href='/setup'>Assessment setup</a> · <a href='/research'>Research studio</a></p>"
    body+="<p>"+esc(doc["target"]["name"])+" · "+esc(doc["target"]["version"])+"</p>"
    body+="<p>Coordinate existing planned studies in a frozen scope. No model calls or canonical procurement approval.</p>"
    for item in doc["programmes"]:
        body+="<p><a href='/research-program?id="+esc(item["id"])+"'>"+esc(item["id"])+"</a> · "+esc(item["owner"])+"</p>"
    if any(doc["choices"].values()):
        body+="<section><h2>Freeze research programme</h2><form method='post' action='/research-program-create'>"
        body+=_hidden("csrf",actor["csrf"])+_hidden("plan_id",plan_id)
        for kind in sorted(studio.MODULES):
            if kind not in doc["choices"]:
                body+=_hidden(kind,"")
                continue
            body+="<label>"+esc(studio.LABELS[kind])+" <select name='"+esc(kind)+"'><option value=''>Not planned</option>"
            for sid in doc["choices"][kind]:
                body+="<option value='"+esc(sid)+"'>"+esc(sid)+"</option>"
            body+="</select></label>"
        body+="<p>Only your own matching unexposed planned studies are eligible. Missing modules remain explicit; create missing studies in the research studio before freezing.</p>"
        body+="<label><input type='checkbox' name='confirm' value='yes' required> I approve this exact scope and immutable study inventory.</label><button>Freeze programme</button></form></section>"
    else:
        body+="<p>No eligible planned studies. Freeze matching specifications in the research studio before distributing candidate inputs.</p>"
    return page("Institutional research programmes",body)


def detail(directory,actor,identifier):
    from .institution_web import page,esc,_hidden
    doc=programs.status(directory,actor,identifier)
    body="<p><a href='/research-programs?plan_id="+esc(doc["buyer_plan_id"])+"'>Programmes</a> · <a href='/research'>Research studio</a></p>"
    body+="<p>"+esc(doc["target"]["name"])+" · version "+esc(doc["target"]["version"])+"</p>"
    body+="<p>Frozen programme digest: "+esc(doc["program_digest"])+"</p>"
    body+="<p>Unplanned related modules: "+esc(", ".join(doc["unplanned_related_modules"]) or "None")+"</p>"
    body+="<p>"+("All planned reports integrity checked." if doc["all_planned_reports_available"] else "Some planned reports are not available.")+" No clinical approval.</p>"
    body+="<table><thead><tr><th>Module</th><th>State</th><th>Review protocol</th><th>Report</th></tr></thead><tbody>"
    for m in doc["members"]:
        body+="<tr><td>"+esc(studio.LABELS[m["kind"]])+"</td><td>"+esc(m["state"])+"</td><td>"+esc(m.get("review_protocol_status","unavailable"))+"</td><td>"
        if m["report_digest"]:
            body+="<a href='/research-download?kind=report&amp;id="+esc(m["study_id"])+"'>Verified report</a>"
        else:
            body+="Not available"
        body+="</td></tr>"
    body+="</tbody></table><h2>Required suite coverage</h2><table><thead><tr><th>Suite</th><th>Canonical availability</th><th>Planned</th><th>Available experimental reports</th></tr></thead><tbody>"
    for row in doc["coverage"]:
        body+="<tr><td>"+esc(row["suite"])+"</td><td>"+("Runnable" if row["canonical_runnable"] else "Required but not run")+"</td><td>"+esc(", ".join(row["planned_modules"]) or "None")+"</td><td>"+esc(", ".join(row["integrity_checked_reports"]) or "None")+"</td></tr>"
    body+="</tbody></table>"
    if actor["role"]=="admin":
        body+="<form method='post' action='/research-program-dossier'>"+_hidden("csrf",actor["csrf"])+_hidden("id",identifier)
        body+="<label><input type='checkbox' name='confirm' value='yes' required> I authorize an experimental summary export with offline report replay. Partial or unavailable reports remain disclosed.</label><button>Replay and download summary dossier</button></form>"
    body+="<h2>Limitations</h2><ul>"+"".join("<li>"+esc(x)+"</li>" for x in doc["limitations"])+"</ul>"
    return page("Frozen research programme",body)


def create(directory,actor,form):
    if set(form)!={"plan_id","confirm",*studio.MODULES}:
        raise access.AccessDenied("invalid programme form")
    return programs.freeze(directory,actor,form["plan_id"],
        {k:form[k] for k in studio.MODULES if form[k]},confirmed=form["confirm"]=="yes")
