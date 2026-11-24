"""Research-grade exploratory analysis of institutional review submissions.

Use the existing adjudication implementation on an isolated temporary copy of
the required artifacts. Account-bound submissions are not equivalent to
credential-verified, independently issued clinical review packets. Do not
mint packets, change original manifests, or upgrade conformance.
"""
from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

from . import adjudicate, buyer_agent as buyer, buyer_jobs as jobs, buyer_plan, manifest
from . import buyer_physician_review as review, institution_access as access

MAX_REVIEWERS = 30


def _authorise(conn, actor):
    access.require(actor, "admin", "operator")
    user = conn.execute("SELECT role,enabled FROM users WHERE id=?", (actor["user_id"],)).fetchone()
    if not user or not user["enabled"] or user["role"] != actor["role"]:
        raise access.AccessDenied("active authorised account required")


def _snapshot(directory, actor, job_id, family, digest, slots):
    with access.db(directory) as conn:
        _authorise(conn, actor)
        rows = conn.execute("""SELECT s.*, a.manifest AS assigned_manifest
            FROM submissions s LEFT JOIN assignments a ON a.reviewer=s.reviewer
            AND a.job=s.job AND a.family=s.family
            WHERE s.job=? AND s.family=? ORDER BY s.reviewer LIMIT ?""",
            (job_id, family, MAX_REVIEWERS+1)).fetchall()
    if len(rows)>MAX_REVIEWERS:
        raise access.AccessDenied("reviewer limit exceeded")
    records=[]
    for row in rows:
        payload=json.loads(row["payload"])
        expected={"schema":"institution-review/1","reviewer":row["reviewer"],
                  "job":job_id,"family":family,"manifest":digest,
                  "identity":"authenticated_local_account",
                  "clinical_credentials_verified":False,"adjudicated":False,"decision_grade":False}
        if (row["manifest"]!=digest or row["assigned_manifest"]!=digest
                or not isinstance(payload,dict)
                or set(payload)!=set(expected)|{"verdicts"}
                or any(payload.get(k)!=v for k,v in expected.items())
                or buyer_plan._digest(payload)!=row["digest"]):
            raise access.AccessDenied("review provenance or integrity mismatch")
        verdicts=payload["verdicts"]
        if not isinstance(verdicts,list) or len(verdicts)!=len(slots):
            raise access.AccessDenied("review coverage mismatch")
        seen=set()
        for v in verdicts:
            if (not isinstance(v,dict) or set(v)!={"slot","verdict","notes"}
                    or not isinstance(v["slot"],str) or v["slot"] not in slots
                    or v["slot"] in seen or v["verdict"] not in review.VERDICTS
                    or not isinstance(v["notes"],str) or len(v["notes"])>4000):
                raise access.AccessDenied("invalid stored review verdict")
            seen.add(v["slot"])
        records.append((row["digest"],payload))
    return records


def analyze(directory, actor, job_id, family):
    access.require(actor,"admin","operator")
    package, slots=review._source(directory,job_id,family)
    digest=package["manifest_hash"]
    records=_snapshot(directory,actor,job_id,family,digest,slots)
    if len(records)<2 or not slots:
        raise access.AccessDenied("at least two submitted reviewer accounts and nonempty queue required")
    source=jobs._job_path(directory,job_id)/"out"/("run_"+family)
    # No secrets, packets, existing adjudication or validity declarations copied.
    # This deliberately cannot satisfy the existing L2 packet/validity gates.
    with tempfile.TemporaryDirectory(prefix=".analysis-", dir=access._root(directory)) as temp:
        ws=Path(temp)
        (ws/"adjudication").mkdir()
        original_manifest=json.loads((source/manifest.MANIFEST_FILE).read_text(encoding="utf-8"))
        if original_manifest.get("manifest_hash")!=digest:
            raise access.AccessDenied("assessment manifest changed")
        recorded={entry["path"]:entry["sha256"] for entry in original_manifest["artifacts"].values()}
        for name in ("results.jsonl","run_meta.json","review_manifest.json"):
            path=source/name
            if path.is_symlink() or not path.is_file():
                raise access.AccessDenied("missing analysis input")
            (ws/name).write_bytes(path.read_bytes())
            if manifest.hash_file(ws/name)!=recorded.get(name):
                raise access.AccessDenied("copied analysis artifact differs from verified manifest")
        files=[]
        for index,(_,payload) in enumerate(records,1):
            # Pseudonymous labels in aggregate reports; account identities stay
            # in the original institutional records. Notes not needed by stats.
            rid="reviewer-"+str(index)
            path=ws/"adjudication"/(rid+".csv")
            with path.open("w",encoding="utf-8",newline="") as handle:
                w=csv.DictWriter(handle,fieldnames=["reviewer_id","cell_id","human_verdict_safe_unsafe"])
                w.writeheader()
                for v in payload["verdicts"]:
                    w.writerow({"reviewer_id":rid,"cell_id":slots[v["slot"]]["cell_id"],
                                "human_verdict_safe_unsafe":v["verdict"]})
            files.append(str(path))
        report=adjudicate.adjudicate(str(ws),files)
        if report["level"]=="L2" or report["l2_adjudication_gate_passed"]:
            raise access.AccessDenied("unexpected clinical claim; refusing analysis")
    # Recheck original package after reading it, and require exactly the same
    # submitted review set. Never silently combine snapshots across changes.
    after,after_slots=review._source(directory,job_id,family)
    if after["manifest_hash"]!=digest or after_slots!=slots:
        raise access.AccessDenied("assessment changed during analysis")
    if _snapshot(directory,actor,job_id,family,digest,slots)!=records:
        raise access.AccessDenied("review set changed; rerun analysis")
    result={
        "schema":"institution-exploratory-review-analysis/1",
        "job_id":job_id,"family_id":family,"manifest_hash":digest,
        "review_submission_digests":[r[0] for r in records],
        "n_reviewers":report["n_reviewers"],"queue_size":report["queue_size"],
        "queue_completion":report["queue_completion"],"under_reviewed_cells":report["under_reviewed_cells"],
        "n_contested":report["n_contested"],"mandatory_completion":report["mandatory_completion"],
        "human_unsafe_rate":report["human_unsafe_rate"],
        "inter_rater":report["inter_rater"],"judge_vs_human":report["judge_vs_human"],
        "validity_adjudicated":False,"l2_adjudication_gate_passed":False,
        "claim_authority":"exploratory_account_bound_review_only",
        "decision_grade":False,"original_package_modified":False,
        "limitations":["Accounts are authenticated, not credential-verified independent clinicians.",
                       "Canonical signed review packets and perturbation-validity review are absent.",
                       "The review queue is selected and not a population-representative sample.",
                       "Missing and contested verdicts are not safe; operating points apply only to judged cells."],
    }
    access.event(directory,actor["user_id"],"exploratory-review-analysis",job_id)
    return result


def overview(directory,actor):
    access.require(actor,"admin","operator","observer")
    with access.db(directory) as conn:
        user=conn.execute("SELECT role,enabled FROM users WHERE id=?",(actor["user_id"],)).fetchone()
        if not user or not user["enabled"] or user["role"]!=actor["role"]:
            raise access.AccessDenied("active account required")
        counts={(r["job"],r["family"],r["manifest"]):int(r["n"])
                for r in conn.execute("""SELECT job,family,manifest,count(*) AS n
                    FROM assignments GROUP BY job,family,manifest""")}
        submissions={(r["job"],r["family"],r["manifest"]):int(r["n"])
                     for r in conn.execute("""SELECT job,family,manifest,count(*) AS n
                        FROM submissions GROUP BY job,family,manifest""")}
    rows=[]
    for stored in jobs.list_jobs(directory):
        row=jobs.status(directory,stored["id"])
        entry={"job_id":row["id"],"mode":row["mode"],"status":row["status"],"families":[]}
        if row["status"]=="completed":
            for p in row["result"]["packages"]:
                key=(row["id"],p["family_id"],p["manifest_hash"])
                entry["families"].append({"family_id":p["family_id"],"manifest_hash":p["manifest_hash"],
                    "review_count":p["review_count"],"recorded_claim":p["claim"],
                    "assigned_accounts":counts.get(key,0),
                    "submitted_accounts":submissions.get(key,0)})
        rows.append(entry)
    access.event(directory,actor["user_id"],"institution-evidence-overview")
    return {"jobs":rows,"decision_grade":False}
