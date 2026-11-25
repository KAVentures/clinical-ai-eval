"""Governed local corpus-bound RAG collection.

The exchange is candidate-facing. No reference labels, reviewer identities or
private study plan are needed. Local transport is not execution attestation.
"""
import json
import os
import secrets
import time
import urllib.request
from pathlib import Path
from . import diagnostic_imaging as d, rag_grounding as g, adapters, buyer_manual

PROTOCOL="clinical-ai-eval-rag-trace/1"
MAX_REQUEST=1024*1024
MAX_REPLY=32*1024
MAX_EXCHANGE=4*1024*1024
MAX_TRACE=550000
MAX_SECONDS=900
MAX_FAILURES=3
LIMITS=[
    "Literal loopback HTTP only; the adapter's downstream actions and data handling are not verified.",
    "Exact exchange, request and response correlation is checked; model execution and retrieval truth are self-reported.",
    "A local exclusive journal prevents ordinary repeated dispatch in the same trusted directory, not across machines or deleted journals.",
    "Interrupted collection cannot be resumed automatically; earlier requests may have reached the candidate.",
    "No clinical validity, independent reference adjudication or deployment authorization.",
]


def _wire(doc):
    return json.dumps(doc,sort_keys=True,ensure_ascii=True,allow_nan=False,separators=(",",":")).encode("ascii")


def _identity(c):
    d._keys(c,"id version")
    d._require(all(d._token(v) for v in c.values()),"invalid candidate identity")


def _case(case):
    d._keys(case,"slot query corpus")
    d._require(d._hex(case["slot"]) and g._text(case["query"],4000),"invalid case")
    docs=case["corpus"]
    d._require(isinstance(docs,list) and 1<=len(docs)<=g.MAX_DOCS,"bounded case corpus required")
    by={}
    for doc in docs:
        d._keys(doc,"id version title text state digest")
        ident=doc["id"]
        d._require(d._token(ident) and ident not in by and d._token(doc["version"])
                   and g._text(doc["title"],300) and g._text(doc["text"])
                   and doc["state"] in ("current","withdrawn")
                   and doc["digest"]==g._doc_hash({k:v for k,v in doc.items() if k!="digest"}),
                   "invalid or changed corpus document")
        by[ident]=doc["digest"]
    return by


def _exchange(ex):
    d._keys(ex,"schema plan_digest candidate cases")
    d._require(ex["schema"]=="rag-grounding-exchange/1" and d._hex(ex["plan_digest"]),"invalid grounding exchange")
    _identity(ex["candidate"])
    cases=ex["cases"]
    d._require(isinstance(cases,list) and 1<=len(cases)<=2*g.MAX_CASES and len(_wire(ex))<=MAX_EXCHANGE,
               "exchange exceeds collection bounds")
    seen=set()
    for case in cases:
        _case(case)
        d._require(case["slot"] not in seen,"duplicate exchange slot")
        seen.add(case["slot"])
    return sorted(cases,key=lambda c:c["slot"])


def _nonce(x):
    return isinstance(x,str) and len(x)==32 and all(c in "0123456789abcdef" for c in x)


def request(ex,case,request_id):
    # Internal callers validate the entire exchange once; the request itself
    # is validated on both sides of the public protocol.
    doc={"protocol":PROTOCOL,"request_id":request_id,"target":ex["candidate"],"case":case}
    validate_request(doc)
    return doc


def validate_request(req,*,expected_target=None):
    d._keys(req,"protocol request_id target case")
    d._require(req["protocol"]==PROTOCOL and _nonce(req["request_id"]),"invalid request correlation")
    _identity(req["target"])
    d._require(expected_target is None or expected_target==req["target"],"exact served version required")
    _case(req["case"])
    d._require(len(_wire(req))<=MAX_REQUEST,"request byte limit")
    return req["case"]


def _empty(slot):
    return {"slot":slot,"status":"unavailable","retrieved":[],"answer":"","claims":[]}


def _trace(case,row):
    d._keys(row,"slot status retrieved answer claims")
    d._require(row["slot"]==case["slot"] and row["status"] in ("answered","abstained","unavailable"),
               "invalid trace identity or status")
    allowed=_case(case)
    hits=row["retrieved"]
    d._require(isinstance(hits,list) and len(hits)<=len(allowed),"invalid retrieval inventory")
    used=set()
    for hit in hits:
        d._keys(hit,"id digest")
        ident=hit["id"]
        d._require(d._token(ident) and ident in allowed and ident not in used and hit["digest"]==allowed[ident],
                   "retrieved source outside exact case corpus")
        used.add(ident)
    if row["status"]=="unavailable":
        d._require(row==_empty(case["slot"]),"unavailable trace must be empty")
        return
    d._require(g._text(row["answer"]) and isinstance(row["claims"],list) and len(row["claims"])<=g.MAX_CLAIMS,
               "bounded answer and claims required")
    end=0
    for claim in row["claims"]:
        d._keys(claim,"start end citations")
        a,b=claim["start"],claim["end"]
        d._require(type(a) is int and type(b) is int and end<=a<b<=len(row["answer"])
                   and bool(row["answer"][a:b].strip()) and d._unique_tokens(claim["citations"],0,g.MAX_DOCS),
                   "invalid claim spans or citations")
        end=b


def validate_response(req,reply):
    d._keys(reply,"protocol request_id target trace")
    d._require(reply["protocol"]==PROTOCOL and reply["request_id"]==req["request_id"]
               and reply["target"]==req["target"],"response correlation mismatch")
    _trace(req["case"],reply["trace"])
    d._require(len(_wire(reply))<=MAX_REPLY,"response byte limit")
    return reply


def response(req,*,target,trace):
    validate_request(req,expected_target=target)
    return validate_response(req,{"protocol":PROTOCOL,"request_id":req["request_id"],"target":target,"trace":trace})


def _config(cfg):
    d._keys(cfg,"url timeout_seconds")
    adapters.validate_local_endpoint(cfg["url"])
    d._require(type(cfg["timeout_seconds"]) is int and 1<=cfg["timeout_seconds"]<=60,"invalid socket timeout")


def _post(cfg,req):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),adapters._RefuseRedirect())
    http=urllib.request.Request(cfg["url"],data=_wire(req),headers={"Content-Type":"application/json"},method="POST")
    with opener.open(http,timeout=cfg["timeout_seconds"]) as reply:
        d._require(reply.status==200 and reply.headers.get_content_type()=="application/json","HTTP 200 JSON required")
        raw=reply.read(MAX_REPLY+1)
    d._require(len(raw)<=MAX_REPLY,"response byte limit")
    return validate_response(req,buyer_manual._parse(raw))


def preview(ex,cfg):
    _config(cfg)
    cases=_exchange(ex)
    entries=[]
    for case in cases:
        req=request(ex,case,"0"*32)
        entries.append({"slot":case["slot"],"case_digest":d._digest(case),"request_bytes":len(_wire(req))})
    return d._sealed({"schema":"rag-collection-preview/1","exchange_digest":d._digest(ex),
                      "candidate":ex["candidate"],"config":dict(cfg),"entries":entries,
                      "decision_grade":False})


def _write_once(path,doc):
    # Trusted, private directory only. No overwrite, including after an
    # interrupted write. Sync before any network call that depends on it.
    with path.open("xb") as f:
        f.write(_wire(doc))
        f.flush()
        os.fsync(f.fileno())


def _sync_dir(path):
    # Directory fsync is not portable (notably Windows). Fail closed on
    # supported POSIX systems if durability cannot be established.
    if os.name=="posix":
        fd=os.open(str(path),os.O_RDONLY|getattr(os,"O_DIRECTORY",0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _journal(root,binding):
    root=Path(root)
    d._require(root.is_dir() and not root.is_symlink(),"existing trusted journal root required")
    path=root/binding["exchange_digest"]
    path.mkdir(mode=0o700,exist_ok=False)
    _sync_dir(root)
    _write_once(path/"approval.json",binding)
    _sync_dir(path)
    return path


def _trace_document(ex,rows):
    return {"schema":"rag-grounding-trace/1","plan_digest":ex["plan_digest"],
            "candidate":ex["candidate"],"traces":rows}


def _budget(ex,rows):
    return (sum(len(r["claims"]) for r in rows)<=g.MAX_TOTAL_CLAIMS_PER_CANDIDATE
            and len(json.dumps(_trace_document(ex,rows),ensure_ascii=True).encode("ascii"))<=MAX_TRACE)


def collect(ex,cfg,approval,journal_root,*,confirmed=False):
    d._require(confirmed is True,"explicit authorized corpus transmission required")
    binding=preview(ex,cfg)
    d._require(binding["digest"]==approval,"collection differs from approval")
    path=_journal(journal_root,binding)
    cases=_exchange(ex)
    rows=[]
    events=[]
    failures=0
    stopped=False
    deadline=time.monotonic()+MAX_SECONDS
    for i,case in enumerate(cases):
        slot=case["slot"]
        row=_empty(slot)
        event={"slot":slot,"request_id":None,"request_digest":None,
               "response_digest":None,"state":"not_attempted"}
        if not stopped and failures<MAX_FAILURES and time.monotonic()<deadline:
            nonce=secrets.token_hex(16)
            req=request(ex,case,nonce)
            event.update(request_id=nonce,request_digest=d._digest(req),state="failed")
            _write_once(path/("%03d-intent.json"%i),event)
            _sync_dir(path)
            try:
                reply=_post(cfg,req)
                validate_response(req,reply)
            except (ValueError,RuntimeError,OSError,TypeError,KeyError,AttributeError,RecursionError):
                failures+=1
            else:
                candidate=reply["trace"]
                # Reserve room for every remaining unavailable row, so an
                # oversize aggregate never silently drops a case.
                if _budget(ex,rows+[candidate]+[_empty(c["slot"]) for c in cases[i+1:]]):
                    row=candidate
                    event.update(state="correlated",response_digest=d._digest(reply))
                else:
                    event["state"]="output_budget_refused"
                    stopped=True
        _write_once(path/("%03d-result.json"%i),{"event":event,"trace":row})
        _sync_dir(path)
        rows.append(row)
        events.append(event)
    receipt=d._sealed({"schema":"rag-collection-receipt/1","binding":binding,
                      "trace":_trace_document(ex,rows),"events":events,
                      "decision_grade":False,"limitations":LIMITS[:]})
    verify(ex,receipt)
    _write_once(path/"receipt.json",receipt)
    _sync_dir(path)
    return receipt


def verify(ex,receipt,expected_digest=None):
    d._keys(receipt,"schema binding trace events decision_grade limitations digest")
    d._require(receipt["schema"]=="rag-collection-receipt/1" and receipt["decision_grade"] is False
               and receipt["limitations"]==LIMITS
               and d._digest({k:v for k,v in receipt.items() if k!="digest"})==receipt["digest"]
               and (expected_digest is None or expected_digest==receipt["digest"]),"receipt digest mismatch")
    binding=receipt["binding"]
    d._require(binding==preview(ex,binding["config"]),"exact collection binding mismatch")
    cases=_exchange(ex)
    trace=receipt["trace"]
    d._keys(trace,"schema plan_digest candidate traces")
    rows=trace["traces"]
    events=receipt["events"]
    d._require(isinstance(rows,list) and isinstance(events,list) and len(rows)==len(events)==len(cases)
               and trace==_trace_document(ex,rows),"complete trace inventory required")
    seen=set()
    failures=0
    stopped=False
    for case,row,event in zip(cases,rows,events):
        _trace(case,row)
        d._keys(event,"slot request_id request_digest response_digest state")
        d._require(event["slot"]==case["slot"],"event slot mismatch")
        state=event["state"]
        nonce=event["request_id"]
        if state=="not_attempted":
            d._require(nonce is None and event["request_digest"] is None and event["response_digest"] is None
                       and row==_empty(case["slot"]),"invalid unattempted case")
            stopped=True
            continue
        d._require(not stopped and failures<MAX_FAILURES and _nonce(nonce) and nonce not in seen
                   and state in ("failed","correlated","output_budget_refused"),"invalid dispatch sequence")
        seen.add(nonce)
        req=request(ex,case,nonce)
        d._require(event["request_digest"]==d._digest(req),"request digest mismatch")
        if state=="correlated":
            reply=response(req,target=ex["candidate"],trace=row)
            d._require(event["response_digest"]==d._digest(reply),"response digest mismatch")
        else:
            d._require(event["response_digest"] is None and row==_empty(case["slot"]),"failed request cannot produce evidence")
            if state=="failed":
                failures+=1
            else:
                stopped=True
    d._require(_budget(ex,rows),"trace output budget exceeded")
    return {"status":"REPLAY_VERIFIED","digest":receipt["digest"],"expected_digest_matched":expected_digest is not None,
            "n_attempted":len(seen),"n_transport_failures":failures,"execution_attested":False,"decision_grade":False}


def openapi():
    def obj(props):
        return {"type":"object","required":list(props),"properties":props,"additionalProperties":False}
    token={"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$"}
    sha={"type":"string","pattern":"^[0-9a-f]{64}$"}
    text=lambda n:{"type":"string","minLength":1,"maxLength":n}
    target=obj({"id":token,"version":token})
    doc=obj({"id":token,"version":token,"title":text(300),"text":text(g.MAX_TEXT),
             "state":{"enum":["current","withdrawn"]},"digest":sha})
    case=obj({"slot":sha,"query":text(4000),"corpus":{"type":"array","minItems":1,"maxItems":g.MAX_DOCS,"items":doc}})
    common={"protocol":{"const":PROTOCOL},"request_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},"target":target}
    req=obj({**common,"case":case})
    claim=obj({"start":{"type":"integer","minimum":0},"end":{"type":"integer","minimum":1},
               "citations":{"type":"array","maxItems":g.MAX_DOCS,"uniqueItems":True,"items":token}})
    trace=obj({"slot":sha,"status":{"enum":["answered","abstained","unavailable"]},
               "retrieved":{"type":"array","maxItems":g.MAX_DOCS,"items":obj({"id":token,"digest":sha})},
               "answer":{"type":"string","maxLength":g.MAX_TEXT},
               "claims":{"type":"array","maxItems":g.MAX_CLAIMS,"items":claim}})
    trace["allOf"]=[{"if":{"properties":{"status":{"const":"unavailable"}}},
                     "then":{"properties":{"answer":{"const":""},"retrieved":{"maxItems":0},"claims":{"maxItems":0}}},
                     "else":{"properties":{"answer":{"minLength":1}}}}]
    reply=obj({**common,"trace":trace})
    return {"openapi":"3.1.0","info":{"title":"Local controlled RAG trace contract","version":"1.0.0",
                "description":"One isolated case-specific corpus per request. Runtime validates UTF-8 byte limits, exact hashes, span order and cross-message identity."},
            "paths":{"/evaluate":{"post":{"operationId":"evaluateRagTrace","requestBody":{"required":True,
                     "content":{"application/json":{"schema":req}}},"responses":{"200":{"description":"Correlated product-reported trace",
                     "content":{"application/json":{"schema":reply}}}}}}}}


def configure(parser):
    sub=parser.add_subparsers(dest="collection_action",required=True)
    sub.add_parser("openapi")
    for action in ("preview","collect"):
        p=sub.add_parser(action)
        p.add_argument("exchange")
        p.add_argument("--url",required=True)
        p.add_argument("--timeout",type=int,default=30)
        if action=="collect":
            p.add_argument("--approved-digest",required=True)
            p.add_argument("--journal-root",required=True)
            p.add_argument("--confirm-local-corpus-transmission",action="store_true")
    v=sub.add_parser("verify")
    v.add_argument("exchange")
    v.add_argument("receipt")
    v.add_argument("--expected-digest")
    v=sub.add_parser("verify-institution")
    v.add_argument("study_evidence")
    v.add_argument("collection_evidence")
    v.add_argument("--expected-digest")
    parser.set_defaults(func=cli)


def cli(args):
    try:
        action=args.collection_action
        if action=="openapi":
            out=openapi()
        elif action=="verify":
            out=verify(d._read(args.exchange),d._read(args.receipt),args.expected_digest)
        elif action=="verify-institution":
            from . import institution_rag_collection as institution
            out=institution.verify_package(d._read(args.collection_evidence),d._read(args.study_evidence),args.expected_digest)
        else:
            ex=d._read(args.exchange)
            cfg={"url":args.url,"timeout_seconds":args.timeout}
            if action=="preview":
                out=preview(ex,cfg)
            else:
                out=collect(ex,cfg,args.approved_digest,args.journal_root,confirmed=args.confirm_local_corpus_transmission)
        print(json.dumps(out,ensure_ascii=True,allow_nan=False,indent=2))
    except (ValueError,RuntimeError,OSError,TypeError,KeyError,AttributeError,RecursionError):
        raise SystemExit("Local RAG collection refused or interrupted. Inspect the private journal; do not blindly rerun.")
