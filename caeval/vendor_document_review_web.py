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
        body+="<p>Native source review is unavailable. This release accepts bounded UTF-8 text only. PDFs, scans and office documents remain registered evidence, not parsed or verified claims.</p>"
    else:
        body+="<details open><summary>Exact original text</summary><pre style='white-space:pre-wrap;overflow-wrap:anywhere'>"+esc(doc["text"])+"</pre></details>"
        previous=doc["history"][-1]["digest"] if doc["history"] else ""
        body+="<form method='post' action='/vendor-document-review-record'>"+_hidden("csrf",actor["csrf"])+_hidden("id",eid)+_hidden("sha",doc["source_sha256"])+_hidden("previous",previous)
        body+="<label>Exact vendor statement being assessed<textarea name='claim' required maxlength='2000'></textarea></label>"
        body+="<label>Source finding<select name='verdict' required><option value=''>Choose</option>"+"".join("<option value='"+esc(k)+"'>"+esc(k.replace("_"," "))+"</option>" for k in review.VERDICTS)+"</select></label>"
        body+="<p>Paste exact unique quotations from the original text. Repeated or normalized quotations are refused; no guessed offsets.</p>"
        for i in range(3):
            body+="<label>Original quotation "+str(i+1)+"<textarea name='quote_"+str(i)+"' maxlength='4000'></textarea></label>"
        body+="<label>Reviewer rationale<textarea name='rationale' required maxlength='4000'></textarea></label>"
        body+="<label>Document version (or explicitly unknown)<input name='version' required maxlength='256'></label>"
        body+="<label>Declared issue date (optional)<input type='date' name='issued'></label><label>Declared valid through (optional)<input type='date' name='expiry'></label>"
        body+="<label><input type='checkbox' name='confirm' value='yes' required> I reviewed the exact original and confirm this source finding. This is not a clinical or compliance approval.</label><button>Record immutable source review</button></form>"
    body+="<h2>Review history</h2>"
    for item in reversed(doc["history"]):
        body+="<section><p>"+esc(item["verdict"])+" · "+esc(item["reviewed_by"])+" · "+esc(item["document_version"])+"</p><p>"+esc(item["claim"])+"</p><p>"+esc(item["rationale"])+"</p><p>Declared validity: "+esc(item["issued_on"] or "unknown")+" – "+esc(item["valid_until"] or "unknown")+"</p>"
        for span in item["spans"]:
            body+="<p>Characters "+str(span["start"])+"–"+str(span["end"])+": "+esc(span["quote"])+"</p>"
        body+="<p>Review digest: "+esc(item["digest"])+"</p></section>"
    return page("Vendor document source review",body)


def dispatch(directory,actor,form):
    fields={"id","sha","previous","claim","verdict","rationale","version","issued","expiry","confirm","quote_0","quote_1","quote_2"}
    if set(form)!=fields or form["confirm"]!="yes":
        raise access.AccessDenied("explicit document review form required")
    return review.record_review(directory,actor,form["id"],form["sha"],form["previous"],form["claim"],form["verdict"],
        [form["quote_"+str(i)] for i in range(3) if form["quote_"+str(i)]],form["rationale"],form["version"],
        form["issued"],form["expiry"],confirmed=True)
