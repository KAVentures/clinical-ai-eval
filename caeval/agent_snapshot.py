"""Explicitly exported, aggregate-only metadata for local AI agent clients.

The MCP process is intentionally separate from the clinical workspace. It opens
one administrator-approved snapshot and has no tools for files, clinical text,
credentials, mutations or executing assessments. Export does not imply approval
to send even aggregate metadata to an external model.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import buyer_agent as buyer, buyer_jobs, buyer_plan, buyer_research, capabilities

MAX_BYTES = 1024 * 1024
STATES = {"queued","running","completed","failed","cancelled","invalid"}
MODES = {"demonstration","internal_regression","calibrated_assessment",
         "procurement_comparison","surveillance"}
CLAIMS = {"none","demonstration","internal_regression","calibrated_assessment",
          "procurement_comparison","surveillance"}
MATURITY = {"experimental","calibrated","validated"}
VERDICTS = {"VALID","INVALID","INCOMPLETE"}


def _count(value):
    if type(value) is not int or not 0 <= value <= 10000000:
        raise buyer.BuyerError("invalid aggregate count")
    return value


def _enum(value, allowed, default="unknown"):
    return value if isinstance(value,str) and value in allowed else default


def build(directory):
    state=buyer.status(directory)
    families=capabilities.table()
    known={r["family_id"] for r in families if "error" not in r}
    caps=[]
    for r in families:
        if r["family_id"] not in known:
            continue
        caps.append({"family_id":r["family_id"],"runnable":bool(r["selectable"] and r["executor"]),
                     "maturity":_enum(r["maturity"],MATURITY),
                     "conformance_ceiling":_enum(r["conformance_ceiling"],{"L0","L1","L2"})})
    jobs=[]
    # Do not export actual job identifiers, target names, version strings,
    # free-text diagnostics, dates, paths, model names or reviewer identities.
    for i,stored in enumerate(buyer_jobs.list_jobs(directory),1):
        row=buyer_jobs.status(directory,stored["id"])
        status=_enum(row["status"],STATES)
        packages=[]
        if status=="completed":
            for p in row["result"]["packages"]:
                if p["family_id"] in known:
                    packages.append({"family_id":p["family_id"],
                                     "claim":_enum(p["claim"],CLAIMS),
                                     "review_items":_count(p["review_count"]),
                                     "response_count":_count(p["n_responses"])})
        jobs.append({"snapshot_job_number":i,"status":status,
                     "mode":_enum(row["mode"],MODES),"packages":packages})
    runs=[]
    for row in buyer_research.overview(directory)["runs"]:
        runs.append({"family_id":row.get("family_id") if row.get("family_id") in known else "unknown",
                     "verdict":_enum(row.get("verdict"),VERDICTS),
                     "claim":_enum(row.get("recorded_claim"),CLAIMS)})
    vendors=state["vendors"]
    evidence={"candidate_count":len(vendors),
              "missing_intake_count":len(state["missing_intake"]),
              "missing_document_kind_count":sum(len(v["missing_evidence_kinds"]) for v in vendors),
              "intact_document_count":sum(d["integrity"]=="intact" for v in vendors for d in v["documents"]),
              "other_document_count":sum(d["integrity"]!="intact" for v in vendors for d in v["documents"])}
    payload={"schema":"clinical-ai-eval-agent-summary/1","capabilities":caps,
             "candidate_evaluation_jobs":jobs,"research_runs":runs,
             "procurement_inventory":evidence,"decision_grade":False,
             "scope":"aggregate_snapshot_only"}
    return {"payload":payload,"digest":buyer_plan._digest(payload)}


def export(directory, output):
    envelope=build(directory)
    raw=buyer_plan._canonical(envelope)
    if len(raw)>MAX_BYTES:
        raise buyer.BuyerError("agent snapshot exceeds limit")
    # Explicit file chosen by the trusted operator; never overwrite.
    import os
    p=Path(output).expanduser()
    fd=os.open(str(p),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"wb") as handle:
        handle.write(raw)
    return {"digest":envelope["digest"],"decision_grade":False}


def _exact(obj, keys):
    if not isinstance(obj,dict) or set(obj)!=set(keys):
        raise buyer.BuyerError("invalid snapshot fields")


def _known_families():
    # Public scientific identifiers are derived from the installed definitions,
    # not arbitrary strings stored in the export.
    return {r["family_id"] for r in capabilities.table() if "error" not in r}


def _validate(payload):
    _exact(payload,("schema","capabilities","candidate_evaluation_jobs","research_runs",
                    "procurement_inventory","decision_grade","scope"))
    if (payload["schema"]!="clinical-ai-eval-agent-summary/1"
            or payload["decision_grade"] is not False
            or payload["scope"]!="aggregate_snapshot_only"):
        raise buyer.BuyerError("unsupported agent snapshot")
    known=_known_families()
    def enum(value,allowed):
        if not isinstance(value,str) or value not in allowed|{"unknown"}:
            raise buyer.BuyerError("unexpected metadata value")
    def array(value,max_size):
        if not isinstance(value,list) or len(value)>max_size:
            raise buyer.BuyerError("invalid metadata array")
    array(payload["capabilities"],100)
    for row in payload["capabilities"]:
        _exact(row,("family_id","runnable","maturity","conformance_ceiling"))
        enum(row["family_id"],known)
        if type(row["runnable"]) is not bool:
            raise buyer.BuyerError("invalid capability flag")
        enum(row["maturity"],MATURITY)
        enum(row["conformance_ceiling"],{"L0","L1","L2"})
    array(payload["candidate_evaluation_jobs"],200)
    for i,row in enumerate(payload["candidate_evaluation_jobs"],1):
        _exact(row,("snapshot_job_number","status","mode","packages"))
        if type(row["snapshot_job_number"]) is not int or row["snapshot_job_number"]!=i:
            raise buyer.BuyerError("invalid snapshot job index")
        enum(row["status"],STATES)
        enum(row["mode"],MODES)
        array(row["packages"],100)
        if row["status"]!="completed" and row["packages"]:
            raise buyer.BuyerError("unverified job packages")
        for package in row["packages"]:
            _exact(package,("family_id","claim","review_items","response_count"))
            enum(package["family_id"],known)
            enum(package["claim"],CLAIMS)
            _count(package["review_items"])
            _count(package["response_count"])
    array(payload["research_runs"],200)
    for row in payload["research_runs"]:
        _exact(row,("family_id","verdict","claim"))
        enum(row["family_id"],known)
        enum(row["verdict"],VERDICTS)
        enum(row["claim"],CLAIMS)
    evidence=payload["procurement_inventory"]
    _exact(evidence,("candidate_count","missing_intake_count","missing_document_kind_count",
                     "intact_document_count","other_document_count"))
    for v in evidence.values():
        _count(v)
    return payload


def load(path):
    p=Path(path).expanduser()
    if p.is_symlink() or not p.is_file():
        raise buyer.BuyerError("approved snapshot must be a regular file")
    with p.open("rb") as handle:
        raw=handle.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:
        raise buyer.BuyerError("snapshot exceeds size limit")
    try:
        envelope=json.loads(raw.decode("utf-8"))
        _exact(envelope,("payload","digest"))
        payload=_validate(envelope["payload"])
        if buyer_plan._digest(payload)!=envelope["digest"]:
            raise buyer.BuyerError("snapshot digest mismatch")
        return envelope
    except (ValueError,TypeError,KeyError,RecursionError) as exc:
        raise buyer.BuyerError("invalid snapshot") from exc
