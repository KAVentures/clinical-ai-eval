"""Offline IT-staged PDF extraction. No PDF parser runs in a browser request.

The derived page text is not the original visual content. The local operator
must approve an isolated, patched Poppler environment; limits are not a sandbox.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from . import buyer_agent as buyer, buyer_manual as manual, buyer_plan, formal_review as formal
from . import institution_access as access, institution_backup as backup

SCHEMA="local-pdf-text/1"
MAX_PAGES=50
MAX_TEXT=512*1024
MAX_RECORD=3*1024*1024
EVENT="vendor-pdf-extraction-staged"
# Apply limits inside the child interpreter, not preexec_fn in the parent.
_RUNNER="""import os,resource,sys
resource.setrlimit(resource.RLIMIT_CPU,(10,10))
resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
resource.setrlimit(resource.RLIMIT_FSIZE,(1048576,1048576))
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
os.execv(sys.argv[1],sys.argv[1:])
"""


def _fail(msg):
    raise buyer.BuyerError(msg)


def _raw(root,record):
    if buyer._verify_document(root,record)!="intact":
        _fail("original PDF unavailable")
    with (root/"evidence"/record["sha256"]).open("rb") as f:
        raw=f.read(buyer.MAX_DOCUMENT_BYTES+1)
    if len(raw)>buyer.MAX_DOCUMENT_BYTES or hashlib.sha256(raw).hexdigest()!=record["sha256"] or not raw.startswith(b"%PDF-"):
        _fail("exact original PDF required")
    return raw


def _run(binary,args,raw,work):
    output=work/"stdout"
    error=work/"stderr"
    with output.open("wb") as out,error.open("wb") as err:
        try:
            result=subprocess.run([sys.executable,"-I","-c",_RUNNER,binary,*args],
                input=raw,stdout=out,stderr=err,timeout=15,check=False,
                cwd=work,env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C","HOME":str(work)})
        except (OSError,subprocess.TimeoutExpired):
            _fail("local PDF extraction timed out or is unavailable")
    if result.returncode or output.stat().st_size>MAX_TEXT or error.stat().st_size>MAX_TEXT:
        _fail("local PDF extraction refused or exceeded limits")
    return output.read_bytes(),error.read_bytes()


def _extract(raw):
    if os.name!="posix":
        _fail("PDF staging requires an approved POSIX extraction environment")
    binary=shutil.which("pdftotext")
    if not binary:
        _fail("IT must provision a patched local pdftotext executable")
    binary=str(Path(binary).resolve())
    with tempfile.TemporaryDirectory(prefix="caeval-pdf-") as tmp:
        work=Path(tmp)
        version_out,version_err=_run(binary,["-v"],b"",work)
        match=re.search(rb"pdftotext version ([0-9.]+)",version_out+version_err)
        if not match:
            _fail("unknown extractor version")
        version=match.group(1).decode("ascii")
        out,err=_run(binary,["-f","1","-l",str(MAX_PAGES+1),"-layout","-enc","UTF-8","-","-"],raw,work)
        # Warnings may indicate malformed input or incomplete extraction. Refuse,
        # rather than silently accepting a partially recovered document.
        if err.strip():
            _fail("PDF extraction reported warnings; manual review required")
    try:
        pages=out.decode("utf-8").split("\f")
    except UnicodeError:
        _fail("PDF text extraction encoding invalid")
    if pages and pages[-1]=="":
        pages.pop()
    if not 1<=len(pages)<=MAX_PAGES:
        _fail("PDF exceeds page limit or has no pages")
    if not any(p.strip() for p in pages):
        _fail("no extractable text; scanned PDF requires external review")
    if any(any(ord(c)<32 and c not in "\t\n\r" for c in p) for p in pages):
        _fail("unsupported extracted control characters")
    return version,pages


def _material(record,version,pages):
    if not isinstance(version,str) or not re.fullmatch(r"[0-9.]{1,40}",version):
        _fail("invalid extractor version")
    if (not isinstance(pages,list) or not 1<=len(pages)<=MAX_PAGES or
        any(not isinstance(p,str) or any(ord(c)<32 and c not in "\t\n\r" for c in p) for p in pages) or
        not any(p.strip() for p in pages) or sum(len(p.encode("utf-8")) for p in pages)>MAX_TEXT):
        _fail("invalid bounded extracted page text")
    doc={"schema":SCHEMA,"source_sha256":record["sha256"],"extractor":"pdftotext",
         "extractor_version":version,"configuration":"layout-utf8-page-breaks",
         "pages":[{"page":i+1,"text":p,"text_sha256":hashlib.sha256(p.encode("utf-8")).hexdigest()}
                  for i,p in enumerate(pages)],"decision_grade":False}
    return {**doc,"digest":buyer_plan._digest(doc)}


def _path(root,record):
    return manual._directory(root,"pdf-extractions")/(manual._identifier(record["sha256"],digest=True)+".json")


def load(root,record,conn=None):
    path=_path(root,record)
    if path.is_symlink():
        _fail("linked extraction refused")
    if not path.exists():
        return None
    if not path.is_file():
        _fail("invalid extraction path")
    _raw(root,record)
    with path.open("rb") as f:
        data=f.read(MAX_RECORD+1)
    if len(data)>MAX_RECORD:
        _fail("extraction record exceeds limit")
    doc=manual._parse(data)
    expected=_material(record,doc["extractor_version"],[r["text"] for r in doc["pages"]])
    if buyer_plan._canonical(doc)!=buyer_plan._canonical(expected):
        _fail("extracted page integrity mismatch")
    # The local extraction is an IT-attested derivative, not a signature.
    def audited(db):
        return db.execute("SELECT 1 FROM audit WHERE actor='local-admin' AND action=? AND object_id=? AND outcome='ok'",
                          (EVENT,doc["digest"])).fetchone()
    if conn is None:
        with access.db(root) as db:
            exists=audited(db)
    else:
        exists=audited(conn)
    if not exists:
        _fail("extraction lacks trusted local staging event")
    return doc


def stage(directory,eid,*,services_stopped=False,confirmed=False,replay=False):
    if services_stopped is not True or confirmed is not True:
        _fail("stop services and explicitly approve isolated local PDF extraction")
    from . import vendor_document_review as review
    root,record=review._record(directory,eid)
    backup._inventory(root)
    backup._sqlite_check(root)
    raw=_raw(root,record)
    version,pages=_extract(raw)
    doc=_material(record,version,pages)
    path=_path(root,record)
    if replay:
        if load(root,record)!=doc:
            _fail("local extraction replay differs from staged record")
    else:
        path.parent.mkdir(mode=0o700,exist_ok=True)
        if path.is_symlink():
            _fail("linked extraction refused")
        formal._publish_once(path,doc)
        access.event(root,"local-admin",EVENT,doc["digest"])
    return {"source_sha256":record["sha256"],"extraction_digest":doc["digest"],
            "extractor_version":version,"pages":len(pages),
            "pages_without_text":[i+1 for i,p in enumerate(pages) if not p.strip()],
            "status":"replay_verified" if replay else "staged","decision_grade":False}


def spans(doc,quotes):
    if not isinstance(quotes,list) or len(quotes)>12:
        _fail("bounded page citation inventory required")
    result=[]
    for row in quotes:
        if not isinstance(row,dict) or set(row)!={"page","quote"}:
            _fail("explicit PDF page and exact quotation required")
        page,quote=row["page"],row["quote"]
        if type(page) is not int or not 1<=page<=len(doc["pages"]) or not isinstance(quote,str) or not quote.strip() or len(quote)>4000:
            _fail("invalid page quotation")
        text=doc["pages"][page-1]["text"]
        start=text.find(quote)
        if start<0 or text.find(quote,start+1)>=0:
            _fail("quotation must be unique on the selected extracted page")
        result.append({"page":page,"start":start,"end":start+len(quote),"quote":quote,
                       "page_text_sha256":doc["pages"][page-1]["text_sha256"]})
    if len({(s["page"],s["start"],s["end"]) for s in result})!=len(result):
        _fail("duplicate PDF quotation")
    return sorted(result,key=lambda s:(s["page"],s["start"],s["end"]))
