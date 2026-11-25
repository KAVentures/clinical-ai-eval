"""Activation uses real canonical conditions, exact scoped inputs and missingness."""
import json
import unittest

from caeval import (buyer_agent as buyer, buyer_plan, buyer_risk_protocol as risk,
                    buyer_risk_activation as activation, buyer_manual as manual,
                    buyer_assessment as assessment, buyer_jobs as jobs, project,
                    institution_access as access, procurement_workflow as pw)
from caeval.util import repo_root
from tests_unit import test_buyer_manual as fixture


class RiskActivationTests(unittest.TestCase):
    setUp=fixture.ManualExchangeTests.setUp
    prepare_exchange=fixture.ManualExchangeTests.prepare
    fill=fixture.ManualExchangeTests.fill
    submit=fixture.ManualExchangeTests.submit

    def staged(self):
        for uid,role in (("admin","admin"),("operator","operator")):
            access.provision(self.root,uid,role,"Synthetic long password")
        self.admin={"user_id":"admin","role":"admin"}
        self.operator={"user_id":"operator","role":"operator"}
        panels=self.root/"approved-panels"
        panels.mkdir()
        self.panel=panels/"mock.toml"
        self.panel.write_bytes((repo_root()/"configs"/"judge_panel.toml").read_bytes())
        self.pid=self.plan["plan_id"]
        self.hazards=[{"hazard_id":"h-"+f,"suite":f,"description":"Fictional hazard",
            "severity":"critical","endpoint":"Proportion of all generated cells",
            "denominator":"All generated originals and variants, missingness explicit",
            "operator":"<=","threshold":0.1,"rationale":"Fictional criterion",
            "mitigation":"Escalate"} for f in self.plan["plan"]["selection"]["runnable_suites"]]
        governance={k:"Fictional scope "+k for k in
                    ("population","setting_boundaries","oversight","data_governance","jurisdiction_review","stop_rules")}
        risk.freeze(self.root,self.operator,self.pid,self.hazards,governance,confirmed=True)
        self.mappings={h["hazard_id"]:"panel_any_unsafe" for h in self.hazards}
        self.gov={"influences_diagnosis_or_treatment":"yes","clinician_reviews_every_response":"yes",
                  "regulated_medical_device":"unsure","deployment_jurisdictions":"SE"}

    def preview(self):
        return activation.preview(self.root,self.operator,self.pid,"fixture","mock",self.mappings)

    def activate(self):
        doc=self.preview()
        return activation.activate(self.root,self.admin,self.pid,"fixture","mock",self.mappings,
                                  doc["digest"],confirmed=True)

    def test_exact_activation_and_prepared_project_handoff(self):
        self.staged()
        doc=self.preview()
        self.assertEqual(doc["denominator"],activation.DENOMINATOR)
        self.assertFalse(doc["decision_grade"])
        self.assertTrue(doc["required_but_not_run"])
        self.assertFalse((self.root/"assessment"/pw.STATE_FILE).exists())
        with self.assertRaises(access.AccessDenied):
            activation.activate(self.root,self.operator,self.pid,"fixture","mock",self.mappings,doc["digest"],confirmed=True)
        with self.assertRaises(access.AccessDenied):
            activation.activate(self.root,self.admin,self.pid,"fixture","mock",self.mappings,"0"*64,confirmed=True)
        active=self.activate()
        state=pw.load(self.root/"assessment")
        self.assertEqual(state["conditions_hash"],active["conditions_hash"])
        self.assertEqual(state["conditions"]["hazards"],doc["hazards"])
        with self.assertRaises(access.AccessDenied):
            self.activate()
        with self.assertRaises(pw.ProcurementError):
            pw.add_vendor(self.root/"assessment","other",doc["target"])
        sub=self.submit(self.fill(self.prepare_exchange()))
        prepared=assessment.prepare(self.root,sub["submission_id"],"mock",self.gov,
                                    conditions_hash=active["conditions_hash"])
        cfg=project.load(self.root/"prepared-assessments"/prepared["assessment_id"])
        self.assertEqual(cfg.data["procurement"]["conditions_hash"],active["conditions_hash"])
        self.assertEqual(assessment.check(self.root,prepared["assessment_id"]),prepared)
        legacy=assessment.prepare(self.root,sub["submission_id"],"mock",self.gov)
        self.assertNotIn("conditions_hash",legacy)
        self.assertNotEqual(legacy["assessment_id"],prepared["assessment_id"])
        self.panel.write_bytes(self.panel.read_bytes()+b"\n# changed\n")
        with self.assertRaises(buyer.BuyerError):
            assessment.check(self.root,prepared["assessment_id"])

    def test_missing_state_cannot_be_reactivated_or_hidden_from_committee(self):
        from caeval import institution_committee as committee
        self.staged()
        self.activate()
        path=self.root/"assessment"/pw.STATE_FILE
        path.unlink()
        with self.assertRaises(access.AccessDenied):
            self.activate()
        with self.assertRaises(access.AccessDenied):
            activation.read_active(self.root)
        with self.assertRaises(access.AccessDenied):
            committee.overview(self.root,self.admin)

    def test_missing_approval_blocks_readback_and_committee(self):
        from caeval import institution_committee as committee
        self.staged()
        self.activate()
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM audit WHERE action=?",(activation.EVENT,))
        with self.assertRaises(access.AccessDenied):
            activation.read_active(self.root)
        with self.assertRaises(access.AccessDenied):
            committee.overview(self.root,self.admin)

    def test_incomplete_mapping_and_unsupported_metric_refused(self):
        self.staged()
        for mapping in ({},{next(iter(self.mappings)):"unsafe_overconfident"},
                        {"unknown":"panel_any_unsafe"}):
            with self.assertRaises(access.AccessDenied):
                activation.preview(self.root,self.operator,self.pid,"fixture","mock",mapping)

    def test_exact_inventory_and_missingness_gate(self):
        self.staged()
        active=self.activate()
        b=active["binding"]
        state=pw.load(self.root/"assessment")
        family=b["families"][0]
        env={"conditions_hash":active["conditions_hash"],"judge_panel_hash":b["judge_panel_hash"],
             "judge_config_digest":b["judge_config_digest"],"judge_prompt_hash":b["judge_prompt_hash"],
             "selection_rules_hash":b["selection_rules_hash"],
             "family_version":"sha256:"+b["family_definition_hashes"][family]}
        bound={"target_name":b["target"]["name"],"target_version":b["target"]["version"],
               "family_id":family,"case_pack_hash":b["pack_content_hash"]}
        cells=[{"cell_id":cid,"kind":"variant","evaluation_complete":True,"validity_valid":True,
                "panel_any_unsafe":0} for cid in b["cell_inventory"][family]]
        cells[0]["validity_valid"]=False
        cells[1]["evaluation_complete"]=False
        normalized=activation.check_package(state,b["vendor_id"],family,env,bound,cells)
        self.assertIsNone(normalized[0]["panel_any_unsafe"])
        self.assertIsNone(normalized[1]["panel_any_unsafe"])
        self.assertEqual(cells[0]["panel_any_unsafe"],0)
        with self.assertRaises(access.AccessDenied):
            activation.check_package(state,b["vendor_id"],family,env,bound,cells[:-1])
        with self.assertRaises(access.AccessDenied):
            activation.check_package(state,b["vendor_id"],family,env,{**bound,"target_version":"different"},cells)
        with self.assertRaises(access.AccessDenied):
            activation.check_package(state,b["vendor_id"],family,{**env,"judge_panel_hash":"different"},bound,cells)
        with self.assertRaises(access.AccessDenied):
            activation.check_package(state,b["vendor_id"],family,{**env,"conditions_hash":"different"},bound,cells)
        with self.assertRaises(access.AccessDenied):
            activation.check_package(state,b["vendor_id"],family,{**env,"judge_config_digest":"different"},bound,cells)
        with self.assertRaises(pw.ProcurementError):
            pw.record_result(self.root/"assessment",b["vendor_id"],family,cells,
                             {"conditions_hash":active["conditions_hash"],"case_pack_hash":b["pack_content_hash"]})

    def test_canonical_job_emits_conditions_hash(self):
        self.staged()
        active=self.activate()
        sub=self.submit(self.fill(self.prepare_exchange()))
        prepared=assessment.prepare(self.root,sub["submission_id"],"mock",self.gov,
                                    conditions_hash=active["conditions_hash"])
        job=jobs.enqueue(self.root,prepared["assessment_id"])
        result=jobs.execute(self.root,job["id"])
        self.assertEqual(result["status"],"completed",result)
        for ref in result["result"]["packages"]:
            path=jobs._job_path(self.root,job["id"])/"out"/("run_"+ref["family_id"])
            analysis=json.loads((path/"analysis.json").read_text())
            self.assertEqual(analysis["conditions_hash"],active["conditions_hash"])
            state=pw.ingest_package(self.root/"assessment","vendor",path)
            self.assertEqual(state["results"]["vendor"][ref["family_id"]]["ingestion"],"verified_package")


if __name__=="__main__":
    unittest.main()
