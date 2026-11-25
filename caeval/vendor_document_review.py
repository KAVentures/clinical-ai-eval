"""Source-bound vendor document review (no automated compliance claims).

The original evidence remains content-addressed. UTF-8 originals and explicitly
IT-staged PDF page text are supported. PDF citations bind the exact derived
page text, not the visual PDF. Offsets count Unicode codepoints.
"""
import hashlib
import json
from datetime import date
from pathlib import Path

from . import buyer_agent as buyer, buyer_plan, buyer_manual as manual
from . import institution_access as access, institution_setup as setup, pdf_source_review as pdf

SCHEMA="vendor-document-review/1"
MAX_TEXT=512*1024
MAX_SPANS=12
MAX_HISTORY=50
VERDICTS=("supports_documented_claim","contradicts_documented_claim","insufficient_evidence","not_applicable")
EVENT="vendor-document-review-recorded"


def _fail(message):
    raise access.AccessDenied(message)


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS vendor_document_reviews(
        digest TEXT PRIMARY KEY, evidence_id TEXT NOT NULL, previous TEXT,
        payload TEXT NOT NULL, audit_seq INTEGER NOT NULL UNIQUE,
        FOREIGN KEY(previous) REFERENCES vendor_document_reviews(digest))""")
    conn.execute("CREATE INDEX IF NOT EXISTS vendor_document_review_evidence ON vendor_document_reviews(evidence_id,audit_seq)")


def _record(directory,eid):
    manual._identifier(eid)
    root=buyer._root(directory)
    rows=[r for r in buyer.load(root)["evidence"] if r["evidence_id"]==eid]
    if len(rows)!=1:
        _fail("unknown evidence document")
    return root,rows[0]


def _source(root,record,conn):
    if buyer._verify_document(root,record)!="intact":
        return "unavailable",None,None
    path=root/"evidence"/record["sha256"]
    with path.open("rb") as handle:
        header=handle.read(5)
        if header==b"%PDF-":
            extraction=pdf.load(root,record,conn)
            if extraction is None:
                return "unsupported_format",None,None
            return "pdf_extracted_text","\n\n".join(p["text"] for p in extraction["pages"]),extraction
        if path.stat().st_size>MAX_TEXT:
            return "too_large_for_text_review",None,None
        handle.seek(0)
        raw=handle.read(MAX_TEXT+1)
    if len(raw)>MAX_TEXT or hashlib.sha256(raw).hexdigest()!=record["sha256"]:
        return "unavailable",None,None
    try:
        text=raw.decode("utf-8")
    except UnicodeError:
        return "unsupported_format",None,None
    if (not text.strip() or text.startswith("PK\x03\x04") or
            any(ord(c)<32 and c not in "\t\n\r" for c in text)):
        return "unsupported_format",None,None
    return "exact_utf8_text",text,None

def _bounded(value,limit):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        _fail("nonempty bounded review field required")
    return value.strip()


def _date(value,optional=False):
    if optional and value=="":
        return None
    try:
        result=date.fromisoformat(value)
        if result.isoformat()!=value:
            _fail("ISO date required")
        return result
    except (ValueError,TypeError):
        _fail("ISO date required")


def _spans(text,quotes):
    if not isinstance(quotes,list) or len(quotes)>MAX_SPANS:
        _fail("bounded source quotation inventory required")
    result=[]
    for quote in quotes:
        if not isinstance(quote,str) or not quote.strip() or len(quote)>4000:
            _fail("nonempty bounded exact quotation required")
        start=text.find(quote)
        if start<0 or text.find(quote,start+1)>=0:
            _fail("quotation not unique in exact original text")
        result.append({"start":start,"end":start+len(quote),"quote":quote})
    if len({(s["start"],s["end"]) for s in result})!=len(result):
        _fail("duplicate quotation")
    return sorted(result,key=lambda s:(s["start"],s["end"]))


def _history(conn,record):
    rows=conn.execute("SELECT * FROM vendor_document_reviews WHERE evidence_id=? ORDER BY audit_seq",
                      (record["evidence_id"],)).fetchall()
    audits=conn.execute("SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok' AND object_id LIKE ? ORDER BY seq",
                        (EVENT,record["evidence_id"]+":%")).fetchall()
    if len(rows)!=len(audits) or len(rows)>MAX_HISTORY:
        _fail("review ledger inventory changed")
    result=[]
    previous=None
    for row,ev in zip(rows,audits):
        payload=json.loads(row["payload"])
        if (not isinstance(payload,dict) or payload.get("schema")!=SCHEMA or
                payload.get("decision_grade") is not False or
                payload.get("evidence_id")!=record["evidence_id"] or
                payload.get("vendor_id")!=record["vendor_id"] or
                payload.get("kind")!=record["kind"] or
                payload.get("source_sha256")!=record["sha256"] or
                payload.get("previous")!=previous or row["previous"]!=previous or
                buyer_plan._digest(payload)!=row["digest"] or
                ev["seq"]!=row["audit_seq"] or ev["actor"]!=payload.get("reviewed_by") or
                ev["object_id"]!=record["evidence_id"]+":"+row["digest"]):
            _fail("review ledger or audit binding changed")
        result.append({**payload,"digest":row["digest"]})
        previous=row["digest"]
    return result


def _check_source(text,review,extraction=None):
    if extraction is not None:
        if (review["source_mode"]!="pdf_extracted_text" or review.get("extraction_digest")!=extraction["digest"] or
                pdf.spans(extraction,[{"page":s["page"],"quote":s["quote"]} for s in review["spans"]])!=review["spans"]):
            _fail("PDF extraction or page citation changed")
    elif (review["source_mode"]!="exact_utf8_text" or "extraction_digest" in review or
            _spans(text,[s["quote"] for s in review["spans"]])!=review["spans"]):
        _fail("source quotation offsets changed")


def _freshness(review,today):
    if not review:
        return "not_reviewed"
    if review["valid_until"] is None:
        return "expiry_unknown"
    expiry=_date(review["valid_until"])
    return "expired" if expiry<today else "within_declared_validity"


def inspect(directory,actor,eid):
    with access.db(directory) as conn:
        setup._actor(conn,actor)
        _schema(conn)
        root,record=_record(directory,eid)
        mode,text,extraction=_source(root,record,conn)
        history=_history(conn,record)
        if text is not None:
            for review in history:
                _check_source(text,review,extraction)
        access._audit(conn,actor["user_id"],"vendor-document-review-opened",eid)
        return {"evidence_id":eid,"vendor_id":record["vendor_id"],"kind":record["kind"],
                "title":record["title"],"source_sha256":record["sha256"],"source_mode":mode,
                "text":text,"extraction":extraction,"history":history,"freshness":_freshness(history[-1] if history else None,date.today()),
                "decision_grade":False}


def record_review(directory,actor,eid,source_sha256,previous,claim,verdict,quotes,
                  rationale,document_version,issued_on,valid_until,*,confirmed=False,extraction_digest=None):
    if confirmed is not True:
        _fail("explicit source review confirmation required")
    if verdict not in VERDICTS:
        _fail("unsupported document review verdict")
    claim=_bounded(claim,2000)
    rationale=_bounded(rationale,4000)
    document_version=_bounded(document_version,256)
    issued=_date(issued_on,optional=True)
    expiry=_date(valid_until,optional=True)
    if issued and expiry and expiry<issued:
        _fail("expiry precedes issuance")
    if issued and issued>date.today():
        _fail("issuance date is in the future")
    with access.db(directory) as conn:
        setup._actor(conn,actor)
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        root,record=_record(directory,eid)
        if record["sha256"]!=source_sha256:
            _fail("document changed since review opened")
        mode,text,extraction=_source(root,record,conn)
        if mode not in ("exact_utf8_text","pdf_extracted_text"):
            _fail("exact supported source required; unstaged PDFs are not accepted")
        if extraction_digest!=(extraction["digest"] if extraction is not None else None):
            _fail("review does not bind current source extraction")
        history=_history(conn,record)
        prior=history[-1]["digest"] if history else None
        if (previous or None)!=prior or len(history)>=MAX_HISTORY:
            _fail("review history changed or limit reached; reopen document")
        for review in history:
            _check_source(text,review,extraction)
        spans=pdf.spans(extraction,quotes) if extraction is not None else _spans(text,quotes)
        if verdict in ("supports_documented_claim","contradicts_documented_claim") and not spans:
            _fail("support or contradiction requires exact source quotation")
        payload={"schema":SCHEMA,"evidence_id":eid,"vendor_id":record["vendor_id"],
                 "kind":record["kind"],"source_sha256":source_sha256,"source_mode":mode,
                 "previous":prior,"reviewed_by":actor["user_id"],"claim":claim,"verdict":verdict,
                 "spans":spans,"rationale":rationale,"document_version":document_version,
                 "issued_on":issued.isoformat() if issued else None,
                 "valid_until":expiry.isoformat() if expiry else None,"decision_grade":False}
        if extraction is not None:
            payload["extraction_digest"]=extraction["digest"]
        digest=buyer_plan._digest(payload)
        access._audit(conn,actor["user_id"],EVENT,eid+":"+digest)
        seq=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO vendor_document_reviews VALUES(?,?,?,?,?)",
                     (digest,eid,prior,buyer_plan._canonical(payload).decode("utf-8"),seq))
        return digest


def committee_inventory(directory,actor):
    """Only administrative status, no quotes, claim text, reviewer IDs or rationale."""
    with access.db(directory) as conn:
        access.require(actor,"admin","operator","observer")
        user=conn.execute("SELECT role,enabled FROM users WHERE id=?",(actor["user_id"],)).fetchone()
        if not user or not user["enabled"] or user["role"]!=actor["role"]:
            _fail("active committee account required")
        _schema(conn)
        root=buyer._root(directory)
        result={}
        for record in buyer.load(root)["evidence"]:
            eid=record["evidence_id"]
            row={"source_review_status":"not_reviewed","document_freshness":"not_reviewed",
                 "source_review_digest":None,"source_review_count":0}
            try:
                history=_history(conn,record)
                if history:
                    mode,text,extraction=_source(root,record,conn)
                    if mode not in ("exact_utf8_text","pdf_extracted_text"):
                        _fail("original source unavailable")
                    for review in history:
                        _check_source(text,review,extraction)
                    latest=history[-1]
                    row.update(source_review_status=latest["verdict"],
                               document_freshness=_freshness(latest,date.today()),
                               source_review_digest=latest["digest"],source_review_count=len(history))
            except (ValueError,RuntimeError,OSError,KeyError,TypeError,AttributeError,IndexError):
                row.update(source_review_status="unavailable",document_freshness="unavailable")
            result[eid]=row
        return result
