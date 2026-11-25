"""Provider-neutral contract and real loopback HTTP transport, synthetic only."""
import copy
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler,HTTPServer

from caeval import candidate_protocol as p, buyer_connector as connector, buyer_agent as buyer, adapters


TARGET={"name":"Fictional vendor","version":"1"}


class ProtocolTests(unittest.TestCase):
    def test_exact_binding_and_unavailable(self):
        req=p.request(TARGET,"Synthetic prompt")
        response=p.response(req,target=TARGET,status="complete",answer="Fictional answer")
        self.assertEqual(p.parse_response(req,json.dumps(response).encode()),response)
        for change in ({"request_id":"0"*32},{"target":{**TARGET,"version":"2"}},
                       {"protocol":"unknown"},{"extra":"data"},{"status":"unavailable"},
                       {"answer":"x"*(p.MAX_ANSWER_BYTES+1)}):
            with self.subTest(change=change), self.assertRaises(buyer.BuyerError):
                p.validate_response(req,{**response,**change})
        self.assertEqual(p.response(req,target=TARGET,status="unavailable")["answer"],"")
        with self.assertRaises(buyer.BuyerError):
            p.validate_request(req,expected_target={**TARGET,"version":"2"})
        self.assertNotEqual(req["request_id"],p.request(TARGET,"Synthetic prompt")["request_id"])

    def test_duplicate_keys_and_prompt_limit(self):
        req=p.request(TARGET,"Synthetic prompt")
        with self.assertRaises(buyer.BuyerError):
            p.parse_response(req,b'{"answer":"a","answer":"b"}')
        with self.assertRaises(buyer.BuyerError):
            p.request(TARGET,"å"*(p.MAX_PROMPT_BYTES//2+1))
        self.assertEqual(p.openapi()["openapi"],"3.1.0")
        self.assertIn("/evaluate",p.openapi()["paths"])

    def test_real_loopback_standard_contract(self):
        seen=[]
        mode=["valid"]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def do_POST(self):
                doc=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(doc)
                reply=p.response(doc,target=TARGET,status="unavailable" if mode[0]=="unavailable" else "complete",
                                 answer="" if mode[0]=="unavailable" else "Fictional output")
                if mode[0]=="wrong-version":
                    reply["target"]["version"]="2"
                self.send_response(200)
                self.send_header("Content-Type","application/json")
                self.end_headers()
                self.wfile.write(json.dumps(reply).encode())
        with HTTPServer(("127.0.0.1",0),Handler) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                cfg={"url":"http://127.0.0.1:"+str(server.server_port)+"/evaluate",
                     "protocol":p.PROTOCOL,"target_name":TARGET["name"],"target_version":TARGET["version"],
                     "timeout_seconds":5}
                self.assertEqual(connector._call(cfg,"Synthetic prompt",{}),"Fictional output")
                self.assertEqual(set(seen[0]),{"protocol","request_id","target","input"})
                self.assertEqual(seen[0]["input"],{"kind":"single_turn_text","text":"Synthetic prompt"})
                mode[0]="wrong-version"
                with self.assertRaises(buyer.BuyerError):
                    connector._call(cfg,"Synthetic prompt",{})
                mode[0]="unavailable"
                with self.assertRaises(adapters.AdapterError):
                    connector._call(cfg,"Synthetic prompt",{})
                self.assertEqual(len({x["request_id"] for x in seen}),3)
            finally:
                server.shutdown()
                thread.join(timeout=5)


if __name__=="__main__":
    unittest.main()
