"""Shared, bounded structured candidate protocol for fixed synthetic EHR probes.

Only built-in FHIR and HL7 fixtures from a current institution-owned buyer
plan may leave the workspace. No production EHR access, arbitrary messages,
clinical data or write-back. This is extraction readiness, not conformance.
"""
import json
import secrets
import time
from . import (adapters, buyer_plan, buyer_agent as buyer, candidate_protocol as base,
               fhir_readiness as fhir, hl7_readiness as hl7, research_buyer_coverage as bridge)

PROTOCOL="clinical-ai-eval-interoperability/1"
MODULES={"fhir":fhir,"hl7":hl7}
MAX_SECONDS=600


def _module(kind):
    if not isinstance(kind,str) or kind not in MODULES:
        base._fail("unsupported synthetic interoperability protocol")
    return MODULES[kind]


def request(plan,kind,slot):
    mod=_module(kind)
    exchange=mod.exchange(plan)
    rows=[r for r in exchange["probes"] if r["slot"]==slot]
    if len(rows)!=1:
        base._fail("unknown fixed synthetic probe")
    row=rows[0]
    return {"protocol":PROTOCOL,"request_id":secrets.token_hex(16),
            "target":exchange["target"],"input":{"kind":kind,"synthetic":True,
            "instruction":exchange["instruction"],**{k:v for k,v in row.items() if k!="slot"}}}


def validate_request(req,*,expected_target=None):
    base._strict(req,("protocol","request_id","target","input"))
    if req["protocol"]!=PROTOCOL:
        base._fail("unsupported interoperability protocol")
    base._nonce(req["request_id"])
    base._target(req["target"])
    if expected_target is not None and req["target"]!=expected_target:
        base._fail("target mismatch")
    inp=req["input"]
    if not isinstance(inp,dict):
        base._fail("invalid synthetic probe")
    kind=inp.get("kind")
    mod=_module(kind)
    plan=mod.build(req["target"])
    ex=mod.exchange(plan)
    allowed=[{"kind":kind,"synthetic":True,"instruction":ex["instruction"],
              **{k:v for k,v in row.items() if k!="slot"}} for row in ex["probes"]]
    if buyer_plan._canonical(inp) not in [buyer_plan._canonical(x) for x in allowed]:
        base._fail("only exact built-in synthetic probes allowed")
    return req


def validate_response(req,doc):
    validate_request(req)
    base._strict(doc,("protocol","request_id","target","status","facts"))
    if any(doc[k]!=req[k] for k in ("protocol","request_id","target")):
        base._fail("response correlation mismatch")
    if doc["status"] not in ("complete","no_eligible_observations","unavailable"):
        base._fail("unknown structured extraction status")
    facts=_module(req["input"]["kind"])._facts(doc["facts"])
    if doc["status"]!="complete" and facts:
        base._fail("non-complete response cannot contain observations")
    return doc


def response(req,*,target,status,facts=None):
    validate_request(req,expected_target=target)
    return validate_response(req,{"protocol":PROTOCOL,"request_id":req["request_id"],
                     "target":dict(target),"status":status,"facts":[] if facts is None else facts})


def openapi():
    def obj(props):
        return {"type":"object","required":list(props),"properties":props,"additionalProperties":False}
    txt={"type":"string","minLength":1}
    fields={"protocol":{"const":PROTOCOL},"request_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},
            "target":obj({"name":txt,"version":txt})}
    common={"synthetic":{"const":True},"instruction":txt}
    inputs={"oneOf":[obj({**common,"kind":{"const":"fhir"},"patient_reference":txt,"bundle":{"type":"object"}}),
                     obj({**common,"kind":{"const":"hl7"},"patient_id":txt,"messages":{"type":"array","items":txt}})]}
    fact=obj({**{k:{"type":"string","minLength":1,"maxLength":200} for k in
                   ("source_id","code_system","code","unit_system","unit_code")},
              "value":{"type":"number","minimum":-1e12,"maximum":1e12}})
    req=obj({**fields,"input":inputs})
    reply=obj({**fields,"status":{"enum":["complete","no_eligible_observations","unavailable"]},
               "facts":{"type":"array","maxItems":32,"items":fact}})
    reply["allOf"]=[{"if":{"properties":{"status":{"enum":["no_eligible_observations","unavailable"]}}},
                     "then":{"properties":{"facts":{"maxItems":0}}}}]
    return {"openapi":"3.1.0","info":{"title":"Clinical AI Eval synthetic interoperability adapter","version":"1.0.0",
            "description":"Fixed synthetic probes only; exact fixture membership enforced at runtime."},
            "paths":{"/evaluate":{"post":{"operationId":"evaluateSyntheticInteroperability",
              "requestBody":{"required":True,"content":{"application/json":{"schema":req}}},
              "responses":{"200":{"description":"Correlated extraction","content":{"application/json":{"schema":reply}}},
                           "400":{"description":"Invalid request"}}}}}}


def _material(directory,plan_id,connector_id,kind):
    from . import buyer_connector as connector
    mod=_module(kind)
    bp=bridge._buyer_plan(directory,plan_id)
    target={k:bp["plan"]["target"][k] for k in ("name","version")}
    cfg=connector._config(directory,connector_id)
    if (cfg.get("protocol")!=PROTOCOL or cfg["vendor_id"]!=bp["vendor_id"]
            or cfg["target_name"]!=target["name"] or cfg["target_version"]!=target["version"]):
        base._fail("connector must match exact approved interoperability target")
    plan=mod.build(target)
    payload={"schema":"synthetic-interoperability-collection/1","kind":kind,"plan_id":plan_id,
             "vendor_id":bp["vendor_id"],"target":target,"fixture_digest":plan["digest"],
             "connector_id":connector_id,"connector_hash":buyer_plan._digest(cfg),
             "n_calls":len(plan["rows"]),"synthetic":True,"decision_grade":False}
    return {**payload,"digest":buyer_plan._digest(payload)},cfg,plan


def preview(directory,plan_id,connector_id,kind):
    return _material(directory,plan_id,connector_id,kind)[0]


def collect(directory,plan_id,connector_id,kind,approved_digest,*,confirmed=False):
    if confirmed is not True:
        base._fail("explicit candidate-call approval required")
    binding,cfg,plan=_material(directory,plan_id,connector_id,kind)
    if approved_digest!=binding["digest"]:
        base._fail("exact current binding approval required")
    mod=_module(kind)
    headers=adapters.expand_env(cfg["headers"])
    # Keep the response document within the existing institutional import limit.
    responses=mod.template(plan)
    deadline=time.monotonic()+MAX_SECONDS
    attempted=failures=0
    stopped=False
    for row in responses["responses"]:
        if stopped or time.monotonic()>=deadline:
            continue
        if _material(directory,plan_id,connector_id,kind)[0]!=binding:
            base._fail("collection binding changed")
        req=request(plan,kind,row["slot"])
        attempted+=1
        try:
            reply=adapters.post_local_json(cfg["url"],req,headers,timeout=cfg["timeout_seconds"],strict=True)
            reply=validate_response(req,reply)
            row.update(status=reply["status"],facts=reply["facts"])
            if len(buyer_plan._canonical(responses))>mod.MAX_BYTES:
                raise adapters.AdapterError("collection response budget exceeded")
            if row["status"]!="unavailable":
                continue
        except (OSError,ValueError,RuntimeError,TypeError,KeyError,AttributeError):
            row.update(status="unavailable",facts=[])
        failures+=1
        if failures>=3:
            stopped=True
    if _material(directory,plan_id,connector_id,kind)[0]!=binding:
        base._fail("collection binding changed")
    evidence=mod.package(plan,responses)
    receipt={"schema":"synthetic-interoperability-receipt/1","binding":binding,
             "evidence_digest":evidence["digest"],"attempted":attempted,
             "unavailable_attempts":failures,"not_attempted":binding["n_calls"]-attempted,
             "decision_grade":False}
    return {"evidence":evidence,"receipt":{**receipt,"digest":buyer_plan._digest(receipt)}}


def cli(args):
    try:
        if args.action=="openapi":
            result=openapi()
        elif args.action=="preview":
            result=preview(args.path,args.plan_id,args.connector_id,args.kind)
        else:
            result=collect(args.path,args.plan_id,args.connector_id,args.kind,args.approved_digest,
                           confirmed=args.confirm_candidate_calls)
        print(json.dumps({"ok":True,"result":result},ensure_ascii=False,allow_nan=False))
    except (OSError,ValueError,RuntimeError,TypeError,KeyError,AttributeError,OverflowError):
        print(json.dumps({"ok":False,"error":"Synthetic interoperability operation refused"}))
        raise SystemExit(1)
