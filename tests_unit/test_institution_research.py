"""Institutional research integration: native decision and blinded scribe workflow."""
import io
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from caeval import (buyer_agent as buyer, buyer_plan, institution_access as access,
                    institution_research as studio, institution_web as web)
from caeval import scribe_assessment as scribe
from tests_unit.test_scribe_assessment import fixture as scribe_fixture
from tests_unit.test_decision_assessment import fixture as decision_fixture, bind_raw
from tests_unit.test_robustness_assessment import spec as robustness_spec
from caeval import decision_assessment as decision


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/"workspace"
        buyer.init(self.root,"Synthetic hospital")
        self.password="Synthetic study password!"
        for uid,role in (("admin","admin"),("operator","operator"),("other","operator"),
                         ("observer","observer"),("doctor-a","reviewer"),("doctor-b","reviewer"),
                         ("doctor-c","reviewer"),("doctor-d","reviewer")):
            access.provision(self.root,uid,role,self.password)
        self.tokens={uid:access.login(self.root,uid,self.password) for uid in
                     ("admin","operator","other","observer","doctor-a","doctor-b","doctor-c","doctor-d")}
        self.actors={uid:access.session(self.root,tok) for uid,tok in self.tokens.items()}
        self.app=web.InstitutionApp(self.root,"https://clinical.example")

    def request(self,uid,path,method="GET",fields=None,blob=None):
        query=urllib.parse.urlsplit(path)
        fields=fields or {}
        if blob is None:
            raw=urllib.parse.urlencode(fields).encode()
            ctype="application/x-www-form-urlencoded"
        else:
            boundary="synthetic-studio-test-boundary"
            parts=[]
            for k,v in fields.items():
                parts.append(("--"+boundary+"\r\nContent-Disposition: form-data; name=\""+k+"\"\r\n\r\n"+v+"\r\n").encode())
            parts.append(("--"+boundary+"\r\nContent-Disposition: form-data; name=\"document\"; filename=\"fixture.json\"\r\nContent-Type: application/json\r\n\r\n").encode()+json.dumps(blob).encode()+b"\r\n")
            parts.append(("--"+boundary+"--\r\n").encode())
            raw=b"".join(parts)
            ctype="multipart/form-data; boundary="+boundary
        env={"REQUEST_METHOD":method,"PATH_INFO":query.path,"QUERY_STRING":query.query,
             "HTTP_HOST":"clinical.example","HTTP_ORIGIN":"https://clinical.example",
             "HTTP_COOKIE":web.COOKIE+"="+self.tokens[uid],"CONTENT_TYPE":ctype,
             "CONTENT_LENGTH":str(len(raw)),"wsgi.input":io.BytesIO(raw)}
        result={}
        def start(status,headers):
            result.update(status=status,headers=dict(headers))
        result["body"]=b"".join(self.app(env,start))
        return result

    def post(self,uid,path,fields,blob=None):
        return self.request(uid,path,"POST",{"csrf":self.actors[uid]["csrf"],**fields},blob)

    def test_browser_decision_study_and_scoped_exports(self):
        spec=decision_fixture()
        self.assertEqual(self.request("operator","/research-create","POST",{"kind":"decision"},spec)["status"],"403 Forbidden")
        self.assertEqual(self.post("operator","/research-create",{"kind":"decision"},spec)["status"],"303 See Other")
        row=studio.overview(self.root,self.actors["operator"])[0]
        ident=row["id"]
        self.assertEqual(studio.overview(self.root,self.actors["other"]),[])
        self.assertEqual(studio.overview(self.root,self.actors["doctor-a"]),[])
        link="/research-download?"+urllib.parse.urlencode({"id":ident,"kind":"plan"})
        for uid in ("observer","other","doctor-a"):
            self.assertEqual(self.request(uid,link)["status"],"403 Forbidden")
        plan=json.loads(self.request("operator",link)["body"])
        responses=decision.response_template(plan)
        for item,res in zip(plan["items"],responses["responses"]):
            ref=item["reference"]
            res.update(status="complete",verdict=ref["verification"]["verdict"],
                       certificate=ref["certificate"],requested_facts=ref["unknown_facts"])
            bind_raw(res)
        self.assertEqual(self.post("operator","/research-collect",{"id":ident,"approve":"wrong","confirm":"yes"},responses)["status"],"403 Forbidden")
        self.assertEqual(self.post("operator","/research-collect",{"id":ident,"approve":plan["plan_hash"],"confirm":"yes"},responses)["status"],"303 See Other")
        self.assertEqual(self.post("operator","/research-analyze",{"id":ident,"confirm":"yes"})["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/research-analyze",{"id":ident,"confirm":"yes"})["status"],"303 See Other")
        report=studio.download(self.root,self.actors["operator"],ident,"report")
        self.assertFalse(report["decision_grade"])
        self.assertEqual(report["summary"]["verdict_agreement"]["rate_on_assessed"],1)
        obs=studio.overview(self.root,self.actors["observer"])[0]
        self.assertIn("summary",obs)
        self.assertNotIn("owner",obs)
        self.assertNotIn("plan_hash",obs)
        path=self.root/"research-studies"/ident/"report.json"
        doc=json.loads(path.read_text())
        doc["summary"]["completed"]=0
        path.write_text(json.dumps(doc))
        self.assertEqual(studio.overview(self.root,self.actors["observer"])[0]["state"],"unavailable")

    def scribe_study(self):
        spec,plan,responses=scribe_fixture()
        ident=studio.create(self.root,self.actors["operator"],"scribe",spec)
        studio.collect(self.root,self.actors["operator"],ident,plan["plan_hash"],responses,confirmed=True)
        studio.assign(self.root,self.actors["admin"],ident,["doctor-a","doctor-b"],[],confirmed=True)
        return ident

    def fill_scribe(self,ident,rid):
        # Reuse the module's independent fixture ratings against the actual
        # account-issued packet; no alternate review schema or scorer.
        doc=studio.download(self.root,self.actors[rid],ident,"review")
        doc["submission"]["independent_review_confirmed"]=True
        for item,row in zip(doc["request"]["items"],doc["submission"]["cases"]):
            row.update(reference_coverage="complete",claim_coverage="complete")
            for f in row["facts"]:
                f.update(reference_validity="valid",documentation="omitted",rationale="Synthetic fixture assessment")
            for c in row["claims"]:
                c.update(verdict="unsupported",importance="important",rationale="Synthetic fixture assessment")
        return doc

    def test_account_bound_scribe_review_and_attestation(self):
        ident=self.scribe_study()
        a=self.fill_scribe(ident,"doctor-a")
        self.assertEqual(self.request("doctor-c","/research-review?id="+ident)["status"],"403 Forbidden")
        self.assertEqual(self.request("doctor-a","/research-review?id="+ident)["status"],"200 OK")
        self.assertEqual(self.post("doctor-b","/research-submit",{"id":ident},a)["status"],"403 Forbidden")
        with self.assertRaises(access.AccessDenied):
            studio.analyze(self.root,self.actors["admin"],ident,confirmed=True)
        for rid in ("doctor-a","doctor-b"):
            doc=self.fill_scribe(ident,rid)
            self.assertEqual(self.post(rid,"/research-lock",{"id":ident,
                "packet_hash":buyer_plan._digest(doc["request"]),
                "submission":json.dumps(doc["submission"])})["status"],"303 See Other")
            digest=buyer_plan._digest(doc)
            self.assertEqual(self.post(rid,"/research-attest",{"id":ident,"reviewer":rid,"digest":digest,"confirm":"yes"})["status"],"403 Forbidden")
            self.assertEqual(self.post("admin","/research-attest",{"id":ident,"reviewer":rid,"digest":"wrong","confirm":"yes"})["status"],"403 Forbidden")
            self.assertEqual(self.post("admin","/research-attest",{"id":ident,"reviewer":rid,"digest":digest,"confirm":"yes"})["status"],"303 See Other")
        self.assertEqual(self.post("admin","/research-analyze",{"id":ident,"confirm":"yes"})["status"],"303 See Other")
        report=studio.download(self.root,self.actors["operator"],ident,"report")
        self.assertEqual(report["summary"]["omissions"]["rate_on_assessed"],1)
        self.assertFalse(report["decision_grade"])

    def test_offline_import_is_not_browser_attestation(self):
        ident=self.scribe_study()
        packet=self.fill_scribe(ident,"doctor-a")
        scribe.submit(self.root/"research-studies"/ident/"review","doctor-a",packet,"admin",confirmed=True)
        with self.assertRaises(access.AccessDenied):
            studio.download(self.root,self.actors["doctor-b"],ident,"review")

    def test_citation_studio_import_and_independent_review(self):
        from tests_unit.test_citation_assessment import CitationAssessmentTests
        fixture=CitationAssessmentTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        ident=studio.create(self.root,self.actors["operator"],"citation",fixture.spec)
        plan=studio.download(self.root,self.actors["operator"],ident,"plan")
        exchange=studio.download(self.root,self.actors["operator"],ident,"exchange")
        template=studio.download(self.root,self.actors["operator"],ident,"responses")
        self.assertEqual(len(exchange["queries"]),4)
        self.assertEqual(len(template["traces"]),4)
        studio.collect(self.root,self.actors["operator"],ident,plan["plan_hash"],fixture.traces,confirmed=True)
        studio.assign(self.root,self.actors["admin"],ident,["doctor-a","doctor-b"],[],confirmed=True)
        for rid in ("doctor-a","doctor-b"):
            doc=studio.download(self.root,self.actors[rid],ident,"review")
            doc["submission"]["independent_review_confirmed"]=True
            for row in doc["submission"]["coverage"]:
                row["status"]="complete"
            for row in doc["submission"]["support"]:
                task=next(t for t in doc["request"]["claim_source_pairs"] if
                          t["claim_id"]==row["claim_id"] and t["source_id"]==row["source_id"])
                row["verdict"]="supports" if task["source_id"]=="GUIDE-NEW-2" else "does_not_support"
                row["evidence_quote"]=task["source_text"] if row["verdict"]=="supports" else ""
            digest=studio.lock_review(self.root,self.actors[rid],ident,doc)
            studio.attest(self.root,self.actors["admin"],ident,rid,digest,confirmed=True)
        studio.analyze(self.root,self.actors["admin"],ident,confirmed=True)
        report=studio.download(self.root,self.actors["operator"],ident,"report")
        self.assertEqual(report["summary"]["baseline"]["unsupported_rate_on_assessed"],0)
        self.assertFalse(report["decision_grade"])

    def test_robustness_roles_and_roster(self):
        ident=studio.create(self.root,self.actors["admin"],"robustness",robustness_spec())
        plan=studio.download(self.root,self.actors["admin"],ident,"plan")
        from caeval import robustness_assessment as robustness
        responses=robustness.response_template(plan)
        for row in responses["responses"]:
            row.update(status="complete",answer="Fictional response.")
        studio.collect(self.root,self.actors["admin"],ident,plan["plan_hash"],responses,confirmed=True)
        with self.assertRaises(buyer.BuyerError):
            studio.assign(self.root,self.actors["admin"],ident,["doctor-a","doctor-b"],["doctor-a","doctor-c"],confirmed=True)
        studio.assign(self.root,self.actors["admin"],ident,["doctor-a","doctor-b"],["doctor-c","doctor-d"],confirmed=True)
        answer=studio.download(self.root,self.actors["doctor-a"],ident,"review")
        validity=studio.download(self.root,self.actors["doctor-c"],ident,"review")
        self.assertEqual(answer["request"]["role"],"isolated_answer")
        self.assertEqual(validity["request"]["role"],"reference_validity")
        self.assertTrue(all("answer" not in item["baseline"] for item in validity["request"]["items"]))
        self.assertTrue(all("acceptable" not in item for item in answer["request"]["items"]))


if __name__=="__main__":
    unittest.main()
