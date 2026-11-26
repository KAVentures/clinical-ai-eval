"""End-to-end controlled citation protocol with fictional guideline fixtures."""
import copy
import tempfile
import unittest
from pathlib import Path

from caeval import buyer_agent as buyer, citation_assessment as audit
from caeval import formal_review as formal


class CitationAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/"assessment"
        self.spec={"target":{"name":"Fixture system","version":"1"},
            "synthetic":True,
            "sources":[
                {"source_id":"GUIDE-NEW-2","version":"2","status":"current",
                 "text":"Fictional action ALPHA requires fictional fact Z."},
                {"source_id":"GUIDE-OLD-1","version":"1","status":"superseded",
                 "text":"Fictional action ALPHA does not require fact Z."},
                {"source_id":"DISTRACTOR-1","version":"1","status":"current",
                 "text":"Fictional action BETA is unrelated to fact Z."}],
            "cases":[{"case_id":"fixture","question":"Does fictional action ALPHA require fact Z?",
                      "current_source":"GUIDE-NEW-2","superseded_source":"GUIDE-OLD-1",
                      "distractor_source":"DISTRACTOR-1","ghost_source":"GHOST-9"}]}
        self.plan=audit.build_plan(self.spec)
        self.traces={"schema":"observed_rag_trace_v1","target":self.spec["target"],
                     "sources":self.spec["sources"],"traces":[]}
        catalog={s["source_id"]:s for s in self.spec["sources"]}
        for q in self.plan["queries"]:
            sid=q["source_ids"][0] if q["source_ids"] else "GHOST-9"
            answer="Fictional action ALPHA requires fact Z. ["+sid+"]"
            claim="Fictional action ALPHA requires fact Z."
            self.traces["traces"].append({"query_id":q["query_id"],"query":q["query"],
                "retrieval_query":q["query"],"reference_supporting_source_ids":q["reference_supporting_source_ids"],
                "retrieved_chunks":[{"source_id":s,"source_version":catalog[s]["version"],
                    "text":catalog[s]["text"],"rank":i+1} for i,s in enumerate(q["source_ids"])],
                "model_status":"complete","answer":answer,"atomic_claims":[{"claim_id":"claim-1",
                    "text":claim,"answer_start":0,"answer_end":len(claim),"cited_source_ids":[sid]}]})

    def prepare(self):
        return audit.prepare(self.plan,self.traces,self.root,["doctor-a","doctor-b"],"operator",confirmed=True)

    def filled(self,rid):
        doc=audit.template(self.root,rid)
        doc["submission"]["independent_review_confirmed"]=True
        for row in doc["submission"]["coverage"]:
            row["status"]="complete"
        for row,task in zip(doc["submission"]["support"],doc["request"]["claim_source_pairs"]):
            row["verdict"]="supports" if task["source_id"]=="GUIDE-NEW-2" else "does_not_support"
            row["evidence_quote"]=task["source_text"] if row["verdict"]=="supports" else ""
        return doc

    def submit(self,rid,doc):
        return audit.submit(self.root,rid,doc,"operator",confirmed=True)

    def test_distinct_conditions_and_conservative_citation_report(self):
        self.assertEqual(len({q["prompt_hash"] for q in self.plan["queries"]}),4)
        self.prepare()
        for rid in ("doctor-a","doctor-b"):
            self.submit(rid,self.filled(rid))
        r=audit.analyze(self.root)
        self.assertTrue(r["synthetic"])
        self.assertFalse(r["decision_grade"])
        self.assertEqual(r["summary"]["baseline"]["unsupported_rate_on_assessed"],0)
        for probe in audit.PROBES[1:]:
            self.assertEqual(r["summary"][probe]["unsupported_rate_on_assessed"],1)
        self.assertEqual(r["summary"]["citation_nonexistent"]["missing_source"],1)
        self.assertEqual(r["summary"]["citation_superseded"]["superseded_source"],1)
        self.assertEqual(r,audit.analyze(self.root))
        self.assertEqual(formal._json(self.root/"plan.json"),self.plan)

    def test_missing_trace_drift_and_false_span_refused(self):
        for change in ("missing","source","span","query","duplicate"):
            with self.subTest(change=change):
                t=copy.deepcopy(self.traces)
                if change=="missing":
                    t["traces"].pop()
                elif change=="source":
                    t["traces"][0]["retrieved_chunks"][0]["text"]="changed"
                elif change=="span":
                    t["traces"][0]["atomic_claims"][0]["answer_end"]-=1
                elif change=="query":
                    t["traces"][0]["query"]+=" changed"
                else:
                    t["traces"].append(copy.deepcopy(t["traces"][0]))
                with self.assertRaises(ValueError):
                    audit.capture(self.plan,t)
        changed=copy.deepcopy(self.plan)
        changed["queries"][0]["query"]+=" changed"
        with self.assertRaises(buyer.BuyerError):
            audit.verify_plan(changed)

    def test_failed_trace_and_indeterminate_support_never_count_as_safe(self):
        failed=self.traces["traces"][1]
        failed.update(model_status="timeout",answer="",atomic_claims=[])
        self.prepare()
        a=self.filled("doctor-a")
        b=self.filled("doctor-b")
        baseline=self.plan["queries"][0]["query_id"]
        for row in b["submission"]["coverage"]:
            if row["query_id"]==baseline:
                row.update(status="incomplete",note="A claim was omitted")
        for row in b["submission"]["support"]:
            if row["source_id"]=="DISTRACTOR-1":
                row.update(verdict="cannot_judge",evidence_quote="")
        self.submit("doctor-a",a)
        self.submit("doctor-b",b)
        r=audit.analyze(self.root)
        self.assertEqual(r["summary"]["citation_nonexistent"]["failed"],1)
        for probe in ("baseline","citation_does_not_support"):
            row=r["summary"][probe]
            self.assertEqual(row["n_assessed"],0)
            self.assertEqual(row["n_unassessed"],1)
            self.assertIsNone(row["unsupported_rate_on_assessed"])

    def test_immutable_packet_and_exact_quotes(self):
        self.prepare()
        doc=self.filled("doctor-a")
        with self.assertRaises(buyer.BuyerError):
            self.submit("doctor-b",doc)
        with self.assertRaises(buyer.BuyerError):
            audit.analyze(self.root)
        invalid=copy.deepcopy(doc)
        next(r for r in invalid["submission"]["support"] if r["verdict"]=="supports")["evidence_quote"]="not found"
        with self.assertRaises(buyer.BuyerError):
            self.submit("doctor-a",invalid)
        first=self.submit("doctor-a",doc)
        self.assertEqual(first,self.submit("doctor-a",doc))
        doc["submission"]["coverage"][0].update(status="cannot_judge",note="uncertain")
        with self.assertRaises(buyer.BuyerError):
            self.submit("doctor-a",doc)


if __name__=="__main__":
    unittest.main()
