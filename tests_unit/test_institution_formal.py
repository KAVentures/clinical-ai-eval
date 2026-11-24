"""Formal browser integration using the canonical mock assessment fixture."""
import io
import unittest
import urllib.parse
from unittest.mock import patch

from caeval import institution_access as access, institution_formal as coord
from caeval import institution_web as web, formal_review as formal
from tests_unit import test_formal_review as fixtures


class FormalBrowserTests(unittest.TestCase):
    def setUp(self):
        fixtures.FormalReviewTests.setUp(self)
        self.password="Synthetic browser password!"
        for user,role in (("admin","admin"),("doc-a","reviewer"),("doc-b","reviewer"),
                          ("doc-validity","reviewer"),("unassigned","reviewer"),("observer","observer")):
            access.provision(self.root,user,role,self.password)
        self.app=web.InstitutionApp(self.root,"https://clinical.example")

    def request(self,path,method="GET",form=None,token="",origin="https://clinical.example"):
        parts=urllib.parse.urlsplit(path)
        raw=urllib.parse.urlencode(form or {}).encode()
        env={"REQUEST_METHOD":method,"PATH_INFO":parts.path,"QUERY_STRING":parts.query,
             "HTTP_HOST":"clinical.example","HTTP_ORIGIN":origin,"HTTP_COOKIE":web.COOKIE+"="+token,
             "CONTENT_TYPE":"application/x-www-form-urlencoded","CONTENT_LENGTH":str(len(raw)),
             "wsgi.input":io.BytesIO(raw)}
        out={}
        def start(status,headers):
            out.update(status=status,headers=dict(headers))
        out["body"]=b"".join(self.app(env,start)).decode()
        return out

    def login(self,user):
        result=self.request("/login","POST",{"user_id":user,"password":self.password})
        self.assertEqual(result["status"],"303 See Other")
        token=result["headers"]["Set-Cookie"].split(";",1)[0].split("=",1)[1]
        return token,access.session(self.root,token)

    def post(self,path,token,actor,form):
        result=self.request(path,"POST",{"csrf":actor["csrf"],**form},token)
        self.assertEqual(result["status"],"303 See Other",result["body"])

    def test_offline_import_cannot_count_as_browser_attestation(self):
        _,actor=self.login("admin")
        created=coord.prepare(self.root,actor,self.job,self.family,["doc-a","doc-b"],"doc-validity",True)
        ident=created["id"]
        doc=formal.template(self.root,ident,"doc-a")
        doc["submission"]["independent_review_confirmed"]=True
        for answer in doc["submission"]["answers"]:
            answer["verdict"]="safe"
        formal.submit(self.root,ident,"doc-a",doc,"admin",confirm_identity=True)
        self.assertTrue(coord.overview(self.root,actor)[0]["unavailable"])
        with self.assertRaises(access.AccessDenied):
            coord.analyze(self.root,actor,ident)

    def test_complete_formal_browser_workflow(self):
        admin,actor=self.login("admin")
        form={"job":self.job,"family":self.family,"reviewers":"doc-a,doc-b","validity_reviewer":"doc-validity","confirmed":"yes"}
        self.assertEqual(self.request("/formal-prepare","POST",form,admin)["status"],"403 Forbidden")
        self.post("/formal-prepare",admin,actor,form)
        row=coord.overview(self.root,actor)[0]
        ident=row["id"]
        self.assertTrue(row["synthetic"])
        self.assertFalse(row["ready"])
        self.assertEqual(self.request("/formal-prepare","POST",{"csrf":actor["csrf"],**form},admin)["status"],"403 Forbidden")
        outsider,other=self.login("unassigned")
        self.assertNotIn(ident,self.request("/formal",token=outsider)["body"])
        with patch.object(formal,"_load",wraps=formal._load) as load:
            self.assertEqual(self.request("/formal-review?id="+ident,token=outsider)["status"],"403 Forbidden")
            load.assert_not_called()
        observer,_=self.login("observer")
        self.assertEqual(self.request("/formal",token=observer)["status"],"403 Forbidden")
        for rid in ("doc-a","doc-b","doc-validity"):
            token,reviewer=self.login(rid)
            page=self.request("/formal-review?id="+ident,token=token)
            self.assertEqual(page["status"],"200 OK")
            self.assertNotIn("doc-a",page["body"])
            self.assertNotIn("doc-b",page["body"])
            self.assertNotIn("Private fixture product",page["body"])
            doc=coord.reviewer_template(self.root,reviewer,ident)
            fields={"id":ident,"binding":doc["request"]["binding_hash"],"confirmed":"yes"}
            for i,item in enumerate(doc["request"]["items"]):
                slot=item["slot"]
                fields["notes_"+slot]="private fixture review note"
                if doc["request"]["kind"]=="validity":
                    for q in formal.VALIDITY:
                        fields[q+"_"+slot]="yes"
                else:
                    fields["verdict_"+slot]="unsafe" if i==0 and rid=="doc-b" else "safe"
            self.post("/formal-lock",token,reviewer,fields)
            self.assertEqual(self.request("/formal-review?id="+ident,token=token)["status"],"403 Forbidden")
            row=coord.overview(self.root,actor)[0]
            pending=next(r for r in row["roster"] if r["reviewer"]==rid)
            self.assertEqual(pending["state"],"pending_attestation")
            self.assertNotIn("private fixture review note",self.request("/formal",token=admin)["body"])
            attest={"id":ident,"reviewer":rid,"digest":pending["digest"],"confirmed":"yes"}
            self.assertEqual(self.request("/formal-attest","POST",{"csrf":reviewer["csrf"],**attest},token)["status"],"403 Forbidden")
            self.post("/formal-attest",admin,actor,attest)
        self.assertTrue(coord.overview(self.root,actor)[0]["ready"])
        before=coord.analyze(self.root,actor,ident)["canonical_adjudication"]
        self.assertEqual(before["n_contested"],1)
        self.assertEqual(self.request("/formal-consensus?id="+ident,token=outsider)["status"],"403 Forbidden")
        doc=coord.consensus_template(self.root,actor,ident)
        from caeval import buyer_plan
        fields={"id":ident,"request_digest":buyer_plan._digest(doc["request"]),"confirmed":"yes"}
        for item in doc["request"]["items"]:
            fields["verdict_"+item["slot"]]="unsafe"
            fields["rationale_"+item["slot"]]="Fixture consensus"
        self.post("/formal-consensus",admin,actor,fields)
        result=coord.analyze(self.root,actor,ident)
        self.assertEqual(result["canonical_adjudication"]["n_contested"],0)
        self.assertEqual(result["canonical_adjudication"]["inter_rater"],before["inter_rater"])
        self.assertFalse(result["decision_grade"])
        self.assertEqual(self.before,{p.relative_to(self.source):p.read_bytes() for p in self.source.rglob("*") if p.is_file()})


if __name__=="__main__":
    unittest.main()
