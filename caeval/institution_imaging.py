"""Audited institution-controlled diagnostic imaging study lifecycle.

Private reference cohort and individual results are never available to operators.
A frozen study ID cannot be replaced, and submissions cannot be retried with
different content. This is a separate exploratory research lane.
"""
import json
from . import diagnostic_imaging as imaging, institution_access as access

FROZEN = "diagnostic-imaging-study-frozen"
SUBMITTED = "diagnostic-imaging-predictions-locked"
SEALED = "diagnostic-imaging-study-sealed"
MAX_STUDIES = 30
MAX_INPUT = 1024 * 1024


def _actor(conn, actor, *roles):
    access.require(actor, *roles)
    user = conn.execute("SELECT role,enabled FROM users WHERE id=?", (actor["user_id"],)).fetchone()
    if not user or not user["enabled"] or user["role"] != actor["role"]:
        raise access.AccessDenied("active imaging research account required")


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS diagnostic_imaging_studies(
        id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL,
        actor TEXT NOT NULL, audit_seq INTEGER NOT NULL UNIQUE)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS diagnostic_imaging_submissions(
        study TEXT NOT NULL REFERENCES diagnostic_imaging_studies(id),
        candidate TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,
        actor TEXT NOT NULL, audit_seq INTEGER NOT NULL UNIQUE,
        PRIMARY KEY(study,candidate))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS diagnostic_imaging_seals(
        study TEXT PRIMARY KEY REFERENCES diagnostic_imaging_studies(id),
        digest TEXT NOT NULL, actor TEXT NOT NULL, audit_seq INTEGER NOT NULL UNIQUE)""")


def _json(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _audit(conn, actor, action, obj):
    access._audit(conn, actor["user_id"], action, obj)
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _inventory(conn):
    """Detect deleted/extra records and incorrect event linkage, not OS compromise."""
    for table, action, cols in (
            ("diagnostic_imaging_studies", FROZEN, ("id", "digest")),
            ("diagnostic_imaging_submissions", SUBMITTED, ("study", "candidate", "digest")),
            ("diagnostic_imaging_seals", SEALED, ("study", "digest"))):
        rows = conn.execute("SELECT * FROM " + table).fetchall()
        expected = {(r["audit_seq"], r["actor"], ":".join(r[k] for k in cols)) for r in rows}
        events = conn.execute("SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok'", (action,)).fetchall()
        actual = {(r["seq"], r["actor"], r["object_id"]) for r in events}
        if len(expected) != len(rows) or len(actual) != len(events) or actual != expected:
            raise access.AccessDenied("imaging audit inventory mismatch")


def _get(conn, ident):
    if not imaging._token(ident):
        raise access.AccessDenied("invalid study identifier")
    row = conn.execute("SELECT * FROM diagnostic_imaging_studies WHERE id=?", (ident,)).fetchone()
    if not row:
        raise access.AccessDenied("unknown imaging study")
    plan = json.loads(row["payload"])
    imaging._verify_plan(plan)
    if plan["digest"] != row["digest"] or plan["spec"]["study_id"] != ident:
        raise access.AccessDenied("imaging study integrity mismatch")
    docs = []
    for sub in conn.execute("SELECT * FROM diagnostic_imaging_submissions WHERE study=? ORDER BY candidate", (ident,)):
        doc = json.loads(sub["payload"])
        imaging._predictions(plan, doc)
        if (imaging._digest(doc) != sub["digest"] or doc["candidate"]["id"] != sub["candidate"]
                or sub["audit_seq"] <= row["audit_seq"]):
            raise access.AccessDenied("imaging submission integrity mismatch")
        docs.append(doc)
    seal = conn.execute("SELECT * FROM diagnostic_imaging_seals WHERE study=?", (ident,)).fetchone()
    evidence = None
    if seal:
        evidence = imaging.package(plan, docs)
        last = conn.execute("SELECT max(audit_seq) FROM diagnostic_imaging_submissions WHERE study=?", (ident,)).fetchone()[0]
        if seal["digest"] != evidence["digest"] or seal["audit_seq"] <= max(row["audit_seq"], last or 0):
            raise access.AccessDenied("sealed imaging evidence integrity mismatch")
    return plan, docs, evidence


def _status(plan, docs, evidence, *, committee=False):
    spec = plan["spec"]
    submitted = {d["candidate"]["id"] for d in docs}
    result = {"id": spec["study_id"], "task_id": spec["task_id"], "modality": spec["modality"],
              "n_cases": len(spec["cases"]), "state": "sealed" if evidence else "collecting",
              "candidates": [{**c, "submitted": c["id"] in submitted} for c in spec["candidates"]],
              "decision_grade": False}
    # No reference prevalence, case labels, strata, salt or individual
    # predictions. Committee sees coverage only, not even subgroup metrics.
    if evidence:
        result["evidence_digest"] = evidence["digest"]
        result["roster_complete"] = evidence["report"]["roster_complete"]
        if not committee:
            result["metrics"] = [
                {"candidate": c["candidate"], "status": c["status"],
                 "overall": c["metrics"]["overall"] if c["metrics"] else None}
                for c in evidence["report"]["candidates"]]
    return result


def preview(directory, actor, spec):
    with access.db(directory) as conn:
        _actor(conn, actor, "admin")
        _schema(conn)
        _inventory(conn)
        # Deterministic validation only. The preview nonce is never exported.
        p = imaging.freeze(spec, private_nonce="0"*64, confirmed=True)
        if conn.execute("SELECT 1 FROM diagnostic_imaging_studies WHERE id=?", (p["spec"]["study_id"],)).fetchone():
            raise access.AccessDenied("study ID already frozen")
        return {"spec": p["spec"], "approval_digest": imaging._digest(p["spec"]),
                "n_cases": len(p["spec"]["cases"]), "decision_grade": False}


def freeze(directory, actor, spec, approval_digest, *, confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit prospective freeze required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn, actor, "admin")
        _inventory(conn)
        plan = imaging.freeze(spec, confirmed=True)
        if imaging._digest(plan["spec"]) != approval_digest:
            raise access.AccessDenied("study differs from approved preview")
        ident = plan["spec"]["study_id"]
        if conn.execute("SELECT 1 FROM diagnostic_imaging_studies WHERE id=?", (ident,)).fetchone():
            raise access.AccessDenied("study ID already frozen")
        if conn.execute("SELECT count(*) FROM diagnostic_imaging_studies").fetchone()[0] >= MAX_STUDIES:
            raise access.AccessDenied("imaging study limit reached")
        seq = _audit(conn, actor, FROZEN, ident+":"+plan["digest"])
        conn.execute("INSERT INTO diagnostic_imaging_studies VALUES(?,?,?,?,?)",
                     (ident, _json(plan), plan["digest"], actor["user_id"], seq))
        return ident


def overview(directory, actor, *, committee=False):
    with access.db(directory) as conn:
        _actor(conn, actor, *(("admin", "operator", "observer") if committee else ("admin", "operator")))
        _schema(conn)
        _inventory(conn)
        result = []
        for row in conn.execute("SELECT id FROM diagnostic_imaging_studies ORDER BY audit_seq").fetchall():
            try:
                result.append(_status(*_get(conn, row["id"]), committee=committee))
            except (ValueError, KeyError, TypeError, AttributeError, RuntimeError):
                result.append({"id": row["id"], "state": "unavailable", "decision_grade": False})
        return result


def detail(directory, actor, ident):
    with access.db(directory) as conn:
        _actor(conn, actor, "admin", "operator")
        _schema(conn)
        _inventory(conn)
        return _status(*_get(conn, ident))


def download(directory, actor, ident, candidate, kind):
    if kind not in ("exchange", "template"):
        raise access.AccessDenied("invalid candidate export")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn, actor, "admin", "operator")
        _inventory(conn)
        plan, _, evidence = _get(conn, ident)
        if evidence:
            raise access.AccessDenied("sealed study cannot export candidate material")
        doc = (imaging.exchange if kind == "exchange" else imaging.template)(plan, candidate)
        _audit(conn, actor, "diagnostic-imaging-candidate-exported", ident+":"+candidate+":"+kind+":"+imaging._digest(doc))
        return doc


def submit(directory, actor, ident, doc, *, confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit prediction lock required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn, actor, "admin", "operator")
        _inventory(conn)
        plan, docs, evidence = _get(conn, ident)
        if evidence:
            raise access.AccessDenied("study already sealed")
        imaging._predictions(plan, doc)
        candidate = doc["candidate"]["id"]
        digest = imaging._digest(doc)
        for prior in docs:
            if prior["candidate"]["id"] == candidate:
                if imaging._digest(prior) != digest:
                    raise access.AccessDenied("cannot replace locked candidate predictions")
                return digest
        seq = _audit(conn, actor, SUBMITTED, ident+":"+candidate+":"+digest)
        conn.execute("INSERT INTO diagnostic_imaging_submissions VALUES(?,?,?,?,?,?)",
                     (ident, candidate, _json(doc), digest, actor["user_id"], seq))
        return digest


def _seal_binding(plan, docs):
    # Preview binds exact submissions but does not disclose reference labels
    # or scores. No interim scientific report is released.
    return imaging._digest([plan["digest"], sorted(imaging._digest(d) for d in docs)])


def seal_preview(directory, actor, ident):
    with access.db(directory) as conn:
        _actor(conn, actor, "admin")
        _schema(conn)
        _inventory(conn)
        plan, docs, evidence = _get(conn, ident)
        if evidence:
            raise access.AccessDenied("study already sealed")
        return {"approval_digest": _seal_binding(plan, docs),
                "n_submitted": len(docs), "n_expected": len(plan["spec"]["candidates"])}


def seal(directory, actor, ident, approval_digest, *, confirmed=False, allow_incomplete=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit study seal required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn, actor, "admin")
        _inventory(conn)
        plan, docs, prior = _get(conn, ident)
        if prior:
            raise access.AccessDenied("study already sealed")
        if _seal_binding(plan, docs) != approval_digest:
            raise access.AccessDenied("study submission inventory changed since preview")
        if len(docs) < len(plan["spec"]["candidates"]) and allow_incomplete is not True:
            raise access.AccessDenied("explicit incomplete-roster seal required")
        evidence = imaging.package(plan, docs)
        seq = _audit(conn, actor, SEALED, ident+":"+evidence["digest"])
        conn.execute("INSERT INTO diagnostic_imaging_seals VALUES(?,?,?,?)",
                     (ident, evidence["digest"], actor["user_id"], seq))
        return evidence["digest"]


def export(directory, actor, ident, expected_digest, *, confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit restricted evidence export required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn, actor, "admin")
        _inventory(conn)
        _, _, evidence = _get(conn, ident)
        if not evidence or evidence["digest"] != expected_digest:
            raise access.AccessDenied("exact sealed evidence digest required")
        _audit(conn, actor, "diagnostic-imaging-private-evidence-exported", ident+":"+expected_digest)
        return evidence
