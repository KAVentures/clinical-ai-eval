"""Bounded exact-byte diagnostic imaging bridge for approved local research.

The caller stages externally approved assets by hash. No PACS queries, decoding,
de-identification, file discovery, remote URL fetching or clinical write-back.
The candidate response is a self-declared structured prediction, not attestation.
"""
import base64
import binascii
import hashlib
import json
import os
import secrets
import stat
import time
import urllib.request
from pathlib import Path
from . import diagnostic_imaging as d, adapters, buyer_manual

PROTOCOL = "clinical-ai-eval-diagnostic-image/1"
MAX_ASSET = 8 * 1024 * 1024
MAX_WIRE = 12 * 1024 * 1024
MAX_REPLY = 32 * 1024
MAX_CALLS = 64
MAX_TOTAL = 128 * 1024 * 1024
MAX_SECONDS = 900
MEDIA = ("application/dicom", "image/png", "image/jpeg")
LIMITATIONS = [
    "Asset bytes and file-format signatures checked, not DICOM conformance, image integrity beyond digest, or de-identification.",
    "Only a literal loopback candidate endpoint; its downstream behavior and clinical output are not independently verified.",
    "Operator approval is an attestation of authorized local research and staging, not a privacy detector.",
    "No actual PACS connection, clinical deployment or decision authorization.",
]


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _format(raw):
    if len(raw) > 132 and raw[128:132] == b"DICM":
        return "application/dicom"
    if len(raw) > 8 and raw.startswith(bytes.fromhex("89504e470d0a1a0a")):
        return "image/png"
    if len(raw) > 4 and raw.startswith(bytes.fromhex("ffd8ff")) and raw.endswith(bytes.fromhex("ffd9")):
        return "image/jpeg"
    raise ValueError("unsupported imaging file signature")


def _exchange(ex):
    d._keys(ex, "schema plan_digest candidate task_id modality cases")
    d._require(ex["schema"] == "diagnostic-imaging-exchange/1" and d._hex(ex["plan_digest"])
               and d._token(ex["task_id"]) and d._token(ex["modality"]), "invalid candidate exchange")
    d._candidate(ex["candidate"])
    cases = ex["cases"]
    d._require(isinstance(cases, list) and 1 <= len(cases) <= MAX_CALLS, "local collection case limit")
    slots, assets = set(), set()
    for case in cases:
        d._keys(case, "slot asset_sha256")
        slot, asset = case["slot"], case["asset_sha256"]
        d._require(d._hex(slot) and d._hex(asset) and slot not in slots and asset not in assets,
                   "duplicate or invalid case inventory")
        slots.add(slot)
        assets.add(asset)
    return sorted(cases, key=lambda c: c["slot"])


def _file(root, digest):
    """Flat hash-only filename; require regular non-symlink files and exact bytes.

    Root and its parents must be trusted, non-writable by untrusted users.
    O_NOFOLLOW is used where available; file identity and content are rechecked.
    """
    d._require(d._hex(digest), "invalid staged asset digest")
    root = Path(root)
    d._require(root.is_dir() and not root.is_symlink(), "approved staging directory required")
    path = root / digest
    before = path.lstat()
    d._require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= MAX_ASSET,
               "bounded regular staged file required")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(str(path), flags)
    with os.fdopen(fd, "rb") as f:
        opened = os.fstat(f.fileno())
        d._require(stat.S_ISREG(opened.st_mode) and (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                   "staged file changed before open")
        raw = f.read(MAX_ASSET + 1)
    d._require(0 < len(raw) <= MAX_ASSET and _sha(raw) == digest, "staged image digest mismatch")
    return raw, _format(raw)


def _identity(candidate):
    return {"id": candidate["id"], "version": candidate["version"]}


def _target(target):
    d._keys(target, "id version")
    d._require(all(d._token(v) for v in target.values()), "invalid target identity")


def _nonce(value):
    return isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def _outcome(status, score):
    if status == "scored":
        d._require(type(score) is int and 0 <= score <= 10000, "invalid score")
    else:
        d._require(status in ("abstained", "unavailable") and score is None, "invalid outcome")


def request(ex, case, raw, media_type, *, request_id=None):
    d._require(case in _exchange(ex), "case not in approved exchange")
    doc = {"protocol": PROTOCOL, "request_id": secrets.token_hex(16) if request_id is None else request_id,
           "target": _identity(ex["candidate"]), "task_id": ex["task_id"], "modality": ex["modality"],
           "asset": {"sha256": case["asset_sha256"], "media_type": media_type,
                     "base64": base64.b64encode(raw).decode("ascii")}}
    validate_request(doc)
    return doc


def validate_request(doc, *, expected_target=None):
    d._keys(doc, "protocol request_id target task_id modality asset")
    d._require(doc["protocol"] == PROTOCOL and _nonce(doc["request_id"]) and
               d._token(doc["task_id"]) and d._token(doc["modality"]), "invalid imaging protocol request")
    _target(doc["target"])
    d._require(expected_target is None or expected_target == doc["target"], "target version not served")
    a = doc["asset"]
    d._keys(a, "sha256 media_type base64")
    encoded = a["base64"]
    d._require(d._hex(a["sha256"]) and a["media_type"] in MEDIA and isinstance(encoded, str)
               and len(encoded) <= 4 * ((MAX_ASSET + 2) // 3), "invalid asset envelope")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("invalid asset base64") from None
    d._require(0 < len(raw) <= MAX_ASSET and _sha(raw) == a["sha256"] and
               _format(raw) == a["media_type"] and base64.b64encode(raw).decode("ascii") == encoded,
               "asset content or format mismatch")
    return raw


def validate_response(req, doc):
    d._keys(doc, "protocol request_id target asset_sha256 status score_bp")
    _target(doc["target"])
    d._require(doc["protocol"] == PROTOCOL and doc["request_id"] == req["request_id"] and
               doc["target"] == req["target"] and doc["asset_sha256"] == req["asset"]["sha256"],
               "imaging response correlation mismatch")
    _outcome(doc["status"], doc["score_bp"])
    return doc


def response(req, *, target, status, score_bp=None):
    validate_request(req, expected_target=target)
    return validate_response(req, {"protocol": PROTOCOL, "request_id": req["request_id"],
                                  "target": dict(target), "asset_sha256": req["asset"]["sha256"],
                                  "status": status, "score_bp": score_bp})


def _config(cfg):
    d._keys(cfg, "url timeout_seconds")
    adapters.validate_local_endpoint(cfg["url"])
    d._require(type(cfg["timeout_seconds"]) is int and 1 <= cfg["timeout_seconds"] <= 60,
               "invalid local imaging timeout")


def _post(cfg, req):
    _config(cfg)
    raw = json.dumps(req, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
    d._require(len(raw) <= MAX_WIRE, "imaging request exceeds wire budget")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), adapters._RefuseRedirect())
    http = urllib.request.Request(cfg["url"], data=raw, headers={"Content-Type": "application/json"}, method="POST")
    with opener.open(http, timeout=cfg["timeout_seconds"]) as reply:
        d._require(reply.status == 200 and reply.headers.get_content_type() == "application/json",
                   "candidate must return HTTP 200 JSON")
        body = reply.read(MAX_REPLY + 1)
    d._require(len(body) <= MAX_REPLY, "imaging reply exceeds budget")
    return validate_response(req, buyer_manual._parse(body))


def preview(ex, root, cfg):
    """Verify the entire declared inventory without any network access."""
    _config(cfg)
    entries = []
    total = 0
    for case in _exchange(ex):
        raw, media = _file(root, case["asset_sha256"])
        total += len(raw)
        d._require(total <= MAX_TOTAL, "collection total byte budget exceeded")
        entries.append({**case, "media_type": media, "n_bytes": len(raw)})
    return d._sealed({"schema": "diagnostic-image-collection-preview/1",
                      "exchange_digest": d._digest(ex), "endpoint_config": dict(cfg),
                      "entries": entries, "total_bytes": total, "decision_grade": False})


def collect(ex, root, cfg, approved_digest, *, confirmed=False):
    d._require(confirmed is True, "explicit authorized local asset transmission required")
    binding = preview(ex, root, cfg)
    d._require(binding["digest"] == approved_digest, "exact collection approval required")
    deadline = time.monotonic() + MAX_SECONDS
    failures = 0
    rows, events = [], []
    for case in binding["entries"]:
        row = {"slot": case["slot"], "status": "unavailable", "score_bp": None}
        event = {"slot": case["slot"], "asset_sha256": case["asset_sha256"], "request_id": None,
                 "transport_status": "not_attempted"}
        if failures < 3 and time.monotonic() < deadline:
            # Re-read and verify the same bytes immediately before transmission.
            # A local asset change is a hard refusal, not a silent failed case.
            raw, media = _file(root, case["asset_sha256"])
            d._require(len(raw) == case["n_bytes"] and media == case["media_type"], "staged asset changed")
            req = request(ex, {"slot": case["slot"], "asset_sha256": case["asset_sha256"]}, raw, media)
            event.update(request_id=req["request_id"], transport_status="failed")
            try:
                reply = _post(cfg, req)
                row.update(status=reply["status"], score_bp=reply["score_bp"])
                event["transport_status"] = "correlated"
            except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, RecursionError):
                failures += 1
        rows.append(row)
        events.append(event)
    predictions = {"schema": "diagnostic-imaging-predictions/1", "plan_digest": ex["plan_digest"],
                   "candidate": ex["candidate"], "predictions": rows}
    return d._sealed({"schema": "diagnostic-image-collection/1", "binding": binding,
                      "predictions": predictions, "events": events, "decision_grade": False,
                      "limitations": LIMITATIONS[:]})


def verify(ex, receipt, expected_digest=None):
    """Replay inventory and status consistency, not proof of network execution."""
    cases = _exchange(ex)
    d._keys(receipt, "schema binding predictions events decision_grade limitations digest")
    d._require(receipt["schema"] == "diagnostic-image-collection/1" and receipt["decision_grade"] is False
               and receipt["limitations"] == LIMITATIONS and d._digest({k:v for k,v in receipt.items() if k!="digest"}) == receipt["digest"]
               and (expected_digest is None or expected_digest == receipt["digest"]), "collection digest mismatch")
    b = receipt["binding"]
    d._keys(b, "schema exchange_digest endpoint_config entries total_bytes decision_grade digest")
    _config(b["endpoint_config"])
    d._require(b["schema"] == "diagnostic-image-collection-preview/1" and b["decision_grade"] is False
               and b["exchange_digest"] == d._digest(ex) and d._digest({k:v for k,v in b.items() if k!="digest"}) == b["digest"],
               "collection binding mismatch")
    d._require(isinstance(b["entries"],list) and len(b["entries"])==len(cases), "invalid staged inventory")
    total = 0
    for entry, case in zip(b["entries"],cases):
        d._keys(entry,"slot asset_sha256 media_type n_bytes")
        d._require({k:entry[k] for k in case}==case and entry["media_type"] in MEDIA
                   and type(entry["n_bytes"]) is int and 0<entry["n_bytes"]<=MAX_ASSET, "invalid staged metadata")
        total += entry["n_bytes"]
    d._require(type(b["total_bytes"]) is int and total==b["total_bytes"] and total<=MAX_TOTAL,"invalid byte inventory")
    pred = receipt["predictions"]
    d._keys(pred,"schema plan_digest candidate predictions")
    d._require(pred["schema"]=="diagnostic-imaging-predictions/1" and pred["plan_digest"]==ex["plan_digest"]
               and pred["candidate"]==ex["candidate"],"prediction binding mismatch")
    rows,events=pred["predictions"],receipt["events"]
    d._require(isinstance(rows,list) and isinstance(events,list) and len(rows)==len(events)==len(cases),"invalid outcome inventory")
    used=set()
    failed=0
    stopped=False
    for case,row,event in zip(cases,rows,events):
        d._keys(row,"slot status score_bp")
        d._keys(event,"slot asset_sha256 request_id transport_status")
        d._require(row["slot"]==event["slot"]==case["slot"] and event["asset_sha256"]==case["asset_sha256"],"outcome slot mismatch")
        _outcome(row["status"],row["score_bp"])
        state=event["transport_status"]
        nonce=event["request_id"]
        if state=="not_attempted":
            d._require(nonce is None and row["status"]=="unavailable","invalid unattempted outcome")
            stopped=True
        else:
            d._require(not stopped and failed<3 and state in ("failed","correlated") and _nonce(nonce) and nonce not in used,
                       "invalid transport attempt inventory")
            used.add(nonce)
            if state=="failed":
                d._require(row["status"]=="unavailable","failed attempt cannot score")
                failed+=1
    return {"status":"REPLAY_VERIFIED","digest":receipt["digest"],"expected_digest_matched":expected_digest is not None,
            "n_attempted":len(used),"n_transport_failed":failed,"decision_grade":False}


def openapi():
    def obj(props):
        return {"type":"object","required":list(props),"properties":props,"additionalProperties":False}
    token={"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$"}
    sha={"type":"string","pattern":"^[0-9a-f]{64}$"}
    common={"protocol":{"const":PROTOCOL},"request_id":{"type":"string","pattern":"^[0-9a-f]{32}$"},
            "target":obj({"id":token,"version":token})}
    req=obj({**common,"task_id":token,"modality":token,"asset":obj({"sha256":sha,"media_type":{"enum":list(MEDIA)},
              "base64":{"type":"string","contentEncoding":"base64","maxLength":4*((MAX_ASSET+2)//3)}})})
    reply=obj({**common,"asset_sha256":sha,"status":{"enum":["scored","abstained","unavailable"]},
               "score_bp":{"oneOf":[{"type":"integer","minimum":0,"maximum":10000},{"type":"null"}]}})
    reply["allOf"]=[{"if":{"properties":{"status":{"const":"scored"}}},"then":{"properties":{"score_bp":{"type":"integer"}}},
                     "else":{"properties":{"score_bp":{"type":"null"}}}}]
    return {"openapi":"3.1.0","info":{"title":"Local diagnostic image candidate contract","version":"1.0.0"},
            "paths":{"/evaluate":{"post":{"operationId":"evaluateDiagnosticImage","requestBody":{"required":True,
                     "content":{"application/json":{"schema":req}}},"responses":{"200":{"description":"Correlated structured prediction",
                     "content":{"application/json":{"schema":reply}}}}}}}}


def configure(parser):
    sub=parser.add_subparsers(dest="transport_action",required=True)
    sub.add_parser("openapi")
    for action in ("preview","collect"):
        p=sub.add_parser(action)
        p.add_argument("exchange")
        p.add_argument("staging")
        p.add_argument("--url",required=True)
        p.add_argument("--timeout",type=int,default=30)
        if action=="collect":
            p.add_argument("--approved-digest",required=True)
            p.add_argument("--confirm-local-asset-transmission",action="store_true")
    v=sub.add_parser("verify")
    v.add_argument("exchange")
    v.add_argument("receipt")
    v.add_argument("--expected-digest")
    parser.set_defaults(func=cli)


def cli(args):
    try:
        action=args.transport_action
        if action=="openapi":
            result=openapi()
        elif action=="verify":
            result=verify(d._read(args.exchange),d._read(args.receipt),args.expected_digest)
        else:
            ex=d._read(args.exchange)
            cfg={"url":args.url,"timeout_seconds":args.timeout}
            if action=="preview":
                result=preview(ex,args.staging,cfg)
            else:
                result=collect(ex,args.staging,cfg,args.approved_digest,confirmed=args.confirm_local_asset_transmission)
        print(json.dumps(result,ensure_ascii=True,allow_nan=False,indent=2))
    except (ValueError,TypeError,KeyError,OSError,RuntimeError,OverflowError,RecursionError):
        raise SystemExit("Local diagnostic image transport refused; verify approved staging and candidate configuration.")
