"""World validity review runs independently before a candidate, then binds exact evidence."""
import json
import unittest

from caeval import (buyer_plan, institution_access as access,
                    institution_conversation as conversation,
                    institution_patient_review as transcript,
                    institution_patient_validity as validity)
from tests_unit import test_institution_conversation as fixture


class WorldValidityTests(unittest.TestCase):
    def setUp(self):
        fixture.ConversationJobTests.setUp(self)
        for uid,role in (("admin","admin"),("valid-a","reviewer"),("valid-b","reviewer"),
                         ("transcript-a","reviewer"),("transcript-b","reviewer")):
            access.provision(self.root,uid,role,self.password)

    def close(self):
        fixture.ConversationJobTests.close(self)

    def request(self,*args,**kwargs):
        return fixture.ConversationJobTests.request(self,*args,**kwargs)

    def login(self,uid):
        token=access.login(self.root,uid,self.password)
        return token,access.session(self.root,token)

    def test_pre_run_validity_review_and_exact_candidate_link(self):
        admin,a=self.login("admin")
        ta,va=self.login("valid-a")
        tb,vb=self.login("valid-b")
        stranger,_=self.login("reviewer")
        body={"csrf":a["csrf"],"pack_id":"fixture","reviewers":"valid-a,valid-b","confirmed":"yes"}
        self.assertEqual(self.request("/world-review-create","POST",{k:v for k,v in body.items() if k!="csrf"},admin)["status"],"403 Forbidden")
        self.assertEqual(self.request("/world-review-create","POST",body,admin)["status"],"303 See Other")
        self.assertEqual(self.requests,[])
        row=validity.overview(self.root,a)[0]
        ident=row["id"]
        route="/world-review-packet?id="+ident
        self.assertEqual(self.request(route,token=stranger)["status"],"403 Forbidden")
        self.assertEqual(self.request(route,token=admin)["status"],"403 Forbidden")
        self.assertEqual(self.request(route,token=ta)["status"],"200 OK")
        packets=[validity.template(self.root,v,ident) for v in (va,vb)]
        self.assertEqual(len(packets[0]["items"]),row["n_items"])
        self.assertTrue(set(r["slot"] for r in packets[0]["items"]).isdisjoint(r["slot"] for r in packets[1]["items"]))
        for item in packets[0]["items"]:
            self.assertIn("effective_world",item)
            self.assertIn("stress",item)
            self.assertNotIn("response_text",item)
            self.assertNotIn("target",item)
        with self.assertRaises(access.AccessDenied):
            validity.analyze(self.root,a,ident)
        for i,(token,actor,doc) in enumerate(zip((ta,tb),(va,vb),packets)):
            fields={"csrf":actor["csrf"],"id":ident,"packet_digest":buyer_plan._digest(doc),"confirmed":"yes"}
            for j,r in enumerate(doc["items"]):
                for q in validity.QUESTIONS:
                    fields[q+"_"+r["slot"]]="no" if i==0 and j==0 and q=="appropriate_reference" else "yes"
                fields["notes_"+r["slot"]]="Independent synthetic review"
            self.assertEqual(self.request("/world-review-lock","POST",fields,token)["status"],"303 See Other")
            self.assertEqual(self.request("/world-review-lock","POST",fields,token)["status"],"403 Forbidden")
        for r in validity.overview(self.root,a)[0]["roster"]:
            with self.assertRaises(access.AccessDenied):
                validity.attest(self.root,a,ident,r["reviewer"],"0"*64,confirmed=True)
            body={"csrf":a["csrf"],"id":ident,"reviewer":r["reviewer"],"digest":r["digest"],"confirmed":"yes"}
            self.assertEqual(self.request("/world-review-attest","POST",body,admin)["status"],"303 See Other")
        report=validity.analyze(self.root,a,ident)
        self.assertFalse(report["all_items_unanimously_supported"])
        self.assertEqual(report["summary"]["appropriate_reference"]["any_no"],1)
        self.assertEqual(len(report["flagged_items"]),1)
        self.assertFalse(report["decision_grade"])
        self.assertIsNone(report["job_link"])
        self.assertEqual(self.requests,[])
        job=conversation.prepare(self.root,self.actor,self.spec)
        self.assertEqual(conversation.execute(self.root,job["id"],job["binding_hash"],allow_candidate_calls=True)["status"],"completed")
        linked=validity.analyze(self.root,a,ident,job["id"])
        self.assertEqual(linked["job_link"]["job"],job["id"])
        self.assertIsNone(linked["job_link"]["separate_transcript_roster"])
        transcript.prepare(self.root,a,job["id"],["transcript-a","transcript-b"],confirmed=True)
        linked=validity.analyze(self.root,a,ident,job["id"])
        self.assertTrue(linked["job_link"]["separate_transcript_roster"])
        self.assertEqual(linked["source"],report["source"])
        self.assertEqual(self.request("/world-review-analyze","POST",{"csrf":a["csrf"],"id":ident,"job":job["id"]},admin)["status"],"200 OK")
        pack=self.root/"approved-patient-packs"/"fixture"/"cases.json"
        cases=json.loads(pack.read_text())
        cases[0]["opening_message"]="Changed synthetic question"
        pack.write_text(json.dumps(cases))
        self.assertTrue(validity.overview(self.root,a)[0]["unavailable"])
        with self.assertRaises(access.AccessDenied):
            validity.analyze(self.root,a,ident,job["id"])

    def test_roster_overlap_refused(self):
        _,a=self.login("admin")
        b=validity.create(self.root,a,"fixture",["valid-a","valid-b"],confirmed=True)
        job=conversation.prepare(self.root,self.actor,self.spec)
        self.assertEqual(conversation.execute(self.root,job["id"],job["binding_hash"],allow_candidate_calls=True)["status"],"completed")
        transcript.prepare(self.root,a,job["id"],["valid-a","transcript-b"],confirmed=True)
        with access.db(self.root) as db:
            validity._schema(db)
            binding,_,_=validity._load(self.root,db,b["id"])
            with self.assertRaises(access.AccessDenied):
                validity._job_link(self.root,db,binding,job["id"])


class SummaryTests(unittest.TestCase):
    def test_negative_and_indeterminate_findings_never_count_as_unanimous(self):
        items={"x":{"case_id":"a","world_id":"b","stress":"BASELINE"}}
        yes={q:"yes" for q in validity.QUESTIONS}
        mixed={**yes,"plausible_world":"no","appropriate_reference":"cannot_judge"}
        summary,findings=validity._summarize(items,[{"x":yes},{"x":mixed}])
        self.assertEqual(summary["plausible_world"]["any_no"],1)
        self.assertEqual(summary["appropriate_reference"]["indeterminate_without_no"],1)
        self.assertEqual(summary["realistic_disclosure"]["unanimous_yes"],1)
        self.assertEqual(len(findings),1)


if __name__=="__main__":
    unittest.main()
