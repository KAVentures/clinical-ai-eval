"""Browser workflows for source discovery, work tracking and version successors."""
import json
from . import (unified_discovery as discovery, unified_work as work,
               unified_successor as successor, institution_imaging_web as forms,
               institution_access as access)


def discover(directory,actor,ident,rid):
    from .institution_web import page,esc,_hidden
    doc=discovery.discover(directory,actor,ident,rid)
    body="<p><a href='/unified-assessment-detail?id="+esc(ident)+"'>Back to programme</a></p><p>Eligible sources for "+esc(rid)+". No source is recommended or automatically selected. Review its scientific relevance before association.</p>"
    if doc["scan_truncated"]:
        body+="<p>Discovery was bounded; additional sources may exist. Use the exact source ID if needed.</p>"
    if not doc["matches"]:
        body+="<p>No verified eligible source was found in this scan.</p>"
    for p in doc["matches"]:
        source=p["binding"]["source"]
        body+="<section>"
        for k,v in source.items():
            body+="<p>"+esc(k.replace("_"," "))+": "+esc(v)+"</p>"
        body+=("<form method='post' action='/unified-assessment-link-preview'>"+_hidden("csrf",actor["csrf"])+
               _hidden("id",ident)+_hidden("requirement",rid)+_hidden("source",source["source_id"])+
               "<button>Preview this source association</button></form></section>")
    return page("Find verified evidence",body)


def batch_preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    forms._exact(form,"id selections")
    selections=forms._parse(form["selections"])
    doc=discovery.batch_preview(directory,actor,form["id"],selections)
    body="<p>Exact batch digest: "+esc(doc["digest"])+"</p><p>All sources are verified again before the atomic lock. No partial lock is permitted.</p>"
    for entry in doc["entries"]:
        b=entry["binding"]
        body+="<section><h3>"+esc(b["requirement_id"])+"</h3>"
        for k,v in b["source"].items():
            body+="<p>"+esc(k.replace("_"," "))+": "+esc(v)+"</p>"
        body+="</section>"
    body+=("<form method='post' action='/unified-assessment-batch-lock'>"+_hidden("csrf",actor["csrf"])+
           _hidden("id",form["id"])+_hidden("selections",json.dumps(selections))+_hidden("approval",doc["digest"])+
           "<label><input type='checkbox' name='confirm' value='yes' required> Lock these exact source associations and confirm their relevance. No clinical pass is inferred.</label><button>Lock entire batch</button></form>")
    return page("Approve evidence association batch",body)


def work_view(directory,actor,ident):
    from .institution_web import page,esc
    doc=work.work(directory,actor,ident)
    body="<p><a href='/unified-assessment-detail?id="+esc(ident)+"'>Back to programme</a></p><p>Read-only coordination. Native assessment workflows retain execution and approval controls. This page does not launch or cancel jobs.</p>"
    routes={"text":"/setup","conversation":"/conversation","research":"/research-programs",
            "imaging":"/diagnostic-imaging","fhir":"/fhir","hl7":"/hl7","image_text":"/image-text"}
    for r in doc["requirements"]:
        body+="<section><h3>"+esc(r["requirement_id"])+"</h3><p>"+esc(r["vendor_id"])+" · "+esc(r["target"]["version"])+" · "+esc(r["coverage_state"])+"</p>"
        route=routes[r["kind"]]
        if r["kind"] in ("fhir","hl7","image_text"):
            route+="?plan_id="+r["plan_id"]
        if doc["programme_state"]=="current":
            body+="<p><a href='"+esc(route)+"'>Open native workflow</a></p>"
        for job in r["native_work_records"]:
            body+="<p>"+esc(job["id"])+" · "+esc(job["state"])+" · "+esc(job["mode"])+"</p>"
        if not r["native_work_records"]:
            body+="<p>No exact-plan text or conversation work record shown for this requirement.</p>"
        body+="</section>"
    return page("Programme evaluation work",body)


def lineage(directory,actor,ident):
    from .institution_web import page,esc,_hidden
    from . import unified_assessment as u
    status=u.status(directory,actor,ident)
    body="<p><a href='/unified-assessment-detail?id="+esc(ident)+"'>Back to programme</a></p><p>Version-only programme lineage. Previous evidence is not transferred, and coverage change is not performance change.</p>"
    for row in successor.inventory(directory,actor):
        if ident not in (row["baseline_id"],row["successor_id"]):
            continue
        body+="<section><p>"+esc(row["baseline_id"])+" → "+esc(row["successor_id"])+"</p><p><a href='/unified-assessment-compare?id="+esc(row["successor_id"])+"'>Compare administrative coverage</a></p></section>"
    if actor["role"]=="admin":
        options=successor.choices(directory,actor,ident)
        if options and all(o["options"] for o in options):
            body+=("<section><h2>New successor programme</h2><p>Choose current plans or already-approved native version reassessments. At least one version must change.</p><form method='post' action='/unified-assessment-successor-preview'>"+_hidden("csrf",actor["csrf"])+_hidden("baseline",ident)+
                   "<label>Successor programme ID<input name='successor' required maxlength='80'></label>")
            for item in options:
                p=item["plan"]
                body+="<label>"+esc(p["vendor_id"])+" · "+esc(", ".join(p["profiles"]))+"<select name='plan_"+esc(p["plan_id"])+"' required>"
                for o in item["options"]:
                    body+="<option value='"+esc(o["successor_plan_id"])+"'>"+esc(o["successor_version"])+" · "+esc(o["successor_plan_id"])+"</option>"
                body+="</select></label>"
            body+="<button>Preview successor</button></form></section>"
        else:
            body+="<p>No complete eligible mapping is available. Create native version reassessments first.</p>"
    return page("Programme version lineage",body)


def successor_preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    if not {"baseline","successor"}<=set(form) or any(not k.startswith("plan_") for k in set(form)-{"baseline","successor"}):
        raise access.AccessDenied("invalid successor mapping")
    mapping={k[5:]:v for k,v in form.items() if k.startswith("plan_")}
    p=successor.preview(directory,actor,form["baseline"],form["successor"],mapping)
    row=p["lineage"]
    body="<p>Baseline "+esc(row["baseline_id"])+" → successor "+esc(row["successor_id"])+"</p><p>"+esc(row["digest"])+"</p>"
    for plan in row["plans"]:
        body+="<p>"+esc(plan["baseline_version"])+" → "+esc(plan["successor_version"])+" · "+esc(plan["successor_plan_id"])+"</p>"
    body+=("<form method='post' action='/unified-assessment-successor-freeze'>"+_hidden("csrf",actor["csrf"])+
           _hidden("baseline",row["baseline_id"])+_hidden("successor",row["successor_id"])+_hidden("mapping",json.dumps(mapping))+_hidden("approval",row["digest"])+
           "<label><input type='checkbox' name='confirm' value='yes' required> Freeze this exact successor programme without transferring any source associations or authorizing performance comparisons.</label><button>Freeze successor</button></form>")
    return page("Approve programme successor",body)


def compare(directory,actor,ident):
    from .institution_web import page,esc
    doc=successor.compare(directory,actor,ident)
    body="<p>Administrative coverage only. These states do not establish equivalence, noninferiority, improvement or safety.</p><table><tr><th>Requirement</th><th>Baseline version/state</th><th>Successor version/state</th></tr>"
    for r in doc["requirements"]:
        body+="<tr><td>"+esc(r["requirement_id"])+"</td><td>"+esc(r["baseline_version"])+" · "+esc(r["baseline_state"])+"</td><td>"+esc(r["successor_version"])+" · "+esc(r["successor_state"])+"</td></tr>"
    return page("Version coverage comparison",body+"</table>")


def dispatch(directory,actor,path,form):
    forms._confirm(form)
    if path=="/unified-assessment-batch-lock":
        forms._exact(form,"id selections approval confirm")
        discovery.batch_lock(directory,actor,form["id"],forms._parse(form["selections"]),form["approval"],confirmed=True)
        return form["id"]
    if path=="/unified-assessment-successor-freeze":
        forms._exact(form,"baseline successor mapping approval confirm")
        return successor.freeze(directory,actor,form["baseline"],form["successor"],forms._parse(form["mapping"]),form["approval"],confirmed=True)
    raise access.AccessDenied("unknown orchestration action")
