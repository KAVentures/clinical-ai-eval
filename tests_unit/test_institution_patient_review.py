"""Independent transcript review exercises the actual canonical loopback runner."""
import unittest

from caeval import (buyer_plan, institution_access as access,
                    institution_conversation as conversation,
                    institution_patient_review as review)
from tests_unit import test_institution_conversation as fixture


class PatientReviewTests(unittest.TestCase):
    def setUp(self):
        fixture.ConversationJobTests.setUp(self)
        for uid,role in (("admin","admin"),("doctor-a","reviewer"),("doctor-b","reviewer")):
            access.provision(self.root,uid,role,self.password)
        self.job=conversation.prepare(self.root,self.actor,self.spec)
        result=conversation.execute(self.root,self.job["id"],self.job["binding_hash"],allow_candidate_calls=True)
        self.assertEqual(result["status"],"completed",result)

    def close(self):
        fixture.ConversationJobTests.close(self)

    def request(self,*args,**kwargs):
        return fixture.ConversationJobTests.request(self,*args,**kwargs)

    def login(self,uid):
        token=access.login(self.root,uid,self.password)
        return token,access.session(self.root,token)

    def test_full_browser_review_and_source_tamper(self):
        admin,a=self.login("admin")
        da,ra=self.login("doctor-a")
        db,rb=self.login("doctor-b")
        stranger,_=self.login("reviewer")
        observer,_=self.login("observer")
        body={"job":self.job["id"],"reviewers":"doctor-a,doctor-b","confirmed":"yes"}
        self.assertEqual(self.request("/patient-review-prepare","POST",body,admin)["status"],"403 Forbidden")
        body["csrf"]=a["csrf"]
        self.assertEqual(self.request("/patient-review-prepare","POST",body,admin)["status"],"303 See Other")
        handoff=review.overview(self.root,a)[0]
        ident=handoff["id"]
        route="/patient-review-packet?id="+ident
        self.assertEqual(self.request(route,token=stranger)["status"],"403 Forbidden")
        self.assertEqual(self.request(route,token=admin)["status"],"403 Forbidden")
        self.assertEqual(self.request("/patient-review",token=observer)["status"],"403 Forbidden")
        self.assertEqual(self.request(route,token=da)["status"],"200 OK")
        pa=review.template(self.root,ra,ident)
        pb=review.template(self.root,rb,ident)
        self.assertEqual(set(pa),{"schema","handoff","binding_hash","items"})
        self.assertEqual(len(pa["items"]),handoff["n_episodes"])
        self.assertTrue(set(x["slot"] for x in pa["items"]).isdisjoint(x["slot"] for x in pb["items"]))
        for item in pa["items"]:
            self.assertEqual(set(item),{"slot","turns"})
            for turn in item["turns"]:
                self.assertEqual(set(turn),{"speaker","text"})
        with self.assertRaises(access.AccessDenied):
            review.analyze(self.root,a,ident)
        for token,actor,packet in ((da,ra,pa),(db,rb,pb)):
            fields={"csrf":actor["csrf"],"id":ident,"packet_digest":buyer_plan._digest(packet),"confirmed":"yes"}
            for item in packet["items"]:
                slot=item["slot"]
                for k,v in {"safety":"safe" if actor==ra else "unsafe","triage":"appropriate",
                            "usefulness":"cannot_judge","notes":"Independent fixture"}.items():
                    fields[k+"_"+slot]=v
            stale={**fields,"packet_digest":"0"*64}
            self.assertEqual(self.request("/patient-review-lock","POST",stale,token)["status"],"403 Forbidden")
            self.assertEqual(self.request("/patient-review-lock","POST",fields,token)["status"],"303 See Other")
            self.assertEqual(self.request("/patient-review-lock","POST",fields,token)["status"],"403 Forbidden")
        roster=review.overview(self.root,a)[0]["roster"]
        for r in roster:
            with self.assertRaises(access.AccessDenied):
                review.attest(self.root,a,ident,r["reviewer"],"0"*64,confirmed=True)
            fields={"csrf":a["csrf"],"id":ident,"reviewer":r["reviewer"],"digest":r["digest"],"confirmed":"yes"}
            self.assertEqual(self.request("/patient-review-attest","POST",fields,admin)["status"],"303 See Other")
        report=review.analyze(self.root,a,ident)
        n=handoff["n_episodes"]
        self.assertEqual(report["summary"]["safety"]["contested"],n)
        self.assertEqual(report["summary"]["triage"]["unanimous"]["appropriate"],n)
        self.assertEqual(report["summary"]["usefulness"]["one_or_more_abstentions"],n)
        self.assertFalse(report["decision_grade"])
        self.assertEqual(review.analyze(self.root,a,ident),report)
        self.assertEqual(self.request("/patient-review-analyze","POST",{"csrf":a["csrf"],"id":ident},admin)["status"],"200 OK")
        self.assertEqual(conversation.overview(self.root,self.actor)[0]["status"],"completed")
        ws=self.root/"conversation-jobs"/self.job["id"]/"out"/"run_patient_red_flag"
        (ws/"episodes.jsonl").write_text("{}")
        self.assertTrue(review.overview(self.root,a)[0]["unavailable"])
        with self.assertRaises((RuntimeError,ValueError,KeyError)):
            review.analyze(self.root,a,ident)


class AgreementTests(unittest.TestCase):
    def test_no_abstention_or_disagreement_counts_as_unanimity(self):
        a={"safety":"safe","triage":"appropriate","usefulness":"adequate"}
        b={**a,"safety":"unsafe","triage":"cannot_judge"}
        items={"a":{},"b":{}}
        summary=review._summary(items,[{"a":a,"b":a},{"a":a,"b":b}])
        self.assertEqual(summary["safety"]["contested"],1)
        self.assertEqual(summary["safety"]["unanimous"]["safe"],1)
        self.assertEqual(summary["triage"]["one_or_more_abstentions"],1)
        self.assertEqual(summary["usefulness"]["unanimous"]["adequate"],2)


if __name__=="__main__":
    unittest.main()
