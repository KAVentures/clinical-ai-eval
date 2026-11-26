"""Exercise real mock-panel execution followed by the offline review lifecycle."""
import copy
import json
import unittest

from caeval import buyer_agent as buyer, buyer_assessment as assess, buyer_jobs as jobs
from caeval import adjudicate, formal_review as formal
from tests_unit import test_buyer_assessment as fixtures


class FormalReviewTests(unittest.TestCase):
    def setUp(self):
        fixtures.AssessmentHandoffTests.setUp(self)
        bound=assess.prepare(self.root,self.sub["submission_id"],"mock",self.gov)
        self.job=jobs.enqueue(self.root,bound["assessment_id"])["id"]
        completed=jobs.execute(self.root,self.job)
        self.assertEqual(completed["status"],"completed",completed)
        self.family=next(p["family_id"] for p in completed["result"]["packages"] if p["review_count"])
        self.source=jobs._job_path(self.root,self.job)/"out"/("run_"+self.family)
        self.before={p.relative_to(self.source):p.read_bytes() for p in self.source.rglob("*") if p.is_file()}

    def prepare(self,**kw):
        args={"reviewers":["doc-a","doc-b"],"validity_reviewer":"doc-validity",
              "operator":"admin","confirm_roster":True}
        return formal.prepare(self.root,self.job,self.family,**dict(args,**kw))["id"]

    def fill(self,ident,rid,negative=False):
        doc=formal.template(self.root,ident,rid)
        doc["submission"]["independent_review_confirmed"]=True
        for row in doc["submission"]["answers"]:
            if doc["request"]["kind"]=="validity":
                row.update({q:"yes" for q in formal.VALIDITY})
                row["more_than_one_reasonable_response"]="no"
            else:
                row["verdict"]="safe"
            row["notes"]="Synthetic exercise"
        if negative:
            doc["submission"]["answers"][0][adjudicate.VALIDITY_FIELDS[0]]="no"
        return doc

    def submit(self,ident,rid,doc):
        return formal.submit(self.root,ident,rid,doc,"admin",confirm_identity=True)

    def test_complete_offline_workflow_preserves_source_and_synthetic_marker(self):
        ident=self.prepare()
        self.assertTrue(formal.status(self.root,ident)["synthetic"])
        with self.assertRaises(buyer.BuyerError):
            formal.analyze(self.root,ident)
        for rid in ("doc-a","doc-b","doc-validity"):
            doc=self.fill(ident,rid)
            self.assertTrue(doc["request"]["packet"]["synthetic"])
            self.assertNotIn("cell_id",doc["request"]["items"][0])
            self.assertNotIn("Private fixture product",json.dumps(doc))
            first=self.submit(ident,rid,doc)
            self.assertEqual(first,self.submit(ident,rid,doc))
        self.assertTrue(formal.status(self.root,ident)["ready_for_adjudication"])
        out=formal.analyze(self.root,ident)
        self.assertEqual(out["canonical_adjudication"]["n_reviewers"],2)
        self.assertEqual(out["canonical_adjudication"]["queue_completion"],1)
        self.assertTrue(out["canonical_adjudication"]["reviews_are_synthetic"])
        self.assertTrue(out["canonical_adjudication"]["validity_adjudicated"])
        self.assertFalse(out["canonical_adjudication"]["l2_adjudication_gate_passed"])
        self.assertFalse(out["decision_grade"])
        calibration=out["selected_queue_calibration"]
        self.assertTrue(calibration["synthetic"])
        self.assertFalse(calibration["decision_grade"])
        self.assertEqual(sum(calibration["exclusive_coverage"].values()),calibration["n_locked_review_cells"])
        self.assertEqual(out,formal.analyze(self.root,ident))
        self.assertEqual(self.before,{p.relative_to(self.source):p.read_bytes() for p in self.source.rglob("*") if p.is_file()})
        self.assertEqual(jobs.status(self.root,self.job)["status"],"completed")

    def test_roster_packet_and_attestation_controls(self):
        for opts in ({"confirm_roster":False},{"reviewers":["doc-a","doc-a"]},
                     {"validity_reviewer":"doc-a"},{"operator":"doc-b"}):
            with self.subTest(opts=opts),self.assertRaises(buyer.BuyerError):
                self.prepare(**opts)
        ident=self.prepare()
        doc=self.fill(ident,"doc-a")
        with self.assertRaises(buyer.BuyerError):
            formal.submit(self.root,ident,"doc-a",doc,"admin")
        with self.assertRaises(buyer.BuyerError):
            self.submit(ident,"doc-b",doc)
        altered=copy.deepcopy(doc)
        altered["request"]["packet"]["synthetic"]=False
        with self.assertRaises(buyer.BuyerError):
            self.submit(ident,"doc-a",altered)
        altered=copy.deepcopy(doc)
        altered["submission"]["answers"].pop()
        with self.assertRaises(buyer.BuyerError):
            self.submit(ident,"doc-a",altered)
        self.submit(ident,"doc-a",doc)
        doc["submission"]["answers"][0]["verdict"]="unsafe"
        with self.assertRaises(buyer.BuyerError):
            self.submit(ident,"doc-a",doc)

    def test_negative_validity_and_tampering_fail_closed(self):
        ident=self.prepare()
        for rid in ("doc-a","doc-b","doc-validity"):
            self.submit(ident,rid,self.fill(ident,rid,negative=rid=="doc-validity"))
        out=formal.analyze(self.root,ident)
        self.assertEqual(out["n_invalid_or_indeterminate_validity"],1)
        self.assertEqual(out["selected_queue_calibration"]["exclusive_coverage"]["invalid_or_indeterminate_validity"],1)
        self.assertFalse(out["canonical_adjudication"]["validity_adjudicated"])
        self.assertFalse(out["canonical_adjudication"]["l2_adjudication_gate_passed"])
        packet=self.root/"formal-reviews"/ident/"review_packets"/"doc-a.packet.json"
        raw=json.loads(packet.read_text())
        raw["signature"]="0"*64
        packet.write_text(json.dumps(raw))
        with self.assertRaises(buyer.BuyerError):
            formal.status(self.root,ident)

    def test_consensus_resolves_only_ties_without_changing_independent_agreement(self):
        ident=self.prepare()
        with self.assertRaises(buyer.BuyerError):
            formal.consensus_template(self.root,ident)
        for rid in ("doc-a","doc-b","doc-validity"):
            doc=self.fill(ident,rid)
            if rid=="doc-b":
                doc["submission"]["answers"][0]["verdict"]="unsafe"
            self.submit(ident,rid,doc)
        before_report=formal.analyze(self.root,ident)
        before=before_report["canonical_adjudication"]
        self.assertEqual(before["n_contested"],1)
        consensus=formal.consensus_template(self.root,ident)
        self.assertEqual(len(consensus["request"]["items"]),1)
        consensus["submission"]["confirmed_by_original_reviewers"]=True
        consensus["submission"]["answers"][0].update(verdict="unsafe",rationale="Fixture agreement")
        with self.assertRaises(buyer.BuyerError):
            formal.submit_consensus(self.root,ident,consensus,"admin")
        formal.submit_consensus(self.root,ident,consensus,"admin",confirm_consensus=True)
        after_report=formal.analyze(self.root,ident)
        after=after_report["canonical_adjudication"]
        self.assertEqual(before_report["selected_queue_calibration"],after_report["selected_queue_calibration"])
        self.assertEqual(after["n_contested"],0)
        self.assertEqual(after["n_consensus_resolved"],1)
        self.assertEqual(after["inter_rater"],before["inter_rater"])
        self.assertFalse(after["l2_adjudication_gate_passed"])
        consensus["submission"]["answers"][0]["verdict"]="safe"
        with self.assertRaises(buyer.BuyerError):
            formal.submit_consensus(self.root,ident,consensus,"admin",confirm_consensus=True)

    def test_source_package_change_blocks_every_operation(self):
        ident=self.prepare()
        (self.source/"final_report.md").write_text("tampered")
        with self.assertRaises(buyer.BuyerError):
            formal.template(self.root,ident,"doc-a")
        with self.assertRaises(buyer.BuyerError):
            formal.analyze(self.root,ident)


if __name__=="__main__":
    unittest.main()
