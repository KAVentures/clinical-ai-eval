"""Versioned structured controlled-context citation adapter.

The vendor reports its observed retrieval query, chunks and answer. This
protocol only admits the exact predeclared supplied context to the controlled
citation assay. It is not open-world retrieval validation; vendor assertions
are not independently instrumented. No reference labels or expected answers
are sent to the candidate.
"""
import secrets
import re
from . import candidate_protocol as base, buyer_manual as manual, buyer_plan

PROTOCOL="clinical-ai-eval-citation-trace/1"
MAX_TRACE_BYTES=128*1024


def request(target,prompt):
    doc={"protocol":PROTOCOL,"request_id":secrets.token_hex(16),
         "target":dict(target),"input":{"kind":"controlled_citation","text":prompt}}
    return validate_request(doc)


def validate_request(doc,*,expected_target=None):
    base._strict(doc,("protocol","request_id","target","input"))
    if doc["protocol"]!=PROTOCOL:
        base._fail("unsupported citation trace protocol")
    base._nonce(doc["request_id"])
    base._target(doc["target"])
    if expected_target is not None and doc["target"]!=expected_target:
        base._fail("target mismatch")
    base._strict(doc["input"],("kind","text"))
    if doc["input"]["kind"]!="controlled_citation":
        base._fail("unsupported trace input")
    base._text(doc["input"]["text"],base.MAX_PROMPT_BYTES)
    return doc


def validate_trace(trace):
    base._strict(trace,("retrieval_query","retrieved_chunks","answer"))
    query=trace["retrieval_query"]
    if not isinstance(query,str) or len(query.encode("utf-8"))>base.MAX_PROMPT_BYTES:
        base._fail("invalid observed retrieval query")
    base._text(trace["answer"],base.MAX_ANSWER_BYTES)
    chunks=trace["retrieved_chunks"]
    if not isinstance(chunks,list) or len(chunks)>32:
        base._fail("bounded observed chunks required")
    for i,c in enumerate(chunks,1):
        base._strict(c,("source_id","source_version","text","rank"))
        if not isinstance(c["source_id"],str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",c["source_id"]):
            base._fail("invalid source identifier")
        base._text(c["source_version"],512)
        base._text(c["text"],base.MAX_PROMPT_BYTES)
        if type(c["rank"]) is not int or c["rank"]!=i:
            base._fail("ordered contiguous chunk ranks required")
    if len(buyer_plan._canonical(trace))>MAX_TRACE_BYTES:
        base._fail("trace exceeds size limit")
    return trace


def validate_response(req,doc):
    validate_request(req)
    base._strict(doc,("protocol","request_id","target","status","trace"))
    if doc["protocol"]!=PROTOCOL or doc["request_id"]!=req["request_id"] or doc["target"]!=req["target"]:
        base._fail("trace reply does not match issued request")
    if doc["status"]=="complete":
        validate_trace(doc["trace"])
    elif doc["status"]!="unavailable" or doc["trace"] is not None:
        base._fail("invalid trace outcome")
    return doc


def response(req,*,target,status,trace=None):
    validate_request(req,expected_target=target)
    return validate_response(req,{"protocol":PROTOCOL,"request_id":req["request_id"],
                                  "target":dict(target),"status":status,"trace":trace})


def parse_response(req,raw):
    if not isinstance(raw,bytes) or len(raw)>base.MAX_REPLY_BYTES:
        base._fail("trace response exceeds limit")
    return validate_response(req,manual._parse(raw))


def openapi():
    def obj(props):
        return {"type":"object","additionalProperties":False,"required":list(props),"properties":props}
    def text(n):
        return {"type":"string","maxLength":n}
    fields={"protocol":{"const":PROTOCOL},"request_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},
            "target":obj({"name":text(300),"version":text(300)})}
    req=obj({**fields,"input":obj({"kind":{"const":"controlled_citation"},"text":text(base.MAX_PROMPT_BYTES)})})
    chunk=obj({"source_id":text(128),"source_version":text(512),"text":text(base.MAX_PROMPT_BYTES),
               "rank":{"type":"integer","minimum":1}})
    trace=obj({"retrieval_query":text(base.MAX_PROMPT_BYTES),"retrieved_chunks":{"type":"array","maxItems":32,"items":chunk},
               "answer":text(base.MAX_ANSWER_BYTES)})
    reply=obj({**fields,"status":{"enum":["complete","unavailable"]},"trace":{"oneOf":[trace,{"type":"null"}]}})
    reply["allOf"]=[{"if":{"properties":{"status":{"const":"complete"}}},"then":{"properties":{"trace":trace}},
                     "else":{"properties":{"trace":{"type":"null"}}}}]
    return {"openapi":"3.1.0","info":{"title":"Clinical AI Eval controlled citation trace adapter","version":"1.0.0"},
            "paths":{"/evaluate":{"post":{"operationId":"evaluateControlledCitation",
              "requestBody":{"required":True,"content":{"application/json":{"schema":req}}},
              "responses":{"200":{"description":"Correlated structured trace or unavailable",
                 "content":{"application/json":{"schema":reply}}},"400":{"description":"Invalid request"}}}}}}
