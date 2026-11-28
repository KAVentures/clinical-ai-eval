"""Synthetic exact-byte and local candidate transport tests."""
import copy
import hashlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch
from caeval import (diagnostic_imaging as d, imaging_transport as t,
                    institution_imaging as studies, institution_imaging_transport as ledger,
                    institution_access as access)
from tests_unit import test_institution_research as browser
from tests_unit.test_diagnostic_imaging import spec as original_spec


def fixture(root):
    s=original_spec()
    # Signature fixtures only, intentionally not valid diagnostic images.
    for i,c in enumerate(s["cases"]):
        raw=bytes(128)+b"DICM"+bytes([i+1])
        digest=hashlib.sha256(raw).hexdigest()
        (root/digest).write_bytes(raw)
        c["asset_sha256"]=digest
    return s


class Candidate(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def do_POST(self):
        req=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(req)
        doc=t.response(req,target={"id":"a","version":"v1"},status="scored",score_bp=7000)
        if self.server.bad:
            doc["asset_sha256"]="0"*64
        raw=json.dumps(doc).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.plan=d.freeze(fixture(self.root),confirmed=True)
        self.ex=d.exchange(self.plan,"a")
        self.server=HTTPServer(("127.0.0.1",0),Candidate)
        self.server.requests=[]
        self.server.bad=False
        thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join,5)
        self.addCleanup(self.server.shutdown)
        self.cfg={"url":"http://127.0.0.1:"+str(self.server.server_port)+"/evaluate","timeout_seconds":5}

    def test_real_loopback_and_receipt(self):
        p=t.preview(self.ex,self.root,self.cfg)
        self.assertEqual(self.server.requests,[])
        r=t.collect(self.ex,self.root,self.cfg,p["digest"],confirmed=True)
        self.assertEqual(len(self.server.requests),len(self.ex["cases"]))
        self.assertTrue(all(x["status"]=="scored" for x in r["predictions"]["predictions"]))
        for req in self.server.requests:
            self.assertEqual(set(req),{"protocol","request_id","target","task_id","modality","asset"})
            self.assertNotIn("threshold_bp",req["target"])
            self.assertEqual(hashlib.sha256(t.validate_request(req)).hexdigest(),req["asset"]["sha256"])
        self.assertTrue(t.verify(self.ex,r,r["digest"])["expected_digest_matched"])
        self.assertEqual(d.assess(self.plan,[r["predictions"]])["candidates"][0]["status"],"submitted")
        self.assertNotIn("base64",json.dumps(r))

    def test_missing_modified_and_symlink_assets_fail_before_calls(self):
        case=self.ex["cases"][0]
        path=self.root/case["asset_sha256"]
        original=path.read_bytes()
        p=t.preview(self.ex,self.root,self.cfg)
        path.write_bytes(original+b"altered")
        with self.assertRaises(ValueError):
            t.collect(self.ex,self.root,self.cfg,p["digest"],confirmed=True)
        path.unlink()
        with self.assertRaises(OSError):
            t.preview(self.ex,self.root,self.cfg)
        other=self.root/"other"
        other.write_bytes(original)
        try:
            path.symlink_to(other)
        except (OSError,NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(ValueError):
            t.preview(self.ex,self.root,self.cfg)
        self.assertEqual(self.server.requests,[])

    def test_approval_and_network_scope(self):
        p=t.preview(self.ex,self.root,self.cfg)
        with self.assertRaises(ValueError):
            t.collect(self.ex,self.root,self.cfg,p["digest"])
        with self.assertRaises(ValueError):
            t.collect(self.ex,self.root,self.cfg,"0"*64,confirmed=True)
        for url in ("https://example.org/evaluate","http://localhost:1234/evaluate",
                    "http://127.0.0.1:1234/evaluate?secret=x"):
            with self.assertRaises(RuntimeError):
                t.preview(self.ex,self.root,{**self.cfg,"url":url})
        self.assertEqual(self.server.requests,[])

    def test_correlation_failures_and_stop_budget(self):
        self.server.bad=True
        p=t.preview(self.ex,self.root,self.cfg)
        r=t.collect(self.ex,self.root,self.cfg,p["digest"],confirmed=True)
        self.assertEqual(len(self.server.requests),3)
        self.assertTrue(all(x["status"]=="unavailable" for x in r["predictions"]["predictions"]))
        self.assertEqual(t.verify(self.ex,r)["n_transport_failed"],3)
        self.assertEqual(sum(e["transport_status"]=="not_attempted" for e in r["events"]),len(self.ex["cases"])-3)
        bad=copy.deepcopy(r)
        bad["predictions"]["predictions"][0].update(status="scored",score_bp=1)
        bad["digest"]=d._digest({k:v for k,v in bad.items() if k!="digest"})
        with self.assertRaises(ValueError):
            t.verify(self.ex,bad)

    def test_request_asset_format_and_target_validation(self):
        case=self.ex["cases"][0]
        raw,media=t._file(self.root,case["asset_sha256"])
        req=t.request(self.ex,case,raw,media)
        self.assertEqual(t.validate_request(req,expected_target={"id":"a","version":"v1"}),raw)
        with self.assertRaises(ValueError):
            t.validate_request(req,expected_target={"id":"a","version":"v2"})
        for field,value in (("media_type","image/png"),("sha256","0"*64),("base64","!!!")):
            changed=copy.deepcopy(req)
            changed["asset"][field]=value
            with self.assertRaises(ValueError):
                t.validate_request(changed)
        self.assertIn("/evaluate",t.openapi()["paths"])


class InstitutionalTransportTests(unittest.TestCase):
    setUp=browser.StudioTests.setUp
    request=browser.StudioTests.request
    post=browser.StudioTests.post

    def test_receipt_atomic_lock_and_private_replay(self):
        stage=Path(self.temp.name)/"images"
        stage.mkdir()
        s=fixture(stage)
        admin=self.actors["admin"]
        operator=self.actors["operator"]
        p=studies.preview(self.root,admin,s)
        ident=studies.freeze(self.root,admin,s,p["approval_digest"],confirmed=True)
        ex=studies.download(self.root,operator,ident,"a","exchange")
        cfg={"url":"http://127.0.0.1:12345/evaluate","timeout_seconds":1}
        binding=t.preview(ex,stage,cfg)
        def respond(cfg,req):
            return t.response(req,target={"id":"a","version":"v1"},status="scored",score_bp=5000)
        with patch.object(t,"_post",side_effect=respond):
            receipt=t.collect(ex,stage,cfg,binding["digest"],confirmed=True)
        fields={"id":ident,"receipt":json.dumps(receipt),"confirm":"yes"}
        self.assertEqual(self.request("operator","/diagnostic-imaging-transport-import","POST",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("operator","/diagnostic-imaging-transport-import",fields)["status"],"303 See Other")
        self.assertEqual(self.post("operator","/diagnostic-imaging-transport-import",fields)["status"],"303 See Other")
        with self.assertRaises(access.AccessDenied):
            studies.submit(self.root,operator,ident,receipt["predictions"],confirmed=True)
        self.assertEqual(studies.detail(self.root,operator,ident)["collection_receipts"][0]["receipt_digest"],receipt["digest"])
        p=studies.seal_preview(self.root,admin,ident)
        digest=studies.seal(self.root,admin,ident,p["approval_digest"],confirmed=True,allow_incomplete=True)
        f={"id":ident,"digest":digest,"confirm":"yes"}
        self.assertEqual(self.post("operator","/diagnostic-imaging-transport-evidence",f)["status"],"403 Forbidden")
        result=self.post("admin","/diagnostic-imaging-transport-evidence",f)
        self.assertEqual(result["status"],"200 OK")
        pkg=json.loads(result["body"])
        evidence=studies.export(self.root,admin,ident,digest,confirmed=True)
        self.assertEqual(ledger.verify_package(pkg,evidence,pkg["digest"])["n_receipts"],1)
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM diagnostic_imaging_collection_receipts")
        with self.assertRaises(access.AccessDenied):
            studies.detail(self.root,admin,ident)


if __name__=="__main__":
    unittest.main()
