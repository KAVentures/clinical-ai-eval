"""Exercise synthetic interoperability collection against real local HTTP."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
from caeval import (interoperability_protocol as api,buyer_agent as buyer,
                    buyer_plan,intake,buyer_connector)


class InteroperabilityTests(unittest.TestCase):
    def test_shared_real_loopback_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"workspace"
            buyer.init(root,"Synthetic institution")
            buyer.add_vendor(root,"fixture","Fixture")
            target={"name":"Fixture","version":"1"}
            plan=buyer_plan.preview(root,{"vendor_id":"fixture","target_name":"Fixture","target_version":"1",
                     "profiles":["clinician_decision_support"],
                     "answers":{q:"Fictional "+q for q in intake.INTAKE_QUESTIONS}})
            seen=[]
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*args):
                    pass
                def do_POST(self):
                    req=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    api.validate_request(req,expected_target=target)
                    seen.append(req)
                    reply=api.response(req,target=target,status="unavailable")
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
                        "protocol":api.PROTOCOL,"headers":{},"timeout_seconds":5}))
                    for kind,mod in api.MODULES.items():
                        args=(root,plan["plan_id"],"local",kind)
                        binding=api.preview(*args)
                        before=len(seen)
                        with self.assertRaises(buyer.BuyerError):
                            api.collect(*args,binding["digest"])
                        with self.assertRaises(buyer.BuyerError):
                            api.collect(*args,"0"*64,confirmed=True)
                        self.assertEqual(len(seen),before)
                        result=api.collect(*args,binding["digest"],confirmed=True)
                        self.assertEqual(len(seen)-before,3)
                        self.assertEqual(result["receipt"]["attempted"],3)
                        report=mod.verify(result["evidence"])
                        self.assertEqual(report["summary"]["unavailable"],binding["n_calls"])
                        self.assertEqual(report["summary"]["exact_match"],0)
                        self.assertFalse(report["decision_grade"])
                        self.assertNotIn("expected_facts",json.dumps(seen))
                    self.assertEqual(len({x["request_id"] for x in seen}),6)
                finally:
                    server.shutdown()
                    t.join(timeout=5)

    def test_arbitrary_inputs_and_wrong_correlation_refused(self):
        target={"name":"Fixture","version":"1"}
        for kind,mod in api.MODULES.items():
            p=mod.build(target)
            req=api.request(p,kind,p["rows"][0]["slot"])
            api.validate_request(req)
            reply=api.response(req,target=target,status="complete",facts=p["rows"][0]["expected_facts"])
            self.assertEqual(api.validate_response(req,reply),reply)
            with self.assertRaises(buyer.BuyerError):
                api.validate_response(req,{**reply,"request_id":"0"*32})
            req["input"]["synthetic"]=False
            with self.assertRaises(buyer.BuyerError):
                api.validate_request(req)
        self.assertEqual(api.openapi()["openapi"],"3.1.0")
