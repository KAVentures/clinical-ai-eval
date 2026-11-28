"""Standard conversation transport exercised over actual loopback HTTP."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler,HTTPServer
from caeval import adapters,conversation_protocol as protocol

TARGET={"name":"Fictional triage product","version":"1"}


class ConversationContractTests(unittest.TestCase):
    def test_correlated_turns_and_reset(self):
        seen=[]
        mode=["valid"]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def do_POST(self):
                req=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(req)
                reply=protocol.response(req,target=TARGET,status="complete",answer="Fictional response")
                if mode[0]=="wrong-episode":
                    reply["episode_id"]="0"*32
                self.send_response(200)
                self.send_header("Content-Type","application/json")
                self.end_headers()
                self.wfile.write(json.dumps(reply).encode())
        with HTTPServer(("127.0.0.1",0),Handler) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                spec=adapters.AdapterSpec(adapter_id=TARGET["name"],kind="http_conversation",
                    version=TARGET["version"],target_name=TARGET["name"],protocol=protocol.PROTOCOL,
                    url="http://127.0.0.1:"+str(server.server_port)+"/evaluate",local_only=True,timeout=5)
                a=adapters.build_conversation_adapter(spec)
                history=[{"role":"patient","text":"Opening","hidden":"never send"}]
                self.assertEqual(a(history),"Fictional response")
                history.extend([{"role":"system","text":"Fictional response"},{"role":"patient","text":"Follow-up"}])
                a(history)
                a.reset()
                a(history[:1])
                self.assertEqual(seen[0]["episode_id"],seen[1]["episode_id"])
                self.assertNotEqual(seen[0]["episode_id"],seen[2]["episode_id"])
                self.assertEqual(len({r["request_id"] for r in seen}),3)
                self.assertNotIn("never send",json.dumps(seen))
                self.assertEqual(len(seen[1]["input"]["messages"]),3)
                mode[0]="wrong-episode"
                with self.assertRaises(adapters.AdapterError):
                    a(history[:1])
            finally:
                server.shutdown()
                thread.join(timeout=5)

    def test_exact_roles_and_openapi(self):
        from caeval import buyer_agent as buyer
        with self.assertRaises(buyer.BuyerError):
            protocol.request(TARGET,"a"*32,[{"role":"system","text":"Invalid"}])
        self.assertEqual(protocol.openapi()["openapi"],"3.1.0")
