"""Exact-source document review and committee projection acceptance."""
import json
import unittest
from datetime import date, timedelta

from caeval import (buyer_agent as buyer, institution_access as access,
                    institution_committee as committee, committee_portable as portable,
                    vendor_document_review as review)
from tests_unit import test_institution_research as browser


class DocumentReviewTests(unittest.TestCase):
    setUp=browser.StudioTests.setUp
    request=browser.StudioTests.request
    post=browser.StudioTests.post

    def fixture(self,raw="Fictional Å claim.\r\nValid for version one.\r\nRepeated repeated.".encode()):
        buyer.add_vendor(self.root,"candidate","Fictional candidate")
        path=self.root.parent/"source.txt"
        path.write_bytes(raw)
        rec=buyer.add_evidence(self.root,"candidate",buyer.EVIDENCE_KINDS[0],str(path),"Fictional source")
        self.eid=rec["evidence_id"]
        self.sha=rec["sha256"]
        return rec

    def fields(self,**overrides):
        return {"id":self.eid,"sha":self.sha,"previous":"","claim":"Private fictional claim",
                "verdict":"supports_documented_claim","rationale":"Private source rationale",
                "version":"v1","issued":"2020-01-01","expiry":"2021-01-01",
                "confirm":"yes","quote_0":"Fictional Å claim.","quote_1":"","quote_2":"",
                **overrides}

    def test_browser_review_and_committee_snapshot(self):
        self.fixture()
        snapshot=committee.freeze(self.root,self.actors["admin"])["snapshot_id"]
        page="/vendor-document-review?id="+self.eid
        self.assertEqual(self.request("operator",page)["status"],"200 OK")
        self.assertIn("Fictional Å claim.".encode(),self.request("operator",page)["body"])
        self.assertIn(self.eid.encode(),self.request("operator","/setup")["body"])
        path="/vendor-document-review-record"
        for actor in ("observer","doctor-a"):
            self.assertEqual(self.request(actor,page)["status"],"403 Forbidden")
            self.assertEqual(self.post(actor,path,self.fields())["status"],"403 Forbidden")
        self.assertEqual(self.request("operator",path,"POST",self.fields())["status"],"403 Forbidden")
        self.assertEqual(self.post("operator",path,self.fields())["status"],"303 See Other")
        state=review.inspect(self.root,self.actors["operator"],self.eid)
        self.assertEqual(state["text"],"Fictional Å claim.\r\nValid for version one.\r\nRepeated repeated.")
        self.assertEqual(state["history"][0]["spans"],[{"start":0,"end":len("Fictional Å claim."),"quote":"Fictional Å claim."}])
        self.assertEqual(state["freshness"],"expired")
        payload=committee.overview(self.root,self.actors["observer"])
        doc=payload["vendors"][0]["documents"][0]
        self.assertEqual(doc["source_review_status"],"supports_documented_claim")
        self.assertEqual(doc["document_freshness"],"expired")
        self.assertEqual(doc["source_review_count"],1)
        for secret in ("Private source rationale","Private fictional claim","Fictional Å claim.","operator"):
            self.assertNotIn(secret,json.dumps(doc,ensure_ascii=False))
        self.assertFalse(committee.snapshot(self.root,self.actors["observer"],snapshot)["current"])
        new=committee.freeze(self.root,self.actors["admin"])["snapshot_id"]
        package=portable.export(self.root,self.actors["observer"],new,confirmed=True)
        self.assertEqual(portable.verify(package)["status"],"DOSSIER_INTEGRITY_VERIFIED")

    def test_stale_write_and_append_only_review_history(self):
        self.fixture()
        path="/vendor-document-review-record"
        self.assertEqual(self.post("operator",path,self.fields())["status"],"303 See Other")
        first=review.inspect(self.root,self.actors["operator"],self.eid)["history"][0]["digest"]
        self.assertEqual(self.post("admin",path,self.fields())["status"],"403 Forbidden")
        future=(date.today()+timedelta(days=30)).isoformat()
        second=self.fields(previous=first,expiry=future,quote_0="Valid for version one.")
        self.assertEqual(self.post("admin",path,second)["status"],"303 See Other")
        doc=review.inspect(self.root,self.actors["admin"],self.eid)
        self.assertEqual(len(doc["history"]),2)
        self.assertEqual(doc["history"][1]["previous"],first)
        self.assertEqual(doc["freshness"],"within_declared_validity")
        self.assertEqual(doc["history"][1]["spans"][0]["start"],doc["text"].index("Valid for version one."))
        self.assertEqual(self.post("operator",path,second)["status"],"403 Forbidden")

    def test_invalid_source_binding_and_dates_refused(self):
        self.fixture()
        path="/vendor-document-review-record"
        for fields in (self.fields(sha="0"*64),self.fields(quote_0="not present"),
                       self.fields(quote_0="epeated"),self.fields(quote_0=""),
                       self.fields(expiry="2019-12-31"),self.fields(confirm="no")):
            self.assertEqual(self.post("operator",path,fields)["status"],"403 Forbidden")
        self.assertEqual(review.inspect(self.root,self.actors["operator"],self.eid)["history"],[])

    def test_binary_source_not_parsed_or_reviewed(self):
        self.fixture(b"%PDF-1.7 synthetic binary placeholder")
        doc=review.inspect(self.root,self.actors["operator"],self.eid)
        self.assertIsNone(doc["text"])
        self.assertEqual(doc["source_mode"],"unsupported_format")
        self.assertEqual(self.post("operator","/vendor-document-review-record",self.fields())["status"],"403 Forbidden")

    def test_tampered_source_and_ledger_fail_closed(self):
        self.fixture()
        self.assertEqual(self.post("operator","/vendor-document-review-record",self.fields())["status"],"303 See Other")
        (self.root/"evidence"/self.sha).write_bytes(b"changed")
        projection=review.committee_inventory(self.root,self.actors["observer"])[self.eid]
        self.assertEqual(projection["source_review_status"],"unavailable")
        self.assertIsNone(projection["source_review_digest"])
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM vendor_document_reviews WHERE evidence_id=?",(self.eid,))
        with self.assertRaises(access.AccessDenied):
            review.inspect(self.root,self.actors["operator"],self.eid)
        self.assertEqual(review.committee_inventory(self.root,self.actors["observer"])[self.eid]["source_review_status"],"unavailable")


if __name__=="__main__":
    unittest.main()
