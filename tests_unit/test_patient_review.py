"""Run the actual patient CLI, then exercise the independent clinician workflow."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from caeval import patient_review as review, project, manifest, buyer_agent as buyer
from caeval.util import repo_root


class PatientReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root=Path(cls.temp.name)
        config=project.template("Synthetic patient review")
        config["target"]={"name":"Private fixture candidate","version":"1","vendor":"Fixture"}
        config["target_profile"]["types"]=["patient_triage_chatbot"]
        config["intake"]={q:"Synthetic fixture" for q in project.MANDATORY_INTAKE}
        config["governance"]={"influences_diagnosis_or_treatment":"yes","clinician_reviews_every_response":"no",
            "regulated_medical_device":"unsure","deployment_jurisdictions":"SE"}
        config["subject"]={"kind":"mock","modality":"conversation","arm":"mock_repaired"}
        config["case_pack"]={"source":"builtin:public_smoke"}
        (cls.root/"project.yaml").write_text(json.dumps(config))
        proc=subprocess.run([sys.executable,"-m","caeval.cli","run","--project",str(cls.root)],
                            cwd=repo_root(),capture_output=True,text=True,timeout=180)
        if proc.returncode:
            raise AssertionError(proc.stderr[-4000:])
        cls.source=cls.root/"out"/"run_patient_red_flag"
        cls.approved=json.loads((cls.source/manifest.MANIFEST_FILE).read_text())["manifest_hash"]

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.tmp.cleanup)
        self.dest=Path(self.tmp.name)/"review"
        self.before={p.relative_to(self.source):p.read_bytes() for p in self.source.rglob("*") if p.is_file()}

    def prepare(self):
        return review.prepare(self.source,self.approved,self.dest,["doctor-a","doctor-b"],"coordinator",confirmed=True)

    def fill(self,rid):
        doc=review.template(self.source,self.dest,rid)
        doc["submission"]["independent_review_confirmed"]=True
        for row in doc["submission"]["answers"]:
            row.update({k:"no" for k in review.FIELDS})
            row.update(final_disposition="routine",rationale="Synthetic fixture rating")
        return doc

    def submit(self,rid,doc):
        return review.submit(self.source,self.dest,rid,doc,"coordinator",confirmed=True)

    def test_all_episodes_blinded_and_indeterminate_preserved(self):
        b=self.prepare()
        self.assertTrue(b["synthetic"])
        a=self.fill("doctor-a")
        self.assertEqual(len(a["request"]["items"]),b["n_episodes"])
        self.assertGreater(b["n_episodes"],0)
        for item in a["request"]["items"]:
            self.assertEqual(set(item),{"slot","transcript","disclosed_facts_by_turn"})
        self.assertNotIn("doctor-b",json.dumps(a))
        self.assertNotIn("Private fixture candidate",json.dumps(a))
        self.submit("doctor-a",a)
        with self.assertRaises(buyer.BuyerError):
            review.analyze(self.source,self.dest)
        d=self.fill("doctor-b")
        d["submission"]["answers"][0]["under_triage"]="cannot_judge"
        d["submission"]["answers"][1]["under_triage"]="yes"
        self.submit("doctor-b",d)
        out=review.analyze(self.source,self.dest)
        axis=out["axes"]["under_triage"]
        self.assertEqual(axis["indeterminate"],1)
        self.assertEqual(axis["disagreement"],1)
        self.assertEqual(axis["unanimous_determinate"],b["n_episodes"]-2)
        self.assertEqual(axis["pairwise_observed"],b["n_episodes"]-1)
        self.assertFalse(out["decision_grade"])
        self.assertFalse(out["claim_upgrade"])
        self.assertEqual(out,review.analyze(self.source,self.dest))
        self.assertEqual(self.before,{p.relative_to(self.source):p.read_bytes() for p in self.source.rglob("*") if p.is_file()})

    def test_packet_swap_missing_labels_and_overwrite_rejected(self):
        self.prepare()
        doc=self.fill("doctor-a")
        with self.assertRaises(buyer.BuyerError):
            self.submit("doctor-b",doc)
        doc["submission"]["answers"][0]["under_triage"]=""
        with self.assertRaises(buyer.BuyerError):
            self.submit("doctor-a",doc)
        doc["submission"]["answers"][0]["under_triage"]="no"
        first=self.submit("doctor-a",doc)
        self.assertEqual(first,self.submit("doctor-a",doc))
        doc["submission"]["answers"][0]["under_triage"]="yes"
        with self.assertRaises(buyer.BuyerError):
            self.submit("doctor-a",doc)

    def test_exact_manifest_and_explicit_roster_required(self):
        for approved,roster,confirmed in (("0"*64,["doctor-a","doctor-b"],True),
                                         (self.approved,["doctor-a","doctor-a"],True),
                                         (self.approved,["doctor-a","doctor-b"],False)):
            with self.assertRaises(buyer.BuyerError):
                review.prepare(self.source,approved,self.dest,roster,"coordinator",confirmed=confirmed)
        self.assertFalse(self.dest.exists())


if __name__=="__main__":
    unittest.main()
