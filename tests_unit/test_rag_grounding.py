"""Synthetic-only grounding research protocol and browser acceptance tests."""
import copy
import json
import unittest
from caeval import (rag_grounding as g, institution_rag_grounding as service,
                    diagnostic_imaging as d, institution_access as access,
                    unified_assessment as unified, unified_discovery as discovery,
                    institution_committee as committee)
from tests_unit import test_unified_assessment as fixtures


def spec():
    return {"study_id":"grounding-study","corpus_id":"fictional-corpus","corpus_version":"v1",
            "documents":[
                {"id":"reference","version":"v2","title":"Fictional reference","text":"In the invented scenario the token is blue.","state":"current"},
                {"id":"distractor","version":"v1","title":"Fictional near miss","text":"A different invented scenario uses red.","state":"current"},
                {"id":"withdrawn","version":"v1","title":"Withdrawn fictional reference","text":"The token was previously green.","state":"withdrawn"}],
            "questions":[{"id":"q1","cluster_id":"c1","query":"Which token is specified?",
                          "reference_ids":["reference"],"distractor_ids":["distractor","withdrawn"]}],
            "candidates":[{"id":"a","version":"v1"},{"id":"b","version":"v1"}],
            "reviewers":["doctor-a","doctor-b"],
            "attestations":{"authorized_corpus":True,"independently_reviewed_references":True,
                            "no_patient_identifiers":True,"prospective":True,"isolated_case_execution":True}}


def traces(plan,cid):
    exchange=g.exchange(plan,cid)
    result=g.template(plan,cid)
    by={r["slot"]:r for r in exchange["cases"]}
    for row in result["traces"]:
        docs={doc["id"]:doc for doc in by[row["slot"]]["corpus"]}
        if "reference" in docs:
            row.update(status="answered",answer="The token is blue.",retrieved=[
                {"id":"reference","digest":docs["reference"]["digest"]}],
                claims=[{"start":0,"end":18,"citations":["reference"]}])
        else:
            row.update(status="abstained",answer="Insufficient evidence.",retrieved=[
                {"id":"withdrawn","digest":docs["withdrawn"]["digest"]}],claims=[])
    return result


def ratings(packet,label="supported"):
    result=g.review_template(packet)
    for row in result["units"]:
        row["claim_coverage"]="complete"
        for claim in row["claims"]:
            claim["label"]=label
    return result


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.plan=g.freeze(spec(),nonce="a"*64,confirmed=True)
        self.docs=[traces(self.plan,cid) for cid in ("a","b")]

    def test_paired_controls_and_blind_packets(self):
        ex=g.exchange(self.plan,"a")
        self.assertEqual(len(ex["cases"]),2)
        self.assertEqual({len(r["corpus"]) for r in ex["cases"]},{2,3})
        self.assertNotIn("reference_ids",json.dumps(ex))
        self.assertNotIn("doctor-a",json.dumps(ex))
        self.assertFalse({r["slot"] for r in ex["cases"]}&{r["slot"] for r in g.exchange(self.plan,"b")["cases"]})
        p=g.review_packet(self.plan,self.docs,"doctor-a")
        self.assertEqual(p,g.review_packet(self.plan,list(reversed(self.docs)),"doctor-b"))
        self.assertNotIn("candidate",json.dumps(p))
        self.assertNotIn("reference_ids",json.dumps(p))
        self.assertEqual(len(p["units"]),4)

    def test_consensus_missingness_and_replay(self):
        # One supported case unavailable; one other supported claim disputed.
        row=next(r for r in self.docs[1]["traces"] if r["status"]=="answered")
        row.update(status="unavailable",answer="",retrieved=[],claims=[])
        packet=g.review_packet(self.plan,self.docs,"doctor-a")
        a=ratings(packet)
        b=ratings(packet,"unsupported")
        ev=g.package(self.plan,self.docs,{"doctor-a":a,"doctor-b":b})
        first,second=ev["results"]
        pos,neg=first["conditions"]
        self.assertEqual(pos["reported_reference_hit_rate"],1)
        self.assertEqual(pos["counts"]["n_review_unresolved"],1)
        self.assertIsNone(pos["reviewed_declared_claim_support_rate"])
        self.assertEqual(pos["declared_claim_support_bounds"],[0,1])
        self.assertEqual(neg["declared_abstention_rate"],1)
        self.assertEqual(neg["counts"]["n_withdrawn_retrieved"],1)
        self.assertIsNone(second["conditions"][0]["reported_reference_hit_rate"])
        self.assertEqual(second["conditions"][0]["reference_hit_bounds"],[0,1])
        self.assertTrue(g.verify(ev,ev["digest"])["expected_digest_matched"])
        bad=copy.deepcopy(ev)
        bad["results"][0]["conditions"][0]["counts"]["n_review_supported"]=100
        bad["digest"]=d._digest({k:v for k,v in bad.items() if k!="digest"})
        with self.assertRaises(ValueError):
            g.verify(bad)

    def test_exact_retrieved_documents_and_claim_inventory(self):
        bad=copy.deepcopy(self.docs[0])
        row=next(r for r in bad["traces"] if r["status"]=="answered")
        row["retrieved"][0]["digest"]="0"*64
        with self.assertRaises(ValueError):
            g.locked_traces(self.plan,[bad])
        bad=copy.deepcopy(self.docs[0])
        row=next(r for r in bad["traces"] if r["status"]=="answered")
        row["claims"][0]["end"]=1000
        with self.assertRaises(ValueError):
            g.locked_traces(self.plan,[bad])
        with self.assertRaises(ValueError):
            g.review_packet(self.plan,self.docs[:1],"doctor-a")
        with self.assertRaises(ValueError):
            g.review_packet(self.plan,self.docs,"doctor-c")

    def test_no_support_for_unavailable_citation(self):
        row=next(r for r in self.docs[0]["traces"] if r["status"]=="answered")
        row["claims"][0]["citations"]=["nonexistent"]
        p=g.review_packet(self.plan,self.docs,"doctor-a")
        with self.assertRaises(ValueError):
            g.package(self.plan,self.docs,{"doctor-a":ratings(p),"doctor-b":ratings(p)})
        r=ratings(p,"unsupported")
        ev=g.package(self.plan,self.docs,{"doctor-a":r,"doctor-b":r})
        c=ev["results"][0]["conditions"][0]["counts"]
        self.assertEqual(c["n_outside_corpus_citation_claims"],1)
        self.assertEqual(c["n_nonretrieved_citation_claims"],1)
        self.assertEqual(c["n_review_unsupported"],1)

    def test_wrong_reference_and_missing_review_rejected(self):
        bad=spec()
        bad["questions"][0]["reference_ids"]=["withdrawn"]
        bad["questions"][0]["distractor_ids"]=["distractor"]
        with self.assertRaises(ValueError):
            g.freeze(bad,confirmed=True)
        p=g.review_packet(self.plan,self.docs,"doctor-a")
        with self.assertRaises(ValueError):
            g.package(self.plan,self.docs,{"doctor-a":ratings(p)})
        r=ratings(p)
        r["units"].pop()
        with self.assertRaises(ValueError):
            g.package(self.plan,self.docs,{"doctor-a":r,"doctor-b":ratings(p)})


class BrowserGroundingTests(unittest.TestCase):
    setUp=fixtures.UnifiedAssessmentTests.setUp
    request=fixtures.UnifiedAssessmentTests.request
    post=fixtures.UnifiedAssessmentTests.post
    plan=fixtures.UnifiedAssessmentTests.plan
    freeze=fixtures.UnifiedAssessmentTests.freeze

    def test_browser_lifecycle_and_programme_link(self):
        s=spec()
        admin=self.actors["admin"]
        operator=self.actors["operator"]
        observer=self.actors["observer"]
        fields={"spec":json.dumps(s)}
        self.assertEqual(self.post("operator","/rag-grounding-preview",fields)["status"],"403 Forbidden")
        self.assertEqual(self.request("admin","/rag-grounding-preview","POST",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/rag-grounding-preview",fields)["status"],"200 OK")
        p=service.preview(self.root,admin,s)
        fields.update(approval=p["approval"],confirm="yes")
        self.assertEqual(self.post("admin","/rag-grounding-freeze",{**fields,"approval":"bad"})["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/rag-grounding-freeze",fields)["status"],"303 See Other")
        ident=s["study_id"]
        # Private plan only accessed in test; production candidate sees exchange.
        with access.db(self.root) as conn:
            plan,_,_,_=service._get(conn,ident)
        review_url="/rag-grounding-review?id="+ident
        self.assertEqual(self.request("doctor-a",review_url)["status"],"403 Forbidden")
        for cid in ("a","b"):
            doc=traces(plan,cid)
            f={"id":ident,"trace":json.dumps(doc),"confirm":"yes"}
            self.assertEqual(self.post("operator","/rag-grounding-submit",f)["status"],"303 See Other")
        self.assertEqual(self.request("doctor-c",review_url)["status"],"403 Forbidden")
        self.assertEqual(self.request("operator",review_url)["status"],"403 Forbidden")
        self.assertEqual(self.request("doctor-a",review_url)["status"],"200 OK")
        for uid in ("doctor-a","doctor-b"):
            packet=service.review_download(self.root,self.actors[uid],ident,"packet")
            doc=ratings(packet)
            f={"id":ident,"review":json.dumps(doc),"confirm":"yes"}
            self.assertEqual(self.post(uid,"/rag-grounding-review-lock",f)["status"],"303 See Other")
            self.assertEqual(self.post(uid,"/rag-grounding-review-lock",f)["status"],"403 Forbidden")
        p=service.seal_preview(self.root,admin,ident)
        f={"id":ident,"approval":p["approval"],"confirm":"yes"}
        self.assertEqual(self.post("operator","/rag-grounding-seal",f)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/rag-grounding-seal",f)["status"],"303 See Other")
        self.assertNotIn("results",service.detail(self.root,operator,ident))
        self.assertEqual(self.post("operator","/rag-grounding-evidence",f)["status"],"403 Forbidden")
        out=self.post("admin","/rag-grounding-evidence",f)
        self.assertEqual(out["status"],"200 OK")
        self.assertTrue(g.verify(json.loads(out["body"]),p["approval"])["expected_digest_matched"])
        pid=self.plan(profiles=["clinician_rag"])
        programme=self.freeze({"programme_id":"grounding-programme","plans":[pid],"requirements":[
            {"id":"grounding-a","kind":"rag_grounding","plan_id":pid,"family":None}]})
        matches=discovery.discover(self.root,admin,programme,"grounding-a")["matches"]
        self.assertEqual([x["binding"]["source"]["source_id"] for x in matches],[ident])
        preview=unified.link_preview(self.root,admin,programme,"grounding-a",ident)
        unified.lock(self.root,admin,programme,"grounding-a",ident,preview["approval_digest"],confirmed=True)
        status=unified.status(self.root,observer,programme)
        self.assertTrue(status["all_planned_sources_available"])
        self.assertFalse(status["canonical_requirements_satisfied"])
        self.assertEqual(status["requirements"][0]["locked_source"]["scope"],"vendor_id_and_version_only")
        self.assertNotIn("documents",json.dumps(committee.overview(self.root,observer)["lifecycle_inventory"]["rag_grounding_studies"]))
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM rag_grounding_reviews")
        with self.assertRaises(access.AccessDenied):
            service.detail(self.root,admin,ident)


if __name__=="__main__":
    unittest.main()
