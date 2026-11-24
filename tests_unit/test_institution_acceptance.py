"""End-to-end institutional synthetic workflow: browser to engine to review/recovery.

Uses shipped non-identifiable fixtures, mock judges and the real canonical
runner. This acceptance test exercises product integration, not clinical safety.
"""
import io
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from caeval import buyer_agent as buyer, buyer_jobs as jobs, buyer_physician_review as reviews
from caeval import institution_access as access, institution_setup as setup
from caeval import institution_web as web, institution_analysis as analysis
from caeval import institution_backup as backup, agent_snapshot, intake
from caeval.util import repo_root
from targets import demo_target


class InstitutionalAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.root=self.base/"workspace"
        buyer.init(self.root,"Synthetic hospital","Decision support","ED","SE")
        self.password="Synthetic test password!"
        for uid,role in (("operator","operator"),("doc-a","reviewer"),("doc-b","reviewer"),("observer","observer")):
            access.provision(self.root,uid,role,self.password)
        pack=self.root/"approved-packs"/"demo"
        pack.mkdir(parents=True)
        (pack/"cases.json").write_text(json.dumps(demo_target.base_cases()),encoding="utf-8")
        panel=self.root/"approved-panels"
        panel.mkdir()
        (panel/"mock.toml").write_bytes((repo_root()/"configs"/"judge_panel.toml").read_bytes())
        self.app=web.InstitutionApp(self.root,"https://clinical.example")

    def request(self,path,method="GET",form=None,token="",upload=None):
        parts=urllib.parse.urlsplit(path)
        form=form or {}
        if upload is None:
            body=urllib.parse.urlencode(form).encode()
            ctype="application/x-www-form-urlencoded"
        else:
            chunks=[]
            boundary="caeval-test-multipart"
            for k,v in form.items():
                chunks.append(("--"+boundary+"\r\nContent-Disposition: form-data; name=\""+k+"\"\r\n\r\n"+v+"\r\n").encode())
            chunks.append(("--"+boundary+"\r\nContent-Disposition: form-data; name=\"document\"; filename=\"fixture.json\"\r\nContent-Type: application/json\r\n\r\n").encode()+upload+b"\r\n")
            body=b"".join(chunks)+("--"+boundary+"--\r\n").encode()
            ctype="multipart/form-data; boundary="+boundary
        env={"REQUEST_METHOD":method,"PATH_INFO":parts.path,"QUERY_STRING":parts.query,
             "HTTP_HOST":"clinical.example","HTTP_ORIGIN":"https://clinical.example",
             "HTTP_COOKIE":web.COOKIE+"="+token,"CONTENT_TYPE":ctype,
             "CONTENT_LENGTH":str(len(body)),"wsgi.input":io.BytesIO(body)}
        result={}
        def start(status,headers):
            result.update(status=status,headers=dict(headers))
        result["body"]=b"".join(self.app(env,start)).decode()
        return result

    def login(self,uid):
        r=self.request("/login","POST",{"user_id":uid,"password":self.password})
        self.assertEqual(r["status"],"303 See Other")
        token=r["headers"]["Set-Cookie"].split(";",1)[0].split("=",1)[1]
        return token,access.session(self.root,token)

    def post(self,path,token,actor,form=None,upload=None):
        r=self.request(path,"POST",{"csrf":actor["csrf"],**(form or {})},token,upload)
        self.assertEqual(r["status"],"303 See Other",r["body"])
        return r

    def test_browser_to_engine_to_review_and_recovery(self):
        token,operator=self.login("operator")
        self.post("/setup-vendor",token,operator,{"vendor_id":"candidate","name":"Private fixture candidate"})
        self.post("/setup-document",token,operator,{"vendor_id":"candidate","kind":"security","title":"Fixture evidence"},b"synthetic evidence")
        response=self.request("/setup-download?kind=questionnaire&id=candidate",token=token)
        self.assertEqual(response["status"],"200 OK")
        disclosure=json.loads(response["body"])
        disclosure["submission"]["respondent"]="Fixture supplier"
        disclosure["submission"]["product_version"]="1"
        for answer in disclosure["submission"]["answers"]:
            answer.update(status="provided",answer="Synthetic self-report",references=[])
        self.post("/setup-questionnaire",token,operator,{"vendor_id":"candidate"},json.dumps(disclosure).encode())
        plan={"vendor_id":"candidate","target_name":"Private fixture product","target_version":"1",
              "profile_clinician_decision_support":"yes",**{q:"Synthetic "+q for q in intake.INTAKE_QUESTIONS}}
        self.post("/setup-plan",token,operator,plan)
        view=setup.overview(self.root,operator)
        self.assertEqual(len(view["plans"]),1)
        self.post("/setup-exchange",token,operator,{"plan_id":view["plans"][0]["id"],"pack_id":"demo"})
        exchange=setup.overview(self.root,operator)["exchanges"][0]["id"]
        response=self.request("/setup-download?kind=prompts&id="+exchange,token=token)
        self.assertEqual(response["status"],"200 OK")
        doc=json.loads(response["body"])
        doc["submission"].update(operator="Fixture operator",tested_product_version="1")
        for answer in doc["submission"]["answers"]:
            answer.update(status="provided",answer="Synthetic fixture response")
        self.post("/setup-responses",token,operator,upload=json.dumps(doc).encode())
        submission=setup.overview(self.root,operator)["submissions"][0]
        self.assertEqual(submission["status"],"complete_unscored")
        self.post("/setup-assessment",token,operator,{"submission_id":submission["id"],"panel_id":"mock",
            "influences_diagnosis_or_treatment":"yes","clinician_reviews_every_response":"yes",
            "regulated_medical_device":"unsure","deployment_jurisdictions":"SE"})
        prepared=setup.overview(self.root,operator)["prepared"][0]
        self.assertEqual(prepared["mode"],"demonstration")
        self.post("/setup-queue",token,operator,{"assessment_id":prepared["id"]})
        job=jobs.list_jobs(self.root)[0]
        self.assertEqual(job["status"],"queued")
        finished=jobs.execute(self.root,job["id"])
        self.assertEqual(finished["status"],"completed",finished)
        self.assertFalse(finished["decision_grade"])
        family=next(p["family_id"] for p in finished["result"]["packages"] if p["review_count"]>0)
        for uid in ("doc-a","doc-b"):
            access.assign(self.root,uid,job["id"],family)
        req=reviews.template(self.root,job["id"],family)["request"]
        self.assertTrue(req["items"])
        for uid in ("doc-a","doc-b"):
            rt,actor=self.login(uid)
            url="/review?"+urllib.parse.urlencode({"job":job["id"],"family":family})
            page=self.request(url,token=rt)
            self.assertEqual(page["status"],"200 OK")
            self.assertNotIn("Private fixture product",page["body"])
            self.assertNotIn("confidential-review-note",page["body"])
            form={"job":job["id"],"family":family,"manifest":req["manifest_hash"]}
            for i,item in enumerate(req["items"]):
                form["v_"+item["slot"]]="unsafe" if i%2 else "safe"
                form["n_"+item["slot"]]="confidential-review-note"
            self.post("/review",rt,actor,form)
        stats=analysis.analyze(self.root,operator,job["id"],family)
        self.assertEqual(stats["n_reviewers"],2)
        self.assertFalse(stats["l2_adjudication_gate_passed"])
        self.assertNotIn("confidential-review-note",json.dumps(stats))
        observer,_=self.login("observer")
        self.assertEqual(self.request("/setup",token=observer)["status"],"403 Forbidden")
        self.assertEqual(self.request("/evidence",token=observer)["status"],"200 OK")
        snapshot=self.base/"agent-summary.json"
        agent_snapshot.export(self.root,snapshot)
        raw=snapshot.read_text()
        self.assertNotIn("Private fixture",raw)
        self.assertNotIn("confidential-review-note",raw)
        self.assertNotIn("Synthetic fixture response",raw)
        self.assertEqual(agent_snapshot.load(snapshot)["payload"]["candidate_evaluation_jobs"][0]["status"],"completed")
        checkpoint=self.base/"checkpoint"
        backup.create(self.root,checkpoint,services_stopped=True)
        recovered=self.base/"recovered"
        backup.restore(checkpoint,recovered,services_stopped=True)
        self.assertEqual(jobs.status(recovered,job["id"])["status"],"completed")
        with self.assertRaises(access.AccessDenied):
            access.session(recovered,token)


if __name__=="__main__":
    unittest.main()
