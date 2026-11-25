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
