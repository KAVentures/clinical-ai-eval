"""Versioned, offline vendor evidence exchange. Not a compliance assessment.

The request is deterministically bound to the buyer's scope and candidate.
Responses are untrusted, self-reported claims. They are stored as original
bytes in the content-addressed evidence register, never as clinical results.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from . import buyer_agent as buyer

VERSION = "buyer-disclosure-v1"
KIND = "vendor_questionnaire"
MAX_BYTES = 512 * 1024
STATUSES = ("provided", "not_available", "not_applicable")
QUESTIONS = (
    ("product_version", "intended_use", "What exact product, model/release and configuration is offered?"),
    ("intended_use", "intended_use", "Describe intended users, clinical actions, target populations and exclusions."),
    ("clinical_evidence", "clinical_validation", "Describe validation methods, populations, outcomes, uncertainty and limitations."),
    ("local_validation", "clinical_validation", "What evidence is available for this buyer's intended use and clinical setting?"),
    ("regulatory", "regulatory_status", "Identify applicable jurisdictions, regulatory status, scope and supporting documentation."),
    ("security", "security", "Describe authentication, access controls, security testing and incident handling."),
    ("data_flows", "data_processing", "Identify data inputs, processing locations, subprocessors, retention and any external transmission."),
    ("local_deployment", "integration", "Describe on-premises or private deployment options, infrastructure requirements and egress dependencies."),
    ("integration", "integration", "Describe interfaces, workflow integration, interoperability and implementation requirements."),
    ("oversight", "monitoring", "Describe human oversight, known hazards, escalation, monitoring, version updates and rollback."),
    ("cost", "integration", "Describe licensing, implementation, support and ongoing operational costs."),
)


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _request(directory, vendor_id: str) -> dict:
    data = buyer.load(directory)
    vendors = [v for v in data["vendors"] if v["vendor_id"] == vendor_id]
    if len(vendors) != 1:
        raise buyer.BuyerError("candidate vendor is not registered uniquely")
    # Do not send competitor identities, case packs, test thresholds, documents,
    # results, filesystem paths, or reviewer details to a candidate supplier.
    scope = {k: data.get(k, "") for k in
             ("project_name", "intended_use", "setting", "jurisdiction", "created_at")}
    req = {"questionnaire_version": VERSION, "buyer_scope": scope,
           "vendor": {"vendor_id": vendor_id, "name": vendors[0]["name"]},
           "questions": [{"id": ident, "category": kind, "prompt": prompt}
                         for ident, kind, prompt in QUESTIONS]}
    return {**req, "request_sha256": hashlib.sha256(_canonical(req)).hexdigest()}


def template(directory, vendor_id: str) -> dict:
    """A blank response file for offline transfer to the candidate supplier."""
    request = _request(directory, vendor_id)
    return {"request": request, "submission": {
        "respondent": "", "product_version": "",
        "answers": [{"id": q["id"], "status": "", "answer": "", "references": []}
                    for q in request["questions"]],
    }}


def _unique_object(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise buyer.BuyerError("duplicate key in questionnaire JSON")
        obj[k] = v
    return obj


def _text(value, label, limit=12000, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise buyer.BuyerError(f"invalid {label}")
    return value


def validate(directory, vendor_id: str, raw: bytes) -> dict:
    if len(raw) > MAX_BYTES:
        raise buyer.BuyerError("questionnaire exceeds 512 KiB limit")
    try:
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                         parse_constant=lambda _: (_ for _ in ()).throw(
                             buyer.BuyerError("non-finite JSON value")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise buyer.BuyerError("invalid questionnaire JSON") from exc
    if not isinstance(obj, dict) or set(obj) != {"request", "submission"}:
        raise buyer.BuyerError("expected request and submission objects")
    expected = _request(directory, vendor_id)
    if obj["request"] != expected:
        raise buyer.BuyerError("questionnaire does not match this exact buyer scope, vendor and version")
    sub = obj["submission"]
    if not isinstance(sub, dict) or set(sub) != {"respondent", "product_version", "answers"}:
        raise buyer.BuyerError("submission fields do not match schema")
    _text(sub["respondent"], "respondent", 300, True)
    _text(sub["product_version"], "product version", 300, True)
    answers = sub["answers"]
    if not isinstance(answers, list) or len(answers) != len(QUESTIONS):
        raise buyer.BuyerError("supply exactly one explicit response to each question")
    expected_ids = {q[0] for q in QUESTIONS}
    seen = set()
    counts = {s: 0 for s in STATUSES}
    for a in answers:
        if not isinstance(a, dict) or set(a) != {"id", "status", "answer", "references"}:
            raise buyer.BuyerError("answer fields do not match schema")
        ident = _text(a["id"], "question id", 80, True)
        if ident not in expected_ids or ident in seen:
            raise buyer.BuyerError("unknown or repeated question id")
        seen.add(ident)
        state = _text(a["status"], "answer status", 40, True)
        if state not in STATUSES:
            raise buyer.BuyerError("answer status must be provided, not_available or not_applicable")
        _text(a["answer"], "answer or explanation", required=True)
        refs = a["references"]
        if not isinstance(refs, list) or len(refs) > 30:
            raise buyer.BuyerError("references must be a bounded list of strings")
        for ref in refs:
            _text(ref, "reference", 1000, True)
        counts[state] += 1
    return {"request_sha256": expected["request_sha256"], "vendor_id": vendor_id,
            "respondent_self_reported": sub["respondent"],
            "product_version_self_reported": sub["product_version"],
            "counts": counts, "claim_authority": "vendor_self_reported_unverified",
            "decision_grade": False}


def import_response(directory, vendor_id: str, raw: bytes) -> dict:
    """Validate before storing original bytes. Never execute/fetch a reference."""
    summary = validate(directory, vendor_id, raw)
    root = Path(directory).resolve()
    with tempfile.TemporaryDirectory(prefix="questionnaire-", dir=root) as tmp:
        source = Path(tmp) / "vendor-questionnaire.json"
        source.write_bytes(raw)
        result = buyer.add_evidence(root, vendor_id, KIND, str(source),
                                    title="Vendor disclosure (self-reported, unverified)",
                                    source_filename=source.name, _validated_questionnaire=True)
    return {**summary, "evidence_id": result["evidence_id"],
            "sha256": result["sha256"], "network_calls": 0}


def import_file(directory, vendor_id: str, source_file: str) -> dict:
    with Path(source_file).expanduser().open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    return import_response(directory, vendor_id, raw)
