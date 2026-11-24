"""Committee snapshots are descriptive, immutable and role-scoped."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer, institution_access as access
from caeval import institution_committee as committee, procurement_workflow as pw


class CommitteeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"buyer"
        buyer.init(self.root,"Committee","CDS","ED","SE")
        buyer.add_vendor(self.root,"a","Candidate A")
        buyer.add_vendor(self.root,"b","Candidate B")
        for uid,role in (("admin","admin"),("operator","operator"),("observer","observer"),("reviewer","reviewer")):
            access.provision(self.root,uid,role,"Synthetic test password!")
        self.admin={"user_id":"admin","role":"admin"}
        self.operator={"user_id":"operator","role":"operator"}
        self.observer={"user_id":"observer","role":"observer"}
        self.reviewer={"user_id":"reviewer","role":"reviewer"}

    def test_freeze_and_stale_evidence(self):
        first=committee.freeze(self.root,self.operator)
        identifier=first["snapshot_id"]
        self.assertEqual(committee.freeze(self.root,self.operator),first)
        snap=committee.snapshot(self.root,self.observer,identifier)
        self.assertTrue(snap["current"])
        self.assertFalse(snap["decision_grade"])
        outcome=committee.disposition(self.root,self.admin,identifier,"request_more_evidence","Missing clinical evidence")
        self.assertFalse(outcome["deployment_authorized"])
        self.assertEqual(len(committee.snapshot(self.root,self.admin,identifier)["dispositions"]),1)
        buyer.add_vendor(self.root,"c","New candidate")
        historical=committee.snapshot(self.root,self.observer,identifier)
        self.assertFalse(historical["current"])
        self.assertEqual(len(historical["payload"]["vendors"]),2)
        with self.assertRaises(access.AccessDenied):
            committee.disposition(self.root,self.admin,identifier,"close_review","Old evidence")
        self.assertNotEqual(committee.freeze(self.root,self.operator),first)

    def test_access_and_invalid_disposition(self):
        with self.assertRaises(access.AccessDenied):
            committee.overview(self.root,self.reviewer)
        with self.assertRaises(access.AccessDenied):
            committee.freeze(self.root,self.observer)
        identifier=committee.freeze(self.root,self.operator)["snapshot_id"]
        with self.assertRaises(access.AccessDenied):
            committee.disposition(self.root,self.operator,identifier,"defer","reason")
        with self.assertRaises(access.AccessDenied):
            committee.disposition(self.root,self.admin,identifier,"approve_purchase","reason")
        with self.assertRaises(access.AccessDenied):
            committee.disposition(self.root,self.admin,identifier,"defer","")

    def test_snapshot_integrity(self):
        identifier=committee.freeze(self.root,self.admin)["snapshot_id"]
        with access.db(self.root) as conn:
            conn.execute("UPDATE committee_snapshots SET payload='{}' WHERE id=?",(identifier,))
        with self.assertRaises(access.AccessDenied):
            committee.snapshot(self.root,self.admin,identifier)

    def test_missing_and_unverified_study_results(self):
        study=self.root/"assessment"
        state=pw.init(study,"Study",["missing_information"],{"content_hash":"pack"},
                      "panel.toml",[{"hazard_id":"h","acceptance_criterion":{"metric":"unsafe_overconfident","operator":"<=","threshold":0.2}}])
        pw.add_vendor(study,"a",{"name":"A","version":"1"},"Product A")
        pw.record_result(study,"a","missing_information",[],{"conditions_hash":state["conditions_hash"],"case_pack_hash":"pack"})
        before=pw.load(study)
        out=committee.overview(self.root,self.admin)["clinical_comparison"]
        self.assertEqual(out["families"][0]["status"],"not_measured")
        self.assertTrue(any("no verified package" in s for s in out["gaps"]))
        self.assertTrue(any("not registered" in s for s in out["gaps"]))
        self.assertEqual(pw.load(study),before)

    def test_package_path_boundary_and_identity(self):
        vendor={"subject_spec":{"name":"A","version":"1"}}
        with self.assertRaises(access.AccessDenied):
            committee._package(self.root,vendor,"missing_information",{"package":{"workspace":str(self.root.parent),"package_digest":"x"}})
        ws=self.root/"assessment"/"packages"/"run"
        ws.mkdir(parents=True)
        (ws/"plan_binding.json").write_text(json.dumps({"target_name":"A","target_version":"2","family_id":"missing_information"}))
        with patch.object(committee.buyer_research,"_preflight",return_value={"manifest_hash":"x","family_id":"missing_information"}), \
             patch.object(committee.manifest,"verify_manifest",return_value={"verdict":committee.manifest.VALID}):
            with self.assertRaises(access.AccessDenied):
                committee._package(self.root,vendor,"missing_information",{"package":{"workspace":str(ws),"package_digest":"x"}})

    def test_failed_post_ingestion_check_does_not_publish_result(self):
        study=self.root/"assessment"
        pw.init(study,"Study",["missing_information"],{"content_hash":"pack"},
                "panel.toml",[{"hazard_id":"h","acceptance_criterion":{"metric":"unsafe_overconfident","operator":"<=","threshold":0.2}}])
        pw.add_vendor(study,"a",{"name":"A","version":"1"},"Product A")
        state=pw.load(study)
        state["results"]={"a":{"missing_information":{"ingestion":"verified_package","package":{"workspace":"not-real","package_digest":"x"}}}}
        pw.save(study,state)
        fake={"results":{"a":{"missing_information":{"environment":{"package_digest":"different"}}}}}
        with patch.object(committee,"_package",return_value=(self.root,"x")), \
             patch.object(pw,"ingest_package",return_value=fake):
            out=committee.overview(self.root,self.admin)["clinical_comparison"]
        self.assertEqual(out["families"][0]["status"],"not_measured")
        self.assertTrue(any("excluded" in s for s in out["gaps"]))


if __name__=="__main__":
    unittest.main()
