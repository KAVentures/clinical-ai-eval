"""Role-scoped administrative evidence-gap view."""
from . import unified_work as service

TASKS={
    "review_stale_programme":"Review stale programme scope",
    "investigate_locked_source":"Investigate changed or unavailable locked evidence",
    "review_evidence_limitations":"Review evidence limitations",
    "investigate_native_inventory":"Investigate partial or unavailable work inventory",
    "verify_candidate_association":"Verify potential candidate evidence before association",
    "continue_native_work":"Continue native evaluation work",
    "inspect_or_prepare_native_work":"Inspect or prepare native evaluation work",
}


def render(directory,actor,ident):
    from .institution_web import page,esc
    doc=service.gaps(directory,actor,ident)
    root="/unified-assessment-detail?id="+ident
    work="/unified-assessment-work?id="+ident
    body="<p><a href='"+esc(root)+"'>Back to programme</a> · <a href='"+esc(work)+"'>Native work</a></p>"
    body+="<p>Administrative evidence-gap report for "+esc(ident)+" · "+esc(doc["programme_state"])+". No clinical pass, combined score or deployment permission is implied.</p>"
    if doc["partial_work_inventory"]:
        body+="<p>Work inventory is bounded or partial. No matching record does not prove that work is absent.</p>"
    body+="<h2>Canonical suite coverage</h2><table><tr><th>Vendor/version</th><th>Plan</th><th>Required suite</th><th>Evidence condition</th></tr>"
    for r in doc["canonical_suites"]:
        body+="<tr>"+"".join("<td>"+esc(v)+"</td>" for v in (r["vendor_id"]+" · "+r["target"]["version"],r["plan_id"],r["suite"],r["condition"].replace("_"," ")))+"</tr>"
    body+="</table><h2>Planned requirements and next actions</h2>"
    for r in doc["requirements"]:
        body+="<section><h3>"+esc(r["requirement_id"])+" · "+esc(r["vendor_id"])+" · "+esc(r["target"]["version"])+"</h3>"
        body+="<p>"+esc(r["kind"])+" · "+esc(r["coverage_state"])+" · "+esc(r["condition"].replace("_"," "))+"</p>"
        body+="<p>Work inventory: "+esc(r["work_inventory_state"])+" · "+esc(r["potential_ready_records"])+" potential ready records · "+esc(r["active_work_records"])+" active or prepared records.</p>"
        for task in r["tasks"]:
            code=task["code"]
            body+="<p>"+esc(task["priority"])+" administrative priority · "+esc(TASKS[code])+" · "+esc(task["role"])
            if actor["role"]=="admin" or actor["role"]==task["role"]:
                route=root
                if code=="verify_candidate_association":
                    route="/unified-assessment-discover?id="+ident+"&requirement="+r["requirement_id"]
                elif task["role"]=="operator":
                    route=work
                body+=" · <a href='"+esc(route)+"'>Open workflow</a>"
            body+="</p>"
        body+="</section>"
    for limitation in doc["limitations"]:
        body+="<p>"+esc(limitation)+"</p>"
    body+="<p>Projection digest "+esc(doc["digest"])+" (not a frozen approval or signature).</p>"
    if actor["role"]=="admin":
        body+="<p><a href='/unified-gap-handoff-preview?id="+esc(ident)+"'>Preview portable evidence-gap handoff</a></p>"
    return page("Programme evidence gaps and next actions",body)

def preview(directory,actor,ident):
    from .institution_web import page,esc,_hidden
    from . import institution_gap_handoff as handoff
    package=handoff.preview(directory,actor,ident)
    body="<p>Review the exact administrative handoff. It contains the existing programme coverage, associated source metadata and aggregate native work counts, but no unassociated job identities or raw clinical outputs.</p>"
    body+="<p>Programme "+esc(ident)+" · "+esc(package["report"]["programme_state"])+" · SHA-256 "+esc(package["digest"])+"</p>"
    body+="<p>Offline verification replays the administrative projection; it cannot independently verify native evidence or establish clinical validity. Share only with authorized recipients.</p>"
    body+=("<form method='post' action='/unified-gap-handoff-export'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("approval",package["digest"])+
           "<label><input type='checkbox' name='confirm' value='yes' required> Export this exact administrative snapshot to an authorized recipient.</label><button>Download approved JSON handoff</button></form>")
    return page("Approve evidence gap handoff",body)


def export(directory,actor,form):
    from . import institution_gap_handoff as handoff, institution_imaging_web as forms
    forms._exact(form,"id approval confirm")
    forms._confirm(form)
    return handoff.export(directory,actor,form["id"],form["approval"],confirmed=True)
