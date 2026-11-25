"""No-install programme planning, coverage and restricted evidence export."""
import json
from . import (unified_assessment as service, institution_imaging_web as forms,
               institution_access as access)


def home(directory,actor):
    from .institution_web import page,esc,_hidden
    body="<p><a href='/'>Dashboard</a></p><p>Buyer-owned cross-modality evidence inventory. No combined score, pass or deployment decision.</p>"
    if actor["role"]=="admin":
        plans=service.available_plans(directory,actor)
        if plans:
            body+=("<section><h2>Guided programme setup</h2><p>Select current saved plans. Supported text, conversation and synthetic RAG requirements are generated automatically; select any additional independent evidence lanes. Unsupported suites remain explicit gaps.</p><form method='post' action='/unified-assessment-guided-preview'>"+_hidden("csrf",actor["csrf"])+
                   "<label>Programme ID<input name='programme_id' required maxlength='80'></label>")
            for p in plans:
                pid=p["plan_id"]
                body+="<section><label><input type='checkbox' name='plan_"+esc(pid)+"' value='yes'> "+esc(p["vendor_id"])+" · "+esc(p["target"]["version"])+" · "+esc(", ".join(p["profiles"]))+"</label>"
                for kind in ("research","imaging","fhir","hl7","image_text"):
                    body+="<label><input type='checkbox' name='kind_"+esc(pid)+"_"+kind+"' value='yes'> "+esc(kind.replace("_"," "))+"</label>"
                body+="</section>"
            body+="<button>Preview selected plans</button></form></section>"
        body+=("<section><h2>Advanced JSON programme setup</h2><p>Choose saved buyer plan IDs and explicit requirements. Every required canonical suite not included remains visible as a gap.</p>"
               "<form method='post' action='/unified-assessment-preview'>"+_hidden("csrf",actor["csrf"])+
               "<label>Programme specification JSON<textarea name='spec' required rows='12' maxlength='1048576'></textarea></label><button>Preview programme</button></form></section>")
    for doc in service.overview(directory,actor):
        p=doc["programme"]
        ident=esc(p["programme_id"])
        n=sum(r["state"]=="source_available" for r in doc["requirements"])
        body+="<section><a href='/unified-assessment-detail?id="+ident+"'>"+ident+"</a><p>"+esc(doc["state"])+" · "+esc(n)+" / "+esc(len(doc["requirements"]))+" planned sources available · "+esc(len(p["unplanned_canonical_suites"]))+" unplanned canonical suites</p></section>"
    return page("Unified clinical AI assessment programmes",body)


def preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    forms._exact(form,"spec")
    doc=service.preview(directory,actor,forms._parse(form["spec"]))
    body="<p>Programme: "+esc(doc["programme_id"])+" · "+esc(doc["digest"])+"</p>"
    body+=_plan_table(doc)
    body+=("<form method='post' action='/unified-assessment-freeze'>"+_hidden("csrf",actor["csrf"])+
           _hidden("spec",json.dumps(service._spec(doc)))+_hidden("approval",doc["digest"])+
           "<label><input type='checkbox' name='confirm' value='yes' required> Freeze these exact product versions, buyer plans and evidence requirements. This does not authorize execution or clinical use.</label><button>Freeze programme</button></form>")
    return page("Approve unified evidence requirements",body)


def _plan_table(doc):
    from .institution_web import esc
    body="<h2>Frozen buyer plans</h2><table><tr><th>Vendor</th><th>Version</th><th>Profiles</th><th>Plan</th></tr>"
    for p in doc["plans"]:
        body+="<tr><td>"+esc(p["vendor_id"])+"</td><td>"+esc(p["target"]["version"])+"</td><td>"+esc(", ".join(p["profiles"]))+"</td><td>"+esc(p["plan_id"])+"</td></tr>"
    body+="</table><h2>Required evidence</h2><table><tr><th>ID</th><th>Kind</th><th>Plan</th><th>Suite</th></tr>"
    for r in doc["requirements"]:
        body+="<tr><td>"+esc(r["id"])+"</td><td>"+esc(r["kind"])+"</td><td>"+esc(r["plan_id"])+"</td><td>"+esc(r["family"] or "Not canonical")+"</td></tr>"
    body+="</table><h2>Unplanned canonical requirements</h2>"
    for gap in doc["unplanned_canonical_suites"]:
        body+="<p>"+esc(gap["plan_id"])+" · "+esc(gap["suite"])+" · "+esc(gap["state"])+"</p>"
    if not doc["unplanned_canonical_suites"]:
        body+="<p>No omitted suites in this administrative inventory. This is not a canonical pass.</p>"
    return body


def detail(directory,actor,ident):
    from .institution_web import page,esc,_hidden
    doc=service.status(directory,actor,ident)
    p=doc["programme"]
    body="<p><a href='/unified-assessment'>Programmes</a></p><p>"+esc(doc["state"])+" · Frozen digest "+esc(p["digest"])+"</p>"
    if actor["role"] in ("admin","operator"):
        body+="<p><a href='/unified-assessment-work?id="+esc(ident)+"'>Native evaluation work and handoffs</a></p>"
    body+="<p><a href='/unified-assessment-lineage?id="+esc(ident)+"'>Version reassessment history</a></p>"
    body+=_plan_table(p)
    body+="<h2>Evidence coverage</h2><p>Source available means its restricted metadata was verified and still matches the locked association. It does not mean the candidate passed.</p>"
    for row in doc["requirements"]:
        r=row["requirement"]
        body+="<section><h3>"+esc(r["id"])+" · "+esc(row["vendor_id"])+"</h3><p>"+esc(r["kind"])+" · "+esc(row["target"]["version"])+" · "+esc(row["state"])+"</p>"
        source=row["locked_source"]
        if source:
            for key,value in source.items():
                body+="<p>"+esc(key.replace("_"," "))+": "+esc(value)+"</p>"
        elif actor["role"]=="admin" and doc["state"]=="current":
            body+="<p><a href='/unified-assessment-discover?id="+esc(ident)+"&amp;requirement="+esc(r["id"])+"'>Find verified eligible evidence</a></p>"
            body+=("<form method='post' action='/unified-assessment-link-preview'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("requirement",r["id"])+
                   "<label>Existing source record ID<input name='source' required maxlength='128'></label><button>Verify and preview source</button></form>")
        body+="</section>"
    body+="<p>"+("All planned sources available." if doc["all_planned_sources_available"] else "Planned evidence incomplete or unavailable.")+" No clinical or procurement conclusion is implied.</p>"
    if actor["role"]=="admin":
        if doc["state"]=="current":
            body+=("<section><h2>Batch evidence association</h2><p>Paste explicitly selected requirement/source pairs. Every pair must be eligible; the whole batch is rejected if any source changes.</p><form method='post' action='/unified-assessment-batch-preview'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+
                   "<label>JSON array of objects with requirement_id and source_id<textarea name='selections' required rows='6' maxlength='65536'></textarea></label><button>Verify selected batch</button></form></section>")
        export=service.export_preview(directory,actor,ident)
        body+=("<form method='post' action='/unified-assessment-export'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("approval",export["digest"])+
               "<label><input type='checkbox' name='confirm' value='yes' required> Export this exact restricted administrative coverage snapshot. No raw clinical evidence included.</label><button>Download coverage snapshot</button></form>")
    return page("Assessment programme: "+ident,body)


def link_preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    forms._exact(form,"id requirement source")
    p=service.link_preview(directory,actor,form["id"],form["requirement"],form["source"])
    body="<p>Requirement "+esc(form["requirement"])+"</p>"
    for key,value in p["binding"]["source"].items():
        body+="<p>"+esc(key.replace("_"," "))+": "+esc(value)+"</p>"
    body+=("<form method='post' action='/unified-assessment-lock'>"+_hidden("csrf",actor["csrf"])+
           _hidden("id",form["id"])+_hidden("requirement",form["requirement"])+_hidden("source",form["source"])+_hidden("approval",p["approval_digest"])+
           "<label><input type='checkbox' name='confirm' value='yes' required> Lock this exact verified source association. I confirm its relevance to the buyer requirement. No substitutions or retrospective prospective-study claim.</label><button>Lock evidence source</button></form>")
    return page("Approve exact evidence association",body)


def dispatch(directory,actor,path,form):
    forms._confirm(form)
    if path=="/unified-assessment-freeze":
        forms._exact(form,"spec approval confirm")
        return service.freeze(directory,actor,forms._parse(form["spec"]),form["approval"],confirmed=True)
    if path=="/unified-assessment-lock":
        forms._exact(form,"id requirement source approval confirm")
        service.lock(directory,actor,form["id"],form["requirement"],form["source"],form["approval"],confirmed=True)
        return form["id"]
    if path=="/unified-assessment-export":
        forms._exact(form,"id approval confirm")
        return service.export(directory,actor,form["id"],form["approval"],confirmed=True)
    raise access.AccessDenied("unknown programme action")


def guided_preview(directory,actor,form):
    if "programme_id" not in form:
        raise access.AccessDenied("programme ID required")
    choices={}
    extra=[]
    for key,value in form.items():
        if key=="programme_id":
            continue
        if value!="yes":
            raise access.AccessDenied("invalid guided selection")
        if key.startswith("plan_"):
            choices[key[5:]]=[]
        elif key.startswith("kind_") and len(key)>70 and key[69]=="_":
            extra.append((key[5:69],key[70:]))
        else:
            raise access.AccessDenied("unexpected guided field")
    for pid,kind in extra:
        if pid not in choices:
            raise access.AccessDenied("select the plan for every additional evidence lane")
        choices[pid].append(kind)
    spec=service.guided_spec(directory,actor,form["programme_id"],choices)
    return preview(directory,actor,{"spec":json.dumps(spec)})
