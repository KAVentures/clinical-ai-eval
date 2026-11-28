"""Prospective double-reader binary imaging reference protocol (experimental).

Readers inspect approved external assets. No image loading or clinical truth
verification. The third reader adjudicates disagreements only after both
independent reads are locked. All cases, including unresolved ones, survive.
"""
import json
import secrets
from . import diagnostic_imaging as imaging

SCHEMA = "imaging-reference-protocol/1"
METHOD = "independent-two-reader-adjudication"
MAX_CASES = 500
LABELS = ("positive", "negative", "unresolved")
LIMITS = [
    "Independent accounts and blinded packets do not prove clinical credentials, independent behavior or external non-exposure.",
    "Images and clinical context are inspected in an approved external environment, not verified by this tool.",
    "Two-reader agreement and third-reader adjudication are not proof of diagnostic truth.",
    "Unresolved cases remain unresolved; no clinical validity or deployment authorization.",
]


def protocol(spec, readers, adjudicator, *, nonce=None):
    imaging._keys(spec, "study_id task_id modality candidates strata cases attestations")
    imaging._require(imaging._unique_tokens(readers, 2, 2) and imaging._token(adjudicator)
                     and adjudicator not in readers, "three distinct reviewer account identifiers required")
    imaging._keys(spec["attestations"], "authorized_local_research deidentified_or_synthetic pre_exposure")
    imaging._require(all(v is True for v in spec["attestations"].values()), "all prospective attestations required")
    imaging._require(isinstance(spec["cases"], list) and 1 <= len(spec["cases"]) <= MAX_CASES,
                     "bounded reference review cohort required")
    for case in spec["cases"]:
        imaging._keys(case, "case_id cluster_id asset_sha256 strata")
    nonce = secrets.token_hex(32) if nonce is None else nonce
    # Reuse the exact imaging schema, but the temporary all-unresolved plan is
    # never stored as a candidate study or used to score predictions.
    filled = {**spec, "reference_method": METHOD,
              "attestations": {**spec["attestations"], "independent_reference": True},
              "cases": [{**case, "reference": "unresolved"} for case in spec["cases"]]}
    normalized = imaging.freeze(filled, private_nonce=nonce, confirmed=True)["spec"]
    normalized.pop("reference_method")
    normalized["attestations"].pop("independent_reference")
    for case in normalized["cases"]:
        case.pop("reference")
    return imaging._sealed({"schema": SCHEMA, "spec": normalized, "readers": sorted(readers),
                            "adjudicator": adjudicator, "private_nonce": nonce,
                            "decision_grade": False, "limitations": LIMITS[:]})


def verify_protocol(p):
    imaging._keys(p, "schema spec readers adjudicator private_nonce decision_grade limitations digest")
    expected = protocol(p["spec"], p["readers"], p["adjudicator"], nonce=p["private_nonce"])
    imaging._require(imaging._digest(p) == imaging._digest(expected), "reference protocol changed")


def _slots(p):
    return {imaging._digest([SCHEMA, p["private_nonce"], c["case_id"]]): c for c in p["spec"]["cases"]}


def reader_packet(p, reader):
    verify_protocol(p)
    imaging._require(reader in p["readers"], "assigned primary reader required")
    return {"schema": "imaging-reference-reader-packet/1", "protocol_digest": p["digest"],
            "task_id": p["spec"]["task_id"], "modality": p["spec"]["modality"],
            "cases": [{"slot": slot, "asset_sha256": c["asset_sha256"]}
                      for slot,c in sorted(_slots(p).items())]}


def ratings(packet, values):
    imaging._require(isinstance(values, dict) and set(values) == {c["slot"] for c in packet["cases"]},
                     "exact review slot inventory required")
    imaging._require(all(v in LABELS for v in values.values()), "invalid reference outcome")
    return {"schema": "imaging-reference-ratings/1", "packet_digest": imaging._digest(packet),
            "ratings": [{"slot": k, "label": values[k]} for k in sorted(values)]}


def _read_ratings(packet, doc):
    imaging._keys(doc, "schema packet_digest ratings")
    imaging._require(isinstance(doc["ratings"], list), "ratings must be a list")
    values = {}
    for row in doc["ratings"]:
        imaging._keys(row, "slot label")
        imaging._require(imaging._hex(row["slot"]) and row["slot"] not in values, "duplicate or invalid slot")
        values[row["slot"]] = row["label"]
    imaging._require(imaging._digest(doc) == imaging._digest(ratings(packet, values)), "ratings differ from exact packet")
    return values


def _primary(p, docs):
    imaging._require(isinstance(docs, dict) and set(docs) == set(p["readers"]),
                     "both primary readers must be locked")
    return [_read_ratings(reader_packet(p, uid), docs[uid]) for uid in p["readers"]]


def adjudication_packet(p, docs, reviewer):
    verify_protocol(p)
    imaging._require(reviewer == p["adjudicator"], "assigned adjudicator required")
    a,b = _primary(p, docs)
    return {"schema": "imaging-reference-adjudicator-packet/1", "protocol_digest": p["digest"],
            "task_id": p["spec"]["task_id"], "modality": p["spec"]["modality"],
            "primary_digests": sorted(imaging._digest(d) for d in docs.values()),
            "cases": [{"slot": slot, "asset_sha256": c["asset_sha256"], "prior_labels": sorted([a[slot],b[slot]])}
                      for slot,c in sorted(_slots(p).items()) if a[slot] != b[slot]]}


def resolve(p, docs, adjudication):
    verify_protocol(p)
    a,b = _primary(p, docs)
    packet = adjudication_packet(p, docs, p["adjudicator"])
    if packet["cases"]:
        values = _read_ratings(packet, adjudication)
    else:
        imaging._require(adjudication is None, "no disagreement; no adjudication allowed")
        values = {}
    final = {slot: a[slot] if a[slot] == b[slot] else values[slot] for slot in a}
    spec = p["spec"]
    out = {**spec, "reference_method": METHOD,
           "attestations": {**spec["attestations"], "independent_reference": True},
           "cases": [{**case, "reference": final[slot]} for slot,case in _slots(p).items()]}
    return out, {"n_cases": len(final), "n_primary_agreement": sum(a[s] == b[s] for s in a),
                 "n_disagreement": len(values), "n_unresolved": sum(v == "unresolved" for v in final.values()),
                 "n_positive": sum(v == "positive" for v in final.values()),
                 "n_negative": sum(v == "negative" for v in final.values())}


def package(p, docs, adjudication, final_plan):
    spec, summary = resolve(p, docs, adjudication)
    imaging._verify_plan(final_plan)
    rebuilt = imaging.freeze(spec, private_nonce=final_plan["private_nonce"], confirmed=True)
    imaging._require(imaging._digest(rebuilt) == imaging._digest(final_plan), "final imaging plan differs from reference review")
    return imaging._sealed({"schema": "imaging-reference-evidence/1", "protocol": p,
                            "primary_ratings": docs, "adjudication": adjudication,
                            "final_plan": final_plan, "summary": summary,
                            "decision_grade": False, "limitations": LIMITS[:]})


def verify(evidence, expected_digest=None):
    imaging._keys(evidence, "schema protocol primary_ratings adjudication final_plan summary decision_grade limitations digest")
    rebuilt = package(evidence["protocol"], evidence["primary_ratings"], evidence["adjudication"], evidence["final_plan"])
    imaging._require(imaging._digest(rebuilt) == imaging._digest(evidence), "reference evidence replay mismatch")
    imaging._require(expected_digest is None or expected_digest == evidence["digest"], "external reference digest mismatch")
    return {"status": "REPLAY_VERIFIED", "digest": evidence["digest"],
            "final_plan_digest": evidence["final_plan"]["digest"],
            "expected_digest_matched": expected_digest is not None, "decision_grade": False}
