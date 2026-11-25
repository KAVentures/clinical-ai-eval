"""Scoped service contract: isolated auth, request, status and handoff."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer, institution_access as access, evaluator_service as api


class ServiceTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/"buyer"
        buyer.init(self.root,"Synthetic evaluation")
        access.provision(self.root,"operator","operator","Synthetic long password")
        self.aid="a"*64
        with patch.object(api.assessment,"check",return_value={"assessment_id":self.aid}):
            self.grant=api.issue(self.root,"operator",self.aid,3600,confirmed=True)
            self.other=api.issue(self.root,"operator","b"*64,3600,confirmed=True)
        self.token=self.grant["token"]
        self.key="synthetic-key-0001"

    def call(self,method,path,body=None,token=None,**extra):
        raw=json.dumps(body).encode() if body is not None else b""
        env={"REQUEST_METHOD":method,"PATH_INFO":path,"CONTENT_LENGTH":str(len(raw)),
             "CONTENT_TYPE":"application/json","wsgi.input":io.BytesIO(raw),
             "HTTP_AUTHORIZATION":"Bearer "+(self.token if token is None else token),**extra}
        result={}
        def start(code,headers):
            result["code"]=code
        result["body"]=json.loads(b"".join(api.App(self.root)(env,start)))
        return result

    def test_http_request_is_idempotent_and_does_not_execute(self):
        path="/v1/evaluation-requests"
        with patch.object(api.jobs,"enqueue",side_effect=AssertionError("must not enqueue")),patch.object(api.jobs,"execute",side_effect=AssertionError("must not execute")):
            first=self.call("POST",path,{"idempotency_key":self.key})
            self.assertEqual(first["code"],"200 OK")
            self.assertEqual(first,self.call("POST",path,{"idempotency_key":self.key}))
            self.assertEqual(first["body"]["result"]["status"],"awaiting_local_approval")
            self.assertFalse(first["body"]["result"]["execution_authorized_by_api"])
            rid=first["body"]["result"]["request_id"]
            self.assertEqual(self.call("GET",path+"/"+rid)["code"],"200 OK")
            self.assertEqual(self.call("POST",path,{"idempotency_key":self.key+"x"})["code"],"403 Forbidden")
            self.assertEqual(self.call("GET",path+"/"+rid,token=self.other["token"])["code"],"403 Forbidden")
            self.assertEqual(self.call("GET",path+"/"+rid,HTTP_ORIGIN="https://external.invalid")["code"],"403 Forbidden")

    def test_link_exact_assessment_and_restricted_results(self):
        row=api.submit(self.root,self.token,self.key)
        rid=row["request_id"]
        job={"id":"job-fictional","assessment_id":self.aid,"status":"completed",
             "result":{"packages":[{"family_id":"fictional","manifest_hash":"c"*64,
                       "claim":"demonstration","n_responses":2,"review_count":2,
                       "path":"/private","raw":"do not expose"}],"required_but_not_run":[]}}
        with patch.object(api.jobs,"status",return_value={**job,"assessment_id":"b"*64}):
            with self.assertRaises(access.AccessDenied):
                api.link(self.root,rid,job["id"],confirmed=True)
        with patch.object(api.jobs,"status",return_value=job):
            with self.assertRaises(access.AccessDenied):
                api.link(self.root,rid,job["id"])
            api.link(self.root,rid,job["id"],confirmed=True)
            result=api.status(self.root,self.token,rid)
            self.assertEqual(result["status"],"completed")
            self.assertNotIn("private",json.dumps(result))
            self.assertNotIn("do not expose",json.dumps(result))
            self.assertFalse(result["decision_grade"])
        api.revoke(self.root,self.grant["grant_id"])
        with self.assertRaises(access.AccessDenied):
            api.status(self.root,self.token,rid)

    def test_browser_administrator_approval_and_revocation(self):
        from tests_unit.test_institution_research import StudioTests
        from caeval import institution_web
        access.provision(self.root,"admin","admin","Synthetic long password")
        self.tokens={uid:access.login(self.root,uid,"Synthetic long password") for uid in ("admin","operator")}
        self.actors={uid:access.session(self.root,tok) for uid,tok in self.tokens.items()}
        self.app=institution_web.InstitutionApp(self.root,"https://clinical.example")
        row=api.submit(self.root,self.token,self.key)
        rid=row["request_id"]
        job={"id":"job-fictional","assessment_id":self.aid,"status":"queued"}
        fields={"request_id":rid,"job_id":job["id"],"confirm":"yes"}
        with patch.object(api.jobs,"list_jobs",return_value=[job]),patch.object(api.jobs,"status",return_value=job):
            self.assertEqual(StudioTests.request(self,"operator","/evaluator-requests")["status"],"403 Forbidden")
            page=StudioTests.request(self,"admin","/evaluator-requests")
            self.assertEqual(page["status"],"200 OK")
            self.assertIn(b"job-fictional",page["body"])
            self.assertNotIn(self.token.encode(),page["body"])
            self.assertEqual(StudioTests.request(self,"admin","/evaluator-request-link","POST",fields)["status"],"403 Forbidden")
            form={**fields,"csrf":self.actors["admin"]["csrf"]}
            self.assertEqual(StudioTests.request(self,"admin","/evaluator-request-link","POST",form)["status"],"303 See Other")
            self.assertEqual(api.status(self.root,self.token,rid)["status"],"queued")
        revoke={"grant_id":self.grant["grant_id"],"confirm":"yes","csrf":self.actors["admin"]["csrf"]}
        self.assertEqual(StudioTests.request(self,"admin","/evaluator-request-revoke","POST",revoke)["status"],"303 See Other")
        with self.assertRaises(access.AccessDenied):
            api.status(self.root,self.token,rid)

    def test_restore_invalidates_machine_grants(self):
        from caeval import institution_backup as backup
        row=api.submit(self.root,self.token,self.key)
        checkpoint=self.root.parent/"checkpoint"
        restored=self.root.parent/"restored"
        backup.create(self.root,checkpoint,services_stopped=True)
        result=backup.restore(checkpoint,restored,services_stopped=True)
        self.assertTrue(result["evaluator_service_grants_revoked"])
        with self.assertRaises(access.AccessDenied):
            api.status(restored,self.token,row["request_id"])
        self.assertEqual(api.status(self.root,self.token,row["request_id"])["status"],"awaiting_local_approval")
        self.assertFalse(api.inventory(restored)[0]["grant_active"])

    def test_discovery_scoped_history_and_receipt_integrity(self):
        path="/v1/evaluation-requests"
        info=self.call("GET","/v1/assessment")["body"]["result"]
        self.assertEqual(info["assessment_id"],self.aid)
        self.assertNotIn("owner",info)
        row=api.submit(self.root,self.token,self.key)
        rid=row["request_id"]
        self.assertEqual(self.call("GET",path)["body"]["result"]["requests"],[row])
        self.assertEqual(self.call("GET",path,token=self.other["token"])["body"]["result"]["requests"],[])
        self.assertEqual(self.call("GET",path+"/"+rid+"/receipt")["code"],"403 Forbidden")
        job={"id":"job-fixture","assessment_id":self.aid,"status":"completed",
             "result":{"packages":[{"family_id":"fixture","manifest_hash":"c"*64,
             "claim":"demonstration","n_responses":2,"review_count":1}],"required_but_not_run":[]}}
        with patch.object(api.jobs,"status",return_value=job):
            api.link(self.root,rid,job["id"],confirmed=True)
            receipt=self.call("GET",path+"/"+rid+"/receipt")["body"]["result"]
            self.assertEqual(api.verify_receipt(receipt,receipt["digest"])["status"],"METADATA_INTEGRITY_VERIFIED")
            self.assertEqual(self.call("GET",path+"/"+rid+"/receipt",token=self.other["token"])["code"],"403 Forbidden")
            receipt["result"]["packages"][0]["n_responses"]=100
            with self.assertRaises(access.AccessDenied):
                api.verify_receipt(receipt,receipt["digest"])

    def test_approval_recovers_interrupted_queue_without_duplicate(self):
        row=api.submit(self.root,self.token,self.key)
        rid=row["request_id"]
        jid="job-service-"+rid
        record={"assessment_id":self.aid,"mode":"demonstration","panel_id":"mock"}
        with patch.object(api.assessment,"check",return_value=record),patch.object(api.assessment,"_panel",return_value=(b"",{"judges":[{"mock":True}]})):
            with self.assertRaises(access.AccessDenied):
                api.approve_queue(self.root,rid)
            with access.db(self.root) as conn:
                conn.execute("BEGIN IMMEDIATE")
                orphan=api.jobs.enqueue(self.root,self.aid,service_request=rid,conn=conn)
            self.assertEqual(orphan["id"],jid)
            with self.assertRaises(access.AccessDenied):
                api.jobs.execute(self.root,jid)
            with self.assertRaises(access.AccessDenied):
                api.link(self.root,rid,jid,confirmed=True)
            result=api.approve_queue(self.root,rid,confirmed=True)
            self.assertEqual(result["job_id"],jid)
            self.assertEqual(api.approve_queue(self.root,rid,confirmed=True)["status"],"already_approved")
            self.assertEqual(len(api.jobs.list_jobs(self.root)),1)
            self.assertEqual(api.require_queue_approval(self.root,jid),self.aid)
            self.assertEqual(api.status(self.root,self.token,rid)["status"],"queued")
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM audit WHERE action='evaluator-service-queue-approved'")
        with self.assertRaises(access.AccessDenied):
            api.jobs.execute(self.root,jid)
        with self.assertRaises(access.AccessDenied):
            api.status(self.root,self.token,rid)

    def test_expiry_disabled_owner_and_strict_fields(self):
        path="/v1/evaluation-requests"
        self.assertEqual(self.call("POST",path,{"idempotency_key":self.key,"assessment_id":"b"*64})["code"],"403 Forbidden")
        with access.db(self.root) as conn:
            conn.execute("UPDATE evaluator_service_grants SET expires=0 WHERE id=?",(self.grant["grant_id"],))
        self.assertEqual(self.call("POST",path,{"idempotency_key":self.key})["code"],"403 Forbidden")
        access.disable(self.root,"operator")
        self.assertEqual(self.call("POST",path,{"idempotency_key":self.key},token=self.other["token"])["code"],"403 Forbidden")
        self.assertEqual(api.openapi()["openapi"],"3.1.0")


if __name__=="__main__":
    unittest.main()
