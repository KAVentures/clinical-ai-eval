"""Version-bound research coverage for saved institutional buyer plans.

This is a bridge to the canonical selection contract, not an executor.
Experimental studies cannot satisfy required-but-not-run, change maturity,
or supply a procurement verdict. The link is explicit and immutable.
"""
from . import buyer_agent as buyer, buyer_plan, selection
from . import institution_access as access, institution_research as studio

MODULE_SUITES = {
    "citation": ("citation_verification",),
    "scribe": ("omission", "fabrication"),
    "decision": ("decision_certifiability",),
    "robustness": ("unsupported_inference", "rewording_robustness"),
}
SCOPE_KEYS = ("project_name", "intended_use", "setting", "jurisdiction", "created_at")
SCHEMA = "buyer-research-link/1"


def _schema(conn):
    studio._schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS buyer_research_links(
        study TEXT PRIMARY KEY REFERENCES research_studies(id),
        plan_id TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL)""")


def _buyer_plan(directory, identifier):
    plan = buyer_plan.load(directory, identifier)
    state = buyer.load(directory)
    expected_scope = {k: state.get(k, "") for k in SCOPE_KEYS}
    if (not plan["definitions_current"] or plan["buyer_scope"] != expected_scope
            or plan["vendor_id"] not in {v["vendor_id"] for v in state["vendors"]}):
        raise access.AccessDenied("current buyer plan and registered vendor required")
    actual = selection.select_suites(plan["plan"]["target_profile"]["types"])
    if actual != plan["plan"]["selection"]:
        raise access.AccessDenied("saved suite selection differs from current rules")
    return plan


def _material(directory, row, bp):
    _, _, research = studio._plan(directory, row)
    target = bp["plan"]["target"]
    if research["spec"]["target"] != {k: target[k] for k in ("name", "version")}:
        raise access.AccessDenied("study and buyer plan target/version must match exactly")
    required = {x["suite"] for x in bp["plan"]["selection"]["required_suites"]}
    related = sorted(required.intersection(MODULE_SUITES[row["kind"]]))
    if not related:
        raise access.AccessDenied("study does not address a required suite")
    return {"schema": SCHEMA, "buyer_plan_id": bp["plan_id"], "vendor_id": bp["vendor_id"],
            "study_id": row["id"], "study_plan_hash": research["plan_hash"],
            "study_kind": row["kind"], "study_owner": row["owner"],
            "target": research["spec"]["target"], "synthetic": research["spec"]["synthetic"],
            "related_suites": related, "decision_grade": False}


def link(directory, actor, study_id, plan_id, *, confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit scope and evidence association confirmation required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = studio._row(conn, study_id)
        studio._manager(conn, actor, row)
        bp = _buyer_plan(directory, plan_id)
        material = _material(directory, row, bp)
        if row["state"] == "analyzed":
            studio._report(directory, conn, row)
        material["link_stage"] = row["state"]
        material["linked_by"] = actor["user_id"]
        prior = conn.execute("SELECT * FROM buyer_research_links WHERE study=?", (study_id,)).fetchone()
        if prior:
            raise access.AccessDenied("study already associated; create a new study for a new scope")
        digest = buyer_plan._digest(material)
        conn.execute("INSERT INTO buyer_research_links VALUES(?,?,?,?)",
                     (study_id, plan_id, buyer_plan._canonical(material).decode("utf-8"), digest))
        access._audit(conn, actor["user_id"], "buyer-research-study-linked", study_id)
        return digest


def _read_link(directory, conn, bp, record, row):
    import json
    payload = json.loads(record["payload"])
    digest = record["digest"]
    if (buyer_plan._digest(payload) != digest or record["plan_id"] != bp["plan_id"]
            or record["study"] != row["id"]):
        raise access.AccessDenied("research association changed")
    expected = _material(directory, row, bp)
    if {k: v for k, v in payload.items() if k not in ("link_stage", "linked_by")} != expected:
        raise access.AccessDenied("research association no longer matches scope")
    if payload["link_stage"] not in ("planned", "collecting", "raw_collected", "collected",
            "reviewing", "analyzed", "collection_cancelled", "collection_interrupted"):
        raise access.AccessDenied("invalid association stage")
    if not isinstance(payload["linked_by"], str):
        raise access.AccessDenied("invalid association actor")
    result = {"study_id": row["id"], "kind": row["kind"], "related_suites": expected["related_suites"],
              "synthetic": expected["synthetic"], "link_digest": digest,
              "prospective_link": payload["link_stage"] == "planned",
              "study_state": row["state"], "report_hash": None,
              "evidence_state": "not_analyzed", "decision_grade": False}
    if row["state"] == "analyzed":
        report = studio._report(directory, conn, row)
        if report.get("synthetic") is not expected["synthetic"] or report.get("plan_hash") != row["plan_hash"]:
            raise access.AccessDenied("research report provenance differs from association")
        result.update(report_hash=report["digest"],
                      evidence_state="verified_experimental_report", summary=report["summary"])
    return result


def coverage(directory, actor, plan_id):
    """Read only the actor's authorized linked studies. Never change suite status."""
    with access.db(directory) as conn:
        _schema(conn)
        studio._actor(conn, actor, "admin", "operator")
        bp = _buyer_plan(directory, plan_id)
        rows = []
        invalid = 0
        for record in conn.execute("SELECT * FROM buyer_research_links WHERE plan_id=? ORDER BY study", (plan_id,)):
            row = studio._row(conn, record["study"])
            if actor["role"] != "admin" and row["owner"] != actor["user_id"]:
                continue
            try:
                rows.append(_read_link(directory, conn, bp, record, row))
            except (ValueError, RuntimeError, OSError, KeyError, TypeError, AttributeError):
                invalid += 1
                # Do not recover a corrupted association using guessed suite labels.
                rows.append({"study_id": row["id"], "evidence_state": "unavailable",
                             "related_suites": [], "decision_grade": False})
        selected = bp["plan"]["selection"]
        suite_rows = []
        for suite in selected["required_suites"]:
            relevant = [r for r in rows if suite["suite"] in r["related_suites"]]
            reports = [r for r in relevant if r["evidence_state"] == "verified_experimental_report"]
            suite_rows.append({"suite": suite["suite"], "because": suite["because"],
                "canonical_status": "runnable_not_execution_verified" if suite["implemented"] else "required_but_not_run",
                "canonical_blocked_reason": suite["blocked_reason"],
                "related_studies": [r["study_id"] for r in relevant],
                "verified_experimental_reports": len(reports),
                "non_synthetic_experimental_reports": sum(not r["synthetic"] for r in reports),
                "canonical_requirement_satisfied": False,
                "decision_grade": False})
        result = {"schema": "buyer-research-coverage/1", "buyer_plan_id": plan_id,
                  "vendor_id": bp["vendor_id"], "target": bp["plan"]["target"],
                  "required_but_not_run": selected["required_but_not_run"],
                  "suites": suite_rows, "studies": rows, "unavailable_associations": invalid,
                  "scope_visibility": "all_associations" if actor["role"] == "admin" else "own_associations",
                  "decision_grade": False,
                  "limitations": ["Related research is not execution of a canonical suite.",
                    "Canonical execution receipts are not evaluated by this view.",
                    "Verified report means integrity verified, not clinically validated or passed.",
                    "A report may be synthetic, incomplete or have no assessed units.",
                    "A prospective association does not independently prove preregistration.",
                    "No procurement or clinical decision is inferred."]}
        result["digest"] = buyer_plan._digest(result)
        access._audit(conn, actor["user_id"], "buyer-research-coverage-viewed", plan_id)
        return result
