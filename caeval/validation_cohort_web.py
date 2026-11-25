"""Browser for prospective full-variant validation."""
import json
from . import validation_cohort as cohort,institution_access as access,buyer_plan as plans,buyer_manual as manual


def render(directory,actor):
    from .institution_web import esc,page,_hidden
    body="<p>Full generated variant cohort, frozen before local candidate exchange. Experimental; no clinical approval.</p>"
    def form(action,fields,label):
        return "<form method='post' action='/validation-"+action+"'>"+_hidden("csrf",actor["csrf"])+fields+"<button>"+esc(label)+"</button></form>"
    if actor["role"]=="admin":
        fields="".join("<label>"+esc(label)+"<input required name='"+name+"'></label>" for name,label in
            (("plan_id","Saved plan ID"),("pack_id","Approved pack ID"),("family","Runnable family ID"),("panel_id","Approved evaluator panel ID"),("reviewers","2–5 independent reviewer IDs, comma-separated"),("validity_reviewer","Different validity reviewer ID")))
        fields+="<label>Approved candidate connector ID (optional; required for controlled collection)<input name='connector_id'></label>"
        body+=form("preview",fields,"Preview full cohort")
    for row in cohort.inventory(directory,actor):
        ident=row["id"]
        body+="<section><h2>"+esc(ident)+"</h2>"
        if actor["role"]=="reviewer":
            if row["linked"]:body+="<p><a href='/validation-review?id="+esc(ident)+"'>Review blinded cohort</a></p>"
            else:body+="<p>Awaiting source.</p>"
        else:
            body+="<p>"+esc(row["family"])+" · "+str(row["n_variants"])+" variants</p>"
            if not row["linked"]:
                collection=row["reserved_collection"]
                if row["collection_controlled"] and collection is None:
                    body+=form("collect",_hidden("id",ident)+"<label><input type='checkbox' name='confirm' value='yes' required> Reserve one collection; no transmission approved</label>","Reserve candidate collection")
                elif row["collection_controlled"] and collection["state"]!="complete_unscored":
                    body+="<p>Reserved collection "+esc(collection["id"])+" · "+esc(collection["state"])+" · execute via authorized collection worker. No replacement allowed.</p>"
                elif row["panel_bound"] and not row["reserved_job"]:
                    if collection:
                        body+="<p>Prepare an assessment from exact collected submission "+esc(collection["receipt"]["submission_id"])+"</p>"
                    body+=form("reserve",_hidden("id",ident)+"<label>Prepared assessment ID<input required name='assessment_id'></label><label><input type='checkbox' name='confirm' value='yes' required> Reserve one execution before scoring; no retries or model calls approved</label>","Reserve execution")
                elif row["panel_bound"]:
                    job=row["reserved_job"]
                    body+="<p>Reserved job "+esc(job["id"])+" · "+esc(job["status"])+"</p>"
                    if job["status"]=="completed":
                        body+=form("attach",_hidden("id",ident)+_hidden("job_id",job["id"])+"<label><input type='checkbox' name='confirm' value='yes' required> Attach reserved completed source</label>","Attach source")
                else:
                    body+=form("attach",_hidden("id",ident)+"<label>Completed job ID<input required name='job_id'></label><label><input type='checkbox' name='confirm' value='yes' required> Confirm legacy source (not prospectively bound)</label>","Attach legacy source")
            else:
                for review in row["reviews"]:
                    body+="<p>"+esc(review["reviewer"])+" · "+("attested" if review["attested"] else "locked" if review["digest"] else "pending")+"</p>"
                    if review["digest"] and not review["attested"]:
                        body+=form("attest",_hidden("id",ident)+_hidden("reviewer",review["reviewer"])+_hidden("digest",review["digest"])+"<label><input type='checkbox' name='confirm' value='yes' required> I verified identity, clinical expertise and independence externally</label>","Attest review")
                if row["reviews"] and all(r["attested"] for r in row["reviews"]):
                    body+=form("analyze",_hidden("id",ident),"Compute cohort calibration")
        body+="</section>"
    return page("Prospective validation cohorts",body)


def review(directory,actor,ident):
    from .institution_web import esc,page,_hidden
    req=cohort.packet(directory,actor,ident)
    fields=cohort.review_calibration.VALIDITY_FIELDS if req["kind"]=="validity" else ("verdict",)
    vocab=("yes","no","cannot_judge") if req["kind"]=="validity" else ("safe","unsafe","cannot_judge")
    body="<p>Independent blinded review. Do not consult automated ratings or other reviewers.</p><form method='post' action='/validation-submit'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("digest",plans._digest(req))
    for item in req["items"]:
        body+="<section>"+ "".join("<p><strong>"+esc(k)+"</strong></p><pre style='white-space:pre-wrap'>"+esc(v)+"</pre>" for k,v in item.items() if k!="slot")
        for field in fields:
            body+="<label>"+esc(field)+"<select required name='"+esc(item["slot"]+"."+field)+"'><option value=''>Choose</option>"+"".join("<option value='"+v+"'>"+v+"</option>" for v in vocab)+"</select></label>"
        body+="</section>"
    # The institutional form parser has a 2500-field limit; use a bounded
    # JSON response template for larger validity rosters.
    if len(req["items"])*len(fields)>2496:
        answers=[{"slot":item["slot"],**{k:"" for k in fields}} for item in req["items"]]
        body="<p>Complete each row independently using "+esc(", ".join(vocab))+".</p><pre style='white-space:pre-wrap'>"+esc(json.dumps(req["items"],ensure_ascii=False,indent=2))+"</pre><form method='post' action='/validation-submit'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("digest",plans._digest(req))+"<textarea required name='answers'>"+esc(json.dumps(answers,indent=2))+"</textarea>"
    body+="<label><input type='checkbox' name='confirm' value='yes' required> I performed this review independently</label><button>Lock review</button></form>"
    return page("Blinded cohort review",body)


def dispatch(directory,actor,action,form):
    from .institution_web import esc,page,_hidden
    if action in ("preview","freeze"):
        expected={"plan_id","pack_id","family","reviewers","validity_reviewer","panel_id","connector_id"}
        if action=="freeze":expected|={"digest","confirm"}
        if set(form)!=expected:raise access.AccessDenied("invalid cohort fields")
        args=(directory,actor,form["plan_id"],form["pack_id"],form["family"],[x.strip() for x in form["reviewers"].split(",")],form["validity_reviewer"])
        if action=="freeze":
            cohort.freeze(*args,form["digest"],panel_id=form["panel_id"],connector_id=form["connector_id"] or None,confirmed=form["confirm"]=="yes")
            return None
        p=cohort.preview(*args,panel_id=form["panel_id"],connector_id=form["connector_id"] or None)
        body="<p>"+str(len(p["cohort"]["variant_ids"]))+" variants. No candidate answers or automated ratings used in selection.</p><p>Digest "+esc(p["digest"])+"</p><form method='post' action='/validation-freeze'>"+_hidden("csrf",actor["csrf"])+"".join(_hidden(k,v) for k,v in form.items())+_hidden("digest",p["digest"])+"<label><input type='checkbox' name='confirm' value='yes' required> I confirm prospective scope, independent roster and no known prior candidate exposure</label><button>Freeze cohort</button></form>"
        return page("Review prospective cohort",body)
    if action=="collect" and set(form)=={"id","confirm"}:
        cohort.reserve_collection(directory,actor,form["id"],confirmed=form["confirm"]=="yes")
    elif action=="reserve" and set(form)=={"id","assessment_id","confirm"}:
        cohort.reserve(directory,actor,form["id"],form["assessment_id"],confirmed=form["confirm"]=="yes")
    elif action=="attach" and set(form)=={"id","job_id","confirm"}:
        cohort.attach(directory,actor,form["id"],form["job_id"],confirmed=form["confirm"]=="yes")
    elif action=="attest" and set(form)=={"id","reviewer","digest","confirm"}:
        cohort.attest(directory,actor,form["id"],form["reviewer"],form["digest"],confirmed=form["confirm"]=="yes")
    elif action=="analyze" and set(form)=={"id"}:
        return page("Cohort calibration","<p>Experimental full-pack evidence, not population validation.</p><pre style='white-space:pre-wrap'>"+esc(json.dumps(cohort.analyze(directory,actor,form["id"]),indent=2))+"</pre>")
    elif action=="submit":
        ident=form.pop("id","")
        digest=form.pop("digest","")
        confirmed=form.pop("confirm","")=="yes"
        req=cohort.packet(directory,actor,ident)
        fields=cohort.review_calibration.VALIDITY_FIELDS if req["kind"]=="validity" else ("verdict",)
        if set(form)=={"answers"}:
            answers=manual._parse(form["answers"].encode())
        else:
            expected={r["slot"]+"."+k for r in req["items"] for k in fields}
            if set(form)!=expected:raise access.AccessDenied("exact review inventory required")
            answers=[{"slot":r["slot"],**{k:form[r["slot"]+"."+k] for k in fields}} for r in req["items"]]
        cohort.submit(directory,actor,ident,digest,answers,confirmed=confirmed)
    else:raise access.AccessDenied("invalid validation action")
    return None
