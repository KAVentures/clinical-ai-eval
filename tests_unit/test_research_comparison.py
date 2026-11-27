"""Matched research comparisons: fixed opportunity denominators and access gates."""
import json
import tempfile
import unittest
from pathlib import Path

from caeval import (buyer_agent as buyer, buyer_plan, institution_access as access,
                    institution_research as studio, research_comparison as compare)
from tests_unit.test_decision_assessment import fixture, bind_raw
from caeval import decision_assessment as decision


class MatchedMetricsTests(unittest.TestCase):
    def test_missingness_and_discordant_events(self):
        a={"1":True,"2":False,"3":True,"4":False,"5":None,"6":True,"7":None}
        b={"1":True,"2":True,"3":False,"4":False,"5":False,"6":None,"7":None}
        m=compare.matched(a,b)
        self.assertEqual(m["planned_units"],7)
        self.assertEqual(m["both_assessed"],4)
        self.assertEqual(m["baseline_only_assessed"],1)
        self.assertEqual(m["candidate_only_assessed"],1)
        self.assertEqual(m["neither_assessed"],1)
        self.assertEqual(m["both_event"],1)
        self.assertEqual(m["baseline_event_only"],1)
        self.assertEqual(m["candidate_event_only"],1)
        self.assertEqual(m["neither_event"],1)
        self.assertEqual(m["candidate_minus_baseline"],0)
        self.assertIsNone(compare.matched({"a":None},{"a":False})["candidate_minus_baseline"])
        with self.assertRaises(access.AccessDenied):
            compare.matched({"a":False},{"b":False})

    def test_candidate_claim_counts_not_equated(self):
        plan={"queries":[{"query_id":"q","case_id":"case","probe":"baseline"}]}
        row={"query_id":"q","case_id":"case","probe":"baseline","status":"complete",
             "coverage_verified":True,"n_claims":3,"supported":1,"unsupported":1,"unassessed":1}
        a=compare._endpoints("citation",plan,{"rows":[row]})
        self.assertTrue(a["baseline/any_unsupported_claim"]["q"])
        row.update(n_claims=1,supported=0,unsupported=0,unassessed=1)
        b=compare._endpoints("citation",plan,{"rows":[row]})
        self.assertIsNone(b["baseline/any_unsupported_claim"]["q"])
        self.assertEqual(compare.matched(a["baseline/any_unsupported_claim"],b["baseline/any_unsupported_claim"])["both_assessed"],0)
        row.update(n_claims=0,supported=0,unsupported=0,unassessed=0)
        self.assertIsNone(compare._endpoints("citation",plan,{"rows":[row]})["baseline/any_unsupported_claim"]["q"])

    def test_scribe_fixed_reference_fact_and_unknown_note_claim(self):
        plan={"items":[{"slot":"s","reference_facts":[{"fact_id":"allergy","importance":"critical"}]}]}
        row={"slot":"s","outcome":"complete","reference_coverage_verified":True,"claim_coverage_verified":True,
             "facts":[{"fact_id":"allergy","importance":"critical","reference_validity":"valid","status":"omitted"}],
             "claims":[{"status":"unsupported"},{"status":"unassessed"}]}
        a=compare._endpoints("scribe",plan,{"rows":[row]})
        self.assertTrue(a["critical_reference_fact_omitted"]["s/allergy"])
        self.assertTrue(a["any_documentation_defect"]["s"])
        row["facts"][0]["reference_validity"]="invalid"
        row["claims"]=[{"status":"supported"},{"status":"contested"}]
        b=compare._endpoints("scribe",plan,{"rows":[row]})
        self.assertIsNone(b["critical_reference_fact_omitted"]["s/allergy"])
        self.assertIsNone(b["any_documentation_defect"]["s"])

    def test_invalid_robustness_pair_has_no_clinical_score(self):
        p={"pair_id":"p","case_id":"c","transform":"paraphrase_query","relation":"invariant"}
        row={**p,"baseline":{"outcome":"complete"},"variant":{"outcome":"complete"},"reference_validity":"invalid"}
        for k in ("baseline_unsupported_inference","variant_unsupported_inference","variant_unsafe_response",
                  "decision_group_flip","both_acceptable","adverse_degradation","new_unsupported_inference","new_unsafe_response"):
            row[k]=True
        metrics=compare._endpoints("robustness",{"pairs":[p]},{"pairs":[row]})
        self.assertTrue(metrics["paraphrase_query/pair_completed"]["p"])
        self.assertIsNone(metrics["paraphrase_query/new_unsafe_response"]["p"])


class StudyComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/"workspace"
        buyer.init(self.root,"Fictional hospital")
        self.actors={}
        for uid,role in (("admin","admin"),("operator","operator"),("other","operator"),("observer","observer")):
            access.provision(self.root,uid,role,"Synthetic password 123")
            self.actors[uid]=access.session(self.root,access.login(self.root,uid,"Synthetic password 123"))

    def study(self,name,owner="operator",mutate=None,wrong=False):
        spec=fixture()
        spec["target"]["name"]=name
        if mutate:
            mutate(spec)
        ident=studio.create(self.root,self.actors[owner],"decision",spec)
        plan=studio.download(self.root,self.actors[owner],ident,"plan")
        responses=decision.response_template(plan)
        for item,row in zip(plan["items"],responses["responses"]):
            ref=item["reference"]
            verdict=ref["verification"]["verdict"]
            if wrong and item["case"]["probe"]=="contraindication":
                verdict=decision.v.CERTIFIED
            row.update(status="complete",verdict=verdict,certificate=ref["certificate"],
                       requested_facts=ref["unknown_facts"])
            bind_raw(row)
        studio.collect(self.root,self.actors[owner],ident,plan["plan_hash"],responses,confirmed=True)
        studio.analyze(self.root,self.actors["admin"],ident,confirmed=True)
        return ident

    def test_real_studies_and_report_tampering(self):
        a=self.study("Fictional A")
        b=self.study("Fictional B",wrong=True)
        ident=compare.create(self.root,self.actors["operator"],[a,b],confirmed=True)
        doc=compare.download(self.root,self.actors["operator"],ident)
        self.assertEqual(doc["baseline_study"],a)
        self.assertFalse(doc["decision_grade"])
        metric=doc["comparisons"][0]["metrics"]["missed_block"]
        self.assertEqual(metric["both_assessed"],1)
        self.assertEqual(metric["candidate_event_only"],1)
        self.assertEqual(metric["candidate_minus_baseline"],1)
        self.assertEqual(compare.inventory(self.root,self.actors["other"]),[])
        for actor in ("other","observer"):
            with self.assertRaises(access.AccessDenied):
                compare.download(self.root,self.actors[actor],ident)
        self.assertEqual(doc,compare.download(self.root,self.actors["admin"],ident))
        path=self.root/"research-studies"/b/"report.json"
        report=json.loads(path.read_text())
        report["summary"]["completed"]=0
        path.write_text(json.dumps(report))
        with self.assertRaises(access.AccessDenied):
            compare.download(self.root,self.actors["operator"],ident)

    def test_reject_changed_protocol_duplicate_target_and_other_owner(self):
        a=self.study("Fictional A")
        b=self.study("Fictional B",mutate=lambda s:s["cases"].pop())
        c=self.study("Fictional A")
        d=self.study("Fictional D",owner="other")
        for ids in ([a,b],[a,c],[a,d],[a,a]):
            with self.assertRaises(access.AccessDenied):
                compare.create(self.root,self.actors["operator"],ids,confirmed=True)
        with self.assertRaises(access.AccessDenied):
            compare.create(self.root,self.actors["admin"],[a,d],confirmed=False)
        self.assertTrue(compare.create(self.root,self.actors["admin"],[a,d],confirmed=True))


if __name__=="__main__":
    unittest.main()
