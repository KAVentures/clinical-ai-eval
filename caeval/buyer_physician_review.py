"""Blinded local physician-review collection over verified candidate runs.

These are self-attested review submissions, not authenticated identities or L2
adjudication. They never mutate the original research package or reveal another
reviewer's answers in the review template. A scientific operator must complete
the canonical independent review and adjudication process separately.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from . import buyer_agent as buyer, buyer_jobs as jobs, buyer_manual as manual, buyer_plan, buyer_research, report

VERDICTS = ("safe", "unsafe", "cannot_judge")


def _source(directory, job_id, family):
    row = jobs.status(directory, job_id)
    if row["status"] != "completed":
        raise buyer.BuyerError("review requires a completed verified run")
    package = next((p for p in row["result"]["packages"] if p["family_id"] == family), None)
    if package is None:
        raise buyer.BuyerError("unknown review family")
    ws = jobs._job_path(directory, job_id) / "out" / ("run_" + family)
    metadata = buyer_research._preflight(ws)
    if metadata["manifest_hash"] != package["manifest_hash"]:
        raise buyer.BuyerError("review package changed")
    raw = buyer_research.verified_bytes(ws, metadata, "human_review")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"), newline="")))
    locked = json.loads(buyer_research.verified_bytes(ws, metadata, "review_manifest"))
    if (report.manifest_fingerprint(locked) != locked.get("manifest_hash")
            or locked.get("n_expected") != len(locked["expected_cells"])):
        raise buyer.BuyerError("locked review manifest changed")
    expected = {x["cell_id"] for x in locked["expected_cells"]}
    if len(rows) != len(expected) or len(expected) != len(locked["expected_cells"]) or {r["cell_id"] for r in rows} != expected:
        raise buyer.BuyerError("review queue differs from canonical locked manifest")
    slots = {buyer_plan._digest([job_id, family, package["manifest_hash"], r["cell_id"]]): r
             for r in rows}
    return package, slots


def template(directory, job_id, family):
    package, slots = _source(directory, job_id, family)
    request = {
        "schema": "blinded-review-exchange/1", "job_id": job_id, "family_id": family,
        "manifest_hash": package["manifest_hash"],
        "items": [{"slot": slot, "case": r["case_as_shown_to_product"],
                   "response": r["product_response"]} for slot, r in sorted(slots.items())],
    }
    return {"request": request, "submission": {"reviewer_id": "",
            "verdicts": [{"slot": r["slot"], "verdict": "", "notes": ""} for r in request["items"]]}}


def _folder(directory):
    folder = manual._directory(directory, "physician-reviews")
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder


def submit(directory, document):
    if not isinstance(document, dict) or set(document) != {"request", "submission"}:
        raise buyer.BuyerError("invalid review submission")
    req, sub = document["request"], document["submission"]
    if not isinstance(req, dict) or not isinstance(sub, dict) or set(sub) != {"reviewer_id", "verdicts"}:
        raise buyer.BuyerError("invalid review fields")
    job_id, family = req.get("job_id"), req.get("family_id")
    expected = template(directory, job_id, family)
    if req != expected["request"]:
        raise buyer.BuyerError("review template changed")
    reviewer = manual._identifier(sub["reviewer_id"])
    if not req["items"]:
        raise buyer.BuyerError("no review items were selected")
    wanted = {r["slot"] for r in req["items"]}
    verdicts = sub["verdicts"]
    if not isinstance(verdicts, list) or len(verdicts) != len(wanted):
        raise buyer.BuyerError("explicit verdict required for every review item")
    found = set()
    for r in verdicts:
        if not isinstance(r, dict) or set(r) != {"slot", "verdict", "notes"}:
            raise buyer.BuyerError("invalid verdict row")
        slot = r["slot"]
        if not isinstance(slot, str) or slot not in wanted or slot in found:
            raise buyer.BuyerError("unknown or duplicate review item")
        if r["verdict"] not in VERDICTS or not isinstance(r["notes"], str) or len(r["notes"]) > 4000:
            raise buyer.BuyerError("invalid verdict or notes")
        found.add(slot)
    record = {"schema": "review-submission/1", "job_id": job_id, "family_id": family,
              "manifest_hash": req["manifest_hash"], "reviewer_id": reviewer,
              "verdicts": sorted(verdicts, key=lambda r: r["slot"]),
              "identity_verified": False, "decision_grade": False}
    record["submission_id"] = buyer_plan._digest(record)
    # One immutable submission per reviewer ID and original package. No
    # overwrite of a dissenting verdict; same submission is idempotent.
    key = buyer_plan._digest([job_id, family, req["manifest_hash"], reviewer])
    manual._write_once(_folder(directory) / (key + ".json"), record)
    return {k: v for k, v in record.items() if k != "verdicts"}


def submit_file(directory, source_file):
    with Path(source_file).expanduser().open("rb") as handle:
        return submit(directory, manual._parse(handle.read(manual.MAX_REQUEST_BYTES + 1)))


def inventory(directory, job_id, family):
    package, slots = _source(directory, job_id, family)
    records = []
    for path in sorted(_folder(directory).glob("*.json")):
        if len(records) >= 100:
            break
        obj = manual._read(path)
        if (obj.get("job_id"), obj.get("family_id"), obj.get("manifest_hash")) != (
                job_id, family, package["manifest_hash"]):
            continue
        if buyer_plan._digest({k: v for k, v in obj.items() if k != "submission_id"}) != obj.get("submission_id"):
            raise buyer.BuyerError("review record changed")
        rid = manual._identifier(obj["reviewer_id"])
        if path.stem != buyer_plan._digest([job_id, family, package["manifest_hash"], rid]):
            raise buyer.BuyerError("review identity binding changed")
        verdicts = obj["verdicts"]
        if (len(verdicts) != len(slots) or {r["slot"] for r in verdicts} != set(slots)
                or any(r["verdict"] not in VERDICTS for r in verdicts)):
            raise buyer.BuyerError("review coverage mismatch")
        records.append(obj)
    return {"job_id": job_id, "family_id": family, "n_items": len(slots),
            "n_submitted_reviewer_ids": len(records),
            "submission_ids": [r["submission_id"] for r in records],
            "identity_verified": False, "adjudicated": False, "decision_grade": False}


def export_csv(directory, job_id, family, reviewer_id):
    """Canonical review columns, but not a signed independent review packet."""
    package, slots = _source(directory, job_id, family)
    rid = manual._identifier(reviewer_id)
    key = buyer_plan._digest([job_id, family, package["manifest_hash"], rid])
    record = manual._read(_folder(directory) / (key + ".json"))
    if record.get("submission_id") not in inventory(directory, job_id, family)["submission_ids"]:
        raise buyer.BuyerError("review submission failed integrity check")
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=["reviewer_id", "cell_id",
                                             "human_verdict_safe_unsafe", "human_notes"])
    writer.writeheader()
    for r in record["verdicts"]:
        # Notes are omitted from CSV to prevent spreadsheet formula injection;
        # the original verbatim notes remain in the private review JSON.
        writer.writerow({"reviewer_id": rid, "cell_id": slots[r["slot"]]["cell_id"],
                         "human_verdict_safe_unsafe": r["verdict"], "human_notes": ""})
    return out.getvalue()
