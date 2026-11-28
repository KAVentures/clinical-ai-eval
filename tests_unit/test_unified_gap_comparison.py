"""Longitudinal handoff contract tests using the real gap projection."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from caeval import unified_gap_comparison as c, unified_gap_handoff as h
from caeval import unified_evidence_gaps as g


def fixture(state="unlinked", scan="bounded_scan", ready=0, active=0,
            source=None, programme_state="current", programme_id="demo"):
    plan = {"plan_id": "p", "vendor_id": "a", "target": {"name": "fictional", "version": "v1"},
            "required_suites": ["missing_information", "decision_certifiability"],
            "runnable_suites": ["missing_information"]}
    req = {"id": "r", "plan_id": "p", "kind": "text", "family": "missing_information"}
    p = h.seal({"programme_id": programme_id, "plans": [plan], "requirements": [req],
                "unplanned_canonical_suites": [{"plan_id":"p", "suite":"decision_certifiability",
                                                "state":"required_but_not_run"}]})
    scope = {"vendor_id": "a", "target": plan["target"]}
    coverage = {"programme":p, "state":programme_state, "requirements":[
        {"requirement":req, **scope, "state":state, "locked_source":source}]}
    work = {"programme_id":programme_id, "programme_digest":p["digest"],
            "programme_state":programme_state, "requirements":[
                {"requirement_id":"r", "kind":"text", "plan_id":"p", "family":"missing_information",
                 **scope, "coverage_state":state, "work_inventory_state":scan,
                 "scan_truncated":None if scan=="bounded_legacy_scan" else scan=="partial",
                 "native_work_records":[{"state":"completed"} for _ in range(ready)]+
                                       [{"state":"running"} for _ in range(active)]}]}
    return h.build(coverage, work, project=g.project)


def check(package, expected):
    # Deliberately synthetic coverage; actual native integration is tested separately.
    return h.verify(package, expected, project=g.project, verify_coverage=lambda _: None)


def compare(a, b, **overrides):
    kwargs = {"baseline_digest":a["digest"], "followup_digest":b["digest"], "verify_handoff":check}
    return c.compare(a, b, **{**kwargs, **overrides})


class GapComparisonTests(unittest.TestCase):
    def test_identical_and_deterministic(self):
        a = fixture()
        r = compare(a,a)
        self.assertTrue(r["same_snapshot"])
        self.assertEqual(r["summary"]["requirements_with_observed_change"],0)
        self.assertEqual(r,compare(a,a))
        self.assertFalse(r["chronology_verified"])
        self.assertFalse(r["canonical_requirements_satisfied"])
        self.assertEqual(len(r["canonical_suites"]),2)

    def test_available_source_and_replacement(self):
        source={"mode":"internal_regression","claim":"internal_regression","scope":"exact_buyer_plan",
                "source_id":"private-source"}
        a=fixture(ready=1)
        b=fixture(state="source_available",source=source)
        r=compare(a,b)["requirements"][0]
        self.assertEqual(r["association_change"],"association_added")
        self.assertEqual(r["followup_condition"],"internal_regression_only")
        self.assertEqual(r["followup_source_metadata_digest"],h.digest(source))
        self.assertNotIn("private-source",json.dumps(r))
        self.assertFalse(r["canonical_requirements_satisfied"])
        self.assertIn("verify_candidate_association",[t["code"] for t in r["no_longer_projected_tasks"]])
        replacement=fixture(state="source_available",source={**source,"source_id":"different"})
        self.assertEqual(compare(b,replacement)["requirements"][0]["association_change"],
                         "association_metadata_changed")

    def test_deterioration_and_stale(self):
        source={"mode":"internal_regression","claim":"internal_regression","scope":"exact_buyer_plan"}
        a=fixture(state="source_available",source=source)
        b=fixture(state="source_unavailable",source=source)
        r=compare(a,b)["requirements"][0]
        self.assertIn("previously_available_source_no_longer_available",r["followup_flags"])
        self.assertIn("locked_source_investigation_outstanding",r["followup_flags"])
        self.assertEqual(r["association_change"],"unchanged")
        self.assertIn("investigate_locked_source",[t["code"] for t in r["new_tasks"]])
        stale=fixture(state="stale_plan",scan="stale_plan",source=source,programme_state="stale_plan")
        r=compare(a,stale)["requirements"][0]
        self.assertIn("programme_stale",r["followup_flags"])
        self.assertIsNone(r["work_observations"]["potential_ready_records"]["observed_delta"])

    def test_bounded_counts_and_partial(self):
        a=fixture(ready=2,active=1)
        b=fixture(ready=1,active=3)
        r=compare(a,b)["requirements"][0]
        self.assertTrue(r["bounded_counts_comparable"])
        self.assertEqual(r["work_observations"]["potential_ready_records"]["observed_delta"],-1)
        self.assertEqual(r["work_observations"]["active_work_records"]["observed_delta"],2)
        for scan in ("partial","bounded_legacy_scan","unavailable"):
            r=compare(a,fixture(scan=scan))["requirements"][0]
            self.assertFalse(r["bounded_counts_comparable"])
            self.assertIsNone(r["work_observations"]["potential_ready_records"]["observed_delta"])
            self.assertIn("inventory_incomplete",r["followup_flags"])

    def test_exact_programme_and_trusted_digests(self):
        a=fixture()
        with self.assertRaises(ValueError): compare(a,fixture(programme_id="different"))
        with self.assertRaises(ValueError): compare(a,a,baseline_digest=None)
        with self.assertRaises(ValueError): compare(a,a,followup_digest="0"*64)
        forged=copy.deepcopy(a)
        forged["report"]["decision_grade"]=True
        forged=h.seal({k:v for k,v in forged.items() if k!="digest"})
        with self.assertRaises(ValueError): compare(a,forged)

    def test_full_replay_rejects_resealed_and_reversed(self):
        a,b=fixture(),fixture(ready=1)
        r=compare(a,b)
        kwargs={"baseline_digest":a["digest"],"followup_digest":b["digest"],"verify_handoff":check}
        self.assertEqual(c.verify(r,a,b,expected_digest=r["digest"],**kwargs)["status"],
                         "ADMINISTRATIVE_COMPARISON_REPLAYED")
        forged=copy.deepcopy(r)
        forged["summary"]["requirements_with_observed_change"]=0
        forged=h.seal({k:v for k,v in forged.items() if k!="digest"})
        with self.assertRaises(ValueError): c.verify(forged,a,b,expected_digest=forged["digest"],**kwargs)
        with self.assertRaises(ValueError): c.verify(r,b,a,expected_digest=r["digest"],
            baseline_digest=b["digest"],followup_digest=a["digest"],verify_handoff=check)
        with self.assertRaises(ValueError): c.verify(r,a,b,expected_digest="0"*64,**kwargs)

    def test_reader_rejects_duplicate_keys_nonfinite_oversized(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/"input.json"
            for raw in (b'{"x":1,"x":2}',b'{"x":NaN}',b' '* (h.MAX_BYTES+1)):
                p.write_bytes(raw)
                with self.assertRaises(ValueError): c.read(p)


if __name__ == "__main__":
    unittest.main()
