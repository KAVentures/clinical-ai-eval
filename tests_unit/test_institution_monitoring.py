"""Longitudinal regression tests with actual canonical mock evaluation."""
import json
import time
import unittest
from unittest.mock import patch

from caeval import buyer_assessment as assessment, buyer_connector as connector
from caeval import buyer_jobs as jobs, institution_access as access
from caeval import institution_lifecycle as lifecycle, institution_monitoring as monitor
from caeval import buyer_agent as buyer
from tests_unit import test_buyer_assessment as fixtures


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        fixtures.AssessmentHandoffTests.setUp(self)
        prepared=assessment.prepare(self.root,self.sub["submission_id"],"mock",self.gov)
        self.baseline=jobs.enqueue(self.root,prepared["assessment_id"])["id"]
        self.assertEqual(jobs.execute(self.root,self.baseline)["status"],"completed")
        access.provision(self.root,"admin","admin","Synthetic monitoring password!")
        self.actor={"user_id":"admin","role":"admin"}
        self.pilot=lifecycle.create_pilot(self.root,self.actor,{"vendor_id":"vendor","product_version":"1.2",
            "scope":"Synthetic regression","owner":"admin","review_date":"2030-01-01","rollback_plan":"Review fixture"})
        folder=self.root/"approved-connectors"
        folder.mkdir()
        self.config=folder/"local.json"
        self.config.write_text(json.dumps({"vendor_id":"vendor","target_name":"Example product",
            "target_version":"1.2","url":"http://127.0.0.1:9876/evaluate","prompt_field":"prompt",
            "answer_path":"answer","headers":{},"timeout_seconds":5}))
        self.spec={"baseline_job":self.baseline,"exchange_id":self.sub["exchange_id"],
            "connector_id":"local","panel_id":"mock","governance":self.gov,"pilot_id":self.pilot["id"],
            "interval_seconds":3600,"first_due":int(time.time())-5,"alert_delta":0.1}

    def create(self):
        return monitor.create(self.root,self.actor,self.spec,confirmed=True)

    def run(self,schedule,**kwargs):
        return monitor.run_due(self.root,schedule["id"],schedule["binding_hash"],**kwargs)

    def test_fresh_retest_and_missing_occurrence_tracking(self):
        schedule=self.create()
        with patch.object(connector,"_call",return_value="Fresh synthetic candidate answer") as call:
            with self.assertRaises(access.AccessDenied):
                self.run(schedule)
            with access.db(self.root) as db:
                db.execute("UPDATE monitoring_schedules SET next_due=next_due-7200")
            result=self.run(schedule,allow_candidate_calls=True)
            self.assertEqual(result["status"],"completed",result)
            self.assertGreater(call.call_count,0)
            self.assertEqual(result["skipped_intervals"],2)
            self.assertEqual(self.run(schedule,allow_candidate_calls=True)["status"],"not_due")
        self.assertNotEqual(result["job_id"],self.baseline)
        row=monitor.overview(self.root,self.actor)[0]
        report=row["occurrences"][0]["report"]
        self.assertFalse(report["decision_grade"])
        self.assertEqual(report["baseline_version"],"1.2")
        self.assertEqual(report["baseline_job"],self.baseline)
        self.assertEqual(report["retest_job"],result["job_id"])
        self.assertTrue(report["families"])
        monitor.disable(self.root,self.actor,schedule["id"])
        with self.assertRaises(access.AccessDenied):
            self.run(schedule,allow_candidate_calls=True)

    def test_configuration_drift_prevents_calls(self):
        schedule=self.create()
        cfg=json.loads(self.config.read_text())
        cfg["timeout_seconds"]=6
        self.config.write_text(json.dumps(cfg))
        with patch.object(connector,"_call") as call:
            result=self.run(schedule,allow_candidate_calls=True)
            self.assertEqual(result["status"],"failed")
            call.assert_not_called()
        self.assertEqual(monitor.overview(self.root,self.actor)[0]["occurrences"][0]["state"],"failed")

    def test_incomplete_collection_never_scored(self):
        schedule=self.create()
        with patch.object(connector,"_call",side_effect=ValueError("fixture failure")), \
             patch.object(jobs,"execute") as execution:
            result=self.run(schedule,allow_candidate_calls=True)
            self.assertEqual(result["status"],"incomplete")
            execution.assert_not_called()

    def test_wrong_binding_and_role_fail(self):
        schedule=self.create()
        with self.assertRaises(access.AccessDenied):
            monitor.run_due(self.root,schedule["id"],"bad",allow_candidate_calls=True)
        with self.assertRaises(access.AccessDenied):
            monitor.create(self.root,self.actor,self.spec)
        access.provision(self.root,"observer","observer","Synthetic monitoring password!")
        with self.assertRaises(access.AccessDenied):
            monitor.disable(self.root,{"user_id":"observer","role":"observer"},schedule["id"])


class PairedSummaryTests(unittest.TestCase):
    def row(self,unsafe):
        return {"item_id":"case","kind":"variant","input_text":"case","perturbation_id":"p",
                "validity_valid":True,"severity":"high","evaluation_complete":unsafe is not None,
                "judge_scores":{"judge-a":{"unsafe_overconfident":unsafe}},
                "distinct_ok_providers":["provider-a"],
                **{m:unsafe for m in monitor.METRICS}}

    def test_same_quorum_different_judges_cannot_signal_deterioration(self):
        old={"a":self.row(0),"b":self.row(0)}
        new={"a":self.row(1),"b":self.row(1)}
        new["b"]["judge_scores"]={"judge-b":{"unsafe_overconfident":1}}
        result=monitor.paired_summary(old,new,0.25)["panel_any_unsafe"]
        self.assertEqual(result["matched_observed"],1)
        self.assertEqual(result["judge_participation_mismatch_pairs"],1)
        self.assertEqual(result["missing_quorum_pairs"],0)
        self.assertIsNone(result["delta"])
        self.assertEqual(result["status"],"insufficient_coverage")
        new["b"]=self.row(None)
        result=monitor.paired_summary(old,new,0.25)["panel_any_unsafe"]
        self.assertEqual(result["missing_quorum_pairs"],1)
        self.assertEqual(result["judge_participation_mismatch_pairs"],0)

    def test_alert_coverage_and_structural_alignment(self):
        old={"a":self.row(0),"b":self.row(0)}
        new={"a":self.row(1),"b":self.row(0)}
        result=monitor.paired_summary(old,new,0.25)["panel_any_unsafe"]
        self.assertEqual(result["delta"],0.5)
        self.assertEqual(result["status"],"investigate_increase")
        new["b"]=self.row(None)
        result=monitor.paired_summary(old,new,0.25)["panel_any_unsafe"]
        self.assertIsNone(result["delta"])
        self.assertEqual(result["missing_pairs"],1)
        self.assertEqual(result["status"],"insufficient_coverage")
        new["b"]["input_text"]="different"
        with self.assertRaises(buyer.BuyerError):
            monitor.paired_summary(old,new,0.25)


if __name__=="__main__":
    unittest.main()
