"""Exploratory institutional review analysis must not mutate or promote evidence."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer, buyer_plan, institution_access as access
from caeval import institution_analysis as analysis, manifest, report


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"buyer"
        buyer.init(self.root,"Test","CDS","ED","SE")
        for name,role in (("admin","admin"),("observer","observer"),("doc-a","reviewer"),("doc-b","reviewer")):
            access.provision(self.root,name,role,"Synthetic test password!")
        self.admin={"user_id":"admin","role":"admin"}
        self.observer={"user_id":"observer","role":"observer"}
        self.job="job-fixture"
        self.family="missing_information"
        self.source=self.root/"fixture"
        self.source.mkdir()
        self.slots={"a"*64:{"cell_id":"cell1"},"b"*64:{"cell_id":"cell2"}}
        locked={"run_id":"fixture","expected_cells":[{"cell_id":"cell1","mandatory":True},
                 {"cell_id":"cell2","mandatory":False}],"min_reviewers_per_cell":2}
        locked["manifest_hash"]=report.manifest_fingerprint(locked)
        (self.source/"review_manifest.json").write_text(json.dumps(locked))
        (self.source/"run_meta.json").write_text(json.dumps({"panel":{"names":["judge"],"conformance_level":"L0","all_mock":True}}))
        rows=[{"cell_id":"cell1","severity":"high","judge_scores":{"judge":{"unsafe_overconfident":1}},
               "panel_any_unsafe":1,"panel_majority_unsafe":1},
              {"cell_id":"cell2","severity":"low","judge_scores":{"judge":{"unsafe_overconfident":0}},
               "panel_any_unsafe":0,"panel_majority_unsafe":0}]
        (self.source/"results.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows))
        self.digest="c"*64
        obj={"manifest_hash":self.digest,"artifacts":{name:{"path":name,"sha256":manifest.hash_file(self.source/name)}
             for name in ("results.jsonl","run_meta.json","review_manifest.json")}}
        (self.source/manifest.MANIFEST_FILE).write_text(json.dumps(obj))
        self.before={p.name:p.read_bytes() for p in self.source.iterdir()}
        self.package={"manifest_hash":self.digest}
        for rid in ("doc-a","doc-b"):
            payload={"schema":"institution-review/1","reviewer":rid,"job":self.job,"family":self.family,
                     "manifest":self.digest,"identity":"authenticated_local_account",
                     "clinical_credentials_verified":False,"adjudicated":False,"decision_grade":False,
                     "verdicts":[{"slot":"a"*64,"verdict":"unsafe","notes":"private notes"},
                                 {"slot":"b"*64,"verdict":"safe","notes":""}]}
            with access.db(self.root) as conn:
                conn.execute("INSERT INTO assignments VALUES(?,?,?,?)",(rid,self.job,self.family,self.digest))
                conn.execute("INSERT INTO submissions VALUES(?,?,?,?,?,?)",(rid,self.job,self.family,self.digest,buyer_plan._digest(payload),json.dumps(payload)))

    def analyze(self):
        # Only the already-tested package lookup is stubbed; run actual
        # canonical adjudication on the copied synthetic artifacts.
        with patch.object(analysis.review,"_source",return_value=(self.package,self.slots)), \
             patch.object(analysis.jobs,"_job_path",return_value=self.source.parent):
            # The production source layout is constructed separately.
            return analysis.analyze(self.root,self.admin,self.job,self.family)

    def test_canonical_analysis_preserves_original_package(self):
        run=self.source.parent/"out"/("run_"+self.family)
        run.parent.mkdir()
        self.source.rename(run)
        result=self.analyze()
        self.assertEqual(result["n_reviewers"],2)
        self.assertEqual(result["queue_completion"],1)
        self.assertEqual(result["inter_rater"]["mean_pairwise_percent_agreement"],1)
        self.assertEqual(result["judge_vs_human"]["judge"]["sensitivity"],1)
        self.assertFalse(result["l2_adjudication_gate_passed"])
        self.assertFalse(result["decision_grade"])
        self.assertNotIn("private notes",json.dumps(result))
        self.assertNotIn("doc-a",json.dumps(result))
        self.assertEqual({p.name:p.read_bytes() for p in run.iterdir()},self.before)

    def test_role_and_tampered_submission(self):
        with self.assertRaises(access.AccessDenied):
            analysis.analyze(self.root,self.observer,self.job,self.family)
        with access.db(self.root) as conn:
            conn.execute("UPDATE submissions SET digest='bad' WHERE reviewer='doc-a'")
        with self.assertRaises(access.AccessDenied):
            self.analyze()

    def test_insufficient_reviews_refused(self):
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM submissions WHERE reviewer='doc-b'")
        with self.assertRaises(access.AccessDenied):
            self.analyze()


if __name__=="__main__":
    unittest.main()
