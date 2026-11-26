"""Offline project execution acceptance and exact-scope checks."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import yaml
from caeval import (buyer_agent as buyer, buyer_plan, cli, decision_assessment as decision,
                    project as project, project_experimental as lane, scribe_assessment as scribe)
from tests_unit.test_project import _filled
from tests_unit.test_decision_assessment import fixture as decision_fixture, bind_raw
from tests_unit.test_scribe_assessment import ScribeTests, fixture as scribe_fixture
from tests_unit.test_robustness_assessment import spec as robustness_spec


class ProjectExperimentalTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.config=_filled()
        self.spec=decision_fixture()
        self.config["target"]={**self.spec["target"],"vendor":"Fictional"}
        self.config["subject"]["headers"]={"Authorization":"EXCLUDED_SECRET_FIXTURE"}
        self.write_config()

    def write_config(self):
        (self.root/"project.yaml").write_text(yaml.safe_dump(self.config))

    def freeze(self, specs=None):
        return lane.build_plan(self.root,specs or {"decision":self.spec})

    def answers(self, frozen):
        plan=frozen["modules"]["decision"]["plan"]
        responses=decision.response_template(plan)
        for item,row in zip(plan["items"],responses["responses"]):
            ref=item["reference"]
            row.update(status="complete",verdict=ref["verification"]["verdict"],
                       certificate=copy.deepcopy(ref["certificate"]),requested_facts=list(ref["unknown_facts"]))
            bind_raw(row)
        path=self.root/"answers.json"
        path.write_text(json.dumps(responses))
        return path

    def complete(self, frozen):
        return lane.complete_module(self.root,frozen,frozen["digest"],"decision",
                                    self.answers(frozen),confirmed=True)

    def test_native_execution_retains_every_canonical_gap(self):
        frozen=self.freeze()
        self.assertNotIn("EXCLUDED_SECRET_FIXTURE",json.dumps(frozen))
        self.assertEqual(frozen["unplanned_related_modules"],["robustness"])
        package=self.complete(frozen)
        result=lane.assemble(self.root,frozen,frozen["digest"],{"decision":package})
        self.assertTrue(result["all_planned_modules_replayed"])
        self.assertFalse(result["decision_grade"])
        self.assertEqual(result["module_receipts"]["decision"]["summary"]["verdict_agreement"]["rate_on_assessed"],1)
        self.assertEqual({x["suite"] for x in result["coverage"]},
                         {x["suite"] for x in frozen["scope"]["selection"]["required_suites"]})
        self.assertTrue(all(not x["canonical_requirement_satisfied"] for x in result["coverage"]))
        receipt=lane.verify_result(self.root,frozen,frozen["digest"],result,expected_digest=result["digest"])
        self.assertEqual(receipt["status"],"REPLAY_VERIFIED")
        self.assertTrue(receipt["expected_digest_matched"])

    def test_changed_project_intake_target_approval_and_plan_rejected(self):
        frozen=self.freeze()
        with self.assertRaises(buyer.BuyerError):
            lane.module_plan(self.root,frozen,"0"*64,"decision")
        self.config["intake"]["plausible_harm"]="Changed scope"
        self.write_config()
        with self.assertRaises(buyer.BuyerError):
            lane.verify_plan(self.root,frozen,frozen["digest"])
        self.spec["target"]["version"]="wrong"
        with self.assertRaises(buyer.BuyerError):
            self.freeze()

    def test_unselected_module_and_non_synthetic_mock_rejected(self):
        with self.assertRaises(buyer.BuyerError):
            self.freeze({"scribe":scribe_fixture()[0]})
        self.spec["synthetic"]=False
        with self.assertRaises(buyer.BuyerError):
            self.freeze()

    def test_partial_bundle_and_forged_coverage_rejected(self):
        robust=robustness_spec()
        robust["target"]=self.spec["target"]
        frozen=self.freeze({"decision":self.spec,"robustness":robust})
        package=self.complete(frozen)
        result=lane.assemble(self.root,frozen,frozen["digest"],{"decision":package})
        self.assertFalse(result["all_planned_modules_replayed"])
        self.assertEqual(result["uncompleted_planned_modules"],["robustness"])
        forged=copy.deepcopy(result)
        forged["coverage"][0]["canonical_requirement_satisfied"]=True
        forged["digest"]=buyer_plan._digest({k:v for k,v in forged.items() if k!="digest"})
        with self.assertRaises(buyer.BuyerError):
            lane.verify_result(self.root,frozen,frozen["digest"],forged)
        changed=copy.deepcopy(package)
        changed["kind"]="robustness"
        with self.assertRaises(buyer.BuyerError):
            lane.assemble(self.root,frozen,frozen["digest"],{"decision":changed})

    def test_scribe_review_end_to_end(self):
        fixture=ScribeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.config["target"]={**fixture.spec["target"],"vendor":"Fictional"}
        self.config["target_profile"]["types"]=["medical_scribe"]
        self.write_config()
        frozen=self.freeze({"scribe":fixture.spec})
        binding=lane.prepare_review(self.root,frozen,frozen["digest"],"scribe",
                       fixture.responses,fixture.dest,["doctor-a","doctor-b"],[],"coordinator",confirmed=True)
        for rid in ("doctor-a","doctor-b"):
            fixture.submit(rid,fixture.fill(rid))
        package=lane.complete_module(self.root,frozen,frozen["digest"],"scribe",fixture.dest,confirmed=True)
        self.assertEqual(package["binding"],binding)
        result=lane.assemble(self.root,frozen,frozen["digest"],{"scribe":package})
        self.assertTrue(result["all_planned_modules_replayed"])
        self.assertEqual(len(result["coverage"]),2)
        self.assertTrue(all(x["replayed_experimental_modules"]==["scribe"] for x in result["coverage"]))

    def call(self,*args):
        buf=io.StringIO()
        with redirect_stdout(buf):
            cli.main(["experimental-project",*args])
        result=json.loads(buf.getvalue())
        self.assertTrue(result["ok"])
        return result["result"]

    def test_cli_freeze_exchange_complete_assemble_verify(self):
        specs=self.root/"specs.json"
        specs.write_text(json.dumps({"decision":self.spec}))
        plan=self.root/"frozen.json"
        res=self.call("freeze","--project",str(self.root),"--specs",str(specs),"--out",str(plan))
        frozen=json.loads(plan.read_text())
        bound=["--project",str(self.root),"--plan",str(plan),"--approve-plan",res["digest"]]
        exchange=self.root/"exchange.json"
        self.call("exchange",*bound,"--kind","decision","--out",str(exchange))
        self.assertNotIn("reference",json.loads(exchange.read_text())["items"][0])
        package=self.root/"package.json"
        self.call("complete-module",*bound,"--kind","decision","--source",str(self.answers(frozen)),
                  "--confirm","--out",str(package))
        result=self.root/"result.json"
        assembled=self.call("assemble",*bound,"--package","decision="+str(package),"--out",str(result))
        self.assertEqual(self.call("verify",*bound,"--result",str(result),
                                  "--expected-digest",assembled["digest"])["status"],"REPLAY_VERIFIED")


    def test_inspect_cli_exact_plan_and_no_raw_material(self):
        frozen=self.freeze()
        plan=self.root/"inspection-plan.json"
        plan.write_text(json.dumps(frozen))
        bound=["--project",str(self.root),"--plan",str(plan),"--approve-plan",frozen["digest"]]
        receipt=self.call("inspect",*bound)
        self.assertEqual(receipt,lane.inspect_plan(self.root,frozen,frozen["digest"]))
        self.assertEqual(receipt["modules"][0]["workflow"],"structured_response_scoring")
        self.assertEqual(receipt["modules"][0]["planned_units"],len(frozen["modules"]["decision"]["plan"]["items"]))
        self.assertFalse(receipt["native_workspaces_inspected"])
        self.assertNotIn("EXCLUDED_SECRET_FIXTURE",json.dumps(receipt))
        self.assertNotIn("reference",json.dumps(receipt))
        with redirect_stdout(io.StringIO()),self.assertRaises(SystemExit):
            cli.main(["experimental-project","inspect",*bound[:-1],"0"*64])

    def test_resealed_scientific_report_type_substitution_refused(self):
        from caeval import experimental_evidence as evidence
        frozen=self.freeze()
        package=self.complete(frozen)
        changed=copy.deepcopy(package)
        changed["report"]["decision_grade"]=0
        changed["digest"]=buyer_plan._digest({k:v for k,v in changed.items() if k!="digest"})
        with self.assertRaises(buyer.BuyerError):
            evidence.verify(changed)


if __name__=="__main__":
    unittest.main()
