"""Structured review UI must preserve exact issued submission semantics."""
import json
import unittest

from caeval import (institution_access as access, institution_research as studio,
                    buyer_plan, research_review_forms as forms)
from tests_unit import test_institution_research as institution_fixture
from tests_unit import test_robustness_assessment as robustness_fixture
from tests_unit import test_citation_assessment as citation_fixture
from tests_unit import test_scribe_assessment as scribe_fixture


def blank_fields(form):
    return {**{key:"" for key in form.fields},"confirm":"yes"}


def set_path(form,fields,path,value):
    key=next(k for k,v in form.fields.items() if v[0]==path)
    fields[key]=value


class ReviewFormTests(unittest.TestCase):
    def test_scribe_roundtrip_exact_evidence(self):
        fixture=scribe_fixture.ScribeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare()
        doc=fixture.fill("doctor-a")
        form=forms.Form(doc)
        self.assertTrue(form.available)
        fields=blank_fields(form)
        for k,(path,options,limit,required,record) in form.fields.items():
            if record:
                value=doc["submission"]
                for part in path:
                    value=value[part]
                fields[k]=value
        for path,text,turns,pairs in form.evidence:
            value=doc["submission"]
            for part in path:
                value=value[part]
            for (turn_key,quote_key),span in zip(pairs,value):
                fields[quote_key]=span["quote"]
                if turn_key is not None:
                    fields[turn_key]=span["turn_id"]
        parsed=form.parse(fields)
        self.assertEqual(parsed,doc["submission"])
        doc["submission"]=parsed
        fixture.submit("doctor-a",doc)
        bad=dict(fields)
        bad.pop(next(iter(form.fields)))
        with self.assertRaises(access.AccessDenied):
            forms.Form(doc).parse(bad)
        bad=dict(fields,unexpected="yes")
        with self.assertRaises(access.AccessDenied):
            forms.Form(doc).parse(bad)
        with self.assertRaises(access.AccessDenied):
            forms._span("same same","same")
        self.assertEqual(forms._span("åäö test","test"),{"start":4,"end":8,"quote":"test"})

    def test_citation_form_uses_exact_existing_ratings(self):
        fixture=citation_fixture.CitationAssessmentTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare()
        doc=fixture.filled("doctor-a")
        form=forms.Form(doc)
        fields=blank_fields(form)
        for key,(path,*_) in form.fields.items():
            value=doc["submission"]
            for part in path:
                value=value[part]
            fields[key]=value
        self.assertEqual(form.parse(fields),doc["submission"])
        doc["submission"]=form.parse(fields)
        fixture.submit("doctor-a",doc)

    def test_robustness_forms_preserve_role_blinding_and_ratings(self):
        fixture=robustness_fixture.RobustnessTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare()
        for rid in ("valid-a","answer-a"):
            doc=fixture.fill(rid)
            form=forms.Form(doc)
            fields=blank_fields(form)
            for key,(path,*_) in form.fields.items():
                value=doc["submission"]
                for part in path:
                    value=value[part]
                fields[key]=value
            self.assertEqual(form.parse(fields),doc["submission"])
            html=form.render()
            if rid=="valid-a":
                self.assertNotIn("Unsupported option",html)
            else:
                self.assertNotIn("Authored acceptable groups",html)
            fixture.submit(rid,doc)

    def test_escape_untrusted_text_and_reject_unknown_choice(self):
        fixture=robustness_fixture.RobustnessTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare()
        doc=fixture.fill("answer-a")
        doc["request"]["items"][0]["answer"]="<script>alert(1)</script>"
        form=forms.Form(doc)
        self.assertNotIn("<script>",form.render())
        self.assertIn("&lt;script&gt;",form.render())
        fields=blank_fields(form)
        for k,(path,*_) in form.fields.items():
            value=doc["submission"]
            for part in path:
                value=value[part]
            fields[k]=value
        key=next(k for k,v in form.fields.items() if v[0][-1]=="safety")
        fields[key]="guaranteed_safe"
        with self.assertRaises(access.AccessDenied):
            form.parse(fields)


class BrowserReviewFormTests(unittest.TestCase):
    def test_actual_browser_form_lock_and_attestation(self):
        fixture=institution_fixture.StudioTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        ident=fixture.scribe_study()
        doc=fixture.fill_scribe(ident,"doctor-a")
        form=forms.Form(doc)
        fields=blank_fields(form)
        for key,(path,options,limit,required,record) in form.fields.items():
            if record:
                value=doc["submission"]
                for part in path:
                    value=value[part]
                fields[key]=value
        fields.update(id=ident,packet_hash=buyer_plan._digest(doc["request"]))
        page=fixture.request("doctor-a","/research-review?id="+ident)
        self.assertEqual(page["status"],"200 OK")
        self.assertIn(b"action='/research-lock-form'",page["body"])
        self.assertIn(b"Documentation assessment",page["body"])
        self.assertEqual(fixture.request("doctor-a","/research-lock-form","POST",fields)["status"],"403 Forbidden")
        self.assertEqual(fixture.post("doctor-b","/research-lock-form",fields)["status"],"403 Forbidden")
        self.assertEqual(fixture.post("doctor-a","/research-lock-form",dict(fields,packet_hash="wrong"))["status"],"403 Forbidden")
        self.assertEqual(fixture.post("doctor-a","/research-lock-form",fields)["status"],"303 See Other")
        digest=buyer_plan._digest(doc)
        self.assertEqual(fixture.post("admin","/research-attest",{"id":ident,"reviewer":"doctor-a",
            "digest":digest,"confirm":"yes"})["status"],"303 See Other")
        self.assertEqual(fixture.post("doctor-a","/research-lock-form",fields)["status"],"403 Forbidden")


if __name__=="__main__":
    unittest.main()
