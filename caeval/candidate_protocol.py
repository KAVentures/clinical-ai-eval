"""Provider-neutral, single-turn candidate endpoint contract.

The candidate receives only the presented input, a request correlation nonce
and the declared product/version. It does not receive expected answers, judge
rubrics or clinical thresholds. Echoed identity is an assertion, not attestation.
Transport authentication and deployment controls belong to approved IT.
"""
import secrets
from . import buyer_manual as manual, buyer_agent as buyer

PROTOCOL="clinical-ai-eval-candidate/1"
MAX_PROMPT_BYTES=128*1024
MAX_ANSWER_BYTES=6000
MAX_REPLY_BYTES=256*1024


def _fail(message):
    raise buyer.BuyerError(message)


def _text(value,limit):
    if not isinstance(value,str) or not value.strip() or len(value.encode("utf-8"))>limit:
        _fail("bounded nonempty UTF-8 text required")
    return value


def _strict(obj,keys):
    if not isinstance(obj,dict) or set(obj)!=set(keys):
        _fail("unexpected candidate protocol fields")


def _target(target):
    _strict(target,("name","version"))
    for value in target.values():
        _text(value,1200)
        if len(value)>300:
            _fail("bounded product identity required")


def _nonce(value):
    if not isinstance(value,str) or len(value)!=32 or any(c not in "0123456789abcdef" for c in value):
        _fail("invalid request correlation identifier")


def request(target,prompt):
    doc={"protocol":PROTOCOL,"request_id":secrets.token_hex(16),
         "target":dict(target),"input":{"kind":"single_turn_text","text":prompt}}
    validate_request(doc)
    return doc


def validate_request(doc,*,expected_target=None):
    _strict(doc,("protocol","request_id","target","input"))
    if doc["protocol"]!=PROTOCOL:
        _fail("unsupported candidate protocol")
    _nonce(doc["request_id"])
    _target(doc["target"])
    if expected_target is not None and doc["target"]!=expected_target:
        _fail("requested product or version is not served by this adapter")
    _strict(doc["input"],("kind","text"))
    if doc["input"]["kind"]!="single_turn_text":
        _fail("unsupported candidate input kind")
    _text(doc["input"]["text"],MAX_PROMPT_BYTES)
    return doc


def response(request_doc,*,target,status,answer=""):
    """Vendor helper: target must be the actually served version, not a blind echo."""
    validate_request(request_doc,expected_target=target)
    doc={"protocol":PROTOCOL,"request_id":request_doc["request_id"],
         "target":dict(target),"status":status,"answer":answer}
    validate_response(request_doc,doc)
    return doc


def validate_response(request_doc,doc):
    validate_request(request_doc)
    _strict(doc,("protocol","request_id","target","status","answer"))
    _target(doc["target"])
    if (doc["protocol"]!=PROTOCOL or doc["request_id"]!=request_doc["request_id"]
            or doc["target"]!=request_doc["target"]):
        _fail("candidate reply does not match issued request and target version")
    if doc["status"]=="complete":
        _text(doc["answer"],MAX_ANSWER_BYTES)
    elif doc["status"]=="unavailable":
        if doc["answer"]!="":
            _fail("unavailable output cannot carry a scored answer")
    else:
        _fail("unsupported response status")
    return doc


def parse_response(request_doc,raw):
    if not isinstance(raw,bytes) or len(raw)>MAX_REPLY_BYTES:
        _fail("candidate response exceeds size limit")
    return validate_response(request_doc,manual._parse(raw))


def openapi():
    """Language-neutral API description, no network calls or workspace required."""
    def obj(props):
        return {"type":"object","additionalProperties":False,"required":list(props),"properties":props}
    identity=obj({"name":{"type":"string","minLength":1,"maxLength":300},
                  "version":{"type":"string","minLength":1,"maxLength":300}})
    fields={"protocol":{"const":PROTOCOL},"request_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},
            "target":identity}
    req=obj({**fields,"input":obj({"kind":{"const":"single_turn_text"},
                                  "text":{"type":"string","minLength":1,"maxLength":MAX_PROMPT_BYTES}})})
    reply=obj({**fields,"status":{"enum":["complete","unavailable"]},
               "answer":{"type":"string","maxLength":MAX_ANSWER_BYTES}})
    reply["allOf"]=[{"if":{"properties":{"status":{"const":"unavailable"}}},
                     "then":{"properties":{"answer":{"const":""}}},
                     "else":{"properties":{"answer":{"minLength":1}}}}]
    return {"openapi":"3.1.0","info":{"title":"Clinical AI Eval candidate endpoint","version":"1.0.0",
        "description":"Candidate-side evaluation adapter; never a clinical decision or production EHR write API. UTF-8 byte limits and exact request/target binding are enforced by the client."},
        "paths":{"/evaluate":{"post":{"operationId":"evaluateCandidate","requestBody":{"required":True,
           "content":{"application/json":{"schema":req}}},"responses":{"200":{"description":"Correlated answer or unavailable result",
           "content":{"application/json":{"schema":reply}}},"400":{"description":"Malformed request or unsupported target"},
           "401":{"description":"Authentication required"},"403":{"description":"Access denied"},
           "503":{"description":"Candidate unavailable"}}}}}}
