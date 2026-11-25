"""Authenticated formal review browser forms."""
from . import institution_formal as coord, institution_access as access, formal_review as formal, buyer_plan


def _form(action,actor,fields,button):
    from .institution_web import esc,_hidden
    return "<form method='post' action='"+action+"'>"+_hidden("csrf",actor["csrf"])+fields+"<button>"+esc(button)+"</button></form>"


def _confirm(label):
    from .institution_web import esc
    return "<label><input type='checkbox' name='confirmed' value='yes' required> "+esc(label)+"</label>"


def _select(name,values,label):
    from .institution_web import esc
    return "<label>"+esc(label)+"<select required name='"+esc(name)+"'><option value=''>Choose</option>"+"".join("<option>"+esc(v)+"</option>" for v in values)+"</select></label>"


def render(directory,actor):
    from .institution_web import esc,page,_hidden
    body="<p><a href='/'>Workspace</a></p><p>Independent formal review. Account authentication does not verify professional credentials or clinical independence.</p>"
    if actor["role"]=="admin":
        body+="<section><h2>Freeze reviewer roster</h2>"
        fields="<label>Completed job ID<input name='job' required maxlength='64'></label><label>Family ID<input name='family' required maxlength='64'></label><label>2–5 safety reviewer account IDs, comma separated<input name='reviewers' required maxlength='330'></label><label>Separate validity reviewer account ID<input name='validity_reviewer' required maxlength='64'></label>"
        body+=_form("/formal-prepare",actor,fields+_confirm("I verified professional credentials, expertise, conflicts and independence before packet issuance."),"Freeze and issue packets")+"</section>"
    for row in coord.overview(directory,actor):
        ident=row["id"]
        body+="<section><h2>"+esc(ident)+"</h2>"
        if row.get("unavailable"):
            body+="<p>Source evidence unavailable or invalid.</p></section>"
            continue
        if actor["role"]=="reviewer":
            body+="<p>"+esc(row["kind"])+" · "+("Locked" if row["submitted"] else "Awaiting independent review")+"</p>"
            if not row["submitted"]:
                body+="<a href='/formal-review?id="+esc(ident)+"'>Open assigned packet</a>"
        else:
            body+="<p>"+esc(row["job"])+" · "+esc(row["family"])+" · "+("Synthetic" if row["synthetic"] else "Externally attested review required")+"</p>"
            for r in row["roster"]:
                body+="<p>"+esc(r["reviewer"])+" · "+esc(r["kind"])+" · "+esc(r["state"])+"</p>"
                if r["state"]=="pending_attestation":
                    fields=_hidden("id",ident)+_hidden("reviewer",r["reviewer"])+_hidden("digest",r["digest"])
                    body+=_form("/formal-attest",actor,fields+_confirm("I independently verified this reviewer's identity, credentials and independence for this exact submission."),"Attest and import")
            if row["ready"]:
                body+=_form("/formal-analyze",actor,_hidden("id",ident),"Compute canonical adjudication")
                if row["consensus_state"]=="required":
                    body+="<p><a href='/formal-consensus?id="+esc(ident)+"'>Resolve tied cells after reviewer discussion</a></p>"
                elif row["consensus_state"]=="recorded":
                    body+="<p>Consensus recorded and locked.</p>"
                else:
                    body+="<p>No tied cells require consensus.</p>"
        body+="</section>"
    return page("Formal clinical review",body)


def review_page(directory,actor,identifier):
    from .institution_web import esc,page,_hidden
    doc=coord.reviewer_template(directory,actor,identifier)
    req=doc["request"]
    fields=_hidden("id",identifier)+_hidden("binding",req["binding_hash"])
    for i,item in enumerate(req["items"],1):
        slot=item["slot"]
        fields+="<section><h2>Item "+str(i)+"</h2>"
        if req["kind"]=="validity":
            fields+="<h3>Original case</h3><p class='case'>"+esc(item["original_case"])+"</p><h3>Perturbed case</h3><p class='case'>"+esc(item["perturbed_case"])+"</p>"
            for q in formal.VALIDITY:
                fields+=_select(q+"_"+slot,("yes","no","cannot_judge"),q.replace("_"," "))
        else:
            fields+="<h3>Case as shown</h3><p class='case'>"+esc(item["case"])+"</p><h3>Product response</h3><p class='case'>"+esc(item["response"])+"</p>"
            fields+=_select("verdict_"+slot,formal.VERDICTS,"Safety verdict")
        fields+="<label>Notes (up to "+str(coord.MAX_BROWSER_NOTE)+" characters)<textarea name='notes_"+esc(slot)+"' maxlength='"+str(coord.MAX_BROWSER_NOTE)+"'></textarea></label></section>"
    fields+=_confirm("I completed this review independently, without automated scores or other reviewers' answers.")
    return page("Independent "+req["kind"]+" review","<p><a href='/formal'>Assignments</a></p>"+_form("/formal-lock",actor,fields,"Lock independent answers"))


def consensus_page(directory,actor,identifier):
    from .institution_web import page,esc,_hidden
    req=coord.consensus_template(directory,actor,identifier)["request"]
    fields=_hidden("id",identifier)+_hidden("request_digest",buyer_plan._digest(req))
    for item in req["items"]:
        slot=item["slot"]
        fields+="<section><p class='case'>"+esc(item["case"])+"</p><p class='case'>"+esc(item["response"])+"</p><p>Safe: "+str(item["safe_votes"])+" · Unsafe: "+str(item["unsafe_votes"])+" · Cannot judge: "+str(item["cannot_judge_votes"])+"</p>"
        fields+=_select("verdict_"+slot,formal.VERDICTS,"Consensus verdict")+"<label>Rationale<textarea required name='rationale_"+esc(slot)+"' maxlength='"+str(coord.MAX_BROWSER_NOTE)+"'></textarea></label></section>"
    fields+=_confirm("The original reviewers discussed these ties and confirmed the recorded consensus; unresolved cases remain cannot_judge.")
    return page("Attested consensus","<p><a href='/formal'>Formal review</a></p>"+_form("/formal-consensus",actor,fields,"Lock consensus"))


def _answers(form,items,keys):
    want={key+"_"+r["slot"] for r in items for key in keys}
    if set(form)!=want:
        raise access.AccessDenied("exact review fields required")
    return [{"slot":r["slot"],**{key:form[key+"_"+r["slot"]] for key in keys}} for r in items]


def dispatch(directory,actor,path,form):
    from .institution_web import page,esc
    form=dict(form)
    confirmed=form.pop("confirmed","")=="yes"
    if path=="/formal-prepare" and set(form)=={"job","family","reviewers","validity_reviewer"}:
        coord.prepare(directory,actor,form["job"],form["family"],[x.strip() for x in form["reviewers"].split(",")],form["validity_reviewer"],confirmed)
    elif path=="/formal-lock":
        ident=form.pop("id","")
        binding=form.pop("binding","")
        doc=coord.reviewer_template(directory,actor,ident)
        keys=(*formal.VALIDITY,"notes") if doc["request"]["kind"]=="validity" else ("verdict","notes")
        coord.lock_review(directory,actor,ident,binding,_answers(form,doc["request"]["items"],keys),confirmed)
    elif path=="/formal-attest" and set(form)=={"id","reviewer","digest"}:
        coord.attest(directory,actor,form["id"],form["reviewer"],form["digest"],confirmed)
    elif path=="/formal-consensus":
        ident=form.pop("id","")
        digest=form.pop("request_digest","")
        doc=coord.consensus_template(directory,actor,ident)
        coord.consensus(directory,actor,ident,digest,_answers(form,doc["request"]["items"],("verdict","rationale")),confirmed)
    elif path=="/formal-analyze" and set(form)=={"id"} and not confirmed:
        result=coord.analyze(directory,actor,form["id"])
        canonical=result["canonical_adjudication"]
        calibration=result["selected_queue_calibration"]
        coverage=calibration["exclusive_coverage"]
        details="<h2>Selected-queue judge calibration</h2><p>"+esc(coverage)+"</p>"
        details+="<p>Determinate valid human reference: "+str(calibration["n_human_reference_eligible"])+" cells, including incomplete automated evaluations.</p>"
        details+="<p>Locked review cells: "+str(calibration["n_locked_review_cells"])+" / "+str(calibration["n_full_run_variant_cells"])+" full-run variants. Not population-level validation.</p>"
        details+="<table><tr><th>Evaluator</th><th>Paired / eligible</th><th>Case clusters</th><th>Sensitivity</th><th>Specificity</th><th>Sensitivity bounds</th><th>Specificity bounds</th></tr>"
        for row in calibration["comparisons"]:
            details+="<tr>"+"".join("<td>"+esc(x)+"</td>" for x in
                (row["name"],str(row["n_paired"])+"/"+str(row["n_reference_eligible"]),
                 row["n_paired_case_clusters"],
                 "Not estimable" if row["sensitivity"] is None else f'{row["sensitivity"]:.1%}',
                 "Not estimable" if row["specificity"] is None else f'{row["specificity"]:.1%}',
                 "Not estimable" if row["sensitivity_missing_bounds"] is None else " – ".join(f"{x:.1%}" for x in row["sensitivity_missing_bounds"]),
                 "Not estimable" if row["specificity_missing_bounds"] is None else " – ".join(f"{x:.1%}" for x in row["specificity_missing_bounds"])))+"</tr>"
        details+="</table><ul>"+"".join("<li>"+esc(x)+"</li>" for x in calibration["limitations"])+"</ul>"
        return page("Formal adjudication report","<p><a href='/formal'>Formal review</a></p><p>Source package unchanged. Conditional scientific output, not a decision-grade or clinical approval.</p><p>Source manifest: "+esc(result["source_manifest"])+"</p><p>Invalid or indeterminate validity findings: "+esc(result["n_invalid_or_indeterminate_validity"])+"</p><pre style='white-space:pre-wrap'>"+esc(canonical["summary_md"])+"</pre>"+details+"<p>Report digest: "+esc(result["digest"])+"</p>")
    else:
        raise access.AccessDenied("invalid formal review action")
    return None
