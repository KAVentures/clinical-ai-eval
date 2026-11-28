"""Offline longitudinal administrative evidence review, not clinical progress.

Compare exactly the same frozen programme, with independently supplied handoff
digests. Never interpret bounded inventory as a population or rank modalities.
"""
import argparse
import json
from pathlib import Path
from . import unified_gap_handoff as h

SCHEMA = "unified-evidence-gap-comparison/1"
LIMITATIONS = [
    "Order is caller asserted; neither timestamp nor chronology is authenticated.",
    "Input digests must come from a trusted independent channel; hashes are not signatures.",
    "Source records and clinical outcomes are not independently reverified.",
    "Work counts are bounded observations, not a complete census; changes are not job transitions.",
    "No change denotes clinical improvement, acceptance, or authorization."
]
FIELDS = ("coverage_state", "condition", "source_mode", "source_claim",
          "work_inventory_state", "work_inventory_incomplete")
SCOPE = ("requirement_id", "plan_id", "kind", "family", "vendor_id", "target")
COUNT_FIELDS = ("potential_ready_records", "active_work_records")


def _index(rows, key):
    result = {}
    for row in rows:
        ident = key(row)
        h.require(ident not in result, "duplicate comparison scope")
        result[ident] = row
    return result


def _check(package, expected, verify_handoff):
    h.require(h.hex64(expected), "independently supplied input digest required")
    verdict = verify_handoff(package, expected)
    h.require(verdict["status"] == "HANDOFF_PROJECTION_REPLAYED" and
              verdict["digest"] == expected and
              verdict["expected_digest_matched"] is True and
              verdict["source_records_reverified"] is False and
              verdict["decision_grade"] is False and
              verdict["deployment_authorized"] is False, "handoff replay not established")


def _source_change(before, after):
    if before == after:
        return "unchanged"
    if before is None:
        return "association_added"
    if after is None:
        return "association_removed"
    return "association_metadata_changed"


def _count_observation(before, after, field, comparable):
    a, b = before[field], after[field]
    return {"baseline": a, "followup": b,
            "observed_delta": b-a if comparable else None}


def _tasks(row):
    return {(t["code"], t["role"], t["priority"]) for t in row["tasks"]}


def _task_list(items):
    return [{"code": c, "role": r, "priority": p} for c, r, p in sorted(items)]


def _rows(baseline, followup):
    a, b = baseline["report"], followup["report"]
    ac, bc = baseline["coverage"], followup["coverage"]
    ar = _index(a["requirements"], lambda r: r["requirement_id"])
    br = _index(b["requirements"], lambda r: r["requirement_id"])
    al = _index(ac["requirements"], lambda r: r["requirement"]["id"])
    bl = _index(bc["requirements"], lambda r: r["requirement"]["id"])
    h.require(set(ar) == set(br) == set(al) == set(bl), "requirement inventory changed")
    out = []
    for rid in sorted(ar):
        old, new = ar[rid], br[rid]
        scope = {k: old[k] for k in SCOPE}
        h.require(scope == {k: new[k] for k in SCOPE}, "requirement scope changed")
        old_source, new_source = al[rid]["locked_source"], bl[rid]["locked_source"]
        source_change = _source_change(old_source, new_source)
        complete = (a["programme_state"] == b["programme_state"] == "current" and
                    old["work_inventory_state"] == new["work_inventory_state"] == "bounded_scan" and
                    old["work_inventory_incomplete"] is new["work_inventory_incomplete"] is False)
        counts = {f: _count_observation(old, new, f, complete) for f in COUNT_FIELDS}
        old_tasks, new_tasks = _tasks(old), _tasks(new)
        differences = {k: {"baseline": old[k], "followup": new[k]} for k in FIELDS if old[k] != new[k]}
        flags = []
        if new["coverage_state"] in ("source_changed", "source_unavailable"):
            flags.append("locked_source_investigation_outstanding")
        if new["coverage_state"] == "stale_plan":
            flags.append("programme_stale")
        if new["work_inventory_incomplete"]:
            flags.append("inventory_incomplete")
        if old["coverage_state"] == "source_available" and new["coverage_state"] != "source_available":
            flags.append("previously_available_source_no_longer_available")
        if source_change != "unchanged":
            flags.append("association_requires_review")
        out.append({**scope, "baseline_coverage": old["coverage_state"],
                    "followup_coverage": new["coverage_state"],
                    "baseline_condition": old["condition"], "followup_condition": new["condition"],
                    "changed_fields": differences, "association_change": source_change,
                    "baseline_source_metadata_digest": h.digest(old_source) if old_source is not None else None,
                    "followup_source_metadata_digest": h.digest(new_source) if new_source is not None else None,
                    "bounded_counts_comparable": complete, "work_observations": counts,
                    "new_tasks": _task_list(new_tasks-old_tasks),
                    "no_longer_projected_tasks": _task_list(old_tasks-new_tasks),
                    "persistent_tasks": _task_list(old_tasks & new_tasks),
                    "followup_flags": flags,
                    "administrative_change_observed": bool(differences or source_change != "unchanged" or
                        old_tasks != new_tasks or any(v["baseline"] != v["followup"] for v in counts.values())),
                    "canonical_requirements_satisfied": False, "decision_grade": False})
    return out


def _canonical(a, b):
    key = lambda r: (r["plan_id"], r["suite"])
    old, new = _index(a["canonical_suites"], key), _index(b["canonical_suites"], key)
    h.require(set(old) == set(new), "canonical suite inventory changed")
    result = []
    for ident in sorted(old):
        x, y = old[ident], new[ident]
        scope = {k: x[k] for k in ("plan_id", "vendor_id", "target", "suite", "requirement_id")}
        h.require(scope == {k: y[k] for k in scope}, "canonical suite scope changed")
        result.append({**scope, "baseline_condition": x["condition"], "followup_condition": y["condition"],
                       "changed": x["condition"] != y["condition"], "canonical_requirements_satisfied": False})
    return result


def compare(baseline, followup, *, baseline_digest, followup_digest, verify_handoff):
    """Verify each input, then emit only observed administrative changes."""
    _check(baseline, baseline_digest, verify_handoff)
    _check(followup, followup_digest, verify_handoff)
    a, b = baseline["report"], followup["report"]
    h.require(a["programme_id"] == b["programme_id"] and
              a["programme_digest"] == b["programme_digest"] and
              baseline["coverage"]["programme"] == followup["coverage"]["programme"],
              "different frozen programme; reassessment required, comparison refused")
    rows = _rows(baseline, followup)
    canonical = _canonical(a, b)
    return h.seal({"schema": SCHEMA, "programme_id": a["programme_id"],
                   "programme_digest": a["programme_digest"],
                   "baseline_digest": baseline_digest, "followup_digest": followup_digest,
                   "same_snapshot": baseline_digest == followup_digest,
                   "baseline_programme_state": a["programme_state"],
                   "followup_programme_state": b["programme_state"],
                   "requirements": rows, "canonical_suites": canonical,
                   "summary": {"requirements": len(rows),
                               "requirements_with_observed_change": sum(r["administrative_change_observed"] for r in rows),
                               "requirements_with_followup_flags": sum(bool(r["followup_flags"]) for r in rows),
                               "requirements_with_noncomparable_counts": sum(not r["bounded_counts_comparable"] for r in rows),
                               "canonical_suites": len(canonical),
                               "canonical_conditions_changed": sum(r["changed"] for r in canonical)},
                   "chronology_verified": False, "source_records_reverified": False,
                   "canonical_requirements_satisfied": False, "decision_grade": False,
                   "deployment_authorized": False, "limitations": list(LIMITATIONS)})


def verify(comparison, baseline, followup, *, baseline_digest, followup_digest,
           expected_digest, verify_handoff):
    h.require(h.hex64(expected_digest), "independently supplied comparison digest required")
    rebuilt = compare(baseline, followup, baseline_digest=baseline_digest,
                      followup_digest=followup_digest, verify_handoff=verify_handoff)
    h.require(comparison == rebuilt and rebuilt["digest"] == expected_digest and
              h.digest(comparison) == h.digest(rebuilt), "comparison replay mismatch")
    return {"status": "ADMINISTRATIVE_COMPARISON_REPLAYED", "digest": expected_digest,
            "expected_digest_matched": True, "chronology_verified": False,
            "source_records_reverified": False, "decision_grade": False,
            "deployment_authorized": False}


def _unique_json(pairs):
    out = {}
    for key, value in pairs:
        h.require(key not in out, "duplicate JSON field")
        out[key] = value
    return out


def _invalid_constant(value):
    raise ValueError("nonfinite JSON number")


def read(path):
    with Path(path).open("rb") as f:
        data = f.read(h.MAX_BYTES+1)
    h.require(len(data) <= h.MAX_BYTES, "comparison input exceeds size limit")
    return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_json,
                      parse_constant=_invalid_constant)


def configure(parser):
    parser.add_argument("action", choices=("compare", "verify"))
    parser.add_argument("baseline")
    parser.add_argument("followup")
    parser.add_argument("--baseline-digest", required=True)
    parser.add_argument("--followup-digest", required=True)
    parser.add_argument("--comparison")
    parser.add_argument("--expected-digest")
    parser.set_defaults(func=cli)


def cli(args):
    from . import institution_gap_handoff as native
    from . import unified_assessment as u
    try:
        baseline, followup = read(args.baseline), read(args.followup)
        kwargs = {"baseline_digest": args.baseline_digest, "followup_digest": args.followup_digest,
                  "verify_handoff": native.verify}
        if args.action == "verify":
            h.require(args.comparison is not None and args.expected_digest is not None,
                      "comparison and expected digest required")
            result = verify(read(args.comparison), baseline, followup,
                            expected_digest=args.expected_digest, **kwargs)
        else:
            h.require(args.comparison is None and args.expected_digest is None, "unexpected verification options")
            result = compare(baseline, followup, **kwargs)
        payload = json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False)
        h.require(len(payload.encode("ascii")) <= h.MAX_BYTES, "comparison output exceeds size limit")
        print(payload)
    except u.ERRORS + (OSError, TypeError, KeyError, RecursionError, OverflowError):
        raise SystemExit("Unified evidence gap comparison refused.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    configure(parser)
    cli(parser.parse_args())
