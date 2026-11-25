"""Real mock workflow: prospectively frozen cohort, review roster and replay."""
import unittest
from caeval.util import repo_root
from caeval import (validation_cohort as c,buyer_manual as manual,buyer_assessment as assessment,
                    buyer_jobs as jobs,institution_access as access,buyer_plan as plans)
from tests_unit import test_buyer_manual as fixture

class CohortTests(unittest.TestCase):
    setUp=fixture.ManualExchangeTests.setUp
    prepare_exchange=fixture.ManualExchangeTests.prepare
    fill=fixture.ManualExchangeTests.fill
    submit_exchange=fixture.ManualExchangeTests.submit

    def test_prospective_roster_source_and_full_inventory(self):
        for uid,role in (("admin","admin"),("a","reviewer"),("b","reviewer"),("v","reviewer"),("other","reviewer")):
            access.provision(self.root,uid,role,"Synthetic long password")
        admin={"user_id":"admin","role":"admin"}
        family=self.plan["plan"]["selection"]["runnable_suites"][0]
        args=(self.root,admin,self.plan["plan_id"],"fixture",family,["a","b"],"v")
        p=c.preview(*args)
        with self.assertRaises(access.AccessDenied):c.freeze(*args,p["digest"])
        ident=c.freeze(*args,p["digest"],confirmed=True)
        self.assertEqual(ident,c.freeze(*args,p["digest"],confirmed=True))
        exchange=self.prepare_exchange()
        self.submit_exchange(self.fill(exchange))
        # Another family cannot be newly frozen after local exchange creation.
        other=next(x for x in self.plan["plan"]["selection"]["runnable_suites"] if x!=family)
        later=(self.root,admin,self.plan["plan_id"],"fixture",other,["a","b"],"v")
        p2=c.preview(*later)
        with self.assertRaises(access.AccessDenied):c.freeze(*later,p2["digest"],confirmed=True)
        with self.assertRaises(access.AccessDenied):c.analyze(self.root,admin,ident)
        self.assertEqual(c.inventory(self.root,{"user_id":"other","role":"reviewer"}),[])
        self.assertEqual(c.inventory(self.root,{"user_id":"a","role":"reviewer"}),[{"id":ident,"linked":False}])
        with access.db(self.root) as conn:conn.execute("DELETE FROM validation_cohorts")
        with self.assertRaises(access.AccessDenied):c.freeze(*args,p["digest"],confirmed=True)


    def test_complete_mock_execution_and_review_attestation(self):
        for uid,role in (("admin","admin"),("a","reviewer"),("b","reviewer"),("v","reviewer"),("other","reviewer")):
            access.provision(self.root,uid,role,"Synthetic long password")
        admin={"user_id":"admin","role":"admin"}
        family=self.plan["plan"]["selection"]["runnable_suites"][0]
        args=(self.root,admin,self.plan["plan_id"],"fixture",family,["a","b"],"v")
        preview=c.preview(*args)
        ident=c.freeze(*args,preview["digest"],confirmed=True)
        sub=self.submit_exchange(self.fill(self.prepare_exchange()))
        panels=self.root/"approved-panels"
        panels.mkdir()
        (panels/"mock.toml").write_bytes((repo_root()/"configs"/"judge_panel.toml").read_bytes())
        gov={"influences_diagnosis_or_treatment":"yes","clinician_reviews_every_response":"yes",
             "regulated_medical_device":"unsure","deployment_jurisdictions":"SE"}
        bound=assessment.prepare(self.root,sub["submission_id"],"mock",gov)
        job=jobs.enqueue(self.root,bound["assessment_id"])
        with self.assertRaises(access.AccessDenied):
            c.attach(self.root,admin,ident,job["id"],confirmed=True)
        completed=jobs.execute(self.root,job["id"])
        self.assertEqual(completed["status"],"completed",completed)
        digest=c.attach(self.root,admin,ident,job["id"],confirmed=True)
        self.assertEqual(digest,c.attach(self.root,admin,ident,job["id"],confirmed=True))
        with self.assertRaises(access.AccessDenied):
            c.packet(self.root,{"user_id":"other","role":"reviewer"},ident)
        for rid in ("a","b","v"):
            actor={"user_id":rid,"role":"reviewer"}
            packet=c.packet(self.root,actor,ident)
            self.assertEqual(len(packet["items"]),len(preview["cohort"]["variant_ids"]))
            self.assertNotIn("judge_scores",packet["items"][0])
            self.assertNotIn("cell_id",packet["items"][0])
            fields=c.review_calibration.VALIDITY_FIELDS if rid=="v" else ("verdict",)
            answers=[{"slot":x["slot"],**{k:"yes" if rid=="v" else "safe" for k in fields}} for x in packet["items"]]
            with self.assertRaises(access.AccessDenied):
                c.submit(self.root,actor,ident,plans._digest(packet),answers[:-1],confirmed=True)
            receipt=c.submit(self.root,actor,ident,plans._digest(packet),answers,confirmed=True)
            self.assertEqual(receipt,c.submit(self.root,actor,ident,plans._digest(packet),answers,confirmed=True))
            altered=[dict(x) for x in answers]
            altered[0][fields[0]]="cannot_judge"
            with self.assertRaises(access.AccessDenied):
                c.submit(self.root,actor,ident,plans._digest(packet),altered,confirmed=True)
            with self.assertRaises(access.AccessDenied):
                c.analyze(self.root,admin,ident)
            c.attest(self.root,admin,ident,rid,receipt,confirmed=True)
        out=c.analyze(self.root,admin,ident)
        self.assertEqual(out,c.analyze(self.root,admin,ident))
        self.assertTrue(out["calibration"]["synthetic"])
        self.assertEqual(out["calibration"]["n_locked_review_cells"],len(preview["cohort"]["variant_ids"]))
        self.assertEqual(out["calibration"]["n_locked_review_cells"],out["calibration"]["n_full_run_variant_cells"])
        self.assertFalse(out["decision_grade"])
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM validation_cohort_attestations WHERE reviewer='a'")
        with self.assertRaises(access.AccessDenied):
            c.analyze(self.root,admin,ident)
