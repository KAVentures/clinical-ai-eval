"""Synthetic image contract regression tests."""
import base64
import hashlib
import json
import struct
import unittest
import tempfile
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
import zlib
from caeval import image_text_readiness as image,buyer_agent as buyer

TARGET={"name":"Fixture","version":"1"}


class ImageTextTests(unittest.TestCase):
    def test_fixture_and_replay(self):
        p=image.build(TARGET)
        self.assertEqual(p,image.build(TARGET))
        self.assertEqual(len(p["rows"]),12)
        exchange=image.exchange(p)
        self.assertNotIn("expected_value",json.dumps(exchange))
        self.assertNotIn("target_change",json.dumps(exchange))
        for row in p["rows"]:
            raw=base64.b64decode(row["input"]["image"]["base64"],validate=True)
            self.assertEqual(hashlib.sha256(raw).hexdigest(),row["input"]["image"]["sha256"])
            self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))
            off=8
            chunks={}
            while off<len(raw):
                n=struct.unpack(">I",raw[off:off+4])[0]
                kind,payload=raw[off+4:off+8],raw[off+8:off+8+n]
                self.assertEqual(zlib.crc32(kind+payload)&0xffffffff,
                                 struct.unpack(">I",raw[off+8+n:off+12+n])[0])
                chunks[kind]=payload
                off+=12+n
            self.assertEqual(len(zlib.decompress(chunks[b"IDAT"])),96*49)
            image.validate_request(image.request(p,row["slot"]))
        responses=image.template(p)
        for row,expected in zip(responses["responses"],p["rows"]):
            row.update(status=expected["expected_status"],value=expected["expected_value"])
        pkg=image.package(p,responses)
        self.assertEqual(image.verify(pkg)["summary"],{"planned":12,"exact_match":12,"mismatch":0,"unavailable":0})
        responses["responses"][0].update(status="complete",value="999")
        responses["responses"][1].update(status="unavailable",value=None)
        self.assertEqual(image.package(p,responses)["report"]["summary"],{"planned":12,"exact_match":10,"mismatch":1,"unavailable":1})
        self.assertEqual(image.assess(p,image.template(p))["summary"]["unavailable"],12)

    def test_arbitrary_input_and_mismatched_reply_refused(self):
        p=image.build(TARGET)
        req=image.request(p,p["rows"][0]["slot"])
        with self.assertRaises(buyer.BuyerError):
            image.response(req,target=TARGET,status="complete",value="72")
        reply=image.response(req,target=TARGET,status="complete",value="072")
        with self.assertRaises(buyer.BuyerError):
            image.validate_response(req,{**reply,"request_id":"0"*32})
        req["input"]["image"]["base64"]="AA=="
        with self.assertRaises(buyer.BuyerError):
            image.validate_request(req)

    def test_missing_duplicate_and_tampered_evidence(self):
        p=image.build(TARGET)
        r=image.template(p)
        with self.assertRaises(buyer.BuyerError):
            image.assess(p,{**r,"responses":r["responses"][:-1]})
        with self.assertRaises(buyer.BuyerError):
            image.assess(p,{**r,"responses":[r["responses"][0]]*12})
        with self.assertRaises(buyer.BuyerError):
            image.assess(p,{**r,"target":{**TARGET,"version":"2"}})
        pkg=image.package(p,r)
        with self.assertRaises(buyer.BuyerError):
            image.verify(pkg,"0"*64)
        pkg["report"]["summary"]["exact_match"]=12
        with self.assertRaises(buyer.BuyerError):
            image.verify(pkg)

    def test_real_loopback_failure_denominator_and_approval(self):
        from caeval import buyer_plan,intake
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"workspace"
            buyer.init(root,"Synthetic institution")
            buyer.add_vendor(root,"fixture","Fixture")
            p=buyer_plan.preview(root,{"vendor_id":"fixture","target_name":"Fixture","target_version":"1",
                "profiles":["clinician_decision_support"],"answers":{q:"Fictional "+q for q in intake.INTAKE_QUESTIONS}})
            calls=[]
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*args):
                    pass
                def do_POST(self):
                    req=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    image.validate_request(req,expected_target=TARGET)
                    calls.append(req)
                    reply=image.response(req,target=TARGET,status="unavailable")
                    self.send_response(200)
                    self.send_header("Content-Type","application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(reply).encode())
            with HTTPServer(("127.0.0.1",0),Handler) as server:
                t=threading.Thread(target=server.serve_forever,daemon=True)
                t.start()
                try:
                    folder=root/"approved-connectors"
                    folder.mkdir()
                    (folder/"local.json").write_text(json.dumps({
                        "vendor_id":"fixture","target_name":"Fixture","target_version":"1",
                        "url":"http://127.0.0.1:"+str(server.server_port)+"/evaluate",
                        "protocol":image.PROTOCOL,"headers":{},"timeout_seconds":5}))
                    args=(root,p["plan_id"],"local")
                    binding=image.preview(*args)
                    with self.assertRaises(buyer.BuyerError):
                        image.collect(*args,binding["digest"])
                    with self.assertRaises(buyer.BuyerError):
                        image.collect(*args,"0"*64,confirmed=True)
                    self.assertFalse(calls)
                    result=image.collect(*args,binding["digest"],confirmed=True)
                    self.assertEqual(len(calls),3)
                    self.assertEqual(len({x["request_id"] for x in calls}),3)
                    self.assertEqual(result["receipt"]["not_attempted"],9)
                    self.assertEqual(image.verify(result["evidence"])["summary"]["unavailable"],12)
                finally:
                    server.shutdown()
                    t.join(timeout=5)
