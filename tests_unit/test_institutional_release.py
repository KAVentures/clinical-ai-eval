"""Cross-feature release gates: federated roles, integrated routes and recovery."""
import io
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from caeval import (buyer_agent as buyer, buyer_plan, institution_access as access,
                    institution_backup as backup, institution_federation as federation,
                    institution_monitoring as monitoring, institution_web as web,
                    agent_grants as grants)


class InstitutionalReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.root=self.base/"workspace"
        buyer.init(self.root,"Synthetic hospital","Synthetic evaluation","ED","SE")
        self.issuer="https://id.example.org/tenant"
        self.gateway=federation.GatewayIdentity(self.issuer,"a"*64)
        self.origin="https://clinical.example"
        self.app=web.InstitutionApp(self.root,self.origin,identity=self.gateway)
        for uid,role in (("admin","admin"),("operator","operator"),("observer","observer"),("reviewer","reviewer")):
            federation.provision(self.root,uid,role,self.issuer,"subject-"+uid)

    def headers(self,uid):
        return {"HTTP_X_CAEVAL_GATEWAY_SECRET":"a"*64,
                "HTTP_X_CAEVAL_ISSUER":self.issuer,
                "HTTP_X_CAEVAL_SUBJECT":"subject-"+uid,
                "HTTP_X_CAEVAL_ASSURANCE":"institutional-mfa"}

    def request(self,path,uid,token="",method="GET",fields=None):
        p=urllib.parse.urlsplit(path)
        raw=urllib.parse.urlencode(fields or {}).encode()
        env={"REQUEST_METHOD":method,"PATH_INFO":p.path,"QUERY_STRING":p.query,
             "HTTP_HOST":"clinical.example","HTTP_ORIGIN":self.origin,
             "HTTP_COOKIE":web.COOKIE+"="+token,"CONTENT_TYPE":"application/x-www-form-urlencoded",
             "CONTENT_LENGTH":str(len(raw)),"wsgi.input":io.BytesIO(raw),**self.headers(uid)}
        result={}
        def start(status,headers):
            result.update(status=status,headers=dict(headers))
        result["body"]=b"".join(self.app(env,start)).decode()
        return result

    def test_integrated_routes_respect_federated_roles(self):
        expected={"admin":{"/formal","/monitoring","/conversation","/collections","/setup"},
                  "operator":{"/monitoring","/conversation","/collections","/setup"},
                  "observer":{"/monitoring","/conversation"},
                  "reviewer":{"/formal"}}
        paths=("/formal","/monitoring","/conversation","/collections","/setup")
        for uid,allowed in expected.items():
            token=self.gateway.login(self.root,self.headers(uid))
            dashboard=self.request("/",uid,token)
            self.assertEqual(dashboard["status"],"200 OK",uid)
            for path in paths:
                with self.subTest(uid=uid,path=path):
                    response=self.request(path,uid,token)
                    self.assertEqual(response["status"],"200 OK" if path in allowed else "403 Forbidden")
                    self.assertEqual("href='"+path+"'" in dashboard["body"],path in allowed)
            self.assertEqual(self.request("/conversation-queue",uid,token,"POST",{"plan_id":"invalid"})["status"],"403 Forbidden")
            self.assertEqual(self.request("/monitoring-create",uid,token,"POST",{"plan_id":"invalid"})["status"],"403 Forbidden")
            self.assertEqual(self.request("/formal-prepare",uid,token,"POST",{"job":"invalid"})["status"],"403 Forbidden")

    def test_recovery_revokes_federation_and_agents_and_disables_schedule(self):
        token=self.gateway.login(self.root,self.headers("admin"))
        epoch="b"*64
        grant=grants.issue(self.root,"admin",["clinical_eval_capabilities"],60,3,epoch,confirmed=True)
        # Synthetic persistence-only fixture: the restore gate must not run a
        # schedule or reconstruct clinical evidence to disable an approval.
        binding={"id":"monitor-fixture","owner":"admin"}
        with access.db(self.root) as conn:
            monitoring._schema(conn)
            conn.execute("INSERT INTO monitoring_schedules(id,binding,digest,owner,next_due) VALUES(?,?,?,?,?)",
                         ("monitor-fixture",json.dumps(binding),buyer_plan._digest(binding),"admin",0))
        checkpoint=self.base/"checkpoint"
        restored=self.base/"restored"
        before=backup.create(self.root,checkpoint,services_stopped=True)
        result=backup.restore(checkpoint,restored,services_stopped=True)
        self.assertTrue(result["sessions_revoked"])
        self.assertTrue(result["agent_grants_revoked"])
        self.assertTrue(result["monitoring_schedules_disabled"])
        with self.assertRaises(access.AccessDenied):
            self.gateway.session(restored,self.headers("admin"),token)
        with access.db(restored) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM federated_sessions").fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT revoked FROM agent_grants").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT enabled FROM monitoring_schedules").fetchone()[0],0)
        # The source and immutable checkpoint are untouched.
        self.assertEqual(backup.check(checkpoint),before)
        self.assertEqual(self.gateway.session(self.root,self.headers("admin"),token)["user_id"],"admin")
        self.assertEqual(grants.permitted(self.root,grant["token"],epoch),["clinical_eval_capabilities"])
        with access.db(self.root) as conn:
            self.assertEqual(conn.execute("SELECT enabled FROM monitoring_schedules").fetchone()[0],1)
        new=self.gateway.login(restored,self.headers("admin"))
        self.assertEqual(self.gateway.session(restored,self.headers("admin"),new)["user_id"],"admin")


if __name__=="__main__":
    unittest.main()
