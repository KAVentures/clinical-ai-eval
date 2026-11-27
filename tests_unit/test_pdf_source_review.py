"""Offline PDF staging and page-bound browser review (fictional PDF only)."""
import json
import os
import shutil
import unittest
from unittest.mock import patch

from caeval import (buyer_agent as buyer, buyer_plan, pdf_source_review as pdf,
                    vendor_document_review as review, institution_committee as committee)
from tests_unit import test_vendor_document_review as document_fixture


def synthetic_pdf():
    # Tiny valid PDF with two text pages, no external library dependency.
    streams=[b"BT /F1 12 Tf 72 720 Td (Repeated fictional statement.) Tj ET",
             b"BT /F1 12 Tf 72 720 Td (Repeated fictional statement.) Tj ET"]
    objects=[b"<< /Type /Catalog /Pages 2 0 R >>",
             b"<< /Type /Pages /Count 2 /Kids [3 0 R 4 0 R] >>",
             b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>",
             b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>",
             b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    objects.extend(b"<< /Length "+str(len(s)).encode()+b" >>\nstream\n"+s+b"\nendstream" for s in streams)
    output=b"%PDF-1.4\n"
    offsets=[]
    for i,obj in enumerate(objects,1):
        offsets.append(len(output))
        output+=str(i).encode()+b" 0 obj\n"+obj+b"\nendobj\n"
    xref=len(output)
    output+=b"xref\n0 8\n0000000000 65535 f \n"
    output+=b"".join(("%010d 00000 n \n"%n).encode() for n in offsets)
    output+=b"trailer\n<< /Size 8 /Root 1 0 R >>\nstartxref\n"+str(xref).encode()+b"\n%%EOF\n"
    return output


class PdfReviewTests(unittest.TestCase):
    setUp=document_fixture.DocumentReviewTests.setUp
    request=document_fixture.DocumentReviewTests.request
    post=document_fixture.DocumentReviewTests.post
    fixture=document_fixture.DocumentReviewTests.fixture
    fields=document_fixture.DocumentReviewTests.fields

    def staged(self):
        record=self.fixture(synthetic_pdf())
        with patch.object(pdf,"_extract",return_value=("1.2.3",["Repeated fictional statement.\n","Repeated fictional statement.\n"])):
            result=pdf.stage(self.root,self.eid,services_stopped=True,confirmed=True)
        return record,result

    def test_staged_pdf_page_review_without_parser_in_browser(self):
        record,result=self.staged()
        path="/vendor-document-review-record"
        with patch.object(pdf,"_extract",side_effect=AssertionError("browser must not invoke parser")):
            page=self.request("operator","/vendor-document-review?id="+self.eid)
            self.assertEqual(page["status"],"200 OK")
            self.assertIn(b"Extracted page 2",page["body"])
            form=self.fields(extraction_digest=result["extraction_digest"],
                             page_0="2",page_1="",page_2="",quote_0="Repeated fictional statement.")
            self.assertEqual(self.post("observer",path,form)["status"],"403 Forbidden")
            self.assertEqual(self.post("operator",path,{**form,"page_0":"3"})["status"],"403 Forbidden")
            self.assertEqual(self.post("operator",path,{**form,"extraction_digest":"0"*64})["status"],"403 Forbidden")
            self.assertEqual(self.post("operator",path,self.fields(quote_0="Repeated fictional statement."))["status"],"403 Forbidden")
            self.assertEqual(self.post("operator",path,form)["status"],"303 See Other")
            doc=review.inspect(self.root,self.actors["operator"],self.eid)
            item=doc["history"][0]
            self.assertEqual(item["extraction_digest"],result["extraction_digest"])
            self.assertEqual(item["spans"][0]["page"],2)
            self.assertEqual(item["spans"][0]["start"],0)
            self.assertEqual(item["spans"][0]["page_text_sha256"],doc["extraction"]["pages"][1]["text_sha256"])
            projection=committee.overview(self.root,self.actors["observer"])["vendors"][0]["documents"][0]
            self.assertEqual(projection["source_review_status"],"supports_documented_claim")
            self.assertNotIn("Repeated fictional statement.",json.dumps(projection))

    def test_unapproved_stage_and_tampered_extraction(self):
        record,result=self.staged()
        with self.assertRaises(buyer.BuyerError):
            pdf.stage(self.root,self.eid)
        path=self.root/"pdf-extractions"/(self.sha+".json")
        doc=json.loads(path.read_text())
        doc["pages"][0]["text"]="Tampered"
        path.write_text(json.dumps(doc))
        with self.assertRaises(buyer.BuyerError):
            pdf.load(self.root,record)

    def test_replay_requires_exact_version_and_content(self):
        self.staged()
        with patch.object(pdf,"_extract",return_value=("1.2.3",["Repeated fictional statement.\n","Repeated fictional statement.\n"])):
            self.assertEqual(pdf.stage(self.root,self.eid,services_stopped=True,confirmed=True,replay=True)["status"],"replay_verified")
        with patch.object(pdf,"_extract",return_value=("2.0",["Different text"])):
            with self.assertRaises(buyer.BuyerError):
                pdf.stage(self.root,self.eid,services_stopped=True,confirmed=True,replay=True)
        with self.assertRaises(buyer.BuyerError):
            pdf.spans(pdf._material({"sha256":"a"*64},"1.0",["repeat repeat"]),[{"page":1,"quote":"repeat"}])

    @unittest.skipUnless(os.name=="posix" and shutil.which("pdftotext"),"approved local Poppler executable not installed")
    def test_real_local_parser_on_tiny_synthetic_pdf(self):
        record=self.fixture(synthetic_pdf())
        result=pdf.stage(self.root,self.eid,services_stopped=True,confirmed=True)
        self.assertEqual(result["pages"],2)
        self.assertTrue(all("Repeated fictional statement." in p["text"] for p in pdf.load(self.root,record)["pages"]))
        self.assertEqual(pdf.stage(self.root,self.eid,services_stopped=True,confirmed=True,replay=True)["status"],"replay_verified")


if __name__=="__main__":
    unittest.main()
