"""Fixed synthetic image-text readiness; not diagnostic imaging validation."""
import base64
import hashlib
import json
import re
import secrets
import struct
import time
import zlib
from pathlib import Path
from . import buyer_plan as plans, candidate_protocol as base, buyer_manual as manual

PROTOCOL="clinical-ai-eval-image-text/1"
SCHEMA="synthetic-image-text/1"
WIDTH=192
HEIGHT=96
MAX_FILE=512*1024
MAX_SECONDS=600
LIMITATIONS=[
    "Fictional monitor displays, not medical image diagnosis.",
    "No patient data, radiographs, pathology or clinical interpretation.",
    "Exact numeric transcription and missing-display handling only.",
    "Declared candidate identity is not independent attestation.",
    "No clinical validation or deployment authorization.",
]
DIGITS={"0":"abcdef","1":"bc","2":"abdeg","3":"abcdg","4":"bcfg",
        "5":"acdfg","6":"acdefg","7":"abc","8":"abcdefg","9":"abcdfg"}
SEGMENTS={"a":(5,2,15,4),"b":(16,5,18,16),"c":(16,20,18,31),
          "d":(5,32,15,34),"e":(2,20,4,31),"f":(2,5,4,16),"g":(5,17,15,19)}


def _chunk(kind,payload):
    return struct.pack(">I",len(payload))+kind+payload+struct.pack(">I",zlib.crc32(kind+payload)&0xffffffff)


def _png(left,right,dark=False):
    palette=bytes([19,25,31,35,44,52,125,242,171] if dark else
                  [244,247,248,220,228,232,18,52,45])
    pixels=bytearray(WIDTH*HEIGHT)
    def rect(x,y,w,h,color):
        for yy in range(y,y+h):
            start=yy*WIDTH+x
            pixels[start:start+w]=bytes([color])*w
    for origin,value in ((8,left),(100,right)):
        rect(origin,13,84,70,1)
        for index,ch in enumerate(value or ""):
            for seg in DIGITS[ch]:
                x1,y1,x2,y2=SEGMENTS[seg]
                rect(origin+8+index*23+x1,30+y1,x2-x1+1,y2-y1+1,2)
    raw=bytearray()
    for y in range(HEIGHT):
        raw.append(0)
        for x in range(0,WIDTH,4):
            a,b,c,d=pixels[y*WIDTH+x:y*WIDTH+x+4]
            raw.append((a<<6)|(b<<4)|(c<<2)|d)
    # Stored DEFLATE block: deterministic bytes independent of zlib version.
    compressed=b"\x78\x01\x01"+struct.pack("<HH",len(raw),65535-len(raw))+raw+struct.pack(">I",zlib.adler32(raw)&0xffffffff)
    return (b"\x89PNG\r\n\x1a\n"+_chunk(b"IHDR",struct.pack(">IIBBBBB",WIDTH,HEIGHT,2,3,0,0,0))+
            _chunk(b"PLTE",palette)+_chunk(b"IDAT",compressed)+_chunk(b"IEND",b""))


def _fixtures():
    return [
        ("baseline_left","072","118","left",False),
        ("target_change","083","118","left",False),
        ("distractor_change","072","096","left",False),
        ("baseline_right","072","118","right",False),
        ("swapped_positions","118","072","right",False),
        ("leading_zero","007","223","left",False),
        ("blank_left",None,"101","left",False),
        ("blank_right","112",None,"right",False),
        ("blank_distractor","091",None,"left",False),
        ("dark_background","064","099","left",True),
        ("dark_right","064","099","right",True),
        ("dark_missing",None,"099","left",True),
    ]


def _input(left,right,side,dark):
    image=_png(left,right,dark)
    return {"kind":"image_text","synthetic":True,
            "text":"Read only the "+side+" panel of the fictional display. Return its exact three digits including leading zeros; if blank, return no_visible_value. Do not infer a clinical meaning.",
            "image":{"media_type":"image/png","sha256":hashlib.sha256(image).hexdigest(),
                     "base64":base64.b64encode(image).decode("ascii")}}


def build(target):
    base._target(target)
    rows=[]
    for label,left,right,side,dark in _fixtures():
        inp=_input(left,right,side,dark)
        value=left if side=="left" else right
        rows.append({"probe":label,"slot":plans._digest([SCHEMA,label,inp])[:32],
                     "input":inp,"expected_status":"complete" if value is not None else "no_visible_value",
                     "expected_value":value})
    doc={"schema":SCHEMA,"target":dict(target),"synthetic":True,"rows":rows,
         "decision_grade":False,"limitations":LIMITATIONS[:]}
    return {**doc,"digest":plans._digest(doc)}


def _verify_plan(plan):
    if not isinstance(plan,dict) or plans._canonical(plan)!=plans._canonical(build(plan["target"])):
        base._fail("fixed synthetic image plan differs from definitions")


def exchange(plan):
    _verify_plan(plan)
    return {"schema":"synthetic-image-text-exchange/1","target":plan["target"],
            "plan_digest":plan["digest"],"synthetic":True,
            "probes":[{"slot":r["slot"],"input":r["input"]} for r in plan["rows"]]}


def template(plan):
    _verify_plan(plan)
    return {"schema":"synthetic-image-text-responses/1","target":plan["target"],
            "plan_digest":plan["digest"],"synthetic":True,
            "responses":[{"slot":r["slot"],"status":"unavailable","value":None} for r in plan["rows"]]}


def request(plan,slot):
    _verify_plan(plan)
    rows=[r for r in plan["rows"] if r["slot"]==slot]
    if len(rows)!=1:
        base._fail("unknown synthetic image slot")
    return {"protocol":PROTOCOL,"request_id":secrets.token_hex(16),
            "target":dict(plan["target"]),"input":json.loads(plans._canonical(rows[0]["input"]))}


def validate_request(doc,*,expected_target=None):
    base._strict(doc,("protocol","request_id","target","input"))
    if doc["protocol"]!=PROTOCOL:
        base._fail("unsupported image protocol")
    base._nonce(doc["request_id"])
    base._target(doc["target"])
    if expected_target is not None and doc["target"]!=expected_target:
        base._fail("target mismatch")
    if not isinstance(doc["input"],dict) or len(plans._canonical(doc["input"]))>128*1024:
        base._fail("invalid image input budget")
    if not any(doc["input"]==_input(left,right,side,dark) for _,left,right,side,dark in _fixtures()):
        base._fail("only exact built-in synthetic image inputs allowed")
    return doc


def _outcome(status,value):
    if status=="complete":
        if not isinstance(value,str) or not re.fullmatch(r"[0-9]{3}",value):
            base._fail("exact three-digit string required")
    elif status not in ("no_visible_value","unavailable") or value is not None:
        base._fail("invalid image extraction outcome")


def validate_response(req,doc):
    validate_request(req)
    base._strict(doc,("protocol","request_id","target","status","value"))
    if any(req[k]!=doc[k] for k in ("protocol","request_id","target")):
        base._fail("image response correlation mismatch")
    _outcome(doc["status"],doc["value"])
    return doc


def response(req,*,target,status,value=None):
    validate_request(req,expected_target=target)
    return validate_response(req,{"protocol":PROTOCOL,"request_id":req["request_id"],
                    "target":dict(target),"status":status,"value":value})


def assess(plan,responses):
    _verify_plan(plan)
    base._strict(responses,("schema","target","plan_digest","synthetic","responses"))
    if (responses["schema"]!="synthetic-image-text-responses/1" or responses["target"]!=plan["target"]
            or responses["plan_digest"]!=plan["digest"] or responses["synthetic"] is not True):
        base._fail("response differs from exact synthetic image plan")
    rows=responses["responses"]
    if not isinstance(rows,list) or len(rows)!=len(plan["rows"]):
        base._fail("complete slot inventory required")
    by={}
    for row in rows:
        base._strict(row,("slot","status","value"))
        if not isinstance(row["slot"],str) or row["slot"] in by:
            base._fail("invalid or duplicate image slot")
        _outcome(row["status"],row["value"])
        by[row["slot"]]=row
    if set(by)!={r["slot"] for r in plan["rows"]}:
        base._fail("image slot inventory mismatch")
    results=[]
    for p in plan["rows"]:
        r=by[p["slot"]]
        status=("unavailable" if r["status"]=="unavailable" else
                "exact_match" if (r["status"],r["value"])==(p["expected_status"],p["expected_value"]) else "mismatch")
        results.append({"probe":p["probe"],"status":status,"reported_status":r["status"],
                        "reported_value":r["value"],"expected_status":p["expected_status"],"expected_value":p["expected_value"]})
    doc={"schema":"synthetic-image-text-report/1","target":plan["target"],"plan_digest":plan["digest"],
         "response_digest":plans._digest(responses),"rows":results,
         "summary":{"planned":len(results),**{k:sum(r["status"]==k for r in results)
                        for k in ("exact_match","mismatch","unavailable")}},
         "decision_grade":False,"deployment_authorized":False,"limitations":LIMITATIONS[:]}
    return {**doc,"digest":plans._digest(doc)}


def package(plan,responses):
    doc={"schema":"synthetic-image-text-evidence/1","plan":plan,"responses":responses,
         "report":assess(plan,responses),"decision_grade":False}
    return {**doc,"digest":plans._digest(doc)}


def verify(doc,expected_digest=None):
    base._strict(doc,("schema","plan","responses","report","decision_grade","digest"))
    if (plans._canonical(doc)!=plans._canonical(package(doc["plan"],doc["responses"])) or
            expected_digest is not None and doc["digest"]!=expected_digest):
        base._fail("image evidence differs from deterministic replay")
    return {"status":"REPLAY_VERIFIED","digest":doc["digest"],"summary":doc["report"]["summary"],
            "expected_digest_matched":expected_digest is not None,"decision_grade":False}


def openapi():
    def obj(props):
        return {"type":"object","required":list(props),"properties":props,"additionalProperties":False}
    txt={"type":"string","minLength":1}
    fields={"protocol":{"const":PROTOCOL},"request_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},
            "target":obj({"name":txt,"version":txt})}
    req=obj({**fields,"input":obj({"kind":{"const":"image_text"},"synthetic":{"const":True},"text":txt,
        "image":obj({"media_type":{"const":"image/png"},"sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
                     "base64":{"type":"string","contentEncoding":"base64","maxLength":131072}})})})
    reply=obj({**fields,"status":{"enum":["complete","no_visible_value","unavailable"]},
                    "value":{"oneOf":[{"type":"string","pattern":"^[0-9]{3}$"},{"type":"null"}]}})
    reply["allOf"]=[{"if":{"properties":{"status":{"const":"complete"}}},
                     "then":{"properties":{"value":{"type":"string","pattern":"^[0-9]{3}$"}}},
                     "else":{"properties":{"value":{"type":"null"}}}}]
    return {"openapi":"3.1.0","info":{"title":"Synthetic image-text candidate endpoint","version":"1.0.0"},
            "paths":{"/evaluate":{"post":{"operationId":"evaluateImageText",
                "requestBody":{"required":True,"content":{"application/json":{"schema":req}}},
                "responses":{"200":{"description":"Correlated reading","content":{"application/json":{"schema":reply}}}}}}}}


def _material(directory,plan_id,connector_id):
    from . import buyer_connector as connector, research_buyer_coverage as research
    bp=research._buyer_plan(directory,plan_id)
    target={k:bp["plan"]["target"][k] for k in ("name","version")}
    cfg=connector._config(directory,connector_id)
    if (cfg.get("protocol")!=PROTOCOL or cfg["vendor_id"]!=bp["vendor_id"] or
            cfg["target_name"]!=target["name"] or cfg["target_version"]!=target["version"]):
        base._fail("exact approved image connector and target required")
    plan=build(target)
    doc={"schema":"synthetic-image-collection/1","plan_id":plan_id,"vendor_id":bp["vendor_id"],
         "target":target,"connector_id":connector_id,"connector_hash":plans._digest(cfg),
         "fixture_digest":plan["digest"],"n_calls":len(plan["rows"]),"decision_grade":False}
    return {**doc,"digest":plans._digest(doc)},cfg,plan


def preview(directory,plan_id,connector_id):
    return _material(directory,plan_id,connector_id)[0]


def collect(directory,plan_id,connector_id,approved_digest,*,confirmed=False):
    from . import adapters
    if confirmed is not True:
        base._fail("explicit candidate-call approval required")
    binding,cfg,plan=_material(directory,plan_id,connector_id)
    if binding["digest"]!=approved_digest:
        base._fail("exact collection approval required")
    headers=adapters.expand_env(cfg["headers"])
    responses=template(plan)
    deadline=time.monotonic()+MAX_SECONDS
    attempted=failures=0
    for row in responses["responses"]:
        if failures>=3 or time.monotonic()>=deadline:
            break
        if _material(directory,plan_id,connector_id)[0]!=binding:
            base._fail("collection binding changed")
        req=request(plan,row["slot"])
        attempted+=1
        try:
            reply=validate_response(req,adapters.post_local_json(cfg["url"],req,headers,
                       timeout=cfg["timeout_seconds"],strict=True))
            row.update(status=reply["status"],value=reply["value"])
        except (OSError,ValueError,RuntimeError,TypeError,KeyError,AttributeError):
            pass
        if row["status"]=="unavailable":
            failures+=1
    if _material(directory,plan_id,connector_id)[0]!=binding:
        base._fail("collection binding changed")
    evidence=package(plan,responses)
    receipt={"schema":"synthetic-image-collection-receipt/1","binding":binding,
             "evidence_digest":evidence["digest"],"attempted":attempted,"unavailable_attempts":failures,
             "not_attempted":binding["n_calls"]-attempted,"decision_grade":False}
    return {"evidence":evidence,"receipt":{**receipt,"digest":plans._digest(receipt)}}


def read(path):
    p=Path(path)
    if p.is_symlink() or not p.is_file():
        base._fail("regular local file required")
    with p.open("rb") as f:
        raw=f.read(MAX_FILE+1)
    if len(raw)>MAX_FILE:
        base._fail("image evidence exceeds size limit")
    return manual._parse(raw)


def cli(args):
    try:
        if args.action=="openapi":
            out=openapi()
        elif args.action=="prepare":
            plan=build({"name":args.target_name,"version":args.target_version})
            out={"plan":plan,"exchange":exchange(plan),"response_template":template(plan)}
        elif args.action=="assess":
            out=package(read(args.plan),read(args.responses))
        elif args.action=="verify":
            out=verify(read(args.evidence),args.expected_digest)
        elif args.action=="preview":
            out=preview(args.path,args.plan_id,args.connector_id)
        else:
            out=collect(args.path,args.plan_id,args.connector_id,args.approved_digest,
                        confirmed=args.confirm_candidate_calls)
        print(json.dumps({"ok":True,"result":out},ensure_ascii=False,allow_nan=False))
    except (OSError,ValueError,RuntimeError,TypeError,KeyError,AttributeError,OverflowError,RecursionError):
        print(json.dumps({"ok":False,"error":"Synthetic image-text operation refused"}))
        raise SystemExit(1)
