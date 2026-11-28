"""Role-scoped no-install synthetic RAG rehearsal interface."""
from . import (institution_rag as service, institution_imaging_web as forms,
               institution_access as access, unified_assessment as unified)


def home(directory,actor,plan_id=None):
    from .institution_web import page,esc,_hidden
    body="<p><a href='/'>Dashboard</a></p><p>Bundled synthetic retrieval-context rehearsal only. No real patient data, clinical guidance or vendor retriever measurement. Native flags are heuristic; no clinical or citation-support verdict.</p>"
    if actor["role"]=="admin":
        plans=[p for p in unified.available_plans(directory,actor)
               if "retrieval_failure" in p["runnable_suites"] and (plan_id is None or p["plan_id"]==plan_id)]
        if plans:
            body+=("<section><h2>Freeze a demonstration rehearsal</h2><form method='post' action='/rag-trace-preview'>"+_hidden("csrf",actor["csrf"])+"<label>Saved buyer plan<select name='plan_id' required>")
            for p in plans:
                body+="<option value='"+esc(p["plan_id"])+"'>"+esc(p["vendor_id"])+" · "+esc(p["target"]["version"])+" · "+esc(p["plan_id"])+"</option>"
            body+="</select></label><button>Preview fixture and scope</button></form></section>"
        else:
            body+="<p>No current saved plan with runnable retrieval_failure was found.</p>"
    for r in service.overview(directory,actor):
        if plan_id is not None and r["plan_id"]!=plan_id:
            continue
        body+="<section><a href='/rag-trace-study?id="+esc(r["id"])+"'>"+esc(r["id"])+"</a><p>"+esc(r["state"])+" · "+esc(r.get("target_version","unavailable"))+"</p></section>"
    return page("Synthetic RAG context rehearsal",body)


def preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    forms._exact(form,"plan_id")
    p=service.preview(directory,actor,form["plan_id"])
    body="<p>Candidate "+esc(p["target"]["name"])+" · "+esc(p["target"]["version"])+" · "+esc(len(p["slots"]))+" synthetic prompts.</p><p>Approval digest: "+esc(p["digest"])+"</p><ul>"
    for limit in p["limitations"]:
        body+="<li>"+esc(limit)+"</li>"
    body+="</ul>"
    body+=("<form method='post' action='/rag-trace-freeze'>"+_hidden("csrf",actor["csrf"])+
           _hidden("plan_id",p["plan_id"])+_hidden("approval",p["digest"])+
           "<label><input type='checkbox' name='confirm' value='yes' required> Freeze this exact synthetic fixture before collecting responses. No real patient information.</label><button>Freeze rehearsal</button></form>")
    return page("Approve synthetic retrieval-context rehearsal",body)


def detail(directory,actor,ident):
    from .institution_web import page,esc,_hidden
    row=service.detail(directory,actor,ident)
    body="<p><a href='/rag-trace'>RAG rehearsals</a></p><p>"+esc(row["vendor_id"])+" · "+esc(row["target_version"])+" · "+esc(row["state"])+" · "+esc(row["n_slots"])+" synthetic prompts.</p>"
    body+="<p>Demonstration only. Do not use for live clinical decisions or claim independent model verification.</p>"
    if row["state"]=="collecting":
        body+="<p><a href='/rag-trace-download?id="+esc(ident)+"&amp;kind=exchange'>Download exact prompts</a> · <a href='/rag-trace-download?id="+esc(ident)+"&amp;kind=template'>Download response template</a></p>"
        body+=("<form method='post' action='/rag-trace-submit'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+
               "<label>Complete response JSON<textarea name='responses' required rows='10' maxlength='1048576'></textarea></label>"+
               "<label><input type='checkbox' name='confirm' value='yes' required> Lock the complete responses; confirm synthetic-only inputs and exact candidate version. No clinical identifiers.</label><button>Lock and replay native trace</button></form>")
    else:
        body+="<p>Evidence digest: "+esc(row["evidence_digest"])+"</p>"
        if actor["role"]=="admin":
            body+="<h2>Native descriptive flags</h2><p>Lexical flags do not establish safe abstention or citation support. There is no positive control in this fixture.</p>"
            summary=row["summary"]
            body+="<p>Traces: "+esc(summary["n_traces"])+"</p><table>"
            for group in ("retrieval","generation"):
                for key,value in summary[group].items():
                    body+="<tr><th>"+esc(group+" / "+key.replace("_"," "))+"</th><td>"+esc(value)+"</td></tr>"
            body+="</table>"
            body+=("<form method='post' action='/rag-trace-evidence'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("digest",row["evidence_digest"])+
                   "<label><input type='checkbox' name='confirm' value='yes' required> Export restricted full synthetic prompt, response and native trace evidence.</label><button>Download private replay package</button></form>")
    return page("Synthetic RAG rehearsal",body)


def dispatch(directory,actor,path,form):
    forms._confirm(form)
    if path=="/rag-trace-freeze":
        forms._exact(form,"plan_id approval confirm")
        return service.freeze(directory,actor,form["plan_id"],form["approval"],confirmed=True)
    if path=="/rag-trace-submit":
        forms._exact(form,"id responses confirm")
        service.submit(directory,actor,form["id"],forms._parse(form["responses"]),confirmed=True)
        return form["id"]
    if path=="/rag-trace-evidence":
        forms._exact(form,"id digest confirm")
        return service.export(directory,actor,form["id"],form["digest"],confirmed=True)
    raise access.AccessDenied("unknown RAG rehearsal action")
