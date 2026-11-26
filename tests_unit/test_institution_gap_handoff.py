"""Native synthetic institutional handoff integration regressions."""
import copy
import json
import unittest
from caeval import institution_gap_handoff as service, unified_gap_handoff as h
from caeval import institution_access as access
from tests_unit.test_unified_assessment import UnifiedAssessmentTests


class GapHandoffIntegrationTests(unittest.TestCase):
    setUp=UnifiedAssessmentTests.setUp
    request=UnifiedAssessmentTests.request
    post=UnifiedAssessmentTests.post
    plan=UnifiedAssessmentTests.plan
    spec=UnifiedAssessmentTests.spec
    freeze=UnifiedAssessmentTests.freeze
    fhir_source=UnifiedAssessmentTests.fhir_source
    lock=UnifiedAssessmentTests.lock

    def test_native_approved_download_and_stale_refusal(self):
        pid=self.plan()
        ident=self.freeze(self.spec(pid))
        admin=self.actors["admin"]
        before=service.preview(self.root,admin,ident)
        source=self.fhir_source(pid)
        fields={"id":ident,"approval":before["digest"],"confirm":"yes"}
        self.assertEqual(self.post("admin","/unified-gap-handoff-export",fields)["status"],"403 Forbidden")
        fresh=service.preview(self.root,admin,ident)
        self.assertNotEqual(fresh["digest"],before["digest"])
        self.assertNotIn(source,json.dumps(fresh))
        fields["approval"]=fresh["digest"]
        self.assertEqual(self.request("operator","/unified-gap-handoff-preview?id="+ident)["status"],"403 Forbidden")
        self.assertEqual(self.post("operator","/unified-gap-handoff-export",fields)["status"],"403 Forbidden")
        self.assertEqual(self.request("admin","/unified-gap-handoff-export","POST",fields)["status"],"403 Forbidden")
        r=self.post("admin","/unified-gap-handoff-export",fields)
        self.assertEqual(r["status"],"200 OK")
        package=json.loads(r["body"])
        self.assertEqual(package,fresh)
        self.assertTrue(service.verify(package,fresh["digest"])["expected_digest_matched"])
        self.lock(ident,"fhir-a",source)
        self.assertEqual(self.post("admin","/unified-gap-handoff-export",fields)["status"],"403 Forbidden")
        locked=service.preview(self.root,admin,ident)
        self.assertIn(source,json.dumps(locked))
        self.assertFalse(service.verify(locked)["source_records_reverified"])
        bad=copy.deepcopy(locked)
        bad["report"]["deployment_authorized"]=True
        bad=h.seal({k:v for k,v in bad.items() if k!="digest"})
        with self.assertRaises(ValueError): service.verify(bad)
        with access.db(self.root) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM audit WHERE action=? AND outcome='ok'",(service.EVENT,)).fetchone()[0],1)


if __name__=="__main__": unittest.main()
