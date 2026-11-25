"""Independent patient-world validity review browser."""
import json
from . import institution_patient_validity as validity, institution_access as access, buyer_plan
from .institution_patient_review_web import _form,_confirm

LABELS={"plausible_world":"Is this clinical world plausible?",
        "appropriate_reference":"Is the declared reference disposition appropriate, including any trajectory change?",
        "realistic_disclosure":"Are the available facts, timing and disclosure clinically realistic?",
        "valid_stress_condition":"Is the stress condition (including baseline where applicable) a valid clinical probe?"}


def render(directory,actor):
    from .institution_web import page,esc,_hidden
    body="<p><a href='/'>Workspace</a></p><p>Independent review of benchmark worlds, not candidate performance. Freeze the roster before product testing where possible. Reviewers receive no product answers or judge scores.</p>"
    if actor["role"]=="admin":
        fields="<label>Approved patient pack ID<input name='pack_id' required maxlength='128'></label><label>2–5 independent validity reviewer account IDs<input name='reviewers' required maxlength='330'></label>"
        body+=_form("/world-review-create",actor,fields+_confirm("I verified credentials, expertise, conflicts and independence from product evaluation."),"Freeze validity review")
    for row in validity.overview(directory,actor):
        ident=row["id"]
        body+="<section><h2>"+esc(ident)+"</h2>"
        if row.get("unavailable"):
            body+="<p>Source or review data unavailable.</p></section>"
            continue
        body+="<p>Validity items: "+str(row["n_items"])+"</p>"
        if actor["role"]=="reviewer":
            body+="<p>"+("Locked" if row["locked"] else "<a href='/world-review-packet?id="+esc(ident)+"'>Open independent world review</a>")+"</p>"
        else:
            body+="<p>Pack: "+esc(row["pack_id"])+"</p>"
            for r in row["roster"]:
                body+="<p>"+esc(r["reviewer"])+" · "+esc(r["state"])+"</p>"
                if r["state"]=="pending":
                    fields=_hidden("id",ident)+_hidden("reviewer",r["reviewer"])+_hidden("digest",r["digest"])
                    body+=_form("/world-review-attest",actor,fields+_confirm("I verified this physician's identity, expertise and independence for this exact submission."),"Attest validity review")
            if row["ready"]:
                fields=_hidden("id",ident)+"<label>Optional completed patient job ID for exact evidence linkage<input name='job' maxlength='64'></label>"
                body+=_form("/world-review-analyze",actor,fields,"Compute validity report")
        body+="</section>"
    return page("Independent patient-world validity review",body)


def packet(directory,actor,ident):
    from .institution_web import page,esc,_hidden
    doc=validity.template(directory,actor,ident)
    fields=_hidden("id",ident)+_hidden("packet_digest",buyer_plan._digest(doc))
    fields+="<p>Evaluate the clinical reference independently. The declared facts and dispositions are hypotheses to review, not established truth. Mark cannot judge if clinical context is insufficient.</p>"
    for i,row in enumerate(doc["items"],1):
        slot=row["slot"]
        fields+="<section><h2>World review item "+str(i)+"</h2><p>Condition: "+esc(row["stress"])+"</p><p>Opening: "+esc(row["opening_message"])+"</p>"
        for k,label in (("effective_world","Effective clinical world"),("source_world","Original world"),
                        ("declared_worlds","Declared alternatives"),("trajectory","Trajectory"),("applied","Applied transformation")):
            fields+="<h3>"+label+"</h3><pre style='white-space:pre-wrap;overflow-wrap:anywhere'>"+esc(json.dumps(row[k],ensure_ascii=False,indent=2))+"</pre>"
        fields+="<p>Population: "+esc(row["population"])+" · Specialty: "+esc(row["specialty"])+" · Language: "+esc(row["language"])+"</p>"
        for q in validity.QUESTIONS:
            fields+="<label>"+esc(LABELS[q])+"<select name='"+q+"_"+slot+"' required><option value=''>Select</option>"
            fields+="".join("<option value='"+v+"'>"+esc(v.replace("_"," "))+"</option>" for v in validity.CHOICES)+"</select></label>"
        fields+="<label>Notes<textarea name='notes_"+slot+"' maxlength='"+str(validity.MAX_NOTE)+"'></textarea></label></section>"
    return page("Patient-world validity packet",_form("/world-review-lock",actor,fields+_confirm("I completed this review independently and understand the answers will be locked."),"Lock validity judgments"))


def dispatch(directory,actor,path,form):
    from .institution_web import page,esc
    form=dict(form)
    confirmed=form.pop("confirmed","")=="yes"
    if path=="/world-review-create" and set(form)=={"pack_id","reviewers"}:
        validity.create(directory,actor,form["pack_id"],[x.strip() for x in form["reviewers"].split(",")],confirmed=confirmed)
    elif path=="/world-review-lock":
        ident=form.pop("id","")
        digest=form.pop("packet_digest","")
        doc=validity.template(directory,actor,ident)
        keys=(*validity.QUESTIONS,"notes")
        slots=[r["slot"] for r in doc["items"]]
        if set(form)!={k+"_"+s for k in keys for s in slots}:
            raise access.AccessDenied("invalid validity review form")
        validity.submit(directory,actor,ident,digest,[{"slot":s,**{k:form[k+"_"+s] for k in keys}} for s in slots],confirmed=confirmed)
    elif path=="/world-review-attest" and set(form)=={"id","reviewer","digest"}:
        validity.attest(directory,actor,form["id"],form["reviewer"],form["digest"],confirmed=confirmed)
    elif path=="/world-review-analyze" and set(form)=={"id","job"} and not confirmed:
        result=validity.analyze(directory,actor,form["id"],form["job"] or None)
        body="<p><a href='/world-review'>Validity review</a></p><p>"+esc(result["interpretation"])+"</p>"
        body+="<p>Pack content hash: "+esc(result["source"]["descriptor"]["content_hash"])+"</p>"
        body+="<p>"+("All items unanimously supported" if result["all_items_unanimously_supported"] else "Negative or indeterminate findings require investigation")+"</p>"
        if result["job_link"]:
            link=result["job_link"]
            body+="<p>Exact linked job: "+esc(link["job"])+" · Manifest "+esc(link["manifest"])+"</p>"
            body+="<p>Separate transcript roster: "+esc("verified distinct accounts" if link["separate_transcript_roster"] else "no transcript roster recorded")+"</p>"
        for q,counts in result["summary"].items():
            body+="<section><h2>"+esc(LABELS[q])+"</h2>"
            for k,v in counts.items():
                body+="<p>"+esc(k.replace("_"," "))+": "+str(v)+"</p>"
            body+="</section>"
        for item in result["flagged_items"]:
            body+="<section><h2>Flagged reference item</h2><p>"+esc(item["case_id"])+" · "+esc(item["world_id"])+" · "+esc(item["stress"])+"</p><pre>"+esc(json.dumps(item["findings"],indent=2))+"</pre></section>"
        return page("Patient-world validity report",body+"<p>Report digest: "+esc(result["digest"])+"</p>")
    else:
        raise access.AccessDenied("invalid validity review action")
    return None
