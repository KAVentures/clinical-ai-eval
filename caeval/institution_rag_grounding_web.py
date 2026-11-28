"""Institution browser for controlled RAG grounding research."""
import json
from . import institution_rag_grounding as service, institution_imaging_web as forms, institution_access as access


def home(directory,actor):
    from .institution_web import page,esc,_hidden
    body="<p><a href='/'>Dashboard</a></p><p>Exploratory research. Authorized, non-identifying corpus only. Product traces are self-reported; review is not proof of clinical validity.</p>"
    if actor["role"]=="admin":
        body+=("<section><h2>Freeze corpus and reference protocol</h2><form method='post' action='/rag-grounding-preview'>"+_hidden("csrf",actor["csrf"])+
               "<label>Prospective study specification JSON<textarea name='spec' required rows='12' maxlength='1048576'></textarea></label><button>Preview study</button></form></section>")
    for row in service.overview(directory,actor):
        ident=esc(row["id"])
        body+="<section><h3>"+ident+"</h3><p>"+esc(row["state"])+"</p>"
        if actor["role"]=="reviewer" and row["state"]=="assigned":
            body+="<a href='/rag-grounding-review?id="+ident+"'>Open assigned review</a>"
        elif actor["role"]!="reviewer":
            body+="<a href='/rag-grounding-study?id="+ident+"'>Open study</a>"
        body+="</section>"
    return page("RAG grounding research",body)


def preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    forms._exact(form,"spec")
    p=service.preview(directory,actor,forms._parse(form["spec"]))
    s=p["spec"]
    body="<p>Study "+esc(s["study_id"])+" · "+esc(len(s["questions"]))+" matched questions · "+esc(len(s["documents"]))+" corpus documents · "+esc(len(s["candidates"]))+" candidates</p>"
    body+="<p>Two assigned reviewer accounts: "+esc(", ".join(s["reviewers"]))+"</p><p>Exact approval digest: "+esc(p["approval"])+"</p>"
    body+=("<form method='post' action='/rag-grounding-freeze'>"+_hidden("csrf",actor["csrf"])+
           _hidden("spec",json.dumps(s,ensure_ascii=True))+_hidden("approval",p["approval"])+
           "<label><input type='checkbox' name='confirm' value='yes' required> Freeze the exact authorized independently reviewed corpus, reference questions, candidate roster and reviewer assignments before collection.</label><button>Freeze study</button></form>")
    return page("Approve prospective RAG grounding protocol",body)


def study(directory,actor,ident):
    from .institution_web import page,esc,_hidden
    row=service.detail(directory,actor,ident)
    body="<p><a href='/rag-grounding'>Studies</a></p><p>"+esc(row["state"])+" · "+esc(row["n_questions"])+" paired questions · "+esc(row["n_traces_locked"])+" / "+esc(len(row["candidates"]))+" candidate inventories locked · "+esc(row["n_reviews_locked"])+" / 2 reviews locked</p>"
    comparison=row["paired_comparison"]
    body+="<section><h2>Prospective paired research analysis</h2><p>"+esc(comparison["state"])+"</p>"
    if comparison["state"]=="not_prespecified" and actor["role"]=="admin" and len(row["candidates"])>=2:
        body+="<p>Freeze one ordered pair and primary research endpoint before downloading candidate exchanges or locking any trace. No clinical acceptance decision is implied.</p><form method='post' action='/rag-comparison-preview'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)
        for key in ("baseline","candidate"):
            body+="<label>"+esc(key)+"<select name='"+key+"' required>"
            for candidate in row["candidates"]:
                body+="<option value='"+esc(candidate["id"])+"'>"+esc(candidate["id"])+" · "+esc(candidate["version"])+"</option>"
            body+="</select></label>"
        from . import rag_paired_analysis as analysis
        body+="<label>Primary endpoint<select name='primary_endpoint'>"
        for endpoint in analysis.ENDPOINTS:
            body+="<option value='"+esc(endpoint)+"'>"+esc(endpoint)+"</option>"
        body+="</select></label><label>Familywise alpha (basis points, 500 = 5%)<input type='number' name='alpha_bp' min='100' max='1000' value='500' required></label>"
        body+="<label>Research margin (basis points, candidate minus baseline)<input type='number' name='margin_bp' min='-10000' max='10000' value='0' required></label>"
        body+="<label>Maximum unknown pair fraction (basis points)<input type='number' name='max_unknown_bp' min='0' max='10000' value='1000' required></label>"
        body+="<label>Minimum clusters<input type='number' name='min_clusters' min='10' max='40' value='10' required></label>"
        body+="<label><input type='checkbox' name='independent' value='yes'> I attest the scientific assumption of independent cluster observations (otherwise inference is withheld).</label><button>Preview analysis protocol</button></form>"
    elif comparison["state"]=="unavailable_after_exposure":
        body+="<p>A prospective protocol can no longer be added to this study.</p>"
    elif comparison["state"] in ("frozen","sealed_research_analysis"):
        body+="<p>Primary endpoint: "+esc(comparison["primary_endpoint"])+" · "+esc(comparison["baseline"]["id"])+" → "+esc(comparison["candidate"]["id"])+"</p>"
        if comparison["state"]=="sealed_research_analysis" and actor["role"]=="admin":
            body+="<a href='/rag-comparison?id="+esc(ident)+"'>Inspect paired research analysis</a>"
    body+="</section>"
    for c in row["candidates"]:
        cid=esc(c["id"])
        body+="<section><h3>"+cid+" · "+esc(c["version"])+"</h3>"
        if row["state"]=="collecting" and c["id"] not in row["submitted_candidates"]:
            body+="<a href='/rag-grounding-candidate?id="+esc(ident)+"&amp;candidate="+cid+"&amp;kind=exchange'>Case-specific corpus exchange</a> · <a href='/rag-grounding-candidate?id="+esc(ident)+"&amp;candidate="+cid+"&amp;kind=template'>Trace template</a>"
        body+="</section>"
    if row["collection_receipts"]:
        body+="<p>Local collection receipts (consistency only): "+esc(", ".join(r["candidate_id"] for r in row["collection_receipts"]))+"</p>"
    if row["state"]=="collecting":
        body+=("<form method='post' action='/rag-grounding-submit'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+
               "<label>Complete candidate trace JSON<textarea name='trace' required rows='12' maxlength='1048576'></textarea></label>"+
               "<label><input type='checkbox' name='confirm' value='yes' required> Lock the exact complete externally collected trace. Product and retrieval provenance are self-attested; no real patient identifiers.</label><button>Lock trace</button></form>")
        body+=("<section><h2>Import local collection receipt</h2><p>Use the approved local CLI and exact candidate exchange. Receipt replay is not execution attestation.</p><form method='post' action='/rag-grounding-collection-import'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+
               "<label>Collection receipt JSON<textarea name='receipt' required rows='12' maxlength='1048576'></textarea></label>"+
               "<label><input type='checkbox' name='confirm' value='yes' required> Lock the complete trace and its exact local collection receipt.</label><button>Lock local receipt</button></form></section>")
    if row["state"]=="ready" and actor["role"]=="admin":
        p=service.seal_preview(directory,actor,ident)
        body+=("<form method='post' action='/rag-grounding-seal'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("approval",p["approval"])+
               "<label><input type='checkbox' name='confirm' value='yes' required> Seal the exact complete trace and independent review inventory.</label><button>Seal study</button></form>")
    if row["state"]=="sealed":
        body+="<p>Evidence digest: "+esc(row["evidence_digest"])+"</p>"
        if actor["role"]=="admin":
            body+="<h2>Descriptive results (not clinical validity)</h2><p>Rates conditional on available traces or declared claims; bounds are not confidence intervals. Review coverage must be interpreted separately.</p>"
            for result in row["results"]:
                body+="<section><h3>"+esc(result["candidate"]["id"])+"</h3>"
                for cond in result["conditions"]:
                    body+="<h4>"+esc(cond["condition"])+"</h4><pre>"+esc(json.dumps(cond,indent=2))+"</pre>"
                body+="</section>"
            body+=("<form method='post' action='/rag-grounding-evidence'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("approval",row["evidence_digest"])+
                   "<label><input type='checkbox' name='confirm' value='yes' required> Export restricted full corpus, references, reviewer identities, traces and ratings.</label><button>Download private evidence</button></form>")
            if row["collection_receipts"]:
                from . import institution_rag_collection as collection
                p=collection.export_preview(directory,actor,ident)
                body+=("<form method='post' action='/rag-grounding-collection-evidence'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("approval",p["approval"])+
                       "<label><input type='checkbox' name='confirm' value='yes' required> Export the restricted collection evidence package, bound to the sealed study.</label><button>Download collection receipts</button></form>")
    return page("RAG grounding study",body)


def review_view(directory,actor,ident):
    from .institution_web import page,esc,_hidden
    packet=service.review_download(directory,actor,ident,"packet")
    body="<p>Independently review the assigned query, answer, retrieved documents and cited sources. Only use documents available for that case. Assess whether the candidate-declared claims cover all substantive assertions; label citation support or uncertainty. Do not consult other reviewers or candidate identity.</p>"
    body+="<p>"+esc(len(packet["units"]))+" available answer units. Unavailable traces are not reviewed.</p>"
    body+="<p><a href='/rag-grounding-review-download?id="+esc(ident)+"&amp;kind=packet'>Download blinded review packet</a> · <a href='/rag-grounding-review-download?id="+esc(ident)+"&amp;kind=template'>Download review template</a></p>"
    body+=("<form method='post' action='/rag-grounding-review-lock'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+
           "<label>Complete independent ratings JSON<textarea name='review' required rows='12' maxlength='1048576'></textarea></label>"+
           "<label><input type='checkbox' name='confirm' value='yes' required> Lock my independent review of the exact packet; preserve uncertain ratings.</label><button>Lock review permanently</button></form>")
    return page("Assigned independent citation review",body)


def dispatch(directory,actor,path,form):
    forms._confirm(form)
    if path=="/rag-grounding-freeze":
        forms._exact(form,"spec approval confirm")
        return service.freeze(directory,actor,forms._parse(form["spec"]),form["approval"],confirmed=True)
    if path=="/rag-grounding-submit":
        forms._exact(form,"id trace confirm")
        service.submit(directory,actor,form["id"],forms._parse(form["trace"]),confirmed=True)
        return form["id"]
    if path=="/rag-grounding-collection-import":
        forms._exact(form,"id receipt confirm")
        receipt=forms._parse(form["receipt"])
        service.submit(directory,actor,form["id"],receipt["trace"],confirmed=True,receipt=receipt)
        return form["id"]
    if path=="/rag-grounding-collection-evidence":
        from . import institution_rag_collection as collection
        forms._exact(form,"id approval confirm")
        return collection.export(directory,actor,form["id"],form["approval"],confirmed=True)
    if path=="/rag-grounding-review-lock":
        forms._exact(form,"id review confirm")
        service.review(directory,actor,form["id"],forms._parse(form["review"]),confirmed=True)
        return form["id"]
    if path=="/rag-grounding-seal":
        forms._exact(form,"id approval confirm")
        service.seal(directory,actor,form["id"],form["approval"],confirmed=True)
        return form["id"]
    if path=="/rag-grounding-evidence":
        forms._exact(form,"id approval confirm")
        return service.export(directory,actor,form["id"],form["approval"],confirmed=True)
    raise access.AccessDenied("unknown grounding action")


def comparison_preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    from . import institution_rag_comparison as comparison
    required={"id","baseline","candidate","primary_endpoint","alpha_bp","margin_bp","max_unknown_bp","min_clusters"}
    if not required<=set(form) or set(form)-required-{"independent"} or form.get("independent","no") not in ("yes","no"):
        raise access.AccessDenied("invalid paired analysis form")
    spec={k:form[k] for k in ("baseline","candidate","primary_endpoint")}
    spec.update({k:int(form[k]) for k in ("alpha_bp","margin_bp","max_unknown_bp","min_clusters")})
    spec["independent_clusters_attested"]=form.get("independent")=="yes"
    p=comparison.preview(directory,actor,form["id"],spec)
    body="<p>Exact ordered pair and research parameters:</p><pre>"+esc(json.dumps(p["parameters"],indent=2))+"</pre><p>"+esc(p["digest"])+"</p><ul>"
    for limit in p["limitations"]:
        body+="<li>"+esc(limit)+"</li>"
    body+=("</ul><form method='post' action='/rag-comparison-freeze'>"+_hidden("csrf",actor["csrf"])+_hidden("id",form["id"])+_hidden("spec",json.dumps(spec))+_hidden("approval",p["digest"])+
    "<label><input type='checkbox' name='confirm' value='yes' required> Freeze the exact analysis protocol before recorded candidate exposure. Research only.</label><button>Freeze protocol</button></form>")
    return page("Approve paired RAG analysis",body)


def comparison_result(directory,actor,ident):
    from .institution_web import page,esc,_hidden
    from . import institution_rag_comparison as comparison
    result=comparison.result(directory,actor,ident)
    body="<p>Exploratory paired cluster-weighted contrasts, candidate minus baseline. Not a procurement pass, clinical correctness measure or deployment decision.</p><p>Primary research signal: "+esc(result["primary_research_signal"])+"</p>"
    for endpoint in result["endpoints"]:
        body+="<section><h2>"+esc(endpoint["endpoint"])+"</h2><pre>"+esc(json.dumps(endpoint,indent=2))+"</pre></section>"
    body+=("<form method='post' action='/rag-comparison-export'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("approval",result["digest"])+
    "<label><input type='checkbox' name='confirm' value='yes' required> Export this exact restricted research analysis.</label><button>Download paired analysis</button></form>")
    return page("Paired RAG research analysis",body)
