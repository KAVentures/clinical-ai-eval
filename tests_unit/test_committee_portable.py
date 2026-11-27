"""Portable dossier acceptance: role boundary, exact evidence and offline verification."""
import base64
import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from caeval import (buyer_agent as buyer, buyer_plan, committee_portable as portable,
                    institution_access as access, institution_committee as committee,
                    cli)
from tests_unit import test_institution_research as browser
from tests_unit import test_research_program as programme


def reseal(doc):
    doc["digest"]=buyer_plan._digest({k:v for k,v in doc.items() if k!="digest"})
    return doc


class PortableDossierTests(unittest.TestCase):
    setUp=browser.StudioTests.setUp
    request=browser.StudioTests.request
    post=browser.StudioTests.post

    def evidence_fixture(self):
        buyer.add_vendor(self.root,"vendor-a","Synthetic A")
        buyer.add_vendor(self.root,"vendor-b","Incomplete B")
        source=self.root.parent/"original.txt"
        source.write_bytes(b"Fictional vendor statement. Not clinically verified.")
        rec=buyer.add_evidence(self.root,"vendor-a",buyer.EVIDENCE_KINDS[0],str(source),"Synthetic document")
        snap=committee.freeze(self.root,self.actors["admin"])["snapshot_id"]
        return rec,snap,source

    def test_browser_export_explicit_permissions_and_no_default_raw_documents(self):
        rec,snap,source=self.evidence_fixture()
        ident=rec["evidence_id"]
        path="/committee-portable-export"
        self.assertEqual(self.request("admin",path,"POST",{"id":snap,"confirm":"yes"})["status"],"403 Forbidden")
        for uid in ("reviewer","doctor-a"):
            if uid in self.actors:
                self.assertEqual(self.post(uid,path,{"id":snap,"confirm":"yes"})["status"],"403 Forbidden")
        fields={"id":snap,"confirm":"yes","doc_"+ident:"yes"}
        for uid in ("operator","observer"):
            self.assertEqual(self.post(uid,path,fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin",path,{"id":snap})["status"],"403 Forbidden")
        meta=self.post("observer",path,{"id":snap,"confirm":"yes"})
        self.assertEqual(meta["status"],"200 OK")
        doc=json.loads(meta["body"])
        receipt=portable.verify(doc,expected_digest=doc["digest"])
        self.assertEqual(receipt["documents_not_embedded"],[ident])
        self.assertEqual(doc["attachments"],{"documents":{},"experimental_reports":{}})
        self.assertEqual(len(doc["snapshot"]["payload"]["vendors"]),2)
        raw=self.post("admin",path,fields)
        self.assertEqual(raw["status"],"200 OK")
        self.assertIn("attachment",raw["headers"]["Content-Disposition"])
        attached=json.loads(raw["body"])
        self.assertEqual(base64.b64decode(attached["attachments"]["documents"][ident]),source.read_bytes())
        receipt=portable.verify(attached,expected_digest=attached["digest"])
        self.assertEqual(receipt["documents_byte_verified"],[ident])
        self.assertFalse(receipt["decision_grade"])
        self.assertFalse(receipt["deployment_authorized"])
        self.assertEqual(receipt["documents_not_embedded"],[])
        self.assertEqual(self.post("admin",path,{**fields,"doc_unknown":"yes"})["status"],"403 Forbidden")

    def test_changed_document_remains_historical_and_cannot_be_embedded(self):
        rec,snap,_=self.evidence_fixture()
        ident=rec["evidence_id"]
        original=portable.export(self.root,self.actors["admin"],snap,[ident],confirmed=True)
        self.assertTrue(portable.verify(original)["snapshot_current_at_export"])
        (self.root/"evidence"/rec["sha256"]).write_bytes(b"changed after freeze")
        metadata=portable.export(self.root,self.actors["observer"],snap,confirmed=True)
        self.assertFalse(portable.verify(metadata)["snapshot_current_at_export"])
        self.assertEqual(portable.verify(metadata)["documents_not_embedded"],[ident])
        with self.assertRaises(buyer.BuyerError):
            portable.export(self.root,self.actors["admin"],snap,[ident],confirmed=True)
        # The earlier self-contained attachment remains byte-verifiable offline.
        self.assertEqual(portable.verify(original)["documents_byte_verified"],[ident])

    def test_resealed_attachment_and_inventory_tampering_refused(self):
        rec,snap,_=self.evidence_fixture()
        doc=portable.export(self.root,self.actors["admin"],snap,[rec["evidence_id"]],confirmed=True)
        changed=copy.deepcopy(doc)
        changed["attachments"]["documents"][rec["evidence_id"]]=base64.b64encode(b"tampered").decode()
        with self.assertRaises(buyer.BuyerError):
            portable.verify(reseal(changed))
        changed=copy.deepcopy(doc)
        changed["inventory"]["documents"]={}
        with self.assertRaises(buyer.BuyerError):
            portable.verify(reseal(changed))
        changed=copy.deepcopy(doc)
        changed["decision_grade"]=True
        with self.assertRaises(buyer.BuyerError):
            portable.verify(reseal(changed))
        with self.assertRaises(buyer.BuyerError):
            portable.verify(doc,expected_digest="0"*64)

    def test_cli_and_disposition_integrity(self):
        _,snap,_=self.evidence_fixture()
        committee.disposition(self.root,self.actors["admin"],snap,"request_more_evidence","Synthetic follow-up")
        doc=portable.export(self.root,self.actors["operator"],snap,confirmed=True)
        path=self.root.parent/"portable.json"
        path.write_bytes(portable.encode(doc))
        buf=io.StringIO()
        with redirect_stdout(buf):
            cli.main(["verify-committee-dossier",str(path),"--expected-digest",doc["digest"]])
        receipt=json.loads(buf.getvalue())["result"]
        self.assertTrue(receipt["expected_digest_matched"])
        self.assertEqual(receipt["n_dispositions"],1)
        changed=copy.deepcopy(doc)
        changed["snapshot"]["dispositions"][0]["rationale"]="different"
        with self.assertRaises(buyer.BuyerError):
            portable.verify(reseal(changed))

    def test_exact_research_replay_from_programme_and_wrong_version_refused(self):
        fx=programme.ProgrammeTests()
        fx.setUp()
        self.addCleanup(fx.doCleanups)
        fx.setup_programme()
        pid=fx.freeze({"decision":fx.study})
        fx.finish()
        sid=committee.freeze(fx.root,fx.actors["admin"])["snapshot_id"]
        key=pid+":decision"
        doc=portable.export(fx.root,fx.actors["admin"],sid,research=[key],confirmed=True)
        receipt=portable.verify(doc,expected_digest=doc["digest"])
        self.assertEqual(receipt["experimental_reports_replayed"],[key])
        self.assertFalse(receipt["decision_grade"])
        self.assertEqual(receipt["experimental_reports_not_embedded"],[])
        with self.assertRaises(access.AccessDenied):
            portable.export(fx.root,fx.actors["operator"],sid,research=[key],confirmed=True)
        changed=copy.deepcopy(doc)
        changed["attachments"]["experimental_reports"][key]["report"]["digest"]="0"*64
        with self.assertRaises(buyer.BuyerError):
            portable.verify(reseal(changed))

    def test_external_canonical_reference_requires_exact_manifest_and_binding(self):
        root=self.root.parent/"canonical"
        root.mkdir()
        digest="a"*64
        refs={digest:[{"family":"missing_information","vendor_id":"vendor-a","target_version":"1",
                       "target_name":"Fictional","origin":"frozen_comparison"}]}
        metadata={"family_id":"missing_information","manifest_hash":digest}
        binding={"family_id":"missing_information","target_version":"1","target_name":"Fictional"}
        with patch.object(portable.buyer_research,"_preflight",return_value=metadata), \
             patch.object(portable.manifest,"verify_manifest",return_value={"verdict":portable.manifest.VALID}), \
             patch.object(portable.buyer_research,"verified_bytes",return_value=json.dumps(binding).encode()):
            self.assertEqual(portable._canonical_package(root,refs),digest)
            with self.assertRaises(buyer.BuyerError):
                portable._canonical_package(root,{})
            wrong=copy.deepcopy(refs)
            wrong[digest][0]["target_version"]="different"
            with self.assertRaises(buyer.BuyerError):
                portable._canonical_package(root,wrong)


if __name__=="__main__":
    unittest.main()
