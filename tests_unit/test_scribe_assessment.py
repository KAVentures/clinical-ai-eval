"""Synthetic transcript-to-note evaluation with independent physician packets."""
import copy
import tempfile
import unittest
from pathlib import Path

from caeval import scribe_assessment as scribe, formal_review as formal
from caeval import buyer_agent as buyer


def span(text,quote):
    start=text.index(quote)
    return {"start":start,"end":start+len(quote),"quote":quote}


def evidence(turn,quote):
    return {"turn_id":turn["turn_id"],**span(turn["text"],quote)}


def fixture():
    a={"turn_id":"t1","speaker":"clinician","text":"Tell me about your symptoms."}
    b={"turn_id":"t2","speaker":"patient","text":"I have a headache. I am allergic to fictional drug ALPHA."}
    c={"turn_id":"t3","speaker":"clinician","text":"No examination was performed. We discussed a follow-up call."}
    case={"case_id":"consultation","transcript":[a,b,c],"reference_facts":[
        {"fact_id":"headache","fact":"Patient reports headache","importance":"important",
         "transcript_evidence":[evidence(b,"I have a headache.")]},
        {"fact_id":"allergy","fact":"Patient reports allergy to fictional drug ALPHA","importance":"critical",
         "transcript_evidence":[evidence(b,"I am allergic to fictional drug ALPHA.")]},
        {"fact_id":"exam","fact":"No examination was performed","importance":"important",
         "transcript_evidence":[evidence(c,"No examination was performed.")]},
        {"fact_id":"followup","fact":"Follow-up call discussed","importance":"routine",
         "transcript_evidence":[evidence(c,"We discussed a follow-up call.")]}]}
    spec={"target":{"name":"Private fixture scribe","version":"1"},"synthetic":True,
          "note_format":"soap","cases":[case]}
    plan=scribe.build_plan(spec)
    responses=scribe.response_template(plan)
    note="Patient reports headache. Normal examination. Medication BETA administered. Follow-up call discussed."
    claims=[{"claim_id":key,**span(note,quote)} for key,quote in [
        ("symptom","Patient reports headache."),("exam","Normal examination."),
        ("drug","Medication BETA administered."),("followup","Follow-up call discussed.")]]
    responses["responses"][0].update(status="complete",note=note,claims=claims)
    return spec,plan,responses


class ScribeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dest=Path(self.temp.name)/"review"
        self.spec,self.plan,self.responses=fixture()

    def prepare(self):
        return scribe.prepare(self.plan,self.responses,self.dest,["doctor-a","doctor-b"],"coordinator",confirmed=True)

    def fill(self,rid):
        doc=scribe.template(self.dest,rid)
        doc["submission"]["independent_review_confirmed"]=True
        for item,row in zip(doc["request"]["items"],doc["submission"]["cases"]):
            turns={t["turn_id"]:t for t in item["transcript"]}
            note=item["note"]
            row.update(reference_coverage="complete",claim_coverage="complete")
            for f in row["facts"]:
                f.update(reference_validity="valid",rationale="Independent synthetic fixture review")
                if f["fact_id"]=="allergy":
                    f["documentation"]="omitted"
                elif f["fact_id"]=="exam":
                    f.update(documentation="contradicted",note_evidence=[span(note,"Normal examination.")])
                else:
                    quote="Patient reports headache." if f["fact_id"]=="headache" else "Follow-up call discussed."
                    f.update(documentation="documented",note_evidence=[span(note,quote)])
            for c in row["claims"]:
                c.update(rationale="Independent synthetic fixture review",importance="important")
                if c["claim_id"]=="drug":
                    c.update(verdict="unsupported",importance="critical")
                elif c["claim_id"]=="exam":
                    c.update(verdict="contradicted",transcript_evidence=[evidence(turns["t3"],"No examination was performed.")])
                elif c["claim_id"]=="symptom":
                    c.update(verdict="supported",transcript_evidence=[evidence(turns["t2"],"I have a headache.")])
                else:
                    c.update(verdict="supported",transcript_evidence=[evidence(turns["t3"],"We discussed a follow-up call.")])
        return doc

    def submit(self,rid,doc):
        return scribe.submit(self.dest,rid,doc,"coordinator",confirmed=True)

    def test_candidate_exchange_and_independent_defects(self):
        ex=scribe.exchange(self.plan)
        self.assertNotIn("reference_facts",ex["items"][0])
        self.assertNotIn("importance",ex["items"][0])
        self.assertNotIn("case_id",ex["items"][0])
        binding=self.prepare()
        first=self.fill("doctor-a")
        self.assertNotIn("doctor-b",str(first))
        self.assertNotIn("Private fixture scribe",str(first))
        self.submit("doctor-a",first)
        with self.assertRaises(buyer.BuyerError):
            scribe.analyze(self.dest)
        self.submit("doctor-b",self.fill("doctor-b"))
        report=scribe.analyze(self.dest)
        s=report["summary"]
        self.assertEqual(s["omissions"]["rate_on_assessed"],0.25)
        self.assertEqual(s["critical_omissions"]["rate_on_assessed"],1)
        self.assertEqual(s["reference_contradictions"]["defects"],1)
        self.assertEqual(s["unsupported_documentation"]["defects"],1)
        self.assertEqual(s["contradicted_documentation"]["defects"],1)
        self.assertEqual(s["all_documentation_defects"]["rate_on_assessed"],0.5)
        self.assertEqual(s["critical_documentation_defects"],1)
        self.assertTrue(report["synthetic"])
        self.assertFalse(report["decision_grade"])
        self.assertEqual(binding["binding_hash"],report["binding_hash"])
        self.assertEqual(scribe.analyze(self.dest),report)
        self.assertEqual(formal._json(self.dest/"plan.json"),self.plan)
        self.assertEqual(formal._json(self.dest/"responses.json"),self.responses)

    def test_incomplete_coverage_invalid_reference_and_disagreement(self):
        self.prepare()
        a=self.fill("doctor-a")
        b=self.fill("doctor-b")
        case=b["submission"]["cases"][0]
        case.update(claim_coverage="incomplete",coverage_note="Other factual content omitted from extraction.")
        fact=next(f for f in case["facts"] if f["fact_id"]=="allergy")
        fact.update(reference_validity="cannot_judge",documentation="cannot_judge")
        other=next(f for f in case["facts"] if f["fact_id"]=="followup")
        other.update(documentation="omitted",note_evidence=[])
        self.submit("doctor-a",a)
        self.submit("doctor-b",b)
        s=scribe.analyze(self.dest)["summary"]
        self.assertEqual(s["critical_omissions"]["assessed"],0)
        self.assertIsNone(s["critical_omissions"]["rate_on_assessed"])
        self.assertEqual(s["omissions"]["unassessed"],2)
        self.assertEqual(s["all_documentation_defects"]["unassessed"],4)
        self.assertIsNone(s["all_documentation_defects"]["rate_on_assessed"])

    def test_missing_notes_remain_in_reference_denominator(self):
        other=copy.deepcopy(self.spec["cases"][0])
        other["case_id"]="second"
        self.spec["cases"].append(other)
        self.plan=scribe.build_plan(self.spec)
        existing=self.responses["responses"][0]
        self.responses=scribe.response_template(self.plan)
        self.responses["responses"][0]=existing
        self.responses["responses"][1]["status"]="timeout"
        self.prepare()
        for rid in ("doctor-a","doctor-b"):
            self.submit(rid,self.fill(rid))
        s=scribe.analyze(self.dest)["summary"]
        self.assertEqual(s["planned_cases"],2)
        self.assertEqual(s["failed_cases"],1)
        self.assertEqual(s["omissions"]["total"],8)
        self.assertEqual(s["omissions"]["unassessed"],4)
        self.assertEqual(s["omissions"]["assessed"],4)

    def test_no_output_never_fabricates_review(self):
        self.responses["responses"][0].update(status="no_note",note="",claims=[])
        captured=scribe.capture(self.plan,self.responses)
        self.assertEqual(captured["items"][0]["status"],"no_note")
        with self.assertRaises(buyer.BuyerError):
            self.prepare()
        self.assertFalse(self.dest.exists())

    def test_reject_invalid_sources_spans_and_missing_responses(self):
        for change in ("source","claim","missing","target","failed_payload","duplicate"):
            with self.subTest(change=change):
                r=copy.deepcopy(self.responses)
                if change=="source":
                    r["responses"][0]["transcript_hash"]="bad"
                elif change=="claim":
                    r["responses"][0]["claims"][0]["quote"]="invented"
                elif change=="missing":
                    r["responses"].clear()
                elif change=="target":
                    r["target"]["version"]="2"
                elif change=="failed_payload":
                    r["responses"][0]["status"]="timeout"
                else:
                    r["responses"][0]["claims"].append(copy.deepcopy(r["responses"][0]["claims"][0]))
                with self.assertRaises(buyer.BuyerError):
                    scribe.capture(self.plan,r)
        bad=copy.deepcopy(self.spec)
        bad["cases"][0]["reference_facts"][0]["transcript_evidence"][0]["quote"]="not a transcript quote"
        with self.assertRaises(buyer.BuyerError):
            scribe.build_plan(bad)

    def test_review_evidence_and_packet_are_immutable(self):
        self.prepare()
        doc=self.fill("doctor-a")
        with self.assertRaises(buyer.BuyerError):
            self.submit("doctor-b",doc)
        altered=copy.deepcopy(doc)
        altered["submission"]["cases"][0]["facts"][0]["note_evidence"]=[{"start":0,"end":4,"quote":"FAKE"}]
        with self.assertRaises(buyer.BuyerError):
            self.submit("doctor-a",altered)
        first=self.submit("doctor-a",doc)
        self.assertEqual(first,self.submit("doctor-a",doc))
        doc["submission"]["cases"][0]["facts"][0]["rationale"]="changed"
        with self.assertRaises(buyer.BuyerError):
            self.submit("doctor-a",doc)
        saved=self.dest/"responses.json"
        changed=copy.deepcopy(self.responses)
        changed["responses"][0]["note"]+=" Additional text."
        import json
        saved.write_text(json.dumps(changed))
        with self.assertRaises(buyer.BuyerError):
            scribe.template(self.dest,"doctor-b")


if __name__=="__main__":
    unittest.main()
