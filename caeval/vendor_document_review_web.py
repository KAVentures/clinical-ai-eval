"""Browser-only source review, with no external parser or model calls."""
from . import vendor_document_review as review, institution_access as access


def render(directory,actor,eid):
    from .institution_web import page,esc,_hidden
    doc=review.inspect(directory,actor,eid)
    body="<p><a href='/setup'>Candidate intake and evidence</a></p><h2>"+esc(doc["title"])+"</h2>"
    body+="<p>"+esc(doc["vendor_id"])+" · "+esc(doc["kind"])+" · SHA-256 "+esc(doc["source_sha256"])+"</p>"
    body+="<p>Source availability: "+esc(doc["source_mode"])+" · Declared freshness: "+esc(doc["freshness"])+"</p>"
    body+="<p>Review of a vendor statement, not independent clinical validation or regulatory compliance. Dates and version are reviewer-entered.</p>"
    if doc["text"] is None:
        body+="<p>Native source review is unavailable. Use bounded UTF-8 text or ask IT to stage a local PDF extraction. Scans and office documents require separately governed review.</p>"
    else:
        extraction=doc["extraction"]
        if extraction:
            body+="<p>Derived page text, not original visual content. Compare the original PDF in an approved viewer before attesting; layout, tables, images and scanned text may be missing. Extractor "+esc(extraction["extractor_version"])+" · "+esc(extraction["digest"])+"</p>"
            for p in extraction["pages"]:
                body+="<details><summary>Extracted page "+str(p["page"])+(" (no text)" if not p["text"].strip() else "")+"</summary><pre style='white-space:pre-wrap;overflow-wrap:anywhere'>"+esc(p["text"])+"</pre></details>"
        else:
            body+="<details open><summary>Exact original text</summary><pre style='white-space:pre-wrap;overflow-wrap:anywhere'>"+esc(doc["text"])+"</pre></details>"
        previous=doc["history"][-1]["digest"] if doc["history"] else ""
        body+="<form method='post' action='/vendor-document-review-record'>"+_hidden("csrf",actor["csrf"])+_hidden("id",eid)+_hidden("sha",doc["source_sha256"])+_hidden("previous",previous)
        body+="<label>Exact vendor statement being assessed<textarea name='claim' required maxlength='2000'></textarea></label>"
        body+="<label>Source finding<select name='verdict' required><option value=''>Choose</option>"+"".join("<option value='"+esc(k)+"'>"+esc(k.replace("_"," "))+"</option>" for k in review.VERDICTS)+"</select></label>"
        if extraction:
            body+=_hidden("extraction_digest",extraction["digest"])+"<p>Choose the one-based PDF page and paste an exact quotation unique on that extracted page. Confirm against the visual original.</p>"
        else:
            body+="<p>Paste exact unique quotations from the original text. Repeated or normalized quotations are refused; no guessed offsets.</p>"
        for i in range(3):
            if extraction:
                body+="<label>Page "+str(i+1)+"<input type='number' min='1' max='"+str(len(extraction["pages"]))+"' name='page_"+str(i)+"'></label>"
            body+="<label>Quotation "+str(i+1)+"<textarea name='quote_"+str(i)+"' maxlength='4000'></textarea></label>"
        body+="<label>Reviewer rationale<textarea name='rationale' required maxlength='4000'></textarea></label>"
        body+="<label>Document version (or explicitly unknown)<input name='version' required maxlength='256'></label>"
        body+="<label>Declared issue date (optional)<input type='date' name='issued'></label><label>Declared valid through (optional)<input type='date' name='expiry'></label>"
        body+="<label><input type='checkbox' name='confirm' value='yes' required> I reviewed the exact original and confirm this source finding. This is not a clinical or compliance approval.</label><button>Record immutable source review</button></form>"
    body+="<h2>Review history</h2>"
    for item in reversed(doc["history"]):
        body+="<section><p>"+esc(item["verdict"])+" · "+esc(item["reviewed_by"])+" · "+esc(item["document_version"])+"</p><p>"+esc(item["claim"])+"</p><p>"+esc(item["rationale"])+"</p><p>Declared validity: "+esc(item["issued_on"] or "unknown")+" – "+esc(item["valid_until"] or "unknown")+"</p>"
        for span in item["spans"]:
            body+="<p>"+("Extracted page "+str(span["page"])+" · " if "page" in span else "")+"Characters "+str(span["start"])+"–"+str(span["end"])+": "+esc(span["quote"])+"</p>"
        body+="<p>Review digest: "+esc(item["digest"])+"</p></section>"
    return page("Vendor document source review",body)


def dispatch(directory,actor,form):
    fields={"id","sha","previous","claim","verdict","rationale","version","issued","expiry","confirm","quote_0","quote_1","quote_2"}
    pdf_fields={"extraction_digest","page_0","page_1","page_2"}
    is_pdf=set(form)==fields|pdf_fields
    if (not is_pdf and set(form)!=fields) or form["confirm"]!="yes":
        raise access.AccessDenied("explicit document review form required")
    quotes=[]
    for i in range(3):
        q=form["quote_"+str(i)]
        if is_pdf:
            p=form["page_"+str(i)]
            if bool(p)!=bool(q):
                raise access.AccessDenied("PDF citation requires both page and quotation")
            if q:
                quotes.append({"page":int(p),"quote":q})
        elif q:
            quotes.append(q)
    return review.record_review(directory,actor,form["id"],form["sha"],form["previous"],form["claim"],form["verdict"],
        quotes,form["rationale"],form["version"],form["issued"],form["expiry"],confirmed=True,
        extraction_digest=form.get("extraction_digest"))
