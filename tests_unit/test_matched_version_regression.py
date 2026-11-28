"""Exact cohort and version lineage, descriptive missingness and institution tests."""
import copy
import json
import unittest
from caeval import (diagnostic_imaging as d, rag_grounding as g,
                    matched_version_regression as m, institution_matched_regression as service,
                    institution_imaging as imaging, institution_access as access)
from tests_unit.test_diagnostic_imaging import spec as image_spec, submission
from tests_unit.test_rag_grounding import spec as rag_spec, traces, ratings
from tests_unit.test_institution_research import StudioTests


def settings(endpoint="resolved_accuracy",**changes):
    return {"vendor_id":"a","endpoint":endpoint,"margin_bp":0,"max_unknown_bp":10000,**changes}


def image_pair():
    s=image_spec()
    a=d.freeze(s,private_nonce="1"*64,confirmed=True)
    s["study_id"]="successor-image"
    s["candidates"][0]["version"]="v2"
    b=d.freeze(s,private_nonce="2"*64,confirmed=True)
    baseline=d.package(a,[submission(a,c,[9000,9000,0,0,7000]) for c in ("a","b")])
    successor=d.package(b,[submission(b,"a",[1000,None,0,9000,7000]),
                           submission(b,"b",[9000,9000,0,0,7000])])
    return baseline,successor


def rag_pair():
    s=rag_spec()
    a=g.freeze(s,nonce="1"*64,confirmed=True)
    s["study_id"]="successor-rag"
    s["candidates"][0]["version"]="v2"
    b=g.freeze(s,nonce="2"*64,confirmed=True)
    def evidence(plan,changed):
        docs=[traces(plan,c) for c in ("a","b")]
        if changed:
            row=next(x for x in docs[0]["traces"] if x["status"]=="answered")
            row.update(retrieved=[],claims=[],answer="No support found.")
        packet=g.review_packet(plan,docs,"doctor-a")
        return g.package(plan,docs,{uid:ratings(packet) for uid in plan["spec"]["reviewers"]})
    return evidence(a,False),evidence(b,True)


def metric(result,name):
    return next(r for r in result["endpoints"] if r["endpoint"]==name)


class MatchedRegressionTests(unittest.TestCase):
    def test_imaging_exact_bounds_and_unresolved_reference_exclusion(self):
        a,b=image_pair()
        p=m.protocol("imaging",a,b["plan"],settings())
        result=m.analyse(a,b,p)
        accuracy=metric(result,"resolved_accuracy")
        self.assertEqual(accuracy["n_cases"],4)
        self.assertEqual(accuracy["n_clusters"],2)
        self.assertEqual(accuracy["observed_difference_bounds"],[-0.75,-0.5])
        self.assertEqual(accuracy["unknown_pair_cluster_fraction"],0.25)
        self.assertEqual(metric(result,"sensitivity")["observed_difference_bounds"],[-1,-0.5])
        self.assertEqual(metric(result,"specificity")["observed_difference_bounds"],[-0.5,-0.5])
        self.assertEqual(metric(result,"score_availability")["n_cases"],5)
        self.assertAlmostEqual(metric(result,"score_availability")["observed_difference_bounds"][0],-1/6)
        self.assertEqual(result["descriptive_research_signal"],"observed_upper_bound_below_margin")
        self.assertFalse(result["inference_performed"])
        self.assertTrue(m.verify(a,b,result,result["digest"])["expected_digest_matched"])

    def test_rag_version_change_and_replay(self):
        a,b=rag_pair()
        p=m.protocol("rag_grounding",a,b["plan"],settings("reviewed_grounded_answer"))
        result=m.analyse(a,b,p)
        self.assertEqual(metric(result,"reference_hit")["observed_difference_bounds"],[-1,-1])
        self.assertEqual(metric(result,"reviewed_grounded_answer")["observed_difference_bounds"],[-1,-1])
        self.assertEqual(metric(result,"declared_abstention")["observed_difference_bounds"],[0,0])
        self.assertTrue(m.verify(a,b,result)["status"]=="REPLAY_VERIFIED")
        bad=copy.deepcopy(result)
        bad["descriptive_research_signal"]="observed_lower_bound_above_margin"
        bad["digest"]=d._digest({k:v for k,v in bad.items() if k!="digest"})
        with self.assertRaises(ValueError):
            m.verify(a,b,bad)

    def test_reject_changed_imaging_cohort_threshold_and_other_roster(self):
        a,b=image_pair()
        for mutate in (
            lambda s:s["cases"][0].update(reference="negative"),
            lambda s:s["cases"][0].update(asset_sha256="f"*64),
            lambda s:s["cases"][0].update(cluster_id="different"),
            lambda s:s["candidates"][0].update(threshold_bp=4000),
            lambda s:s["candidates"][0].update(version="v1"),
            lambda s:s["candidates"][1].update(version="v3"),
        ):
            spec=copy.deepcopy(b["plan"]["spec"])
            mutate(spec)
            p=d.freeze(spec,confirmed=True)
            with self.assertRaises(ValueError):
                m.protocol("imaging",a,p,settings())

    def test_reject_rag_corpus_reference_and_reviewer_drift(self):
        a,b=rag_pair()
        for mutate in (
            lambda s:s["documents"][0].update(text="Changed source"),
            lambda s:s["questions"][0].update(query="Different question"),
            lambda s:s.update(reviewers=["doctor-a","doctor-c"]),
        ):
            spec=copy.deepcopy(b["plan"]["spec"])
            mutate(spec)
            p=g.freeze(spec,confirmed=True)
            with self.assertRaises(ValueError):
                m.protocol("rag_grounding",a,p,settings("reference_hit"))

    def test_unknown_gate_and_exact_margin_equality(self):
        a,b=image_pair()
        p=m.protocol("imaging",a,b["plan"],settings(max_unknown_bp=1000))
        result=m.analyse(a,b,p)
        self.assertEqual(result["descriptive_research_signal"],"withheld_unknown_pair_limit")
        p=m.protocol("imaging",a,b["plan"],settings(margin_bp=-5000))
        result=m.analyse(a,b,p)
        self.assertEqual(result["descriptive_research_signal"],"observed_bounds_overlap_margin")

    def test_incomplete_roster_is_not_valid_analysis(self):
        a,b=image_pair()
        incomplete=d.package(b["plan"],b["submissions"][:1])
        p=m.protocol("imaging",a,b["plan"],settings())
        with self.assertRaises(ValueError):
            m.analyse(a,incomplete,p)
        with self.assertRaises(ValueError):
            m.protocol("imaging",d.package(a["plan"],a["submissions"][:1]),b["plan"],settings())


class InstitutionalRegressionTests(unittest.TestCase):
    setUp=StudioTests.setUp
    request=StudioTests.request
    post=StudioTests.post

    def prepare(self):
        admin=self.actors["admin"]
        operator=self.actors["operator"]
        s=image_spec()
        p=imaging.preview(self.root,admin,s)
        baseline=imaging.freeze(self.root,admin,s,p["approval_digest"],confirmed=True)
        with access.db(self.root) as conn:
            plan,_,_=imaging._get(conn,baseline)
        for cid in ("a","b"):
            imaging.submit(self.root,operator,baseline,submission(plan,cid,[9000,9000,0,0,7000]),confirmed=True)
        p=imaging.seal_preview(self.root,admin,baseline)
        imaging.seal(self.root,admin,baseline,p["approval_digest"],confirmed=True)
        s["study_id"]="successor-image"
        s["candidates"][0]["version"]="v2"
        p=imaging.preview(self.root,admin,s)
        successor=imaging.freeze(self.root,admin,s,p["approval_digest"],confirmed=True)
        return baseline,successor

    def test_browser_registration_and_sealed_replay(self):
        admin=self.actors["admin"]
        operator=self.actors["operator"]
        a,b=self.prepare()
        form={"kind":"imaging","baseline":a,"successor":b,**{k:str(v) for k,v in settings().items()}}
        self.assertEqual(self.post("admin","/matched-regression-preview",form)["status"],"200 OK")
        self.assertEqual(self.post("operator","/matched-regression-preview",form)["status"],"403 Forbidden")
        p=service.preview(self.root,admin,"imaging",a,b,settings())
        f={"kind":"imaging","baseline":a,"successor":b,"spec":json.dumps(settings()),"approval":p["digest"],"confirm":"yes"}
        self.assertEqual(self.request("admin","/matched-regression-freeze","POST",f)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/matched-regression-freeze",{**f,"approval":"bad"})["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/matched-regression-freeze",f)["status"],"303 See Other")
        ident=p["digest"]
        self.assertEqual(service.overview(self.root,operator)[0]["state"],"registered")
        with access.db(self.root) as conn:
            plan,_,_=imaging._get(conn,b)
        imaging.download(self.root,operator,b,"a","exchange")
        for cid in ("a","b"):
            imaging.submit(self.root,operator,b,submission(plan,cid,[1000,1000,9000,9000,7000]),confirmed=True)
        p=imaging.seal_preview(self.root,admin,b)
        imaging.seal(self.root,admin,b,p["approval_digest"],confirmed=True)
        row=service.detail(self.root,admin,ident)
        self.assertEqual(row["coverage"]["state"],"sealed_descriptive_comparison")
        self.assertEqual(self.request("observer","/matched-regression")["status"],"200 OK")
        self.assertEqual(self.request("observer","/matched-regression-detail?id="+ident)["status"],"403 Forbidden")
        out=self.post("admin","/matched-regression-export",{"id":ident,"approval":row["result"]["digest"],"confirm":"yes"})
        self.assertEqual(out["status"],"200 OK")
        with access.db(self.root) as conn:
            old=imaging._get(conn,a)[-1]
            new=imaging._get(conn,b)[-1]
        self.assertTrue(m.verify(old,new,json.loads(out["body"]))["status"]=="REPLAY_VERIFIED")
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM matched_version_regressions")
        with self.assertRaises(access.AccessDenied):
            service.overview(self.root,admin)

    def test_exposure_before_registration_refused(self):
        a,b=self.prepare()
        admin=self.actors["admin"]
        p=service.preview(self.root,admin,"imaging",a,b,settings())
        imaging.download(self.root,self.actors["operator"],b,"a","template")
        with self.assertRaises(access.AccessDenied):
            service.freeze(self.root,admin,"imaging",a,b,settings(),p["digest"],confirmed=True)

    def test_incomplete_sealed_successor_withholds_comparison(self):
        a,b=self.prepare()
        admin=self.actors["admin"]
        p=service.preview(self.root,admin,"imaging",a,b,settings())
        ident=service.freeze(self.root,admin,"imaging",a,b,settings(),p["digest"],confirmed=True)
        p=imaging.seal_preview(self.root,admin,b)
        imaging.seal(self.root,admin,b,p["approval_digest"],confirmed=True,allow_incomplete=True)
        row=service.detail(self.root,admin,ident)
        self.assertIsNone(row["result"])
        self.assertEqual(row["coverage"]["state"],"successor_roster_incomplete")


if __name__=="__main__":
    unittest.main()
