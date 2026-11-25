"""Real mock workflow: prospectively frozen cohort, review roster and replay."""
import unittest
import json
from unittest.mock import patch
from caeval.util import repo_root
from caeval import (validation_cohort as c,buyer_manual as manual,buyer_assessment as assessment,
                    buyer_jobs as jobs,institution_access as access,buyer_plan as plans)
from tests_unit import test_buyer_manual as fixture

class CohortTests(unittest.TestCase):
    def setUp(self):
        fixture.ManualExchangeTests.setUp(self)
        panels=self.root/"approved-panels"
        panels.mkdir()
        self.panel=panels/"mock.toml"
        self.panel.write_bytes((repo_root()/"configs"/"judge_panel.toml").read_bytes())
    prepare_exchange=fixture.ManualExchangeTests.prepare
    fill=fixture.ManualExchangeTests.fill
    submit_exchange=fixture.ManualExchangeTests.submit

    def test_prospective_roster_source_and_full_inventory(self):
        for uid,role in (("admin","admin"),("a","reviewer"),("b","reviewer"),("v","reviewer"),("other","reviewer")):
            access.provision(self.root,uid,role,"Synthetic long password")
        admin={"user_id":"admin","role":"admin"}
        family=self.plan["plan"]["selection"]["runnable_suites"][0]
        args=(self.root,admin,self.plan["plan_id"],"fixture",family,["a","b"],"v")
        p=c.preview(*args,panel_id="mock")
        with self.assertRaises(access.AccessDenied):c.freeze(*args,p["digest"],panel_id="mock")
        ident=c.freeze(*args,p["digest"],panel_id="mock",confirmed=True)
        self.assertEqual(ident,c.freeze(*args,p["digest"],panel_id="mock",confirmed=True))
        exchange=self.prepare_exchange()
        self.submit_exchange(self.fill(exchange))
        # Another family cannot be newly frozen after local exchange creation.
        other=next(x for x in self.plan["plan"]["selection"]["runnable_suites"] if x!=family)
        later=(self.root,admin,self.plan["plan_id"],"fixture",other,["a","b"],"v")
        p2=c.preview(*later,panel_id="mock")
        with self.assertRaises(access.AccessDenied):c.freeze(*later,p2["digest"],panel_id="mock",confirmed=True)
        with self.assertRaises(access.AccessDenied):c.analyze(self.root,admin,ident)
        self.assertEqual(c.inventory(self.root,{"user_id":"other","role":"reviewer"}),[])
        self.assertEqual(c.inventory(self.root,{"user_id":"a","role":"reviewer"}),[{"id":ident,"linked":False}])
        with access.db(self.root) as conn:conn.execute("DELETE FROM validation_cohorts")
        with self.assertRaises(access.AccessDenied):c.freeze(*args,p["digest"],panel_id="mock",confirmed=True)


    def test_complete_mock_execution_and_review_attestation(self):
        for uid,role in (("admin","admin"),("a","reviewer"),("b","reviewer"),("v","reviewer"),("other","reviewer")):
            access.provision(self.root,uid,role,"Synthetic long password")
        admin={"user_id":"admin","role":"admin"}
        family=self.plan["plan"]["selection"]["runnable_suites"][0]
        args=(self.root,admin,self.plan["plan_id"],"fixture",family,["a","b"],"v")
        preview=c.preview(*args,panel_id="mock")
        ident=c.freeze(*args,preview["digest"],panel_id="mock",confirmed=True)
        sub=self.submit_exchange(self.fill(self.prepare_exchange()))
        gov={"influences_diagnosis_or_treatment":"yes","clinician_reviews_every_response":"yes",
             "regulated_medical_device":"unsure","deployment_jurisdictions":"SE"}
        bound=assessment.prepare(self.root,sub["submission_id"],"mock",gov)
        ordinary=jobs.enqueue(self.root,bound["assessment_id"])
        with self.assertRaises(access.AccessDenied):
            c.attach(self.root,admin,ident,ordinary["id"],confirmed=True)
        with self.assertRaises(access.AccessDenied):
            c.reserve(self.root,admin,ident,bound["assessment_id"])
        with access.db(self.root) as conn:
            orphan=jobs.enqueue(self.root,bound["assessment_id"],cohort_job="job-cohort-"+ident[:48],conn=conn)
        with self.assertRaises(access.AccessDenied):
            jobs.execute(self.root,orphan["id"])
        job=c.reserve(self.root,admin,ident,bound["assessment_id"],confirmed=True)
        self.assertEqual(job["id"],orphan["id"])
        self.assertEqual(job["id"],c.reserve(self.root,admin,ident,bound["assessment_id"],confirmed=True)["id"])
        alternative=assessment.prepare(self.root,sub["submission_id"],"mock",
                                       {**gov,"deployment_jurisdictions":"Other"})
        with self.assertRaises(access.AccessDenied):
            c.reserve(self.root,admin,ident,alternative["assessment_id"],confirmed=True)
        raw=self.panel.read_bytes()
        self.panel.write_bytes(raw+b"\n# changed\n")
        try:
            with self.assertRaises((access.AccessDenied,manual.buyer.BuyerError)):
                jobs.execute(self.root,job["id"])
            self.assertEqual(jobs.status(self.root,job["id"])["status"],"queued")
        finally:
            self.panel.write_bytes(raw)
        with access.db(self.root) as conn:
            conn.execute("UPDATE validation_cohort_reservations SET assessment_id=? WHERE cohort=?",
                         (alternative["assessment_id"],ident))
        with self.assertRaises(access.AccessDenied):
            jobs.execute(self.root,job["id"])
        with access.db(self.root) as conn:
            conn.execute("UPDATE validation_cohort_reservations SET assessment_id=? WHERE cohort=?",
                         (bound["assessment_id"],ident))
        with self.assertRaises(access.AccessDenied):
            c.attach(self.root,admin,ident,job["id"],confirmed=True)
        completed=jobs.execute(self.root,job["id"])
        self.assertEqual(completed["status"],"completed",completed)
        digest=c.attach(self.root,admin,ident,job["id"],confirmed=True)
        self.assertEqual(digest,c.attach(self.root,admin,ident,job["id"],confirmed=True))
        self.assertEqual(c.reserve(self.root,admin,ident,bound["assessment_id"],confirmed=True)["status"],"completed")
        with self.assertRaises(manual.buyer.BuyerError):
            jobs.execute(self.root,job["id"])
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
        self.assertTrue(out["evaluator_and_job_prospectively_bound"])
        self.assertFalse(out["candidate_submission_prospectively_bound"])
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM validation_cohort_attestations WHERE reviewer='a'")
        with self.assertRaises(access.AccessDenied):
            c.analyze(self.root,admin,ident)


    def _controlled(self):
        from caeval import buyer_connector as connector
        for uid,role in (("admin","admin"),("a","reviewer"),("b","reviewer"),("v","reviewer")):
            access.provision(self.root,uid,role,"Synthetic long password")
        admin={"user_id":"admin","role":"admin"}
        folder=self.root/"approved-connectors"
        folder.mkdir()
        path=folder/"fixture.json"
        path.write_text(json.dumps({"vendor_id":"vendor","target_name":"Example product",
            "target_version":"1.2","url":"http://127.0.0.1:8769/evaluate",
            "protocol":"clinical-ai-eval-candidate/1","headers":{},"timeout_seconds":5}))
        family=self.plan["plan"]["selection"]["runnable_suites"][0]
        args=(self.root,admin,self.plan["plan_id"],"fixture",family,["a","b"],"v")
        p=c.preview(*args,panel_id="mock",connector_id="fixture")
        ident=c.freeze(*args,p["digest"],panel_id="mock",connector_id="fixture",confirmed=True)
        return admin,p,ident,path

    def test_controlled_collection_and_no_substitution(self):
        from caeval import buyer_connector as connector
        admin,p,ident,path=self._controlled()
        d=p["cohort"]
        self.assertEqual(d["schema"],c.CONTROLLED)
        with self.assertRaises(access.AccessDenied):
            c.reserve_collection(self.root,admin,ident)
        manual.prepare(self.root,d["plan_id"],d["pack_id"])
        orphan=connector.queue(self.root,d["exchange_id"],"fixture",
            expected_binding=d["collection_binding_hash"],cohort_id=c._collection_id(d))
        with patch.object(connector,"_call",side_effect=AssertionError("must not call")) as call:
            with self.assertRaises(access.AccessDenied):
                connector.execute(self.root,orphan["id"],orphan["binding_hash"],"operator")
            call.assert_not_called()
        original=path.read_text()
        cfg=json.loads(original)
        path.write_text(json.dumps({**cfg,"timeout_seconds":6}))
        with self.assertRaises(access.AccessDenied):
            c.reserve_collection(self.root,admin,ident,confirmed=True)
        path.write_text(original)
        row=c.reserve_collection(self.root,admin,ident,confirmed=True)
        self.assertEqual(row["id"],orphan["id"])
        self.assertEqual(row,c.reserve_collection(self.root,admin,ident,confirmed=True))
        with patch.object(connector,"_call",return_value="Synthetic collected response") as call:
            completed=connector.execute(self.root,row["id"],row["binding_hash"],"operator")
            self.assertEqual(completed["state"],"complete_unscored")
            self.assertEqual(call.call_count,d["collection_binding"]["n_expected"])
            with self.assertRaises(access.AccessDenied):
                connector.execute(self.root,row["id"],row["binding_hash"],"operator")
        gov={"influences_diagnosis_or_treatment":"yes","clinician_reviews_every_response":"yes",
             "regulated_medical_device":"unsure","deployment_jurisdictions":"SE"}
        alternative=self.submit_exchange(self.fill(self.prepare_exchange()))
        other=assessment.prepare(self.root,alternative["submission_id"],"mock",gov)
        with self.assertRaises(access.AccessDenied):
            c.reserve(self.root,admin,ident,other["assessment_id"],confirmed=True)
        actual=assessment.prepare(self.root,completed["receipt"]["submission_id"],"mock",gov)
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM audit WHERE action=?",(c.SEALED,))
        with self.assertRaises(access.AccessDenied):
            c.reserve(self.root,admin,ident,actual["assessment_id"],confirmed=True)

    def test_incomplete_collection_remains_terminal(self):
        from caeval import buyer_connector as connector
        admin,p,ident,path=self._controlled()
        row=c.reserve_collection(self.root,admin,ident,confirmed=True)
        with patch.object(connector,"_call",side_effect=ValueError("unavailable")) as call:
            result=connector.execute(self.root,row["id"],row["binding_hash"],"operator")
            self.assertEqual(call.call_count,3)
            self.assertEqual(result["state"],"incomplete")
            self.assertEqual(c.reserve_collection(self.root,admin,ident,confirmed=True),result)
            with self.assertRaises(access.AccessDenied):
                connector.execute(self.root,row["id"],row["binding_hash"],"operator")
        alternative=self.submit_exchange(self.fill(self.prepare_exchange()))
        gov={"influences_diagnosis_or_treatment":"yes","clinician_reviews_every_response":"yes",
             "regulated_medical_device":"unsure","deployment_jurisdictions":"SE"}
        other=assessment.prepare(self.root,alternative["submission_id"],"mock",gov)
        with self.assertRaises(access.AccessDenied):
            c.reserve(self.root,admin,ident,other["assessment_id"],confirmed=True)
