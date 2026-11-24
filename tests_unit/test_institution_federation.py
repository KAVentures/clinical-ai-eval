"""Federated identity integration: gateway assertions, account binding and revocation."""
import io
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer, institution_access as access
from caeval import institution_federation as federation, institution_web as web
from caeval import institution_deployment as deployment


class FederationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.root=self.base/"workspace"
        buyer.init(self.root,"Hospital","CDS","ED","SE")
        self.root.chmod(0o700)
        self.issuer="https://id.example/tenant"
        self.secret="a"*64
        self.idp=federation.GatewayIdentity(self.issuer,self.secret)
        for uid,role,subject in (("admin-a","admin","sub-admin"),("clinician-a","reviewer","sub-a"),
                                ("clinician-b","reviewer","sub-b"),("observer-a","observer","sub-observer")):
            federation.provision(self.root,uid,role,self.issuer,subject)
        access.provision(self.root,"local-admin","admin","Local test password!")
        self.app=web.InstitutionApp(self.root,"https://clinical.example",identity=self.idp)
        self.doc={"request":{"job_id":"job-fixture","family_id":"missing_information",
                  "manifest_hash":"f"*64,"items":[{"slot":"b"*64,"case":"CONFIDENTIAL CASE",
                  "response":"Fixture response"}]}}
        mock=patch.object(access.review,"template",return_value=self.doc)
        mock.start()
        self.addCleanup(mock.stop)
        access.assign(self.root,"clinician-a","job-fixture","missing_information")

    def headers(self,subject="sub-a"):
        return {"HTTP_X_CAEVAL_GATEWAY_SECRET":self.secret,"HTTP_X_CAEVAL_ISSUER":self.issuer,
                "HTTP_X_CAEVAL_SUBJECT":subject,"HTTP_X_CAEVAL_ASSURANCE":"institutional-mfa"}

    def request(self,path,method="GET",form=None,token="",headers=None,origin="https://clinical.example"):
        parts=urllib.parse.urlsplit(path)
        raw=urllib.parse.urlencode(form or {}).encode()
        env={"REQUEST_METHOD":method,"PATH_INFO":parts.path,"QUERY_STRING":parts.query,
             "HTTP_HOST":"clinical.example","HTTP_ORIGIN":origin,"HTTP_COOKIE":web.COOKIE+"="+token,
             "CONTENT_TYPE":"application/x-www-form-urlencoded","CONTENT_LENGTH":str(len(raw)),
             "wsgi.input":io.BytesIO(raw),**(self.headers() if headers is None else headers)}
        out={}
        def start(status,headers):
            out.update(status=status,headers=dict(headers))
        out["body"]=b"".join(self.app(env,start)).decode()
        return out

    def login(self,subject="sub-a"):
        r=self.request("/login","POST",{"continue":"yes"},headers=self.headers(subject))
        self.assertEqual(r["status"],"303 See Other",r)
        self.assertIn("Secure; HttpOnly; SameSite=Strict",r["headers"]["Set-Cookie"])
        return r["headers"]["Set-Cookie"].split(";",1)[0].split("=",1)[1]

    def test_forged_missing_or_malformed_gateway_assertions_fail(self):
        for headers in ({},{**self.headers(),"HTTP_X_CAEVAL_GATEWAY_SECRET":"wrong"},
                        {**self.headers(),"HTTP_X_CAEVAL_ISSUER":"https://evil.example"},
                        {**self.headers(),"HTTP_X_CAEVAL_SUBJECT":"sub-a,sub-admin"},
                        {**self.headers(),"HTTP_X_CAEVAL_ASSURANCE":"password-only"}):
            with self.subTest(headers=list(headers)):
                self.assertEqual(self.request("/login",headers=headers)["status"],"403 Forbidden")
        self.assertEqual(self.request("/login","POST",{"continue":"yes"},headers=self.headers("unregistered"))["status"],"403 Forbidden")
        self.assertEqual(self.request("/login","POST",{"user_id":"local-admin","password":"Local test password!"})["status"],"403 Forbidden")
        self.assertEqual(self.request("/login","POST",{"continue":"yes"},origin="https://evil.example")["status"],"403 Forbidden")

    def test_role_and_assignment_are_local_not_from_headers(self):
        token=self.login()
        route="/review?job=job-fixture&family=missing_information"
        good=self.request(route,token=token)
        self.assertEqual(good["status"],"200 OK")
        self.assertIn("CONFIDENTIAL CASE",good["body"])
        other=self.login("sub-b")
        denied=self.request(route,token=other,headers={**self.headers("sub-b"),"HTTP_X_CAEVAL_ROLE":"admin"})
        self.assertEqual(denied["status"],"403 Forbidden")
        self.assertNotIn("CONFIDENTIAL CASE",denied["body"])
        self.assertEqual(self.request("/audit",token=other,headers=self.headers("sub-b"))["status"],"403 Forbidden")
        admin=self.login("sub-admin")
        self.assertEqual(self.request("/audit",token=admin,headers=self.headers("sub-admin"))["status"],"200 OK")

    def test_each_request_rechecks_upstream_identity_and_cookie_mode(self):
        token=self.login()
        self.assertEqual(self.request("/",token=token)["status"],"200 OK")
        self.assertEqual(self.request("/",token=token,headers={})["status"],"403 Forbidden")
        self.assertEqual(self.request("/",token=token,headers=self.headers("sub-b"))["headers"]["Location"],"/login")
        with self.assertRaises(access.AccessDenied):
            access.session(self.root,token)
        local=access.login(self.root,"local-admin","Local test password!")
        self.assertEqual(self.request("/",token=local,headers=self.headers("sub-admin"))["headers"]["Location"],"/login")
        with self.assertRaises(access.AccessDenied):
            access.login(self.root,"clinician-a","Local test password!")

    def test_logout_rotation_expiry_and_disable(self):
        token=self.login()
        actor=self.idp.session(self.root,self.headers(),token)
        wrong=self.request("/logout","POST",{"csrf":"wrong"},token)
        self.assertEqual(wrong["status"],"403 Forbidden")
        response=self.request("/login","POST",{"continue":"yes"},token)
        new=response["headers"]["Set-Cookie"].split(";",1)[0].split("=",1)[1]
        self.assertNotEqual(new,token)
        with self.assertRaises(access.AccessDenied):
            self.idp.session(self.root,self.headers(),token)
        actor=self.idp.session(self.root,self.headers(),new)
        self.assertEqual(self.request("/logout","POST",{"csrf":actor["csrf"]},new)["status"],"303 See Other")
        with self.assertRaises(access.AccessDenied):
            self.idp.session(self.root,self.headers(),new)
        token=self.login()
        with access.db(self.root) as conn:
            conn.execute("UPDATE sessions SET expires=0")
        with self.assertRaises(access.AccessDenied):
            self.idp.session(self.root,self.headers(),token)
        token=self.login()
        access.disable(self.root,"clinician-a")
        with self.assertRaises(access.AccessDenied):
            self.idp.session(self.root,self.headers(),token)
        with access.db(self.root) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM federated_sessions").fetchone()[0],0)

    def test_no_duplicate_subject_or_existing_account_takeover(self):
        for args in (("new","admin",self.issuer,"sub-a"),
                     ("local-admin","admin",self.issuer,"new-sub"),
                     ("vendor","vendor",self.issuer,"vendor-sub")):
            with self.assertRaises(access.AccessDenied):
                federation.provision(self.root,*args)
        with access.db(self.root) as conn:
            self.assertIsNone(conn.execute("SELECT * FROM users WHERE id='new'").fetchone())
            values=[dict(r) for r in conn.execute("SELECT * FROM federated_identities")]
            self.assertNotIn("sub-admin",str(values))

    def test_factory_and_deployment_preflight(self):
        key=self.base/"gateway.key"
        key.write_text(self.secret+"\n")
        key.chmod(0o600)
        env={"CAEVAL_AUTH_MODE":"gateway","CAEVAL_WORKSPACE":str(self.root),
             "CAEVAL_HTTPS_ORIGIN":"https://clinical.example","CAEVAL_FEDERATION_ISSUER":self.issuer,
             "CAEVAL_PROXY_SECRET_FILE":str(key)}
        status=deployment.preflight(env)
        self.assertEqual(status["auth_mode"],"gateway")
        self.assertFalse(status["production_approved"])
        self.assertFalse(status["mfa_policy_tested"])
        self.assertIsNotNone(deployment.create_app(env).identity)
        for changes in ({"CAEVAL_AUTH_MODE":""},{"CAEVAL_AUTH_MODE":"local"},
                        {"CAEVAL_HTTPS_ORIGIN":"http://clinical.example"}):
            with self.assertRaises((access.AccessDenied,ValueError)):
                deployment.preflight({**env,**changes})
        with self.assertRaises(access.AccessDenied):
            deployment.preflight({**env,"CAEVAL_FEDERATION_ISSUER":"https://other.example"})
        if os.name=="posix":
            key.chmod(0o644)
            with self.assertRaises(access.AccessDenied):
                deployment.create_app(env)
        inside=self.root/"secret.key"
        inside.write_text(self.secret)
        inside.chmod(0o600)
        with self.assertRaises(access.AccessDenied):
            deployment.create_app({**env,"CAEVAL_PROXY_SECRET_FILE":str(inside)})


if __name__=="__main__":
    unittest.main()
