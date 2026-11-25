import unittest
from itertools import product
from caeval import matched_procurement as m


def entry(name,values,clusters=None):
    return {"vendor_id":name,"product_id":name,"cells":[
        {"cell_id":str(i),"item_id":str(clusters[i] if clusters else i),"metric":v}
        for i,v in enumerate(values)]}


def contrast(a,b,clusters=None):
    return m.contrast(entry("a",a,clusters),entry("b",b,clusters),
                      [str(i) for i in range(len(a))],"metric")


class MatchedTests(unittest.TestCase):
    def test_exact_matches_brute_force(self):
        for diffs in ([1,1,1],[-2,1,3],[0,2,-2,1],[3,-4,5,1,1],[]):
            vals=[sum(s*x for s,x in zip(signs,diffs)) for signs in product((-1,1),repeat=len(diffs))]
            self.assertEqual(m.exact_sign_flip(diffs),sum(abs(v)>=abs(sum(diffs)) for v in vals)/len(vals))

    def test_no_pseudoreplication(self):
        row=contrast([1]*20,[0]*20,[0]*10+[1]*10)
        self.assertEqual(row["n_case_clusters"],2)
        self.assertEqual(row["p_value_unadjusted"],.5)
        self.assertEqual(row["all_expected_difference_bounds"],[1,1])

    def test_missing_bounds_and_no_p(self):
        row=contrast([1,None,0,None],[None,0,1,None])
        self.assertEqual(row["n_paired"],1)
        self.assertEqual(row["all_expected_difference_bounds"],[-.5,.5])
        self.assertIsNone(row["p_value_unadjusted"])
        self.assertEqual(row["missing_a_only"],1)
        self.assertEqual(row["missing_b_only"],1)
        self.assertEqual(row["missing_both"],1)

    def test_duplicate_or_cluster_mismatch(self):
        a,b=entry("a",[1,0]),entry("b",[0,1])
        b["cells"][0]["item_id"]="other"
        with self.assertRaises(ValueError):
            m.contrast(a,b,["0","1"],"metric")
        b["cells"][0]["item_id"]="0"
        b["cells"][1]["cell_id"]="0"
        with self.assertRaises(ValueError):
            m.contrast(a,b,["0","1"],"metric")

    def test_no_inference_for_one_case(self):
        row=contrast([1,1],[0,0],[0,0])
        self.assertIsNone(row["p_value_unadjusted"])

    def test_full_planned_multiplicity(self):
        entries=[entry("a",[1]*8),entry("b",[0]*8),entry("c",[None]*8)]
        hazards=[{"hazard_id":"h","tests":["ALL"],"acceptance_criterion":{"metric":"metric"}}]
        result=m.family(entries,hazards,[str(i) for i in range(8)],authorized=True)
        self.assertEqual(len(result["contrasts"]),3)
        first=result["contrasts"][0]
        self.assertEqual(first["p_value_unadjusted"],2/256)
        self.assertEqual(first["p_value_holm_within_family"],6/256)
        self.assertTrue(all(r["p_value_holm_within_family"] is None for r in result["contrasts"][1:]))
        self.assertFalse(m.family(entries,hazards,[],authorized=False)["contrasts"])

    def test_invalid_binary_fails_entire_family(self):
        a,b=entry("a",[1,2]),entry("b",[0,1])
        hazards=[{"hazard_id":"h","acceptance_criterion":{"metric":"metric"}}]
        self.assertEqual(m.family([a,b],hazards,["0","1"],authorized=True)["status"],"unavailable")


class ReverificationTests(unittest.TestCase):
    def test_stored_verified_flag_is_not_enough(self):
        import copy
        import tempfile
        from unittest.mock import patch
        from caeval import procurement_workflow as pw
        family="family"
        rec={"cells":[{"cell_id":"case::original","item_id":"case","metric":1}],
             "environment":{"package_digest":"digest"},
             "package":{"workspace":"/fixture"},"ingestion":"verified_package"}
        state={"vendors":[{"vendor_id":"a"}],"results":{"a":{family:rec}},
               "conditions":{"risk_activation":{"cell_inventory":{family:["case::original"]}}}}
        state["conditions_hash"]=pw.conditions_hash(state["conditions"])
        with tempfile.TemporaryDirectory() as tmp:
            pw.save(tmp,state)
            with patch.object(pw,"ingest_package",return_value=copy.deepcopy(state)):
                self.assertTrue(pw._reverify_matched_entries(tmp,state,family))
            changed=copy.deepcopy(state)
            changed["results"]["a"][family]["cells"][0]["metric"]=0
            with patch.object(pw,"ingest_package",return_value=changed):
                self.assertFalse(pw._reverify_matched_entries(tmp,state,family))
            with patch.object(pw,"ingest_package",side_effect=OSError("missing package")):
                self.assertFalse(pw._reverify_matched_entries(tmp,state,family))
            self.assertEqual(pw.load(tmp),state)

    def test_legacy_helper_no_independent_cell_inference(self):
        from caeval import procurement
        a=entry("a",[1]*20,[0]*10+[1]*10)
        b=entry("b",[0]*20,[0]*10+[1]*10)
        self.assertIsNone(procurement.pairwise_difference(a,b,"metric")["p_value"])
