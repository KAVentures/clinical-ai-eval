"""Institutional browser authorization is independent of the research console."""
import io
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer, institution_access as access, institution_web as web


class InstitutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "buyer"
        buyer.init(self.root, "Hospital", "CDS", "ED", "SE")
        self.password = "Synthetic password for tests!"
        for user, role in (("admin","admin"),("doc-a","reviewer"),("doc-b","reviewer"),
                           ("observer","observer"),("supplier","vendor")):
            access.provision(self.root,user,role,self.password)
        self.app = web.InstitutionApp(self.root,"https://clinical.example")
        self.doc = {"request":{"job_id":"job-example","family_id":"missing_information",
                   "manifest_hash":"a"*64,"items":[{"slot":"b"*64,"case":"PRIVATE CASE <script>",
                   "response":"PRIVATE RESPONSE"}]},
                   "submission":{"reviewer_id":"","verdicts":[]}}
        self.mock = patch.object(access.review,"template",return_value=self.doc)
        self.mock.start()
        self.addCleanup(self.mock.stop)
        access.assign(self.root,"doc-a","job-example","missing_information")

    def request(self,path,method="GET",form=None,token="",origin="https://clinical.example",host="clinical.example"):
        parts=urllib.parse.urlsplit(path)
        body=urllib.parse.urlencode(form or {}).encode()
        env={"REQUEST_METHOD":method,"PATH_INFO":parts.path,"QUERY_STRING":parts.query,
             "HTTP_HOST":host,"HTTP_ORIGIN":origin,"HTTP_COOKIE":web.COOKIE+"="+token,
             "CONTENT_TYPE":"application/x-www-form-urlencoded","CONTENT_LENGTH":str(len(body)),
             "wsgi.input":io.BytesIO(body)}
        result={}
        def start(status,headers):
            result.update(status=status,headers=dict(headers))
        result["body"]=b"".join(self.app(env,start)).decode()
        return result

    def login(self,user):
        response=self.request("/login","POST",{"user_id":user,"password":self.password})
        self.assertEqual(response["status"],"303 See Other")
        cookie=response["headers"]["Set-Cookie"]
        self.assertIn("Secure; HttpOnly; SameSite=Strict",cookie)
        return cookie.split(";",1)[0].split("=",1)[1]

    def test_setup_permissions_and_browser_vendor_registration(self):
        token=self.login("admin")
        actor=access.session(self.root,token)
        observer=self.login("observer")
        reviewer=self.login("doc-a")
        form={"csrf":actor["csrf"],"vendor_id":"candidate","name":"Candidate"}
        self.assertEqual(self.request("/setup",token=observer)["status"],"403 Forbidden")
        self.assertEqual(self.request("/setup",token=reviewer)["status"],"403 Forbidden")
        self.assertEqual(self.request("/setup-vendor","POST",form,token,origin="https://evil.example")["status"],"403 Forbidden")
        self.assertEqual(self.request("/setup-vendor","POST",{**form,"csrf":"wrong"},token)["status"],"403 Forbidden")
        self.assertEqual(self.request("/setup-vendor","POST",form,token)["status"],"303 See Other")
        self.assertEqual(self.request("/setup-vendor","POST",form,token)["status"],"403 Forbidden")
        self.assertIn("Candidate",self.request("/setup",token=token)["body"])
        download=self.request("/setup-download?kind=questionnaire&id=candidate",token=token)
        self.assertEqual(download["status"],"200 OK")
        self.assertIn("attachment",download["headers"]["Content-Disposition"])
        self.assertEqual(self.request("/setup-download?kind=questionnaire&id=candidate",token=reviewer)["status"],"403 Forbidden")
        self.assertEqual(self.request("/setup-download?kind=filesystem&id=candidate",token=token)["status"],"403 Forbidden")

    def test_setup_multipart_upload_duplicate_fields_rejected(self):
        def env(parts):
            raw=b"".join(parts)+b"--fixture--\r\n"
            return {"CONTENT_TYPE":"multipart/form-data; boundary=fixture",
                    "CONTENT_LENGTH":str(len(raw)),"wsgi.input":io.BytesIO(raw)}
        csrf=b'--fixture\r\nContent-Disposition: form-data; name="csrf"\r\n\r\nTOKEN\r\n'
        doc=b'--fixture\r\nContent-Disposition: form-data; name="document"; filename="test.txt"\r\nContent-Type: text/plain\r\n\r\nEvidence\r\n'
        fields,blob,name=web._setup_upload(env([csrf,doc]))
        self.assertEqual(fields,{"csrf":"TOKEN"})
        self.assertEqual(blob,b"Evidence")
        self.assertEqual(name,"test.txt")
        with self.assertRaises(access.AccessDenied):
            web._setup_upload(env([csrf,csrf,doc]))
        with self.assertRaises(access.AccessDenied):
            web._setup_upload(env([csrf,doc,doc]))

    def test_login_and_vendor_boundary(self):
        self.assertEqual(self.request("/")["headers"]["Location"],"/login")
        self.assertEqual(self.request("/login","POST",{"user_id":"supplier","password":self.password})["status"],"403 Forbidden")
        self.assertEqual(self.request("/login","POST",{"user_id":"doc-a","password":"bad"})["status"],"403 Forbidden")
        token=self.login("doc-a")
        actor=access.session(self.root,token)
        self.assertEqual(actor["role"],"reviewer")
        self.assertEqual(self.request("/",token=token)["status"],"200 OK")
        access.disable(self.root,"doc-a")
        self.assertEqual(self.request("/",token=token)["headers"]["Location"],"/login")

    def test_roles_and_assignment_prevent_case_leakage(self):
        route="/review?job=job-example&family=missing_information"
        a=self.login("doc-a")
        page=self.request(route,token=a)
        self.assertIn("PRIVATE CASE &lt;script&gt;",page["body"])
        self.assertNotIn("<script>",page["body"])
        for user in ("doc-b","observer","admin"):
            denied=self.request(route,token=self.login(user))
            self.assertEqual(denied["status"],"403 Forbidden")
            self.assertNotIn("PRIVATE CASE",denied["body"])
        self.assertEqual(self.request("/dossier",token=a)["status"],"404 Not Found")
        self.assertEqual(self.request("/audit",token=a)["status"],"403 Forbidden")
        self.assertEqual(self.request("/audit",token=self.login("admin"))["status"],"200 OK")

    def test_immutable_authenticated_submission_and_csrf(self):
        token=self.login("doc-a")
        actor=access.session(self.root,token)
        slot="b"*64
        form={"csrf":actor["csrf"],"job":"job-example","family":"missing_information",
              "manifest":"a"*64,"v_"+slot:"cannot_judge","n_"+slot:"private reviewer notes"}
        self.assertEqual(self.request("/review","POST",form,token,origin="https://evil.example")["status"],"403 Forbidden")
        self.assertEqual(self.request("/review","POST",{**form,"csrf":"bad"},token)["status"],"403 Forbidden")
        self.assertEqual(self.request("/review","POST",{**form,"reviewer_id":"doc-b"},token)["status"],"403 Forbidden")
        self.assertEqual(self.request("/review","POST",form,token)["status"],"303 See Other")
        self.assertEqual(self.request("/review","POST",form,token)["status"],"303 See Other")
        self.assertEqual(self.request("/review","POST",{**form,"v_"+slot:"safe"},token)["status"],"403 Forbidden")
        with access.db(self.root) as conn:
            rows=conn.execute("SELECT * FROM submissions").fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["reviewer"],"doc-a")
            self.assertFalse(json.loads(rows[0]["payload"])["decision_grade"])
        access.assign(self.root,"doc-b","job-example","missing_information")
        other=self.login("doc-b")
        page=self.request("/review?job=job-example&family=missing_information",token=other)
        self.assertNotIn("private reviewer notes",page["body"])
        audit=self.request("/audit",token=self.login("admin"))["body"]
        self.assertIn("review-submitted",audit)
        self.assertNotIn("private reviewer notes",audit)
        self.assertNotIn("PRIVATE CASE",audit)

    def test_evidence_routes_do_not_leak_to_reviewers_or_allow_observer_analysis(self):
        from caeval import institution_analysis
        observer=self.login("observer")
        reviewer=self.login("doc-a")
        actor=access.session(self.root,observer)
        with patch.object(institution_analysis.jobs,"list_jobs",return_value=[]):
            self.assertEqual(self.request("/evidence",token=observer)["status"],"200 OK")
            self.assertEqual(self.request("/evidence",token=reviewer)["status"],"403 Forbidden")
        form={"csrf":actor["csrf"],"job":"job-example","family":"missing_information"}
        with patch.object(institution_analysis.review,"_source") as source:
            self.assertEqual(self.request("/evidence-analysis","POST",form,observer)["status"],"403 Forbidden")
            source.assert_not_called()

    def test_host_origin_logout_and_expiration(self):
        token=self.login("observer")
        self.assertEqual(self.request("/",token=token,host="evil.example")["status"],"400 Bad Request")
        actor=access.session(self.root,token)
        self.assertEqual(self.request("/logout","POST",{"csrf":actor["csrf"]},token)["status"],"303 See Other")
        with self.assertRaises(access.AccessDenied):
            access.session(self.root,token)
        token=self.login("observer")
        with access.db(self.root) as conn:
            conn.execute("UPDATE sessions SET expires=0")
        with self.assertRaises(access.AccessDenied):
            access.session(self.root,token)

    def test_throttle_and_workspace_isolation(self):
        for _ in range(access.MAX_FAILURES):
            with self.assertRaises(access.AccessDenied):
                access.login(self.root,"doc-a","wrong")
        with self.assertRaises(access.AccessDenied):
            access.login(self.root,"doc-a",self.password)
        token=self.login("doc-b")
        other=self.root.parent / "another-hospital"
        buyer.init(other,"Other","CDS","ED","SE")
        with self.assertRaises(access.AccessDenied):
            access.session(other,token)


if __name__ == "__main__":
    unittest.main()
