"""Synthetic WSGI and persistence regression tests for imaging studies."""
import copy
import json
import unittest
from caeval import (institution_imaging as service, institution_access as access,
                    diagnostic_imaging as imaging, institution_committee as committee)
from tests_unit.test_institution_research import StudioTests
from tests_unit.test_diagnostic_imaging import spec


class ImagingBrowserTests(unittest.TestCase):
    setUp = StudioTests.setUp
    request = StudioTests.request
    post = StudioTests.post

    def freeze(self):
        s = spec()
        p = service.preview(self.root, self.actors["admin"], s)
        ident = service.freeze(self.root, self.actors["admin"], s, p["approval_digest"], confirmed=True)
        return ident

    def template(self, ident, cid="a"):
        return json.loads(self.request("operator", "/diagnostic-imaging-download?id="+ident+
                                      "&candidate="+cid+"&kind=template")["body"])

    def test_browser_private_freeze_and_blinded_collection(self):
        s = spec()
        fields = {"spec": json.dumps(s)}
        self.assertEqual(self.request("admin","/diagnostic-imaging-preview","POST",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("operator","/diagnostic-imaging-preview",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/diagnostic-imaging-preview",fields)["status"],"200 OK")
        p = service.preview(self.root,self.actors["admin"],s)
        fields.update(approval=p["approval_digest"],confirm="yes")
        self.assertEqual(self.post("admin","/diagnostic-imaging-freeze",{**fields,"approval":"wrong"})["status"],"403 Forbidden")
        out=self.post("admin","/diagnostic-imaging-freeze",fields)
        self.assertEqual(out["status"],"303 See Other")
        url=out["headers"]["Location"]
        ident=url.split("id=")[1]
        self.assertEqual(self.post("admin","/diagnostic-imaging-freeze",fields)["status"],"403 Forbidden")
        self.assertEqual(self.request("observer",url)["status"],"403 Forbidden")
        self.assertEqual(self.request("doctor-a",url)["status"],"403 Forbidden")
        self.assertEqual(self.request("operator",url)["status"],"200 OK")
        ex=json.loads(self.request("operator","/diagnostic-imaging-download?id="+ident+"&candidate=a&kind=exchange")["body"])
        self.assertTrue(all(set(c)=={"slot","asset_sha256"} for c in ex["cases"]))
        for key in ("private_nonce","reference","cluster_id","strata"):
            self.assertNotIn(key,ex)
        self.assertNotIn("metrics",service.detail(self.root,self.actors["admin"],ident))

    def test_immutable_submission_and_exact_seal(self):
        ident=self.freeze()
        admin=self.actors["admin"]
        operator=self.actors["operator"]
        preview=service.seal_preview(self.root,admin,ident)
        doc=self.template(ident)
        fields={"id":ident,"predictions":json.dumps(doc),"confirm":"yes"}
        self.assertEqual(self.request("operator","/diagnostic-imaging-submit","POST",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("operator","/diagnostic-imaging-submit",fields)["status"],"303 See Other")
        self.assertEqual(self.post("operator","/diagnostic-imaging-submit",fields)["status"],"303 See Other")
        changed=copy.deepcopy(doc)
        changed["predictions"][0].update(status="scored",score_bp=5000)
        self.assertEqual(self.post("operator","/diagnostic-imaging-submit",{**fields,"predictions":json.dumps(changed)})["status"],"403 Forbidden")
        with self.assertRaises(access.AccessDenied):
            service.seal(self.root,admin,ident,preview["approval_digest"],confirmed=True,allow_incomplete=True)
        p=service.seal_preview(self.root,admin,ident)
        lock={"id":ident,"approval":p["approval_digest"],"confirm":"yes"}
        self.assertEqual(self.post("operator","/diagnostic-imaging-seal",{**lock,"incomplete":"yes"})["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/diagnostic-imaging-seal",lock)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/diagnostic-imaging-seal",{**lock,"incomplete":"yes"})["status"],"303 See Other")
        row=service.detail(self.root,admin,ident)
        self.assertFalse(row["roster_complete"])
        self.assertIn("metrics",row)
        self.assertNotIn("metrics",service.detail(self.root,operator,ident))
        self.assertEqual(self.post("operator","/diagnostic-imaging-submit",fields)["status"],"403 Forbidden")
        self.assertEqual(self.request("operator","/diagnostic-imaging-download?id="+ident+"&candidate=b&kind=template")["status"],"403 Forbidden")
        export={"id":ident,"digest":row["evidence_digest"],"confirm":"yes"}
        self.assertEqual(self.post("operator","/diagnostic-imaging-evidence",export)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/diagnostic-imaging-evidence",{**export,"digest":"0"*64})["status"],"403 Forbidden")
        result=self.post("admin","/diagnostic-imaging-evidence",export)
        self.assertEqual(result["status"],"200 OK")
        pkg=json.loads(result["body"])
        self.assertEqual(pkg["report"]["comparisons"],[])
        self.assertTrue(imaging.verify(pkg,row["evidence_digest"])["expected_digest_matched"])

    def test_committee_coverage_without_labels_and_snapshot_changes(self):
        old=committee.freeze(self.root,self.actors["admin"])["snapshot_id"]
        ident=self.freeze()
        rows=committee.overview(self.root,self.actors["observer"])["lifecycle_inventory"]["diagnostic_imaging_studies"]
        self.assertEqual(rows[0]["id"],ident)
        self.assertNotIn("metrics",rows[0])
        self.assertNotIn("private_nonce",json.dumps(rows))
        self.assertFalse(committee.snapshot(self.root,self.actors["observer"],old)["current"])

    def test_audit_inventory_loss_fails_closed(self):
        ident=self.freeze()
        service.submit(self.root,self.actors["operator"],ident,self.template(ident),confirmed=True)
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM diagnostic_imaging_submissions")
        with self.assertRaises(access.AccessDenied):
            service.detail(self.root,self.actors["admin"],ident)
        with self.assertRaises(access.AccessDenied):
            service.overview(self.root,self.actors["observer"],committee=True)

    def test_private_plan_tampering_unavailable(self):
        ident=self.freeze()
        with access.db(self.root) as conn:
            row=conn.execute("SELECT payload FROM diagnostic_imaging_studies WHERE id=?",(ident,)).fetchone()
            plan=json.loads(row["payload"])
            plan["spec"]["cases"][0]["reference"]="unresolved"
            conn.execute("UPDATE diagnostic_imaging_studies SET payload=? WHERE id=?",(json.dumps(plan),ident))
        self.assertEqual(service.overview(self.root,self.actors["observer"],committee=True)[0]["state"],"unavailable")
        self.assertEqual(self.request("admin","/diagnostic-imaging-study?id="+ident)["status"],"403 Forbidden")

    def test_disabled_role_and_duplicate_json_refused(self):
        self.freeze()
        fields={"spec":'{"study_id":"x","study_id":"y"}'}
        self.assertEqual(self.post("admin","/diagnostic-imaging-preview",fields)["status"],"403 Forbidden")
        access.disable(self.root,"operator")
        with self.assertRaises(access.AccessDenied):
            service.overview(self.root,self.actors["operator"])


if __name__ == "__main__":
    unittest.main()
