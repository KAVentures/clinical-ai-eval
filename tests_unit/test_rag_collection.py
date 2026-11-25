"""Local synthetic HTTP integration and immutable collection provenance."""
import copy
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch
from caeval import (rag_grounding as g, rag_collection as c, diagnostic_imaging as d,
                    institution_rag_grounding as service, institution_rag_collection as ledger,
                    institution_access as access, unified_assessment as u, adapters)
from tests_unit.test_rag_grounding import spec, ratings
from tests_unit import test_unified_assessment as fixtures


def reply(req,target=None):
    case=c.validate_request(req,expected_target=target)
    docs={doc["id"]:doc for doc in case["corpus"]}
    if "reference" in docs:
        trace={"slot":case["slot"],"status":"answered","answer":"The token is blue.",
               "retrieved":[{"id":"reference","digest":docs["reference"]["digest"]}],
               "claims":[{"start":0,"end":18,"citations":["reference"]}]}
    else:
        trace={"slot":case["slot"],"status":"abstained","answer":"Insufficient evidence.",
               "retrieved":[],"claims":[]}
    return c.response(req,target=target or req["target"],trace=trace)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def do_POST(self):
        req=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(req)
        if self.server.mode=="redirect":
            self.send_response(307)
            self.send_header("Location","/other")
            self.end_headers()
            return
        doc=reply(req,{"id":"a","version":"v1"})
        if self.server.mode=="wrong_id":
            doc["request_id"]="0"*32
        raw=json.dumps(doc).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class LocalCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        s=spec()
        s["questions"] += [{**s["questions"][0],"id":"q"+str(i),"query":"Fictional question "+str(i)}
                           for i in (2,3)]
        self.plan=g.freeze(s,confirmed=True)
        self.ex=g.exchange(self.plan,"a")
        self.server=HTTPServer(("127.0.0.1",0),Handler)
        self.server.requests=[]
        self.server.mode="ok"
        thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join,5)
        self.addCleanup(self.server.shutdown)
        self.cfg={"url":"http://127.0.0.1:"+str(self.server.server_port)+"/evaluate","timeout_seconds":5}

    def test_real_http_and_one_shot_journal(self):
        p=c.preview(self.ex,self.cfg)
        self.assertEqual(self.server.requests,[])
        with self.assertRaises(ValueError):
            c.collect(self.ex,self.cfg,p["digest"],self.root)
        with self.assertRaises(ValueError):
            c.collect(self.ex,self.cfg,"0"*64,self.root,confirmed=True)
        receipt=c.collect(self.ex,self.cfg,p["digest"],self.root,confirmed=True)
        self.assertEqual(len(self.server.requests),6)
        for req in self.server.requests:
            self.assertEqual(set(req),{"protocol","request_id","target","case"})
            self.assertEqual(set(req["case"]),{"slot","query","corpus"})
            self.assertNotIn("reference_ids",json.dumps(req))
            self.assertNotIn("doctor-a",json.dumps(req))
        self.assertTrue(c.verify(self.ex,receipt,receipt["digest"])["expected_digest_matched"])
        self.assertFalse(c.verify(self.ex,receipt)["execution_attested"])
        self.assertEqual(g.locked_traces(self.plan,[receipt["trace"]]),[receipt["trace"]])
        journal=self.root/p["exchange_digest"]
        self.assertEqual(json.loads((journal/"receipt.json").read_text()),receipt)
        self.assertEqual(len(list(journal.glob("*-intent.json"))),6)
        with self.assertRaises(FileExistsError):
            c.collect(self.ex,self.cfg,p["digest"],self.root,confirmed=True)
        self.assertEqual(len(self.server.requests),6)

    def test_wrong_correlation_stops_and_preserves_missingness(self):
        self.server.mode="wrong_id"
        p=c.preview(self.ex,self.cfg)
        receipt=c.collect(self.ex,self.cfg,p["digest"],self.root,confirmed=True)
        self.assertEqual(len(self.server.requests),3)
        self.assertEqual([e["state"] for e in receipt["events"]],["failed"]*3+["not_attempted"]*3)
        self.assertTrue(all(r["status"]=="unavailable" for r in receipt["trace"]["traces"]))
        self.assertEqual(c.verify(self.ex,receipt)["n_transport_failures"],3)
        bad=copy.deepcopy(receipt)
        bad["events"][0]["request_digest"]="0"*64
        bad["digest"]=d._digest({k:v for k,v in bad.items() if k!="digest"})
        with self.assertRaises(ValueError):
            c.verify(self.ex,bad)

    def test_local_only_and_redirect_refusal(self):
        for url in ("https://example.com/evaluate","http://localhost:8000/evaluate",
                    "http://127.0.0.1:8000/evaluate?token=x"):
            with self.assertRaises(adapters.AdapterError):
                c.preview(self.ex,{**self.cfg,"url":url})
        self.server.mode="redirect"
        p=c.preview(self.ex,self.cfg)
        receipt=c.collect(self.ex,self.cfg,p["digest"],self.root,confirmed=True)
        self.assertEqual(len(self.server.requests),3)
        self.assertTrue(all(r["status"]=="unavailable" for r in receipt["trace"]["traces"]))

    def test_interruption_after_intent_is_not_automatically_resumed(self):
        p=c.preview(self.ex,self.cfg)
        with patch.object(c,"_post",side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                c.collect(self.ex,self.cfg,p["digest"],self.root,confirmed=True)
        journal=self.root/p["exchange_digest"]
        self.assertTrue((journal/"000-intent.json").exists())
        self.assertFalse((journal/"receipt.json").exists())
        with self.assertRaises(FileExistsError):
            c.collect(self.ex,self.cfg,p["digest"],self.root,confirmed=True)
        self.assertEqual(self.server.requests,[])

    def test_changed_document_or_invalid_response_refused(self):
        bad=copy.deepcopy(self.ex)
        bad["cases"][0]["corpus"][0]["text"]="Changed"
        with self.assertRaises(ValueError):
            c.preview(bad,self.cfg)
        req=c.request(self.ex,self.ex["cases"][0],"a"*32)
        doc=reply(req)
        changed=copy.deepcopy(doc)
        changed["trace"]["retrieved"]=[{"id":"unknown","digest":"0"*64}]
        with self.assertRaises(ValueError):
            c.validate_response(req,changed)
        with self.assertRaises(ValueError):
            c.validate_request(req,expected_target={"id":"a","version":"v2"})
        self.assertIn("/evaluate",c.openapi()["paths"])


class InstitutionalReceiptTests(unittest.TestCase):
    setUp=fixtures.UnifiedAssessmentTests.setUp
    request=fixtures.UnifiedAssessmentTests.request
    post=fixtures.UnifiedAssessmentTests.post
    plan=fixtures.UnifiedAssessmentTests.plan
    freeze=fixtures.UnifiedAssessmentTests.freeze

    def test_atomic_receipt_import_and_sealed_evidence(self):
        admin=self.actors["admin"]
        operator=self.actors["operator"]
        s=spec()
        p=service.preview(self.root,admin,s)
        ident=service.freeze(self.root,admin,s,p["approval"],confirmed=True)
        journal=Path(self.temp.name)/"journal"
        journal.mkdir()
        cfg={"url":"http://127.0.0.1:12345/evaluate","timeout_seconds":1}
        ex=service.candidate_download(self.root,operator,ident,"a","exchange")
        p=c.preview(ex,cfg)
        with patch.object(c,"_post",side_effect=lambda cfg,req:reply(req)):
            receipt=c.collect(ex,cfg,p["digest"],journal,confirmed=True)
        fields={"id":ident,"receipt":json.dumps(receipt),"confirm":"yes"}
        self.assertEqual(self.request("operator","/rag-grounding-collection-import","POST",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("observer","/rag-grounding-collection-import",fields)["status"],"403 Forbidden")
        bad=copy.deepcopy(receipt)
        bad["digest"]="0"*64
        self.assertEqual(self.post("operator","/rag-grounding-collection-import",{**fields,"receipt":json.dumps(bad)})["status"],"403 Forbidden")
        self.assertEqual(service.detail(self.root,operator,ident)["n_traces_locked"],0)
        self.assertEqual(self.post("operator","/rag-grounding-collection-import",fields)["status"],"303 See Other")
        self.assertEqual(self.post("operator","/rag-grounding-collection-import",fields)["status"],"303 See Other")
        with self.assertRaises(access.AccessDenied):
            service.submit(self.root,operator,ident,receipt["trace"],confirmed=True)
        exb=service.candidate_download(self.root,operator,ident,"b","exchange")
        p=c.preview(exb,cfg)
        with patch.object(c,"_post",side_effect=lambda cfg,req:reply(req)):
            rb=c.collect(exb,cfg,p["digest"],journal,confirmed=True)
        service.submit(self.root,operator,ident,rb["trace"],confirmed=True)
        with self.assertRaises(access.AccessDenied):
            service.submit(self.root,operator,ident,rb["trace"],confirmed=True,receipt=rb)
        for uid in ("doctor-a","doctor-b"):
            packet=service.review_download(self.root,self.actors[uid],ident,"packet")
            self.assertNotIn("receipt",packet)
            service.review(self.root,self.actors[uid],ident,ratings(packet),confirmed=True)
        p=service.seal_preview(self.root,admin,ident)
        service.seal(self.root,admin,ident,p["approval"],confirmed=True)
        evidence=service.export(self.root,admin,ident,p["approval"],confirmed=True)
        preview=ledger.export_preview(self.root,admin,ident)
        f={"id":ident,"approval":preview["approval"],"confirm":"yes"}
        self.assertEqual(self.post("operator","/rag-grounding-collection-evidence",f)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/rag-grounding-collection-evidence",{**f,"approval":"bad"})["status"],"403 Forbidden")
        out=self.post("admin","/rag-grounding-collection-evidence",f)
        self.assertEqual(out["status"],"200 OK")
        pkg=json.loads(out["body"])
        self.assertEqual(ledger.verify_package(pkg,evidence,pkg["digest"])["n_receipts"],1)
        pid=self.plan(profiles=["clinician_rag"])
        programme=self.freeze({"programme_id":"collection-programme","plans":[pid],"requirements":[
            {"id":"grounding-a","kind":"rag_grounding","plan_id":pid,"family":None}]})
        p=u.link_preview(self.root,admin,programme,"grounding-a",ident)
        self.assertEqual(p["binding"]["source"]["collection_receipt_digest"],receipt["digest"])
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM rag_grounding_collection_receipts")
        with self.assertRaises(access.AccessDenied):
            service.detail(self.root,operator,ident)


if __name__=="__main__":
    unittest.main()
