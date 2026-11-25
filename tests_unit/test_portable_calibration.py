import copy
import json
import tempfile
import unittest
from pathlib import Path
from caeval import portable_calibration as p,review_calibration as c


def fixture():
    ids=["secret-case::a","secret-case::b","other-case::a"]
    lock={"expected_cells":[{"cell_id":x,"severity":"high","mandatory":False} for x in ids],
          "n_expected":3,"min_reviewers_per_cell":2}
    judges=["private-judge-name"]
    reviews=[dict(zip(ids,["unsafe","safe","safe"])),dict(zip(ids,["unsafe","safe","safe"]))]
    validity={i:dict.fromkeys(c.VALIDITY_FIELDS,"yes") for i in ids}
    rows=[{"cell_id":i,"item_id":i.split("::")[0],"kind":"variant","severity":"high",
           "evaluation_complete":n!=2,"judge_scores":{judges[0]:{"unsafe_overconfident":int(n==0)}},
           "panel_any_unsafe":int(n==0),"panel_majority_unsafe":int(n==0),
           "response_text":"private-response","input_text":"private-prompt"} for n,i in enumerate(ids)]
    refs={k:"a"*64 for k in ("cohort","cohort_digest","source_manifest","institutional_report_digest")}
    refs.update(evaluator_and_job_bound=True,candidate_submission_bound=True)
    return lock,rows,judges,reviews,validity,refs


class PortableTests(unittest.TestCase):
    def package(self):
        *args,refs=fixture()
        return p.build(p.project(*args,synthetic=True),refs)

    def test_projection_replay_and_redaction(self):
        pkg=self.package()
        self.assertEqual(pkg,self.package())
        result=p.verify(pkg,pkg["digest"])
        self.assertTrue(result["expected_digest_matched"])
        self.assertFalse(result["source_evidence_independently_verified"])
        raw=p.encode(pkg).decode()
        for x in ("secret-case","other-case","private-judge-name","private-response","private-prompt"):
            self.assertNotIn(x,raw)
        row=result["calibration"]["comparisons"][0]
        self.assertEqual(row["n_reference_eligible"],3)
        self.assertEqual(row["n_paired"],2)
        self.assertEqual(row["n_paired_case_clusters"],1)
        self.assertEqual(row["specificity_missing_bounds"],[.5,1])

    def test_tamper_and_reseal(self):
        pkg=self.package()
        with self.assertRaises(ValueError):p.verify(pkg,"0"*64)
        pkg["calibration"]["comparisons"][0]["tp"]=99
        pkg["digest"]=p._digest({k:v for k,v in pkg.items() if k!="digest"})
        with self.assertRaises(ValueError):p.verify(pkg)

    def test_no_free_text_in_inputs(self):
        for key,value in (("extra","private"),("cluster","private"),("votes",[2,0,True]),
                          ("validity",["private"]*3),("judges",[True])):
            pkg=self.package()
            pkg["inputs"]["rows"][0][key]=value
            with self.assertRaises(ValueError):p.build(pkg["inputs"],pkg["provenance"])

    def test_reject_partial_automated_labels(self):
        pkg=self.package()
        pkg["inputs"]["rows"][2]["judges"]=[1]
        with self.assertRaises(ValueError):p.build(pkg["inputs"],pkg["provenance"])

    def test_reject_enriched_or_partial_cohort(self):
        lock,rows,judges,reviews,validity,_=fixture()
        lock["expected_cells"][0]["mandatory"]=True
        with self.assertRaises(ValueError):p.project(lock,rows,judges,reviews,validity,synthetic=True)
        lock["expected_cells"][0]["mandatory"]=False
        rows.append({**rows[0],"cell_id":"secret-case::extra"})
        with self.assertRaises(ValueError):p.project(lock,rows,judges,reviews,validity,synthetic=True)

    def test_strict_load_and_binding(self):
        pkg=self.package()
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/"cal.json"
            f.write_bytes(p.encode(pkg))
            self.assertEqual(p.load(f),pkg)
            f.write_text('{"schema":1,"schema":2}')
            with self.assertRaises(ValueError):p.load(f)
        pkg["provenance"]["evaluator_and_job_bound"]=False
        with self.assertRaises(ValueError):p.build(pkg["inputs"],pkg["provenance"])
