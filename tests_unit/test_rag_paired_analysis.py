"""Paired research analysis regression and prospective lifecycle tests."""
import copy
import json
import math
import unittest
from caeval import (rag_grounding as g, rag_paired_analysis as a,
                    institution_rag_grounding as studies, institution_rag_comparison as comparison,
                    institution_access as access, diagnostic_imaging as d)
from tests_unit.test_rag_grounding import spec, traces, ratings
from tests_unit import test_unified_assessment as fixtures


def params(**changes):
    return {"baseline":"a","candidate":"b","primary_endpoint":"reviewed_grounded_answer",
            "alpha_bp":500,"margin_bp":0,"max_unknown_bp":1000,"min_clusters":10,
            "independent_clusters_attested":True,**changes}


def evidence(n=1,modify=None,disagreement=False):
    s=spec()
    q=s["questions"][0]
    s["questions"]=[{**q,"id":"q"+str(i),"cluster_id":"c"+str(i)} for i in range(n)]
    p=g.freeze(s,nonce="a"*64,confirmed=True)
    docs=[traces(p,cid) for cid in ("a","b")]
    if modify:
        modify(p,docs)
    packet=g.review_packet(p,docs,"doctor-a")
    r=ratings(packet)
    r2=ratings(packet,"unsupported") if disagreement else copy.deepcopy(r)
    return g.package(p,docs,{"doctor-a":r,"doctor-b":r2})


def endpoint(result,name):
    return next(r for r in result["endpoints"] if r["endpoint"]==name)


class PairedAnalysisTests(unittest.TestCase):
    def test_equal_cluster_weighting_and_unknown_bounds(self):
        qs=[{"id":"q1","cluster_id":"big"},{"id":"q2","cluster_id":"big"},
            {"id":"q3","cluster_id":"small"}]
        def values(xs):
            return {q["id"]:{"reference_hit":x} for q,x in zip(qs,xs)}
        result=a._endpoint(qs,values([0,0,1]),values([1,1,0]),"reference_hit",params())
        self.assertEqual(result["complete_pair_cluster_mean_difference"],0)
        self.assertEqual(result["observed_difference_bounds"],[0,0])
        result=a._endpoint(qs,values([0,0,1]),values([1,None,None]),"reference_hit",params())
        self.assertEqual(result["observed_difference_bounds"],[-0.25,0.5])
        self.assertEqual(result["unknown_pair_cluster_fraction"],0.75)
        self.assertFalse(result["unknown_pair_limit_met"])
        self.assertEqual(result["n_clusters_with_known_pairs"],1)

    def test_zero_variance_not_zero_uncertainty_and_multiplicity(self):
        e=evidence(12)
        p=a.protocol(e["plan"],params())
        result=a.analyse(e,p)
        expected=math.sqrt(2*math.log(160)/12)
        for row in result["endpoints"]:
            self.assertEqual(row["observed_difference_bounds"],[0,0])
            self.assertAlmostEqual(row["hoeffding_radius"],expected)
            self.assertLess(row["simultaneous_confidence_interval"][0],0)
            self.assertGreater(row["simultaneous_confidence_interval"][1],0)
        self.assertEqual(result["primary_research_signal"],"inconclusive")
        self.assertTrue(a.verify(e,result,result["digest"])["expected_digest_matched"])

    def test_too_few_clusters_or_no_independence_withholds_inference(self):
        for n,independent in ((1,True),(12,False)):
            e=evidence(n)
            p=a.protocol(e["plan"],params(independent_clusters_attested=independent))
            r=a.analyse(e,p)
            self.assertTrue(all(x["simultaneous_confidence_interval"] is None for x in r["endpoints"]))
            self.assertEqual(r["primary_research_signal"],"withheld_inference")

    def test_missing_and_review_disagreement_not_success(self):
        def modify(plan,docs):
            row=next(r for r in docs[1]["traces"] if r["status"]=="answered")
            row.update(status="unavailable",answer="",retrieved=[],claims=[])
        e=evidence(modify=modify,disagreement=True)
        r=a.analyse(e,a.protocol(e["plan"],params()))
        grounded=endpoint(r,"reviewed_grounded_answer")
        self.assertEqual(grounded["observed_difference_bounds"],[-1,1])
        self.assertIsNone(grounded["complete_pair_cluster_mean_difference"])
        self.assertEqual(r["primary_research_signal"],"withheld_unknown_pair_limit")
        self.assertEqual(endpoint(r,"paired_availability")["observed_difference_bounds"],[-1,-1])

    def test_deterministic_citation_defect_overrides_review(self):
        def modify(plan,docs):
            row=next(r for r in docs[1]["traces"] if r["status"]=="answered")
            row["claims"][0]["citations"].append("withdrawn")
        e=evidence(modify=modify)
        r=a.analyse(e,a.protocol(e["plan"],params()))
        self.assertEqual(endpoint(r,"reviewed_grounded_answer")["observed_difference_bounds"],[-1,-1])
        self.assertEqual(endpoint(r,"reference_hit")["observed_difference_bounds"],[0,0])
        bad=copy.deepcopy(r)
        bad["primary_research_signal"]="lower_bound_strictly_above_margin"
        bad["digest"]=d._digest({k:v for k,v in bad.items() if k!="digest"})
        with self.assertRaises(ValueError):
            a.verify(e,bad)

    def test_protocol_refuses_invalid_pair_and_numeric_parameters(self):
        e=evidence()
        for change in ({"candidate":"a"},{"candidate":"unknown"},{"alpha_bp":0},
                       {"min_clusters":1},{"max_unknown_bp":True},{"primary_endpoint":"clinical_safety"}):
            with self.assertRaises(ValueError):
                a.protocol(e["plan"],params(**change))


class InstitutionalComparisonTests(unittest.TestCase):
    setUp=fixtures.UnifiedAssessmentTests.setUp
    request=fixtures.UnifiedAssessmentTests.request
    post=fixtures.UnifiedAssessmentTests.post

    def prepare(self):
        admin=self.actors["admin"]
        s=spec()
        p=studies.preview(self.root,admin,s)
        ident=studies.freeze(self.root,admin,s,p["approval"],confirmed=True)
        with access.db(self.root) as conn:
            plan,_,_,_=studies._get(conn,ident)
        return ident,plan

    def test_prospective_browser_and_sealed_replay(self):
        ident,plan=self.prepare()
        admin=self.actors["admin"]
        operator=self.actors["operator"]
        p=comparison.preview(self.root,admin,ident,params())
        form={"id":ident,**{k:str(v) for k,v in params().items() if k!="independent_clusters_attested"},"independent":"yes"}
        self.assertEqual(self.post("admin","/rag-comparison-preview",form)["status"],"200 OK")
        self.assertEqual(self.post("operator","/rag-comparison-preview",form)["status"],"403 Forbidden")
        f={"id":ident,"spec":json.dumps(params()),"approval":p["digest"],"confirm":"yes"}
        self.assertEqual(self.request("admin","/rag-comparison-freeze","POST",f)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/rag-comparison-freeze",{**f,"approval":"bad"})["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/rag-comparison-freeze",f)["status"],"303 See Other")
        self.assertEqual(self.post("admin","/rag-comparison-freeze",f)["status"],"403 Forbidden")
        for cid in ("a","b"):
            studies.candidate_download(self.root,operator,ident,cid,"exchange")
            studies.submit(self.root,operator,ident,traces(plan,cid),confirmed=True)
        for uid in ("doctor-a","doctor-b"):
            packet=studies.review_download(self.root,self.actors[uid],ident,"packet")
            self.assertNotIn("analysis_plan",packet)
            studies.review(self.root,self.actors[uid],ident,ratings(packet),confirmed=True)
        p=studies.seal_preview(self.root,admin,ident)
        studies.seal(self.root,admin,ident,p["approval"],confirmed=True)
        e=studies.export(self.root,admin,ident,p["approval"],confirmed=True)
        result=comparison.result(self.root,admin,ident)
        self.assertEqual(self.request("operator","/rag-comparison?id="+ident)["status"],"403 Forbidden")
        self.assertEqual(self.request("admin","/rag-comparison?id="+ident)["status"],"200 OK")
        f={"id":ident,"approval":result["digest"],"confirm":"yes"}
        out=self.post("admin","/rag-comparison-export",f)
        self.assertEqual(out["status"],"200 OK")
        self.assertTrue(a.verify(e,json.loads(out["body"]),result["digest"])["expected_digest_matched"])
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM rag_paired_analysis_plans")
        with self.assertRaises(access.AccessDenied):
            studies.detail(self.root,admin,ident)

    def test_after_candidate_export_protocol_refused(self):
        ident,_=self.prepare()
        admin=self.actors["admin"]
        p=comparison.preview(self.root,admin,ident,params())
        studies.candidate_download(self.root,self.actors["operator"],ident,"a","template")
        with self.assertRaises(access.AccessDenied):
            comparison.freeze(self.root,admin,ident,params(),p["digest"],confirmed=True)


if __name__=="__main__":
    unittest.main()
