"""Operator-controlled collection from approved local single-turn products.

No browser-triggered network access. A connector must be staged by trusted IT
and bound to a candidate, version, exact prompt exchange, and configuration
digest before explicit execution. The initial transport is literal loopback
only, without proxies or redirects. The existing manual exchange remains the
canonical scoring handoff; a transport receipt is not clinical validation.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import adapters, buyer_agent as buyer, buyer_manual as manual, buyer_plan, candidate_protocol, citation_trace_protocol, interoperability_protocol

MAX_CONNECTOR_BYTES = 65536
MAX_REPLY_BYTES = 256 * 1024
MAX_ANSWER_BYTES = 6000
MAX_TOTAL_ANSWER_BYTES = 2 * 1024 * 1024
MAX_CALLS = 1000
MAX_COLLECTIONS = 200
MAX_RUNTIME_SECONDS = 900
FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
PATH = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")
ENV = re.compile(r"(?:Bearer )?\$\{[A-Za-z_][A-Za-z0-9_]*\}")


def _root(directory):
    buyer.load(directory)
    base = manual._directory(directory, "candidate-collections")
    base.mkdir(mode=0o700, exist_ok=True)
    return base


@contextmanager
def _db(directory):
    path = _root(directory) / "collections.sqlite3"
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise buyer.BuyerError("invalid collection database")
    import os
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS collections(
            id TEXT PRIMARY KEY, binding TEXT NOT NULL, binding_hash TEXT NOT NULL,
            state TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT, finished_at TEXT, receipt TEXT NOT NULL DEFAULT '{}')""")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _endpoint(url):
    if not isinstance(url, str) or len(url) > 512 or not url.isascii() or any(c.isspace() for c in url):
        raise buyer.BuyerError("invalid connector URL")
    try:
        p = urllib.parse.urlsplit(url)
        if (p.scheme != "http" or p.hostname not in ("127.0.0.1","::1")
                or p.port is None or p.username is not None or p.password is not None
                or p.query or p.fragment or not p.path.startswith("/")
                or not re.fullmatch(r"/[A-Za-z0-9_./-]*",p.path)
                or any(s in (".","..") for s in p.path.split("/"))
                or p.netloc not in ("127.0.0.1:"+str(p.port),"[::1]:"+str(p.port))):
            raise ValueError()
    except ValueError as exc:
        raise buyer.BuyerError("only a literal loopback HTTP endpoint with explicit port is supported") from exc
    return url


def _config(directory, connector_id):
    manual._identifier(connector_id)
    base = manual._directory(directory, "approved-connectors")
    path = base / (connector_id + ".json")
    if path.is_symlink() or not path.is_file():
        raise buyer.BuyerError("select an administrator-staged connector")
    with path.open("rb") as f:
        raw = f.read(MAX_CONNECTOR_BYTES+1)
    if len(raw)>MAX_CONNECTOR_BYTES:
        raise buyer.BuyerError("connector configuration exceeds limit")
    config=manual._parse(raw)
    common={"vendor_id","target_name","target_version","url","headers","timeout_seconds"}
    if not isinstance(config,dict) or set(config) not in (common|{"prompt_field","answer_path"},common|{"protocol"}):
        raise buyer.BuyerError("invalid connector configuration fields")
    if "protocol" in config and config["protocol"] not in (candidate_protocol.PROTOCOL,citation_trace_protocol.PROTOCOL,interoperability_protocol.PROTOCOL):
        raise buyer.BuyerError("unsupported candidate protocol")
    manual._identifier(config["vendor_id"])
    for k in ("target_name","target_version"):
        if not isinstance(config[k],str) or not config[k].strip() or len(config[k])>300:
            raise buyer.BuyerError("explicit target name and version required")
    _endpoint(config["url"])
    if "protocol" not in config:
        if not isinstance(config["prompt_field"],str) or not FIELD.fullmatch(config["prompt_field"]):
            raise buyer.BuyerError("invalid prompt field")
        if not isinstance(config["answer_path"],str) or len(config["answer_path"])>200 or not PATH.fullmatch(config["answer_path"]):
            raise buyer.BuyerError("invalid answer path")
    headers=config["headers"]
    if not isinstance(headers,dict) or set(headers)-{"Authorization","X-API-Key"}:
        raise buyer.BuyerError("only approved credential headers accepted")
    if any(not isinstance(v,str) or not ENV.fullmatch(v) for v in headers.values()):
        raise buyer.BuyerError("credential headers must use environment references")
    if type(config["timeout_seconds"]) is not int or not 1<=config["timeout_seconds"]<=60:
        raise buyer.BuyerError("timeout must be 1–60 seconds")
    return config


def connectors(directory):
    buyer.load(directory)
    base=manual._directory(directory,"approved-connectors")
    if not base.is_dir():
        return []
    result=[]
    for path in sorted(base.glob("*.json"))[:100]:
        if path.is_symlink():
            continue
        try:
            cfg=_config(directory,path.stem)
        except (OSError,ValueError,RuntimeError,KeyError,TypeError):
            continue
        result.append({"connector_id":path.stem,"vendor_id":cfg["vendor_id"],
                       "target_name":cfg["target_name"],"target_version":cfg["target_version"],
                       "scope":"literal_loopback_only",
                       "protocol":cfg.get("protocol","custom-json")})
    return result


def _binding(directory, exchange_id, connector_id):
    record=manual._load(directory,exchange_id)
    cfg=_config(directory,connector_id)
    if cfg.get("protocol") in (citation_trace_protocol.PROTOCOL,interoperability_protocol.PROTOCOL):
        raise buyer.BuyerError("structured connector requires its dedicated assessment workflow")
    if (record["vendor_id"]!=cfg["vendor_id"] or record["target"]["name"]!=cfg["target_name"]
            or record["target"]["version"]!=cfg["target_version"]):
        raise buyer.BuyerError("connector does not match the planned candidate and version")
    n=len(record["slots"])
    if not 1<=n<=MAX_CALLS:
        raise buyer.BuyerError("candidate call limit exceeded")
    return {"schema":"local-candidate-collection/1","exchange_id":exchange_id,
            "connector_id":connector_id,"connector_hash":buyer_plan._digest(cfg),
            "vendor_id":cfg["vendor_id"],"target_name":cfg["target_name"],
            "target_version":cfg["target_version"],"n_expected":n,
            "transport":"literal_loopback_http","decision_grade":False},cfg,record


def queue(directory, exchange_id, connector_id):
    binding,_,_=_binding(directory,exchange_id,connector_id)
    digest=buyer_plan._digest(binding)
    identifier="collect-"+uuid.uuid4().hex
    with _db(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT count(*) FROM collections").fetchone()[0]>=MAX_COLLECTIONS:
            raise buyer.BuyerError("collection limit reached")
        conn.execute("INSERT INTO collections(id,binding,binding_hash,state) VALUES(?,?,?,?)",
                     (identifier,json.dumps(binding),digest,"queued"))
    return status(directory,identifier)


def _row(conn,identifier):
    manual._identifier(identifier)
    row=conn.execute("SELECT * FROM collections WHERE id=?",(identifier,)).fetchone()
    if not row:
        raise buyer.BuyerError("collection not found")
    value=dict(row)
    value["binding"]=json.loads(value["binding"])
    value["receipt"]=json.loads(value["receipt"])
    if buyer_plan._digest(value["binding"])!=value["binding_hash"]:
        raise buyer.BuyerError("collection binding integrity mismatch")
    return value


def status(directory,identifier):
    with _db(directory) as conn:
        row=_row(conn,identifier)
    if row["state"] in ("complete_unscored","incomplete"):
        try:
            receipt=row["receipt"]
            if (receipt["binding_hash"]!=row["binding_hash"]
                    or buyer_plan._digest({k:v for k,v in receipt.items() if k!="receipt_hash"})!=receipt["receipt_hash"]):
                raise buyer.BuyerError("collection receipt changed")
            sub=manual.submission_status(directory,receipt["submission_id"])
            if sub["exchange_id"]!=row["binding"]["exchange_id"] or sub["n_provided"]!=receipt["n_provided"] or sub["status"]!=row["state"]:
                raise buyer.BuyerError("collection response mismatch")
        except (OSError,ValueError,RuntimeError,KeyError,TypeError):
            row["state"]="invalid"
            row["receipt"]={"reason":"receipt, submission or source exchange no longer verifies"}
    return row


def list_collections(directory):
    with _db(directory) as conn:
        ids=[r["id"] for r in conn.execute("SELECT id FROM collections ORDER BY created_at DESC,id LIMIT ?",(MAX_COLLECTIONS,))]
    return [status(directory,i) for i in ids]


def cancel(directory,identifier):
    with _db(directory) as conn:
        _row(conn,identifier)
        if conn.execute("UPDATE collections SET state='cancelled',finished_at=CURRENT_TIMESTAMP WHERE id=? AND state='queued'",(identifier,)).rowcount!=1:
            raise buyer.BuyerError("only queued collections can be cancelled")
    return status(directory,identifier)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise adapters.AdapterError("candidate redirect refused")


def _call(cfg,prompt,headers):
    if cfg.get("protocol") not in (None,candidate_protocol.PROTOCOL):
        raise adapters.AdapterError("single-turn text connector required")
    # No proxy, DNS lookup, redirects, or hidden case metadata.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
    standard=cfg.get("protocol")==candidate_protocol.PROTOCOL
    body=(candidate_protocol.request({"name":cfg["target_name"],"version":cfg["target_version"]},prompt)
          if standard else {cfg["prompt_field"]:prompt})
    req=urllib.request.Request(_endpoint(cfg["url"]),method="POST",
            data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode("utf-8"),
            headers={"Content-Type":"application/json",**headers})
    with opener.open(req,timeout=cfg["timeout_seconds"]) as response:
        if standard and (response.status!=200 or response.headers.get_content_type()!="application/json"):
            raise adapters.AdapterError("candidate protocol requires HTTP 200 JSON response")
        raw=response.read(MAX_REPLY_BYTES+1)
    if len(raw)>MAX_REPLY_BYTES:
        raise adapters.AdapterError("candidate response too large")
    if standard:
        parsed=candidate_protocol.parse_response(body,raw)
        if parsed["status"]!="complete":
            raise adapters.AdapterError("candidate explicitly unavailable")
        value=parsed["answer"]
    else:
        value=adapters.resolve_answer_path(json.loads(raw.decode("utf-8")),cfg["answer_path"])
    if not isinstance(value,str) or not value.strip() or len(value.encode("utf-8"))>MAX_ANSWER_BYTES:
        raise adapters.AdapterError("empty or oversized candidate response")
    return value


def call_citation_trace(cfg,prompt,headers):
    if cfg.get("protocol")!=citation_trace_protocol.PROTOCOL:
        raise adapters.AdapterError("structured citation protocol required")
    body=citation_trace_protocol.request({"name":cfg["target_name"],"version":cfg["target_version"]},prompt)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
    req=urllib.request.Request(_endpoint(cfg["url"]),method="POST",
        data=buyer_plan._canonical(body),headers={"Content-Type":"application/json",**headers})
    with opener.open(req,timeout=cfg["timeout_seconds"]) as response:
        if response.status!=200 or response.headers.get_content_type()!="application/json":
            raise adapters.AdapterError("citation trace requires HTTP 200 JSON")
        raw=response.read(MAX_REPLY_BYTES+1)
    parsed=citation_trace_protocol.parse_response(body,raw)
    if parsed["status"]!="complete":
        raise adapters.AdapterError("candidate explicitly unavailable")
    return parsed["trace"]


def execute(directory,identifier,approved_binding_hash,operator):
    """No automatic retries; failed calls become explicit missing responses."""
    if not isinstance(operator,str) or not operator.strip() or len(operator)>200:
        raise buyer.BuyerError("record executing operator")
    with _db(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row=_row(conn,identifier)
        if row["state"]!="queued" or approved_binding_hash!=row["binding_hash"]:
            raise buyer.BuyerError("queued job and exact binding approval required")
        binding,cfg,record=_binding(directory,row["binding"]["exchange_id"],row["binding"]["connector_id"])
        if binding!=row["binding"]:
            raise buyer.BuyerError("connector or planned exchange changed")
        try:
            headers=adapters.expand_env(cfg["headers"])
            if any(not isinstance(v,str) or "\r" in v or "\n" in v or len(v)>8192 for v in headers.values()):
                raise adapters.AdapterError("invalid credential header")
        except adapters.AdapterError as exc:
            raise buyer.BuyerError("approved credential reference unavailable or invalid") from exc
        conn.execute("UPDATE collections SET state='running',started_at=CURRENT_TIMESTAMP WHERE id=?",(identifier,))
    state="failed"
    receipt={"reason":"collection failed before verified handoff"}
    try:
        doc=manual._template(record)
        answers=doc["submission"]["answers"]
        used=0
        failed=0
        attempted=0
        stopped=False
        deadline=time.monotonic()+MAX_RUNTIME_SECONDS
        for item,answer in zip(doc["request"]["prompts"],answers):
            if time.monotonic()>=deadline:
                stopped=True
            if not stopped:
                attempted+=1
                try:
                    text=_call(cfg,item["prompt"],headers)
                    if used+len(text.encode("utf-8"))>MAX_TOTAL_ANSWER_BYTES:
                        stopped=True
                        raise adapters.AdapterError("collection response budget exceeded")
                    used+=len(text.encode("utf-8"))
                    answer.update(status="provided",answer=text)
                    continue
                except (OSError,ValueError,RuntimeError,KeyError,TypeError):
                    failed+=1
                    # Do not continue flooding an unavailable product.
                    if failed>=3:
                        stopped=True
            answer.update(status="not_available",answer="")
        doc["submission"]["operator"]=operator.strip()
        doc["submission"]["tested_product_version"]=cfg["target_version"]
        # Re-validate source before final import. Never persist credentials,
        # endpoint response bodies on errors, or automatic safety labels.
        latest,_,_=_binding(directory,binding["exchange_id"],binding["connector_id"])
        if latest!=binding:
            raise buyer.BuyerError("collection inputs changed during execution")
        result=manual.import_response(directory,buyer_plan._canonical(doc))
        state=result["status"]
        receipt={"schema":"local-candidate-receipt/1","binding_hash":row["binding_hash"],
                 "submission_id":result["submission_id"],"operator":operator.strip(),
                 "n_expected":result["n_expected"],"n_provided":result["n_provided"],
                 "n_attempted":attempted,"n_failed":failed,
                 "source":"operator_authorized_local_http_collection",
                 "decision_grade":False}
        receipt["receipt_hash"]=buyer_plan._digest(receipt)
    except (OSError,ValueError,RuntimeError,KeyError,TypeError):
        state="failed"
        receipt={"reason":"collection inputs, transport or handoff failed"}
    finally:
        with _db(directory) as conn:
            conn.execute("UPDATE collections SET state=?,receipt=?,finished_at=CURRENT_TIMESTAMP WHERE id=? AND state='running'",
                         (state,json.dumps(receipt),identifier))
    return status(directory,identifier)
