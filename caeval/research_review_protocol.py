"""Prospective institutional review protocol, separate from clinical validity.

The local audit sequence establishes ordering within this workspace only.
Neither hashes nor accounts prove external blinding or clinical credentials.
"""
import json

from . import buyer_plan, institution_access as access, institution_research as studio

SCHEMA = "prospective-research-review/1"
EVENT = "research-review-protocol-frozen"
EXPOSURE = ("research-export-exchange", "research-export-responses",
            "research-collection-queued", "research-responses-collected")


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS research_review_protocols(
        study TEXT PRIMARY KEY REFERENCES research_studies(id),
        payload TEXT NOT NULL, digest TEXT NOT NULL, audit_seq INTEGER NOT NULL UNIQUE)""")


def _exposure(conn, identifier, before=None):
    sql = "SELECT 1 FROM audit WHERE object_id=? AND action IN (?,?,?,?) AND outcome='ok'"
    params = (identifier, *EXPOSURE)
    if before is not None:
        sql += " AND seq<?"
        params += (before,)
    return conn.execute(sql+" LIMIT 1", params).fetchone() is not None


def roster(conn, row, mod, reviewers, validity_reviewers, administrator):
    """Use the exact module roster contract and live independent accounts."""
    if mod is studio.decision:
        raise access.AccessDenied("deterministic decision studies have no human review roster")
    if mod is studio.robustness:
        v, a, _ = mod._roster(validity_reviewers, reviewers, administrator)
        result = [(rid, "reference_validity") for rid in v]+[(rid, "isolated_answer") for rid in a]
    else:
        if validity_reviewers:
            raise access.AccessDenied("unexpected separate validity roster")
        a, _ = mod._roster(reviewers, administrator)
        result = [(rid, "clinical") for rid in a]
    for rid, _ in result:
        user = conn.execute("SELECT role,enabled FROM users WHERE id=?", (rid,)).fetchone()
        if not user or user["role"] != "reviewer" or not user["enabled"] or rid == row["owner"]:
            raise access.AccessDenied("independent enabled reviewer account required")
    return sorted([{"reviewer": rid, "role": role} for rid, role in result],
                  key=lambda x: (x["role"], x["reviewer"]))


def read(conn, row):
    """Verify immutable local protocol and ordering; absence is not prospective."""
    _schema(conn)
    record = conn.execute("SELECT * FROM research_review_protocols WHERE study=?",
                          (row["id"],)).fetchone()
    events = conn.execute("SELECT seq,actor,at FROM audit WHERE object_id=? AND action=? AND outcome='ok'",
                          (row["id"], EVENT)).fetchall()
    if not record:
        if events:
            raise access.AccessDenied("review protocol ledger missing")
        return None
    try:
        payload = json.loads(record["payload"])
        if (not isinstance(payload, dict) or set(payload) != {
                "schema", "study_id", "plan_hash", "kind", "owner", "administrator",
                "research_question", "analysis_intent", "roster", "external_confirmation"}
                or payload["schema"] != SCHEMA or payload["study_id"] != row["id"]
                or payload["plan_hash"] != row["plan_hash"] or payload["kind"] != row["kind"]
                or payload["owner"] != row["owner"] or payload["external_confirmation"] is not True
                or buyer_plan._digest(payload) != record["digest"] or len(events) != 1
                or events[0]["seq"] != record["audit_seq"]
                or events[0]["actor"] != payload["administrator"]
                or _exposure(conn, row["id"], record["audit_seq"])):
            raise access.AccessDenied("review protocol or local ordering changed")
    except (ValueError, TypeError, KeyError) as exc:
        raise access.AccessDenied("invalid review protocol ledger") from exc
    return {"protocol": payload, "digest": record["digest"],
            "audit_sequence": record["audit_seq"], "recorded_at_unix": events[0]["at"]}


def freeze(directory, actor, identifier, reviewers, validity_reviewers,
           research_question, analysis_intent, *, confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit independent protocol confirmation required")
    for value in (research_question, analysis_intent):
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise access.AccessDenied("nonblank bounded research question and analysis intent required")
    with access.db(directory) as conn:
        studio._schema(conn)
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        studio._actor(conn, actor, "admin")
        row = studio._row(conn, identifier)
        _, mod, _ = studio._plan(directory, row)
        if row["state"] != "planned" or read(conn, row) or _exposure(conn, identifier):
            raise access.AccessDenied("freeze before any recorded candidate exchange or collection")
        people = roster(conn, row, mod, reviewers, validity_reviewers, actor["user_id"])
        payload = {"schema": SCHEMA, "study_id": identifier, "plan_hash": row["plan_hash"],
                   "kind": row["kind"], "owner": row["owner"], "administrator": actor["user_id"],
                   "research_question": research_question.strip(), "analysis_intent": analysis_intent.strip(),
                   "roster": people, "external_confirmation": True}
        digest = buyer_plan._digest(payload)
        access._audit(conn, actor["user_id"], EVENT, identifier)
        seq = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO research_review_protocols VALUES(?,?,?,?)",
                     (identifier, json.dumps(payload, ensure_ascii=False, allow_nan=False), digest, seq))
        return digest


def verify_assignment(conn, row, proposed=None):
    record = read(conn, row)
    if record is None:
        return
    if proposed is None:
        proposed = [dict(r) for r in conn.execute(
            "SELECT reviewer,role FROM research_reviewers WHERE study=? ORDER BY role,reviewer",
            (row["id"],))]
    if proposed != record["protocol"]["roster"]:
        raise access.AccessDenied("review assignment differs from frozen protocol")


def status(conn, row):
    if row["kind"] == "decision":
        return "not_applicable"
    if read(conn, row):
        return "locally_prospective"
    if row["state"] == "planned" and not _exposure(conn, row["id"]):
        return "available"
    return "not_preregistered"


def receipt(directory, conn, row):
    record = read(conn, row)
    if row["state"] in ("reviewing", "analyzed") and row["kind"] != "decision":
        studio._review(directory, conn, row)
    if row["state"] == "analyzed":
        studio._report(directory, conn, row)
    result = {"schema": "research-review-provenance/1", "study_id": row["id"],
              "plan_hash": row["plan_hash"], "study_state": row["state"],
              "status": status(conn, row), "registration": record,
              "verified_report_digest": row["report_hash"] if row["state"] == "analyzed" else None,
              "decision_grade": False,
              "limitations": ["Local audit ordering only; external prior exposure is not excluded.",
                              "Accounts and attestations do not independently establish clinical credentials or blinding.",
                              "Research intent is descriptive, not a machine-executed statistical analysis plan."]}
    return {**result, "digest": buyer_plan._digest(result)}
