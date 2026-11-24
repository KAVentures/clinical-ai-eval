"""Exercise restricted conversational transport against a real loopback server."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from caeval import adapters


class LocalConversationTests(unittest.TestCase):
    def setUp(self):
        self.requests=[]
        self.mode="normal"
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                if outer.mode=="redirect":
                    self.send_response(307)
                    self.send_header("Location","http://127.0.0.1:"+str(self.server.server_port)+"/other")
                    self.end_headers()
                    return
                reply={"answer":"Fixture answer","session":"episode-session"}
                if outer.mode=="invalid":
                    reply["answer"]=False
                if outer.mode=="oversized":
                    reply["answer"]="x"*(adapters.LOCAL_MAX_REPLY+100)
                raw=json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except OSError:
                    pass
        self.server=HTTPServer(("127.0.0.1",0),Handler)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.close)
        self.url="http://127.0.0.1:"+str(self.server.server_port)+"/chat"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)

    def spec(self,**kw):
        return adapters.AdapterSpec(**dict({"adapter_id":"fixture","kind":"http_conversation",
            "url":self.url,"local_only":True,"timeout":5,"session_field":"session"},**kw))

    def test_conversation_payload_and_session_reset(self):
        a=adapters.build_conversation_adapter(self.spec())
        history=[{"role":"patient","text":"Opening","hidden":"NEVER SEND"}]
        self.assertEqual(a(history),"Fixture answer")
        history.extend([{"role":"system","text":"Fixture answer"},{"role":"patient","text":"Follow-up"}])
        a(history)
        a.reset()
        a(history[:1])
        self.assertEqual(self.requests[0],{"messages":[{"role":"patient","content":"Opening"}]})
        self.assertEqual(self.requests[1]["session"],"episode-session")
        self.assertEqual(len(self.requests[1]["messages"]),3)
        self.assertEqual(self.requests[2],self.requests[0])
        self.assertNotIn("NEVER SEND",json.dumps(self.requests))

    def test_reject_redirect_nontext_and_oversized_reply(self):
        for mode in ("redirect","invalid","oversized"):
            self.mode=mode
            before=len(self.requests)
            a=adapters.build_conversation_adapter(self.spec())
            with self.assertRaises(adapters.AdapterError):
                a([{"role":"patient","text":"Fixture"}])
            self.assertEqual(len(self.requests),before+1)

    def test_invalid_endpoints_and_payload_overrides_rejected(self):
        for url in ("https://example.org/chat","http://localhost:8080/chat",
                    "http://127.0.0.1:8080/chat?secret=x","http://127.0.0.1:8080/../chat",
                    "http://127.0.0.1:8080@outside.example/chat","http://127.0.0.1:0/chat"):
            with self.subTest(url=url),self.assertRaises(adapters.AdapterError):
                self.spec(url=url)
        for extra in ({"messages":[]},{"_retries":2}):
            with self.assertRaises(adapters.AdapterError):
                self.spec(extra=extra)
        with self.assertRaises(adapters.AdapterError):
            self.spec(headers={"Authorization":"literal-secret"})
        with self.assertRaises(adapters.AdapterError):
            self.spec(session_field="messages")

    def test_call_budget_survives_session_reset(self):
        a=adapters.build_conversation_adapter(self.spec())
        with patch.object(adapters,"LOCAL_MAX_CALLS",1):
            a([{"role":"patient","text":"Fixture"}])
            a.reset()
            with self.assertRaises(adapters.AdapterError):
                a([{"role":"patient","text":"Another fixture"}])
        self.assertEqual(len(self.requests),1)

    def test_cli_rejects_non_conversational_local_only_scope_before_loading_panel(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from caeval import cli, project
        with tempfile.TemporaryDirectory() as temp:
            config=project.template("Fixture","internal_regression")
            config["target"]={"name":"Fixture","version":"1","vendor":"Fixture"}
            config["target_profile"]["types"]=["clinician_decision_support"]
            config["intake"]={q:"Synthetic fixture" for q in project.MANDATORY_INTAKE}
            config["governance"]={"influences_diagnosis_or_treatment":"yes",
                "clinician_reviews_every_response":"yes","regulated_medical_device":"unsure",
                "deployment_jurisdictions":"SE"}
            config["subject"]={"kind":"http","url":self.url,"local_only":True}
            config["case_pack"]={"source":"builtin:demo_clinician"}
            (Path(temp)/"project.yaml").write_text(json.dumps(config))
            with patch.object(cli,"_panel_and_keys") as panel:
                with self.assertRaisesRegex(SystemExit,"local_only"):
                    cli._run_project_bound(SimpleNamespace(project=temp,workspace=None))
                panel.assert_not_called()

    def test_failed_repeat_probe_is_not_a_live_connection(self):
        a=adapters.build_conversation_adapter(self.spec())
        with patch.object(adapters,"probe_liveness",return_value={"ok":True}), \
             patch.object(adapters,"probe_determinism",return_value={"ok":False,"reason":"fixture failure"}):
            self.assertFalse(adapters.probe_capabilities(a)["ok"])


if __name__=="__main__":
    unittest.main()
