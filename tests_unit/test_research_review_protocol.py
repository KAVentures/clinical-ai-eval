"""Prospective protocol acceptance tests through actual institutional routes."""
import json
import unittest

from caeval import (institution_access as access, institution_research as studio,
                    research_review_protocol as protocol)
from tests_unit import test_institution_research as fixture
from tests_unit.test_scribe_assessment import fixture as scribe_fixture
from tests_unit.test_robustness_assessment import spec as robustness_spec


class ProspectiveProtocolTests(unittest.TestCase):
    # Reuse the real WSGI fixture helpers without inheriting its test methods.
    setUp = fixture.StudioTests.setUp
    request = fixture.StudioTests.request
    post = fixture.StudioTests.post
    fill_scribe = fixture.StudioTests.fill_scribe

    def new_scribe(self):
        spec, plan, responses = scribe_fixture()
        return studio.create(self.root, self.actors["operator"], "scribe", spec), plan, responses

    def fields(self, ident):
        return {"id":ident, "reviewers":"doctor-b,doctor-a", "validity":"",
                "question":"Does the note preserve the required fictional facts?",
                "intent":"Describe omission and unsupported claim endpoints separately.",
                "confirm":"yes"}

    def freeze(self, ident):
        return self.post("admin", "/research-freeze-review-protocol", self.fields(ident))

    def test_browser_freeze_through_review_and_report(self):
        ident, plan, responses = self.new_scribe()
        fields = self.fields(ident)
        path = "/research-freeze-review-protocol"
        self.assertEqual(self.post("operator", path, fields)["status"], "403 Forbidden")
        self.assertEqual(self.request("admin", path, "POST", fields)["status"], "403 Forbidden")
        self.assertEqual(self.freeze(ident)["status"], "303 See Other")
        self.assertEqual(self.freeze(ident)["status"], "403 Forbidden")
        row = studio.overview(self.root, self.actors["admin"])[0]
        self.assertEqual(row["review_protocol_status"], "locally_prospective")
        self.assertNotIn("review_protocol", studio.overview(self.root, self.actors["operator"])[0])
        link = "/research-download?id="+ident+"&kind=review-provenance"
        for actor in ("operator", "observer", "doctor-a"):
            self.assertEqual(self.request(actor, link)["status"], "403 Forbidden")
        before = json.loads(self.request("admin", link)["body"])
        self.assertEqual(before["status"], "locally_prospective")
        studio.download(self.root,self.actors["operator"],ident,"exchange")
        studio.collect(self.root,self.actors["operator"],ident,plan["plan_hash"],responses,confirmed=True)
        with self.assertRaises(access.AccessDenied):
            studio.assign(self.root,self.actors["admin"],ident,["doctor-a","doctor-c"],[],confirmed=True)
        studio.assign(self.root,self.actors["admin"],ident,["doctor-a","doctor-b"],[],confirmed=True)
        for rid in ("doctor-a","doctor-b"):
            doc = self.fill_scribe(ident,rid)
            digest = studio.lock_review(self.root,self.actors[rid],ident,doc)
            studio.attest(self.root,self.actors["admin"],ident,rid,digest,confirmed=True)
        report_digest = studio.analyze(self.root,self.actors["admin"],ident,confirmed=True)
        after = json.loads(self.request("admin", link)["body"])
        self.assertEqual(after["registration"], before["registration"])
        self.assertEqual(after["verified_report_digest"], report_digest)
        self.assertFalse(after["decision_grade"])
        with access.db(self.root) as conn:
            conn.execute("UPDATE research_review_protocols SET digest='tampered' WHERE study=?", (ident,))
        with self.assertRaises(access.AccessDenied):
            studio.download(self.root,self.actors["admin"],ident,"report")

    def test_export_or_collection_prevents_backdating(self):
        for kind in ("exchange","responses"):
            ident, _, _ = self.new_scribe()
            studio.download(self.root,self.actors["operator"],ident,kind)
            self.assertEqual(self.freeze(ident)["status"], "403 Forbidden")
        ident, plan, responses = self.new_scribe()
        studio.collect(self.root,self.actors["operator"],ident,plan["plan_hash"],responses,confirmed=True)
        self.assertEqual(self.freeze(ident)["status"], "403 Forbidden")
        # Retrospective reviews remain supported, but cannot masquerade as preregistered.
        studio.assign(self.root,self.actors["admin"],ident,["doctor-a","doctor-b"],[],confirmed=True)
        receipt = studio.download(self.root,self.actors["admin"],ident,"review-provenance")
        self.assertEqual(receipt["status"], "not_preregistered")
        self.assertIsNone(receipt["registration"])

    def test_missing_ledger_detected_by_independent_audit_record(self):
        ident, plan, responses = self.new_scribe()
        self.assertEqual(self.freeze(ident)["status"], "303 See Other")
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM research_review_protocols WHERE study=?", (ident,))
        with self.assertRaises(access.AccessDenied):
            studio.collect(self.root,self.actors["operator"],ident,plan["plan_hash"],responses,confirmed=True)

    def test_audit_order_is_sequence_not_wall_clock(self):
        ident, _, _ = self.new_scribe()
        self.assertEqual(self.freeze(ident)["status"], "303 See Other")
        studio.download(self.root,self.actors["operator"],ident,"exchange")
        with access.db(self.root) as conn:
            conn.execute("UPDATE audit SET at=0 WHERE object_id=? AND action='research-export-exchange'", (ident,))
            row=studio._row(conn,ident)
            self.assertEqual(protocol.status(conn,row),"locally_prospective")

    def test_robustness_preserves_separate_rosters(self):
        ident=studio.create(self.root,self.actors["admin"],"robustness",robustness_spec())
        fields=self.fields(ident)
        fields["validity"]="doctor-a,doctor-c"
        result=self.post("admin","/research-freeze-review-protocol",fields)
        self.assertFalse(result["status"].startswith("303"))
        fields["validity"]="doctor-c,doctor-d"
        self.assertEqual(self.post("admin","/research-freeze-review-protocol",fields)["status"],"303 See Other")
        with access.db(self.root) as conn:
            row=studio._row(conn,ident)
            registered=protocol.read(conn,row)["protocol"]["roster"]
        self.assertEqual({x["reviewer"] for x in registered if x["role"]=="reference_validity"},
                         {"doctor-c","doctor-d"})
        self.assertEqual({x["reviewer"] for x in registered if x["role"]=="isolated_answer"},
                         {"doctor-a","doctor-b"})


if __name__ == "__main__":
    unittest.main()
