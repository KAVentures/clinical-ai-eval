"""Real local patient conversation execution through institutional queue."""
import io
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer, buyer_plan, intake, institution_access as access
from caeval import institution_conversation as conversation, institution_web as web
from caeval import institution_backup as backup
from caeval.util import repo_root
from tests_unit import test_local_conversation as fixture


class ConversationJobTests(unittest.TestCase):
    def setUp(self):
        fixture.LocalConversationTests.setUp(self)
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"workspace"
        buyer.init(self.root,"Synthetic hospital","Patient triage","ED","SE")
        buyer.add_vendor(self.root,"candidate","Example candidate")
        self.password="Synthetic conversation password!"
        for user,role in (("operator","operator"),("observer","observer"),("reviewer","reviewer")):
            access.provision(self.root,user,role,self.password)
        self.actor={"user_id":"operator","role":"operator"}
        plan=buyer_plan.preview(self.root,{"vendor_id":"candidate","target_name":"Example product",
            "target_version":"1.0","profiles":["patient_triage_chatbot"],
            "answers":{q:"Synthetic answer "+q for q in intake.INTAKE_QUESTIONS}})
        pack=self.root/"approved-patient-packs"/"fixture"
        pack.mkdir(parents=True)
        (pack/"cases.json").write_text(json.dumps([{"case_id":"fixture",
            "opening_message":"I have a headache.",
            "worlds":[{"world_id":"world","required_disposition":"routine","facts":[]}]}]))
        panels=self.root/"approved-panels"
        panels.mkdir()
        (panels/"mock.toml").write_bytes((repo_root()/"configs"/"judge_panel.toml").read_bytes())
        connectors=self.root/"approved-conversations"
        connectors.mkdir()
        self.config=connectors/"local.json"
        self.config.write_text(json.dumps({"vendor_id":"candidate","target_name":"Example product",
            "target_version":"1.0","url":self.url,"history_field":"messages","answer_path":"answer",
            "session_field":"session","headers":{},"timeout_seconds":5}))
        self.spec={"plan_id":plan["plan_id"],"pack_id":"fixture","connector_id":"local","panel_id":"mock",
                   "governance":{"influences_diagnosis_or_treatment":"yes",
                       "clinician_reviews_every_response":"yes","regulated_medical_device":"unsure",
                       "deployment_jurisdictions":"SE"}}
        self.app=web.InstitutionApp(self.root,"https://clinical.example")

    def close(self):
        fixture.LocalConversationTests.close(self)

    def request(self,path,method="GET",form=None,token=""):
        parts=urllib.parse.urlsplit(path)
        body=urllib.parse.urlencode(form or {}).encode()
        env={"REQUEST_METHOD":method,"PATH_INFO":parts.path,"QUERY_STRING":parts.query,
             "HTTP_HOST":"clinical.example","HTTP_ORIGIN":"https://clinical.example",
             "HTTP_COOKIE":web.COOKIE+"="+token,"CONTENT_TYPE":"application/x-www-form-urlencoded",
             "CONTENT_LENGTH":str(len(body)),"wsgi.input":io.BytesIO(body)}
        result={}
        def start(status,headers):
            result.update(status=status,headers=dict(headers))
        result["body"]=b"".join(self.app(env,start)).decode()
        return result

    def test_browser_queue_then_real_canonical_patient_runner(self):
        token=access.login(self.root,"operator",self.password)
        actor=access.session(self.root,token)
        form={**{k:v for k,v in self.spec.items() if k!="governance"},**self.spec["governance"]}
        self.assertEqual(self.request("/conversation-queue","POST",form,token)["status"],"403 Forbidden")
        form["csrf"]=actor["csrf"]
        self.assertEqual(self.request("/conversation-queue","POST",form,token)["status"],"303 See Other")
        self.assertEqual(self.requests,[])
        job=conversation.overview(self.root,actor)[0]
        self.assertEqual(job["status"],"queued")
        with self.assertRaises(access.AccessDenied):
            conversation.execute(self.root,job["id"],job["binding_hash"])
        result=conversation.execute(self.root,job["id"],job["binding_hash"],allow_candidate_calls=True)
        self.assertEqual(result["status"],"completed",result)
        self.assertGreater(len(self.requests),4)
        done=conversation.overview(self.root,actor)[0]
        self.assertEqual(done["status"],"completed")
        self.assertGreater(done["result"]["n_episodes"],0)
        self.assertFalse(done["decision_grade"])
        observer=access.login(self.root,"observer",self.password)
        self.assertEqual(self.request("/conversation",token=observer)["status"],"200 OK")
        self.assertNotIn(job["binding_hash"],self.request("/conversation",token=observer)["body"])
        reviewer=access.login(self.root,"reviewer",self.password)
        self.assertEqual(self.request("/conversation",token=reviewer)["status"],"403 Forbidden")
        ws=self.root/"conversation-jobs"/job["id"]/"out"/"run_patient_red_flag"
        (ws/"episodes.jsonl").write_text("{}")
        self.assertEqual(conversation.overview(self.root,actor)[0]["status"],"invalid")

    def test_drift_and_invalid_endpoint_block_execution(self):
        queued=conversation.prepare(self.root,self.actor,self.spec)
        cfg=json.loads(self.config.read_text())
        cfg["timeout_seconds"]=6
        self.config.write_text(json.dumps(cfg))
        result=conversation.execute(self.root,queued["id"],queued["binding_hash"],allow_candidate_calls=True)
        self.assertEqual(result["status"],"failed")
        self.assertEqual(self.requests,[])
        cfg["url"]="https://unapproved.example/chat"
        self.config.write_text(json.dumps(cfg))
        with self.assertRaises((ValueError, RuntimeError)):
            conversation.prepare(self.root,self.actor,self.spec)

    def test_cancel_and_running_backup_gate(self):
        queued=conversation.prepare(self.root,self.actor,self.spec)
        conversation.cancel(self.root,self.actor,queued["id"])
        with self.assertRaises(access.AccessDenied):
            conversation.execute(self.root,queued["id"],queued["binding_hash"],allow_candidate_calls=True)
        second=conversation.prepare(self.root,self.actor,self.spec)
        with access.db(self.root) as db:
            db.execute("UPDATE conversation_jobs SET status='running' WHERE id=?",(second["id"],))
        with self.assertRaises(buyer.BuyerError):
            backup.create(self.root,Path(self.tmp.name)/"checkpoint",services_stopped=True)


if __name__=="__main__":
    unittest.main()
