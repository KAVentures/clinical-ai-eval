"""Offline, exploratory binary diagnostic-imaging study with private reference lock.

Only metadata and structured predictions enter the engine. Images are not
loaded, transmitted or embedded. A digest identifies bytes, not clinical truth,
de-identification, provenance or an actual model invocation.
"""
import argparse
import hashlib
import json
import re
import secrets
from pathlib import Path

SCHEMA = "diagnostic-imaging-study/1"
MAX_CASES = 2000
MAX_CANDIDATES = 8
MAX_STRATA = 20
MAX_FILE = 16 * 1024 * 1024
LIMITS = [
    "Binary, single-task, offline exploratory research only.",
    "Reference provenance, independence, de-identification and pre-exposure are operator attestations, not independently verified.",
    "No image decoding, DICOM validation, model execution or proof of image receipt.",
    "Case-weighted descriptive estimates, not confidence intervals; patient clusters may be dependent.",
    "Complete-score AUC is conditional on observed scores and resolved references; missingness can bias it.",
    "No clinical validity, regulatory compliance, deployment authorization or procurement ranking.",
]


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _keys(obj, fields):
    _require(isinstance(obj, dict) and set(obj) == set(fields.split()), "unexpected document fields")


def _token(x):
    return isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", x) is not None


def _hex(x):
    return isinstance(x, str) and re.fullmatch(r"[0-9a-f]{64}", x) is not None


def _digest(obj):
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _sealed(obj):
    return {**obj, "digest": _digest(obj)}


def _unique_tokens(xs, low, high):
    return (isinstance(xs, list) and low <= len(xs) <= high
            and all(_token(x) for x in xs) and len(set(xs)) == len(xs))


def _candidate(x):
    _keys(x, "id version threshold_bp")
    _require(_token(x["id"]) and _token(x["version"]), "invalid candidate identity")
    _require(type(x["threshold_bp"]) is int and 0 <= x["threshold_bp"] <= 10000,
             "threshold must be integer basis points")


def freeze(spec, *, private_nonce=None, confirmed=False):
    """Freeze all cases, references, thresholds and strata before exchange."""
    _require(confirmed is True, "explicit prospective freeze required")
    _keys(spec, "study_id task_id modality reference_method candidates strata cases attestations")
    _require(all(_token(spec[k]) for k in ("study_id", "task_id", "modality", "reference_method")),
             "use bounded non-identifying protocol identifiers")
    attest = spec["attestations"]
    _keys(attest, "authorized_local_research deidentified_or_synthetic independent_reference pre_exposure")
    _require(all(v is True for v in attest.values()), "all operator attestations required")
    candidates = spec["candidates"]
    _require(isinstance(candidates, list) and 1 <= len(candidates) <= MAX_CANDIDATES,
             "invalid candidate roster size")
    for candidate in candidates:
        _candidate(candidate)
    _require(len({c["id"] for c in candidates}) == len(candidates), "duplicate candidate id")
    strata = spec["strata"]
    _require(_unique_tokens(strata, 0, MAX_STRATA), "invalid predeclared strata")
    cases = spec["cases"]
    _require(isinstance(cases, list) and 1 <= len(cases) <= MAX_CASES, "invalid case count")
    ids, assets, groups = set(), set(), set()
    for case in cases:
        _keys(case, "case_id cluster_id asset_sha256 reference strata")
        _require(_token(case["case_id"]) and _token(case["cluster_id"]), "invalid case or cluster")
        _require(_hex(case["asset_sha256"]), "invalid image digest")
        _require(case["case_id"] not in ids and case["asset_sha256"] not in assets,
                 "duplicate case or asset; repeated images require a separate study design")
        _require(case["reference"] in ("positive", "negative", "unresolved"), "invalid reference")
        _require(_unique_tokens(case["strata"], 0, len(strata))
                 and set(case["strata"]) <= set(strata), "undeclared stratum")
        ids.add(case["case_id"])
        assets.add(case["asset_sha256"])
        groups.add(case["cluster_id"])
    nonce = secrets.token_hex(32) if private_nonce is None else private_nonce
    _require(_hex(nonce), "invalid private nonce")
    normalized = json.loads(json.dumps(spec, allow_nan=False))
    normalized["candidates"].sort(key=lambda x: x["id"])
    normalized["strata"].sort()
    normalized["cases"].sort(key=lambda x: x["case_id"])
    for case in normalized["cases"]:
        case["strata"].sort()
    return _sealed({"schema": SCHEMA, "spec": normalized, "private_nonce": nonce,
                    "decision_grade": False, "limitations": LIMITS[:]})


def _verify_plan(plan):
    _keys(plan, "schema spec private_nonce decision_grade limitations digest")
    expected = freeze(plan["spec"], private_nonce=plan["private_nonce"], confirmed=True)
    _require(_digest(plan) == _digest(expected), "private study plan differs from replay")


def _slots(plan):
    return {_digest([SCHEMA, plan["private_nonce"], c["case_id"]]): c
            for c in plan["spec"]["cases"]}


def _find_candidate(plan, candidate_id):
    matches = [c for c in plan["spec"]["candidates"] if c["id"] == candidate_id]
    _require(len(matches) == 1, "unknown frozen candidate")
    return matches[0]


def exchange(plan, candidate_id):
    """Candidate-facing metadata omits private salt, references and strata."""
    _verify_plan(plan)
    candidate = _find_candidate(plan, candidate_id)
    spec = plan["spec"]
    return {"schema": "diagnostic-imaging-exchange/1", "plan_digest": plan["digest"],
            "candidate": dict(candidate), "task_id": spec["task_id"], "modality": spec["modality"],
            "cases": [{"slot": slot, "asset_sha256": c["asset_sha256"]}
                      for slot, c in sorted(_slots(plan).items())]}


def template(plan, candidate_id):
    ex = exchange(plan, candidate_id)
    return {"schema": "diagnostic-imaging-predictions/1", "plan_digest": ex["plan_digest"],
            "candidate": ex["candidate"],
            "predictions": [{"slot": c["slot"], "status": "unavailable", "score_bp": None}
                            for c in ex["cases"]]}


def _predictions(plan, doc):
    _keys(doc, "schema plan_digest candidate predictions")
    _require(doc["schema"] == "diagnostic-imaging-predictions/1"
             and doc["plan_digest"] == plan["digest"], "predictions not bound to frozen plan")
    _candidate(doc["candidate"])
    _require(doc["candidate"] == _find_candidate(plan, doc["candidate"]["id"]),
             "candidate version or threshold changed")
    expected = _slots(plan)
    rows = doc["predictions"]
    _require(isinstance(rows, list) and len(rows) == len(expected), "exact slot inventory required")
    out = {}
    for row in rows:
        _keys(row, "slot status score_bp")
        slot = row["slot"]
        _require(_hex(slot) and slot in expected and slot not in out, "unknown or duplicate slot")
        score = row["score_bp"]
        if row["status"] == "scored":
            _require(type(score) is int and 0 <= score <= 10000, "invalid integer score")
        else:
            _require(row["status"] in ("abstained", "unavailable") and score is None,
                     "invalid missing outcome")
        out[slot] = row
    return out


def _fraction(a, b):
    return a / b if b else None


def _bounds(success, missing, denominator):
    return [_fraction(success, denominator), _fraction(success + missing, denominator)] if denominator else None


def _auc(positives, negatives):
    # Exact integer count of wins and half-ties, no floating rank ambiguity.
    if not positives or not negatives:
        return None
    from bisect import bisect_left, bisect_right
    negatives = sorted(negatives)
    twice = sum(bisect_left(negatives, p) + bisect_right(negatives, p) for p in positives)
    return twice / (2 * len(positives) * len(negatives))


def _metrics(slots, rows, threshold):
    count = dict(tp=0, tn=0, fp=0, fn=0, positive_missing=0, negative_missing=0,
                 unresolved_reference=0, abstained=0, unavailable=0, scored=0)
    pos, neg = [], []
    for slot, c in slots.items():
        row = rows[slot]
        status = row["status"]
        count[status] += 1
        ref = c["reference"]
        if ref == "unresolved":
            count["unresolved_reference"] += 1
            continue
        if status != "scored":
            count[ref + "_missing"] += 1
            continue
        score = row["score_bp"]
        if ref == "positive":
            pos.append(score)
            count["tp" if score >= threshold else "fn"] += 1
        else:
            neg.append(score)
            count["fp" if score >= threshold else "tn"] += 1
    c = count
    p, n = c["tp"] + c["fn"], c["tn"] + c["fp"]
    return {"n_cases": len(slots), "n_clusters": len({v["cluster_id"] for v in slots.values()}),
            "counts": c, "score_coverage": _fraction(c["scored"], len(slots)),
            "reference_coverage": _fraction(len(slots) - c["unresolved_reference"], len(slots)),
            "sensitivity_scored_only": _fraction(c["tp"], p),
            "specificity_scored_only": _fraction(c["tn"], n),
            "sensitivity_resolved_reference_bounds": _bounds(c["tp"], c["positive_missing"], p + c["positive_missing"]),
            "specificity_resolved_reference_bounds": _bounds(c["tn"], c["negative_missing"], n + c["negative_missing"]),
            "auc_scored_resolved_only": _auc(pos, neg), "auc_positive_n": len(pos), "auc_negative_n": len(neg)}


def _correct(case, row, threshold):
    if row["status"] != "scored":
        return None
    return int((row["score_bp"] >= threshold) == (case["reference"] == "positive"))


def _paired(slots, a, b, ta, tb):
    # Compare correct binary decisions on exactly the same resolved cases.
    n = paired = discord_a = discord_b = low = high = 0
    for slot, case in slots.items():
        if case["reference"] == "unresolved":
            continue
        n += 1
        x, y = _correct(case, a[slot], ta), _correct(case, b[slot], tb)
        if x is not None and y is not None:
            paired += 1
            discord_a += x == 1 and y == 0
            discord_b += x == 0 and y == 1
        low += (0 if x is None else x) - (1 if y is None else y)
        high += (1 if x is None else x) - (0 if y is None else y)
    return {"n_resolved": n, "n_paired": paired, "a_only_correct": discord_a,
            "b_only_correct": discord_b, "paired_accuracy_difference_a_minus_b": _fraction(discord_a - discord_b, paired),
            "all_resolved_accuracy_difference_bounds": [_fraction(low, n), _fraction(high, n)] if n else None,
            "inference_performed": False}


def assess(plan, submissions):
    _verify_plan(plan)
    _require(isinstance(submissions, list) and len(submissions) <= MAX_CANDIDATES, "invalid submissions")
    by = {}
    for doc in submissions:
        rows = _predictions(plan, doc)
        cid = doc["candidate"]["id"]
        _require(cid not in by, "duplicate candidate submission")
        by[cid] = (doc, rows)
    spec = plan["spec"]
    slots = _slots(plan)
    groups = [("overall", slots)] + [("stratum:" + tag, {s: c for s, c in slots.items() if tag in c["strata"]})
                                       for tag in spec["strata"]]
    results = []
    for candidate in spec["candidates"]:
        cid = candidate["id"]
        if cid not in by:
            results.append({"candidate": candidate, "status": "not_submitted", "metrics": None})
            continue
        doc, rows = by[cid]
        results.append({"candidate": candidate, "status": "submitted",
                        "prediction_digest": _digest(doc),
                        "metrics": {name: _metrics(group, rows, candidate["threshold_bp"]) for name, group in groups}})
    # Suppress all comparisons until the entire predeclared roster is submitted.
    comparisons = []
    complete = len(by) == len(spec["candidates"])
    if complete:
        from itertools import combinations
        for a, b in combinations(spec["candidates"], 2):
            comparisons.append({"a": a["id"], "b": b["id"],
                                "metrics": {name: _paired(group, by[a["id"]][1], by[b["id"]][1],
                                                           a["threshold_bp"], b["threshold_bp"])
                                            for name, group in groups}})
    return _sealed({"schema": "diagnostic-imaging-report/1", "plan_digest": plan["digest"],
                    "roster_complete": complete, "candidates": results, "comparisons": comparisons,
                    "decision_grade": False, "deployment_authorized": False, "limitations": LIMITS[:]})


def package(plan, submissions):
    report = assess(plan, submissions)
    return _sealed({"schema": "diagnostic-imaging-evidence/1", "plan": plan,
                    "submissions": sorted(submissions, key=lambda x: x["candidate"]["id"]),
                    "report": report, "decision_grade": False})


def verify(doc, expected_digest=None):
    _keys(doc, "schema plan submissions report decision_grade digest")
    rebuilt = package(doc["plan"], doc["submissions"])
    _require(_digest(doc) == _digest(rebuilt), "evidence differs from offline replay")
    _require(expected_digest is None or doc["digest"] == expected_digest, "external digest mismatch")
    return {"status": "REPLAY_VERIFIED", "digest": doc["digest"],
            "expected_digest_matched": expected_digest is not None, "decision_grade": False}


def _read(path):
    p = Path(path)
    _require(p.is_file() and not p.is_symlink(), "regular local JSON file required")
    with p.open("rb") as f:
        raw = f.read(MAX_FILE + 1)
    _require(len(raw) <= MAX_FILE, "JSON file exceeds size limit")
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("nonfinite JSON value")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def configure(parser):
    sub = parser.add_subparsers(dest="imaging_action", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("spec")
    f.add_argument("--confirm-prospective", action="store_true")
    for name in ("exchange", "template"):
        p = sub.add_parser(name)
        p.add_argument("plan")
        p.add_argument("candidate")
    a = sub.add_parser("assess")
    a.add_argument("plan")
    a.add_argument("submissions", nargs="*")
    v = sub.add_parser("verify")
    v.add_argument("evidence")
    v.add_argument("--expected-digest")
    vr = sub.add_parser("verify-reference")
    vr.add_argument("evidence")
    vr.add_argument("--expected-digest")
    parser.set_defaults(func=cli)


def cli(args):
    try:
        action = args.imaging_action
        if action == "freeze":
            out = freeze(_read(args.spec), confirmed=args.confirm_prospective)
        elif action == "exchange":
            out = exchange(_read(args.plan), args.candidate)
        elif action == "template":
            out = template(_read(args.plan), args.candidate)
        elif action == "assess":
            out = package(_read(args.plan), [_read(p) for p in args.submissions])
        elif action == "verify-reference":
            from . import imaging_reference
            out = imaging_reference.verify(_read(args.evidence), args.expected_digest)
        else:
            out = verify(_read(args.evidence), args.expected_digest)
        print(json.dumps(out, allow_nan=False, ensure_ascii=True, indent=2))
    except (ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError):
        raise SystemExit("Diagnostic imaging research input refused; inspect local schema and approval.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    configure(parser)
    cli(parser.parse_args())


if __name__ == "__main__":
    main()
