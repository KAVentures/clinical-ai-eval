import unittest
import copy
from caeval import review_calibration as c


def fixture():
    ids=["a::x","a::y","b::x","c::x","d::x","e::x"]
    queue={"expected_cells":[{"cell_id":x,"severity":"high","mandatory":i==0}
            for i,x in enumerate(ids)],"n_expected":len(ids),"min_reviewers_per_cell":2}
    results=[{"cell_id":x,"item_id":x.split("::")[0],"severity":"high","kind":"variant",
              "evaluation_complete":True,"judge_scores":{"judge":{"unsafe_overconfident":int(i==0)}},
              "panel_any_unsafe":int(i==0),"panel_majority_unsafe":int(i==0)}
             for i,x in enumerate(ids)]
    reviewers=[dict.fromkeys(ids,"safe") for _ in range(3)]
    for r in reviewers:r[ids[0]]="unsafe"
    reviewers[0][ids[2]]="cannot_judge"
    reviewers[1][ids[2]]="cannot_judge"
    reviewers[0][ids[3]]="unsafe"
    reviewers[1][ids[3]]="cannot_judge"
    results[4]["evaluation_complete"]=False
    results[1]["judge_scores"]={}
    validity={x:{k:"yes" for k in c.VALIDITY_FIELDS} for x in ids}
    validity[ids[5]][c.VALIDITY_FIELDS[0]]="no"
    return queue,results,["judge"],reviewers,validity


class CalibrationTests(unittest.TestCase):
    def test_denominator_and_bounds(self):
        d=c.analyze(*fixture(),synthetic=True)
        self.assertEqual(d["exclusive_coverage"],{
            "invalid_or_indeterminate_validity":1,"insufficient_determinate_reviewers":1,
            "no_independent_majority":1,"automated_evaluation_incomplete":1,"reference_eligible":2})
        judge=d["comparisons"][0]
        self.assertEqual(judge["n_paired"],1)
        self.assertEqual(judge["n_paired_case_clusters"],1)
        self.assertEqual(judge["sensitivity"],1)
        self.assertIsNone(judge["specificity"])
        self.assertEqual(judge["specificity_missing_bounds"],[0,1])
        self.assertEqual(d["comparisons"][1]["specificity"],1)
        self.assertFalse(d["decision_grade"])

    def test_reject_duplicate_results_and_wrong_inventory(self):
        q,r,j,h,v=fixture()
        with self.assertRaises(ValueError):c.analyze(q,r+[r[0]],j,h,v,synthetic=False)
        with self.assertRaises(ValueError):c.analyze(q,r[1:],j,h,v,synthetic=False)
        h[0].pop("a::x")
        with self.assertRaises(ValueError):c.analyze(q,r,j,h,v,synthetic=False)

    def test_wrong_cluster_and_nonbinary_label(self):
        q,r,j,h,v=fixture()
        r[0]["item_id"]="wrong"
        with self.assertRaises(ValueError):c.analyze(q,r,j,h,v,synthetic=False)
        r[0]["item_id"]="a"
        r[0]["judge_scores"]["judge"]["unsafe_overconfident"]=2
        with self.assertRaises(ValueError):c.analyze(q,r,j,h,v,synthetic=False)

    def test_empty_eligible_reference_never_implies_good_performance(self):
        q,r,j,h,v=fixture()
        for row in v.values():row[c.VALIDITY_FIELDS[0]]="cannot_judge"
        d=c.analyze(q,r,j,h,v,synthetic=False)
        self.assertEqual(d["exclusive_coverage"]["reference_eligible"],0)
        for x in d["comparisons"]:
            self.assertIsNone(x["sensitivity"])
            self.assertIsNone(x["specificity"])
            self.assertIsNone(x["sensitivity_missing_bounds"])
