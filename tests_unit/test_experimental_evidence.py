"""Portable evidence replay tests across all four experimental research modules."""
import copy
import json
import unittest

from caeval import (experimental_evidence as evidence, buyer_agent as buyer, buyer_plan,
                    formal_review as formal, decision_assessment as decision,
                    citation_assessment as citation, scribe_assessment as scribe,
                    robustness_assessment as robustness, institution_research as studio)
from tests_unit import (test_institution_research as browser, test_decision_assessment as d,
                        test_citation_assessment as c, test_scribe_assessment as s,
                        test_robustness_assessment as r)


class ReplayTests(unittest.TestCase):
    def fixture(self, cls):
        obj=cls()
        obj.setUp()
        self.addCleanup(obj.doCleanups)
        return obj

    def package(self, kind, mod, root, plan, responses, binding, ids):
        records={rid:formal._json(root/"submissions"/(rid+".json")) for rid in ids}
        package=evidence.build(kind,plan,responses,binding,records,mod.analyze(root))
        # Actually deserialize a canonical byte export, not just a Python object.
        decoded=json.loads(evidence.encode(package))
        result=evidence.verify(decoded,expected_digest=package["digest"])
        self.assertEqual(result["report_digest"],package["report"]["digest"])
        self.assertTrue(result["expected_digest_matched"])
        self.assertFalse(result["decision_grade"])
        self.assertEqual(set(decoded),{"schema","kind","plan","responses","binding","submissions",
                                      "report","decision_grade","limitations","digest"})
        return decoded

    def test_citation_replay(self):
        obj=self.fixture(c.CitationAssessmentTests)
        binding=obj.prepare()
        ids=("doctor-a","doctor-b")
        for rid in ids:
            obj.submit(rid,obj.filled(rid))
        self.package("citation",citation,obj.root,obj.plan,obj.traces,binding,ids)

    def test_scribe_replay_and_missing_review(self):
        obj=self.fixture(s.ScribeTests)
        binding=obj.prepare()
        ids=("doctor-a","doctor-b")
        for rid in ids:
            obj.submit(rid,obj.fill(rid))
        package=self.package("scribe",scribe,obj.dest,obj.plan,obj.responses,binding,ids)
        package["submissions"].pop("doctor-b")
        self.rehash(package)
        with self.assertRaises(buyer.BuyerError):
            evidence.verify(package)

    def test_robustness_replay_separate_roles(self):
        obj=self.fixture(r.RobustnessTests)
        binding=obj.prepare()
        ids=("valid-a","valid-b","answer-a","answer-b")
        for rid in ids:
            obj.submit(rid,obj.fill(rid))
        self.package("robustness",robustness,obj.root,obj.plan,obj.responses,binding,ids)

    def rehash(self, package):
        package["digest"]=buyer_plan._digest({k:v for k,v in package.items() if k!="digest"})

    def test_decision_replay_and_forged_report(self):
        obj=self.fixture(d.DecisionAssessmentTests)
        package=evidence.build("decision",obj.plan,obj.responses,None,{},
                               decision.assess(obj.plan,obj.responses))
        result=evidence.verify(package)
        self.assertFalse(result["expected_digest_matched"])
        with self.assertRaises(buyer.BuyerError):
            evidence.verify(package,expected_digest="a"*64)
        forged=copy.deepcopy(package)
        forged["report"]["summary"]["completed"]=0
        forged["report"]["digest"]=buyer_plan._digest({k:v for k,v in forged["report"].items() if k!="digest"})
        self.rehash(forged)
        with self.assertRaises(buyer.BuyerError):
            evidence.verify(forged)
        forged=copy.deepcopy(package)
        forged["decision_grade"]=True
        self.rehash(forged)
        with self.assertRaises(buyer.BuyerError):
            evidence.verify(forged)

    def test_incomplete_and_schema_tampering(self):
        obj=self.fixture(d.DecisionAssessmentTests)
        package=evidence.build("decision",obj.plan,obj.responses,None,{},
                               decision.assess(obj.plan,obj.responses))
        for change in ("response","field","binding","digest"):
            broken=copy.deepcopy(package)
            if change=="response":
                broken["responses"]["responses"].pop()
            elif change=="field":
                broken["extra"]="unexpected"
            elif change=="binding":
                broken["binding"]={}
            else:
                broken["digest"]="f"*64
            if change!="digest":
                self.rehash(broken)
            with self.assertRaises(buyer.BuyerError):
                evidence.verify(broken)


class BrowserReplayTests(unittest.TestCase):
    setUp=browser.StudioTests.setUp
    request=browser.StudioTests.request
    post=browser.StudioTests.post

    def test_admin_only_confirmed_post_and_offline_cli(self):
        from caeval import cli
        from contextlib import redirect_stdout
        from io import StringIO
        obj=d.DecisionAssessmentTests()
        obj.setUp()
        ident=studio.create(self.root,self.actors["operator"],"decision",obj.spec)
        studio.collect(self.root,self.actors["operator"],ident,obj.plan["plan_hash"],obj.responses,confirmed=True)
        path="/research-replay-package"
        fields={"id":ident,"confirm":"yes"}
        self.assertFalse(self.post("admin",path,fields)["status"].startswith("200"))
        studio.analyze(self.root,self.actors["admin"],ident,confirmed=True)
        for actor in ("operator","observer","doctor-a"):
            self.assertEqual(self.post(actor,path,fields)["status"],"403 Forbidden")
        self.assertEqual(self.request("admin",path,"POST",fields)["status"],"403 Forbidden")
        self.assertFalse(self.post("admin",path,{"id":ident,"confirm":"no"})["status"].startswith("200"))
        response=self.post("admin",path,fields)
        self.assertEqual(response["status"],"200 OK")
        self.assertIn("attachment",response["headers"]["Content-Disposition"])
        package=json.loads(response["body"])
        output=self.root/"replay-test.json"
        output.write_bytes(response["body"])
        stream=StringIO()
        with redirect_stdout(stream):
            cli.main(["verify-experimental-evidence",str(output),"--expected-digest",package["digest"]])
        self.assertTrue(json.loads(stream.getvalue())["ok"])
        stream=StringIO()
        with self.assertRaises(SystemExit) as failure, redirect_stdout(stream):
            cli.main(["verify-experimental-evidence",str(output),"--expected-digest","0"*64])
        self.assertEqual(failure.exception.code,1)
        self.assertFalse(json.loads(stream.getvalue())["ok"])


if __name__=="__main__":
    unittest.main()
