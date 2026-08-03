"""Adversarial tests for the certificate verifier and the MMIP solver.

These exist because a prior reference implementation passed its whole suite while
SILENTLY issuing false certificates: every fixture used `severity: "critical"`, the
one value that made checks run, so the suite never varied the field that decides
whether checks execute at all.

The rule encoded here: **no check may be silenced by how a vocabulary token is
spelled, and no malformed input may ever certify.**
"""
import json
import unittest
from pathlib import Path

from caeval.certificates import (
    BLOCK, CERTIFIED, DEFER, UNKNOWN, MMIPError,
    greedy_query_set, is_decision_determining, minimum_query_sets, verify_certificate,
)
from caeval.certificates.mmip import (
    action_is_determined, information_efficiency, resolvable, validate_worlds,
    witness_of_underdetermination,
)
from caeval.util import repo_root


def check(**kw):
    base = {"id": "c1", "label": "active major bleeding", "status": "absent",
            "severity": "critical", "certificate_effect": "block", "provenance": ["ESC#9.1"]}
    base.update(kw)
    return base


def cert(**kw):
    c = {
        "certificate_id": "t1",
        "patient_snapshot": {"captured_at": "2026-08-03T10:00:00Z", "facts": {}},
        "evidence_bundle": [{"id": "ESC-AF", "version": "2024", "status": "active"}],
        "action": {"code": "apixaban-5mg-bid"},
        "support": [{"rule_id": "R1", "applicable": True, "provenance": ["ESC-AF#7.2"]}],
        "critical_questions": [],
        "contraindications": [],
    }
    c.update(kw)
    return c


# Spellings a real case author might plausibly produce. NONE may silence a check.
SEVERITY_SPELLINGS = ["critical", "high", "HIGH", "Critical", " critical ", "moderate",
                      "low", "urgent", "severe", "", None, 1, True, [], {}]


class TestSeverityCannotSilenceAChecks(unittest.TestCase):
    """I5 — the regression that motivated this whole module."""

    def test_present_contraindication_always_blocks(self):
        for sev in SEVERITY_SPELLINGS:
            ci = check(status="present")
            if sev is None:
                ci.pop("severity")
            else:
                ci["severity"] = sev
            v = verify_certificate(cert(contraindications=[ci])).verdict
            self.assertEqual(v, BLOCK, f"severity={sev!r} must not certify a PRESENT contraindication")

    def test_failed_critical_question_always_blocks(self):
        for sev in SEVERITY_SPELLINGS:
            cq = check(id="q1", label="renal dose", status="fail")
            if sev is None:
                cq.pop("severity")
            else:
                cq["severity"] = sev
            self.assertEqual(verify_certificate(cert(critical_questions=[cq])).verdict, BLOCK)

    def test_low_severity_still_blocks_when_effect_is_block(self):
        """severity is NOT the verdict axis — a 'low' severity block still blocks."""
        ci = check(status="present", severity="low", certificate_effect="block")
        self.assertEqual(verify_certificate(cert(contraindications=[ci])).verdict, BLOCK)

    def test_unrecognized_severity_is_reported_not_swallowed(self):
        ci = check(severity="urgent")
        codes = [f.code for f in verify_certificate(cert(contraindications=[ci])).findings]
        self.assertIn("UNRECOGNIZED_SEVERITY", codes)


class TestCertificateEffectIsTheVerdictAxis(unittest.TestCase):
    def test_effect_defer_defers_rather_than_blocks(self):
        ci = check(status="present", certificate_effect="defer")
        self.assertEqual(verify_certificate(cert(contraindications=[ci])).verdict, DEFER)

    def test_missing_or_unrecognized_effect_fails_closed_to_block(self):
        for eff in [None, "", "warn", "BLOCK!", 3, [], "ignore"]:
            ci = check(status="present")
            if eff is None:
                ci.pop("certificate_effect")
            else:
                ci["certificate_effect"] = eff
            r = verify_certificate(cert(contraindications=[ci]))
            self.assertEqual(r.verdict, BLOCK, f"certificate_effect={eff!r} must fail closed")
            self.assertIn("UNRECOGNIZED_CERTIFICATE_EFFECT", [f.code for f in r.findings])

    def test_effect_is_case_insensitive_and_trimmed(self):
        ci = check(status="present", certificate_effect="  DEFER  ")
        self.assertEqual(verify_certificate(cert(contraindications=[ci])).verdict, DEFER)


class TestStatusVocabulary(unittest.TestCase):
    def test_unrecognized_status_never_certifies(self):
        for status in ["Present", "PRESENT", "positive", "yes", "", 0, None, []]:
            ci = check()
            if status is None:
                ci.pop("status")
            else:
                ci["status"] = status
            self.assertNotEqual(verify_certificate(cert(contraindications=[ci])).verdict, CERTIFIED,
                                f"status={status!r} must not certify")

    def test_unknown_status_defers_and_names_itself(self):
        ci = check(status="unknown", label="allergy status")
        r = verify_certificate(cert(contraindications=[ci]))
        self.assertEqual(r.verdict, DEFER)
        self.assertIn("allergy status", r.next_information)

    def test_not_applicable_needs_no_provenance(self):
        ci = check(status="not_applicable")
        ci.pop("provenance")
        self.assertEqual(verify_certificate(cert(contraindications=[ci])).verdict, CERTIFIED)


class TestFailClosedInvariants(unittest.TestCase):
    """I1/I2 — malformed input never certifies, and never with zero findings."""

    MALFORMED = [
        {},
        "not a mapping",
        cert(patient_snapshot={"facts": {}}),
        cert(evidence_bundle=[]),
        cert(evidence_bundle=[{"id": "x"}]),
        cert(evidence_bundle=[{"id": "x", "version": "1", "status": "expired"}]),
        cert(action={}),
        cert(support="nope"),
        cert(support=[]),
        cert(support=[{"rule_id": "R1", "applicable": True}]),          # no provenance
        cert(critical_questions="nope"),
        cert(contraindications=42),
        cert(contraindications=[["not", "a", "mapping"]]),
    ]

    def test_never_certifies(self):
        for c in self.MALFORMED:
            self.assertNotEqual(verify_certificate(c).verdict, CERTIFIED, f"{str(c)[:60]} certified")

    def test_never_zero_findings(self):
        for c in self.MALFORMED:
            self.assertTrue(verify_certificate(c).findings, f"{str(c)[:60]} produced no findings")

    def test_never_raises(self):
        for c in self.MALFORMED + [None, 42, [], set()]:
            verify_certificate(c)          # must not raise

    def test_every_defer_names_next_information(self):
        """I4 — a DEFER with nothing to ask for is an unexplained refusal."""
        for c in self.MALFORMED:
            r = verify_certificate(c)
            if r.verdict == DEFER:
                self.assertTrue(r.next_information, f"{str(c)[:60]} deferred with no next_information")

    def test_duplicate_check_ids_are_flagged(self):
        c = cert(contraindications=[check(id="dup"), check(id="dup", status="present")])
        r = verify_certificate(c)
        self.assertIn("DUPLICATE_CHECK_ID", [f.code for f in r.findings])
        self.assertEqual(r.verdict, BLOCK)      # the present one still blocks

    def test_clean_certificate_certifies(self):
        r = verify_certificate(cert())
        self.assertEqual(r.verdict, CERTIFIED)
        self.assertEqual(r.next_information, ())


class TestSchemaContract(unittest.TestCase):
    SCHEMA = repo_root() / "schemas" / "clinical_certificate.schema.json"

    def test_schema_separates_the_two_axes_and_requires_both(self):
        s = json.loads(self.SCHEMA.read_text())
        for d in ("check_cq", "check_contra"):
            req = s["$defs"][d]["required"]
            self.assertIn("severity", req)
            self.assertIn("certificate_effect", req)
        self.assertEqual(s["$defs"]["certificate_effect"]["enum"], ["block", "defer"])
        self.assertIn("high", s["$defs"]["severity"]["enum"])

    def test_schema_validates_a_clean_certificate(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed (optional)")
        c = cert(contraindications=[check()], critical_questions=[
            check(id="q1", status="pass", certificate_effect="defer")])
        jsonschema.validate(c, json.loads(self.SCHEMA.read_text()))


class TestMMIP(unittest.TestCase):
    W = [
        {"safe": True,  "answers": {"egfr_low": False, "allergy": False, "invasive": True}},
        {"safe": False, "answers": {"egfr_low": True,  "allergy": False, "invasive": True}},
        {"safe": False, "answers": {"egfr_low": False, "allergy": True,  "invasive": False}},
    ]

    def test_finds_minimum_not_merely_minimal(self):
        sols = minimum_query_sets(self.W, ["egfr_low", "allergy", "invasive"])
        self.assertTrue(all(len(s) == 2 for s in sols))
        self.assertIn(("egfr_low", "allergy"), sols)

    def test_no_questions_required_differs_from_no_solution(self):
        decided = [{"safe": True, "answers": {}}, {"safe": True, "answers": {}}]
        self.assertEqual(minimum_query_sets(decided, ["q"]), [tuple()])   # already decided
        unresolvable = [{"safe": True, "answers": {"q": 1}}, {"safe": False, "answers": {"q": 1}}]
        self.assertEqual(minimum_query_sets(unresolvable, ["q"]), [])     # no solution
        self.assertNotEqual(minimum_query_sets(decided, ["q"]),
                            minimum_query_sets(unresolvable, ["q"]))

    def test_absent_answer_is_not_a_test_result(self):
        """Understating required information is the worst failure direction."""
        M = [{"safe": True, "answers": {}}, {"safe": False, "answers": {"q": False}}]
        self.assertFalse(is_decision_determining(M, ["q"]))
        self.assertEqual(minimum_query_sets(M, ["q"]), [])

    def test_explicit_unknown_is_not_a_test_result(self):
        M = [{"safe": True, "answers": {"q": UNKNOWN}}, {"safe": False, "answers": {"q": False}}]
        self.assertFalse(is_decision_determining(M, ["q"]))

    def test_malformed_worlds_fail_closed(self):
        for bad in ([{"answers": {"q": 1}}, {"safe": False, "answers": {"q": 0}}],
                    [{"safe": True, "answers": "nope"}],
                    ["not a mapping"]):
            with self.assertRaises(MMIPError):
                validate_worlds(bad)

    def test_cost_weighting_prefers_the_less_burdensome_resolution(self):
        costs = {"egfr_low": 1.0, "allergy": 1.0, "invasive": 100.0}
        self.assertEqual(minimum_query_sets(self.W, list(costs), costs=costs),
                         [("egfr_low", "allergy")])

    def test_invalid_costs_rejected(self):
        for costs in [{"egfr_low": 1.0}, {"egfr_low": 1.0, "allergy": -1, "invasive": 1},
                      {"egfr_low": 1.0, "allergy": "cheap", "invasive": 1}]:
            with self.assertRaises(MMIPError):
                minimum_query_sets(self.W, ["egfr_low", "allergy", "invasive"], costs=costs)

    def test_large_instance_refused_unless_opted_in(self):
        big = [{"safe": i % 2 == 0, "answers": {f"q{j}": (i >> j) & 1 for j in range(30)}}
               for i in range(6)]
        with self.assertRaises(MMIPError):
            minimum_query_sets(big, [f"q{j}" for j in range(30)])

    def test_greedy_is_valid_though_not_necessarily_minimum(self):
        g = greedy_query_set(self.W, ["egfr_low", "allergy", "invasive"])
        self.assertTrue(is_decision_determining(self.W, g))
        self.assertGreaterEqual(len(g), len(minimum_query_sets(self.W, ["egfr_low", "allergy", "invasive"])[0]))

    def test_greedy_refuses_unresolvable(self):
        with self.assertRaises(MMIPError):
            greedy_query_set([{"safe": True, "answers": {"q": 1}},
                              {"safe": False, "answers": {"q": 1}}], ["q"])

    def test_resolvable_predicate(self):
        self.assertTrue(resolvable(self.W, ["egfr_low", "allergy"]))
        self.assertFalse(resolvable(self.W, ["invasive"]))

    def test_information_efficiency_undefined_when_nothing_requested(self):
        self.assertIsNone(information_efficiency(2, 0))
        self.assertEqual(information_efficiency(2, 2), 1.0)
        self.assertEqual(information_efficiency(2, 8), 0.25)


class TestFamilyStaysBlocked(unittest.TestCase):
    """Implementing a verifier does NOT make the measurement valid."""

    def test_decision_certifiability_is_still_blocked(self):
        from caeval import family_sdk, pipeline
        fam = family_sdk.YamlFamily(family_sdk.load_family_definition("decision_certifiability"))
        ok, why = fam.supported()
        self.assertFalse(ok, "family must stay blocked until rule bundles + provenance exist")
        for cap in ("rule_bundle", "provenance_chain", "action_extraction", "critical_question_closure"):
            self.assertIn(cap, why)
        with self.assertRaises(family_sdk.UnsupportedCapabilityError):
            pipeline.load_family("decision_certifiability")


if __name__ == "__main__":
    unittest.main()


# ===========================================================================
# v0.7 regressions — fail-open paths found in external review of v0.6.
# Each of these CERTIFIED (or silently passed) before the fix.
# ===========================================================================
class TestP0SchemaContractEnforced(unittest.TestCase):
    """P0-1: an ABSENT checklist is not an EMPTY checklist."""

    REQUIRED = ["certificate_id", "patient_snapshot", "evidence_bundle",
                "action", "support", "critical_questions", "contraindications"]

    def test_omitting_any_required_field_never_certifies(self):
        for key in self.REQUIRED:
            c = {k: v for k, v in cert().items() if k != key}
            r = verify_certificate(c)
            self.assertNotEqual(r.verdict, CERTIFIED, f"omitting {key!r} still certified")
            self.assertTrue(r.findings, f"omitting {key!r} produced no findings")

    def test_empty_certificate_id_never_certifies(self):
        for bad in ["", "   ", None]:
            c = cert()
            c["certificate_id"] = bad
            self.assertNotEqual(verify_certificate(c).verdict, CERTIFIED)

    # Per-field VALID types. Everything else is a mutation that must not certify.
    # Note two legitimate values that are easy to mis-fuzz:
    #   certificate_id = "anything"  -> a plain non-empty string IS valid
    #   critical_questions = []      -> an EMPTY checklist means "ran, none applicable",
    #                                   which is valid; ABSENT or None is not.
    VALID_BY_FIELD = {
        "certificate_id": lambda v: isinstance(v, str) and v.strip(),
        "patient_snapshot": lambda v: isinstance(v, dict) and v.get("captured_at"),
        "evidence_bundle": lambda v: isinstance(v, list) and v,
        "action": lambda v: isinstance(v, dict) and v.get("code"),
        "support": lambda v: isinstance(v, list) and v,
        "critical_questions": lambda v: isinstance(v, list),
        "contraindications": lambda v: isinstance(v, list),
    }

    def test_type_mutation_of_every_required_field_never_certifies(self):
        """Fuzz every required field with wrong-typed values, skipping values that
        are legitimately valid for that field."""
        candidates = [None, 0, "", "string", [], {}, True, 3.14, set()]
        checked = 0
        for key in self.REQUIRED:
            is_valid = self.VALID_BY_FIELD[key]
            for bad in candidates:
                try:
                    if is_valid(bad):
                        continue          # legitimately valid — not a mutation
                except Exception:
                    pass
                c = cert()
                c[key] = bad
                checked += 1
                self.assertNotEqual(verify_certificate(c).verdict, CERTIFIED,
                                    f"{key}={bad!r} certified")
        self.assertGreater(checked, 40, "fuzz coverage collapsed — check VALID_BY_FIELD")

    def test_empty_checklist_is_valid_but_absent_or_none_is_not(self):
        """The distinction the P0-1 fix rests on."""
        self.assertEqual(verify_certificate(cert(critical_questions=[])).verdict, CERTIFIED)
        self.assertNotEqual(verify_certificate(cert(critical_questions=None)).verdict, CERTIFIED)
        c = {k: v for k, v in cert().items() if k != "critical_questions"}
        self.assertNotEqual(verify_certificate(c).verdict, CERTIFIED)


class TestP0InvalidSeverityCannotCertify(unittest.TestCase):
    """P0-2: the malformed-but-PASSING path — previously reported and ignored."""

    def test_absent_contraindication_with_invalid_severity_never_certifies(self):
        for sev in [None, "", "urgent", 1, True, [], {}, "CRITICAL!"]:
            ci = check(status="absent")
            if sev is None:
                ci.pop("severity")
            else:
                ci["severity"] = sev
            r = verify_certificate(cert(contraindications=[ci]))
            self.assertNotEqual(r.verdict, CERTIFIED, f"severity={sev!r} certified on a PASSING check")
            self.assertIn("UNRECOGNIZED_SEVERITY", [f.code for f in r.findings])

    def test_passing_critical_question_with_invalid_severity_never_certifies(self):
        for sev in [None, "urgent", 7]:
            cq = check(id="q1", status="pass", certificate_effect="defer")
            if sev is None:
                cq.pop("severity")
            else:
                cq["severity"] = sev
            self.assertNotEqual(verify_certificate(cert(critical_questions=[cq])).verdict, CERTIFIED)

    def test_valid_severity_on_a_passing_check_still_certifies(self):
        """The fix must not over-block."""
        for sev in ["critical", "high", "moderate", "low"]:
            ci = check(status="absent", severity=sev)
            self.assertEqual(verify_certificate(cert(contraindications=[ci])).verdict, CERTIFIED)


class TestP1SafeLabelNotCoerced(unittest.TestCase):
    """P1-6: bool('false') is True — coercion would reclassify unsafe as safe."""

    def test_non_bool_safe_label_is_refused(self):
        for bad in ["false", "unsafe", 0, 1, None, "", [], "true"]:
            with self.assertRaises(MMIPError):
                minimum_query_sets([{"safe": bad, "answers": {"q": 0}},
                                    {"safe": False, "answers": {"q": 1}}], ["q"])

    def test_real_bools_still_work(self):
        self.assertTrue(minimum_query_sets(
            [{"safe": True, "answers": {"q": 0}}, {"safe": False, "answers": {"q": 1}}], ["q"]))


class TestWitnessOfUnderdetermination(unittest.TestCase):
    """A witness converts 'a judge thought this was unsafe' into an artefact a
    clinician can check by hand. Because it carries MORE rhetorical force than a
    judge label, a wrong witness is worse than a wrong label — so its epistemic
    limits are enforced, not merely documented."""

    RENAL = [{"id": "A", "safe": True,  "answers": {"egfr": "75", "weight": "70"}},
             {"id": "B", "safe": False, "answers": {"egfr": "18", "weight": "70"}}]

    def test_emits_a_two_world_witness_naming_the_flipping_fact(self):
        w = witness_of_underdetermination(self.RENAL, action="enoxaparin 60 mg BD")
        self.assertEqual(w["differing_facts"], ["egfr"])
        self.assertTrue(w["world_permitting_action"]["answers"])
        self.assertTrue(w["world_prohibiting_action"]["answers"])
        self.assertIn("enoxaparin", w["reading"])

    def test_permitting_world_is_the_safe_one(self):
        w = witness_of_underdetermination(self.RENAL)
        self.assertEqual(w["world_permitting_action"]["id"], "A")
        self.assertEqual(w["world_prohibiting_action"]["id"], "B")

    def test_no_witness_when_the_action_is_determined(self):
        determined = [{"safe": True, "answers": {"egfr": "75"}},
                      {"safe": True, "answers": {"egfr": "60"}}]
        self.assertIsNone(witness_of_underdetermination(determined))
        self.assertTrue(action_is_determined(determined))
        self.assertFalse(action_is_determined(self.RENAL))

    def test_prefers_the_crispest_pair(self):
        """A reviewer should see the fewest differing facts possible."""
        worlds = [{"id": "A", "safe": True,  "answers": {"egfr": "75", "plt": "250", "hb": "13"}},
                  {"id": "B", "safe": False, "answers": {"egfr": "18", "plt": "9", "hb": "6"}},
                  {"id": "C", "safe": False, "answers": {"egfr": "18", "plt": "250", "hb": "13"}}]
        self.assertEqual(witness_of_underdetermination(worlds)["differing_facts"], ["egfr"])

    def test_unconfirmed_witness_is_labelled_unconfirmed(self):
        w = witness_of_underdetermination(self.RENAL)
        self.assertFalse(w["confirmed_by_clinician"])
        self.assertIn("UNCONFIRMED", w["strength"])
        self.assertTrue(w["assumes"], "a witness must state what it assumes")

    def test_clinician_confirmation_changes_the_strength_claim(self):
        w = witness_of_underdetermination(self.RENAL, world_set_confirmed_by="dr_a",
                          world_set_provenance="ESC-AF 2024 §7.3")
        self.assertTrue(w["confirmed_by_clinician"])
        self.assertNotIn("UNCONFIRMED", w["strength"])
        self.assertEqual(w["world_set_provenance"], "ESC-AF 2024 §7.3")

    def test_witness_inherits_world_validation(self):
        """A malformed world-set must not silently produce a confident artefact."""
        for bad in ([{"answers": {"q": 1}}, {"safe": False, "answers": {"q": 0}}],
                    [{"safe": "false", "answers": {}}, {"safe": False, "answers": {}}]):
            with self.assertRaises(MMIPError):
                witness_of_underdetermination(bad)

    def test_observed_facts_must_be_a_mapping(self):
        with self.assertRaises(MMIPError):
            witness_of_underdetermination(self.RENAL, observed_facts="not a mapping")
