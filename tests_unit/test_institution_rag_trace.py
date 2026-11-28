"""Synthetic RAG rehearsal replay and institutional integration tests."""
import copy
import json
import unittest
from caeval import (institution_rag_trace as trace,institution_rag as rag,
                    diagnostic_imaging as d,unified_assessment as u,
                    unified_discovery as discovery,unified_work as work,
                    institution_access as access,institution_committee as committee)
from tests_unit import test_unified_assessment as fixtures


def complete(plan):
    doc=trace.template(plan)
    for row in doc["responses"]:
        row["answer"]="The supplied context is insufficient. I cannot answer."
    return doc


class TraceReplayTests(unittest.TestCase):
    def plan(self):
        return trace.freeze("a"*64,"vendor",{"name":"Synthetic product","version":"v1"},nonce="b"*64)

    def test_native_inventory_and_exact_replay(self):
        p=self.plan()
        ex=trace.exchange(p)
        self.assertEqual(len(ex["slots"]),4)
        self.assertEqual(len({r["prompt_digest"] for r in ex["slots"]}),4)
        self.assertTrue(all(set(r)=={"slot","prompt","prompt_digest"} for r in ex["slots"]))
        self.assertNotIn("private_nonce",ex)
        self.assertNotIn("definitions",ex)
        doc=complete(p)
        package=trace.package(p,doc)
        self.assertEqual(package,trace.package(p,doc))
        self.assertEqual(package,trace.package(p,{**doc,"responses":list(reversed(doc["responses"]))}))
        self.assertEqual(package["native_run"]["summary"]["n_traces"],4)
        self.assertEqual(package["native_run"]["summary"]["generation"]["correct_abstention"],1.0)
        self.assertEqual(package["mode"],"demonstration")
        self.assertFalse(package["canonical_requirements_satisfied"])
        self.assertFalse(package["decision_grade"])
        self.assertTrue(trace.verify(package,package["digest"])["expected_digest_matched"])

    def test_incomplete_blank_duplicate_and_wrong_version_refused(self):
        p=self.plan()
        doc=complete(p)
        for bad in ({**doc,"responses":doc["responses"][:-1]},
                    {**doc,"responses":[doc["responses"][0]]*4},
                    {**doc,"target":{"name":"Synthetic product","version":"v2"}},
                    {**doc,"plan_digest":"c"*64}):
            with self.assertRaises(ValueError):
                trace.package(p,bad)
        for key,value in (("answer",""),("prompt_digest","0"*64),("answer","x"*(trace.MAX_ANSWER+1))):
            changed=copy.deepcopy(doc)
            changed["responses"][0][key]=value
            with self.assertRaises(ValueError):
                trace.package(p,changed)

    def test_changed_native_trace_even_resealed_refused(self):
        p=self.plan()
        ev=trace.package(p,complete(p))
        bad=copy.deepcopy(ev)
        bad["native_run"]["traces"][0]["deterministic"]["correct_abstention"]=0
        bad["digest"]=d._digest({k:v for k,v in bad.items() if k!="digest"})
        with self.assertRaises(ValueError):
            trace.verify(bad)
        with self.assertRaises(ValueError):
            trace.verify(ev,"0"*64)
        bad=copy.deepcopy(p)
        bad["definitions"]["caeval/rag/execute.py"]="0"*64
        with self.assertRaises(ValueError):
            trace.verify_plan(bad)


class InstitutionalRAGTests(unittest.TestCase):
    setUp=fixtures.UnifiedAssessmentTests.setUp
    request=fixtures.UnifiedAssessmentTests.request
    post=fixtures.UnifiedAssessmentTests.post
    plan=fixtures.UnifiedAssessmentTests.plan
    freeze=fixtures.UnifiedAssessmentTests.freeze

    def prepare(self,pid):
        admin=self.actors["admin"]
        p=rag.preview(self.root,admin,pid)
        return rag.freeze(self.root,admin,pid,p["digest"],confirmed=True)

    def answers(self,ident):
        doc=rag.download(self.root,self.actors["operator"],ident,"template")
        for r in doc["responses"]:
            r["answer"]="Insufficient evidence in the provided synthetic context."
        return doc

    def test_scope_browser_and_exact_response_lock(self):
        pid=self.plan(profiles=["clinician_rag"])
        admin=self.actors["admin"]
        operator=self.actors["operator"]
        p=rag.preview(self.root,admin,pid)
        form={"plan_id":pid}
        self.assertEqual(self.post("admin","/rag-trace-preview",form)["status"],"200 OK")
        self.assertEqual(self.post("operator","/rag-trace-preview",form)["status"],"403 Forbidden")
        self.assertEqual(self.request("admin","/rag-trace-preview","POST",form)["status"],"403 Forbidden")
        form.update(approval=p["digest"],confirm="yes")
        self.assertEqual(self.post("admin","/rag-trace-freeze",{**form,"approval":"bad"})["status"],"403 Forbidden")
        out=self.post("admin","/rag-trace-freeze",form)
        self.assertEqual(out["status"],"303 See Other")
        ident=out["headers"]["Location"].split("id=")[1]
        self.assertEqual(self.request("observer",out["headers"]["Location"])["status"],"403 Forbidden")
        doc=self.answers(ident)
        f={"id":ident,"responses":json.dumps(doc),"confirm":"yes"}
        self.assertEqual(self.post("operator","/rag-trace-submit",f)["status"],"303 See Other")
        self.assertEqual(self.post("operator","/rag-trace-submit",f)["status"],"303 See Other")
        altered=copy.deepcopy(doc)
        altered["responses"][0]["answer"]="Different response"
        self.assertEqual(self.post("operator","/rag-trace-submit",{**f,"responses":json.dumps(altered)})["status"],"403 Forbidden")
        self.assertNotIn("summary",rag.detail(self.root,operator,ident))
        sealed=rag.detail(self.root,admin,ident)
        self.assertEqual(sealed["summary"]["n_traces"],4)
        f={"id":ident,"digest":sealed["evidence_digest"],"confirm":"yes"}
        self.assertEqual(self.post("operator","/rag-trace-evidence",f)["status"],"403 Forbidden")
        out=self.post("admin","/rag-trace-evidence",f)
        self.assertEqual(out["status"],"200 OK")
        package=json.loads(out["body"])
        self.assertTrue(trace.verify(package,sealed["evidence_digest"])["expected_digest_matched"])

    def test_unified_discovery_and_version_scope(self):
        pid=self.plan(profiles=["clinician_rag"])
        other=self.plan(version="v2",profiles=["clinician_rag"])
        admin=self.actors["admin"]
        operator=self.actors["operator"]
        guided=u.guided_spec(self.root,admin,"guided-rag",{pid:[]})
        self.assertTrue(any(r["kind"]=="rag" and r["family"]=="retrieval_failure" for r in guided["requirements"]))
        spec={"programme_id":"rag-programme","plans":[pid],"requirements":[
            {"id":"rag-a","kind":"rag","plan_id":pid,"family":"retrieval_failure"}]}
        programme=self.freeze(spec)
        ident=self.prepare(pid)
        wrong=self.prepare(other)
        row=work.work(self.root,operator,programme)["requirements"][0]
        self.assertEqual([r["id"] for r in row["native_work_records"]],[ident])
        self.assertEqual(row["native_work_records"][0]["state"],"collecting")
        self.assertEqual(discovery.discover(self.root,admin,programme,"rag-a")["matches"],[])
        rag.submit(self.root,operator,wrong,self.answers(wrong),confirmed=True)
        rag.submit(self.root,operator,ident,self.answers(ident),confirmed=True)
        matches=discovery.discover(self.root,admin,programme,"rag-a")["matches"]
        self.assertEqual([p["binding"]["source"]["source_id"] for p in matches],[ident])
        with self.assertRaises(access.AccessDenied):
            u.link_preview(self.root,admin,programme,"rag-a",wrong)
        p=u.link_preview(self.root,admin,programme,"rag-a",ident)
        u.lock(self.root,admin,programme,"rag-a",ident,p["approval_digest"],confirmed=True)
        status=u.status(self.root,self.actors["observer"],programme)
        self.assertTrue(status["all_planned_sources_available"])
        self.assertFalse(status["canonical_requirements_satisfied"])
        self.assertEqual(status["requirements"][0]["locked_source"]["claim"],"synthetic_context_rehearsal_only")
        inventory=committee.overview(self.root,self.actors["observer"])["lifecycle_inventory"]["synthetic_rag_rehearsals"]
        self.assertEqual(len(inventory),2)
        self.assertNotIn("responses",json.dumps(inventory))

    def test_wrong_profile_and_deleted_audit_source(self):
        admin=self.actors["admin"]
        with self.assertRaises(access.AccessDenied):
            rag.preview(self.root,admin,self.plan())
        ident=self.prepare(self.plan(profiles=["clinician_rag"]))
        rag.submit(self.root,self.actors["operator"],ident,self.answers(ident),confirmed=True)
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM institution_rag_reports")
        with self.assertRaises(access.AccessDenied):
            rag.detail(self.root,admin,ident)


if __name__=="__main__":
    unittest.main()
