"""Audited historical handoff to live programme comparison.

No additional snapshot database, uploads to external services, or clinical
authority. Baseline bytes are supplied by the administrator and must match
a prior successful local export audit event and its independently held digest.
"""
import argparse
import json
import sqlite3
from pathlib import Path
from . import institution_access as access, unified_assessment as u
from . import institution_gap_handoff as native, unified_gap_handoff as h
from . import unified_gap_comparison as comparison

SCHEMA = "institutional-gap-review-bundle/1"
EVENT = "unified-gap-review-exported"
MAX_BASELINE_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = h.MAX_BYTES


def encode(doc):
    return json.dumps(doc, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def parse_baseline(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_BASELINE_BYTES:
        raise access.AccessDenied("baseline exceeds browser import bound")
    try:
        return json.loads(raw, object_pairs_hook=comparison._unique_json,
                          parse_constant=comparison._invalid_constant)
    except (ValueError, RecursionError) as exc:
        raise access.AccessDenied("invalid baseline JSON") from exc


def _bundle(baseline, followup, baseline_digest, followup_digest):
    report = comparison.compare(baseline, followup, baseline_digest=baseline_digest,
                                followup_digest=followup_digest, verify_handoff=native.verify)
    bundle = h.seal({"schema": SCHEMA, "baseline": baseline, "followup": followup,
                     "comparison": report, "source_records_reverified": False,
                     "chronology_verified": False, "decision_grade": False,
                     "deployment_authorized": False})
    if len(encode(bundle)) > MAX_BUNDLE_BYTES:
        raise access.AccessDenied("review bundle exceeds export bound")
    return bundle


def verify(bundle, *, baseline_digest, followup_digest, expected_digest):
    """Portable replay, not authentication of the exporting institution."""
    h.keys(bundle, "schema baseline followup comparison source_records_reverified chronology_verified decision_grade deployment_authorized digest")
    h.require(bundle["schema"] == SCHEMA and h.hex64(expected_digest) and
              expected_digest == bundle["digest"], "bundle digest mismatch")
    rebuilt = _bundle(bundle["baseline"], bundle["followup"], baseline_digest, followup_digest)
    h.require(encode(bundle) == encode(rebuilt), "review bundle replay mismatch")
    return {"status": "INSTITUTIONAL_GAP_REVIEW_REPLAYED", "digest": expected_digest,
            "comparison_digest": rebuilt["comparison"]["digest"],
            "expected_digest_matched": True, "local_export_audit_reverified": False,
            "chronology_verified": False, "source_records_reverified": False,
            "decision_grade": False, "deployment_authorized": False}


def _capture(root, conn, actor, ident, baseline, baseline_digest):
    u._actor(conn, actor, "admin")
    # Validate the requested programme ID and baseline before constructing
    # any audit key or looking up historical records.
    current, _ = u._read(conn, ident)
    if not h.hex64(baseline_digest) or len(encode(baseline)) > MAX_BASELINE_BYTES:
        raise access.AccessDenied("bounded digest-pinned baseline required")
    native.verify(baseline, baseline_digest)
    if baseline["coverage"]["programme"] != current:
        raise access.AccessDenied("baseline belongs to a different frozen programme")
    receipt = conn.execute(
        "SELECT 1 FROM audit WHERE action=? AND object_id=? AND outcome='ok' LIMIT 1",
        (native.EVENT, ident+":"+baseline_digest)).fetchone()
    if not receipt:
        raise access.AccessDenied("prior successful local handoff export required")
    followup = native._capture(root, conn, actor, ident)
    return _bundle(baseline, followup, baseline_digest, followup["digest"])


def preview(directory, actor, ident, baseline, baseline_digest):
    root = Path(directory)
    with access.db(root) as conn:
        u._schema(conn)
        conn.execute("BEGIN")
        return _capture(root, conn, actor, ident, baseline, baseline_digest)


def export(directory, actor, ident, baseline, baseline_digest, approval, *, confirmed=False):
    if confirmed is not True or not h.hex64(approval):
        raise access.AccessDenied("explicit exact review bundle approval required")
    root = Path(directory)
    with access.db(root) as conn:
        u._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        bundle = _capture(root, conn, actor, ident, baseline, baseline_digest)
        if bundle["digest"] != approval:
            raise access.AccessDenied("review changed since approval; preview again")
        # Record the included follow-up as an approved handoff export so that
        # it can itself serve as a subsequent browser baseline. Both audit
        # records are committed together; no action occurs on a refused export.
        u._audit(conn, actor, native.EVENT, ident+":"+bundle["followup"]["digest"])
        u._audit(conn, actor, EVENT, ident+":"+approval)
        return bundle


def configure(parser):
    parser.add_argument("action", choices=("verify",))
    parser.add_argument("file")
    parser.add_argument("--baseline-digest", required=True)
    parser.add_argument("--followup-digest", required=True)
    parser.add_argument("--expected-digest", required=True)
    parser.set_defaults(func=cli)


def cli(args):
    try:
        bundle = comparison.read(args.file)
        result = verify(bundle, baseline_digest=args.baseline_digest,
                        followup_digest=args.followup_digest, expected_digest=args.expected_digest)
        print(encode(result).decode("ascii"))
    except u.ERRORS + (sqlite3.Error, RecursionError, OverflowError):
        raise SystemExit("Institutional evidence review verification refused.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    configure(parser)
    cli(parser.parse_args())
