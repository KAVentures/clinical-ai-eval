"""External bridge network policy and real loopback adapter interoperability."""
import json
import os
import socket
import threading
import unittest
from unittest.mock import MagicMock,patch

from caeval import approved_https_bridge as bridge, adapters, buyer_connector


def config(modality="single_turn"):
    with socket.socket() as s:
        s.bind(("127.0.0.1",0))
        port=s.getsockname()[1]
    return {"vendor_id":"candidate","target_name":"Synthetic product","target_version":"1",
            "modality":modality,"listen_port":port,"upstream_url":"https://api.example.com/v1/evaluate",
            "upstream_ip":"8.8.8.8","upstream_input_field":"input" if modality=="single_turn" else "messages",
            "upstream_answer_path":"choices.0.message.content","fixed_fields":{"model":"approved-model","stream":False},
            "local_token_env":"CA_TEST_LOCAL","upstream_token_env":"CA_TEST_REMOTE",
            "upstream_auth":"bearer","timeout_seconds":5,"max_calls":3,"max_seconds":60,
            "data_scope":"approved_synthetic_or_nonidentifiable"}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{"CA_TEST_LOCAL":"a"*64,"CA_TEST_REMOTE":"synthetic-provider-secret"})
        env.start()
        self.addCleanup(env.stop)

    def test_config_binding_and_rejections(self):
        cfg=config()
        self.assertIn(bridge.binding(cfg),bridge.connector(cfg)["url"])
        self.assertNotIn("synthetic-provider-secret",json.dumps(bridge.connector(cfg)))
        for field,values in {
            "upstream_ip":["127.0.0.1","10.0.0.1","169.254.169.254","::1","::ffff:8.8.8.8","224.0.0.1"],
            "upstream_url":["http://api.example.com/x","https://api.example.com/x?secret=x",
                            "https://user@api.example.com/x","https://api.example.com:444/x",
                            "https://api.example.com/a/../x","https://8.8.8.8/x"],
            "fixed_fields":[{"input":"override"},{"api_key":"secret"},{"stream":True}],
            "data_scope":["patient_records"],"max_calls":[0,1501],
            "upstream_token_env":["CA_TEST_LOCAL"]}.items():
            for value in values:
                with self.subTest(field=field,value=value), self.assertRaises(bridge.BridgeError):
                    bridge.validate({**cfg,field:value})
        with self.assertRaises(bridge.BridgeError):
            bridge.Bridge(cfg,"bad",confirmed=True)
        with self.assertRaises(bridge.BridgeError):
            bridge.Bridge(cfg,bridge.binding(cfg))
        self.assertNotEqual(bridge.binding(cfg),bridge.binding({**cfg,"target_version":"2"}))

    def test_bridge_budget_and_no_extra_input(self):
        cfg=config()
        app=bridge.Bridge(cfg,bridge.binding(cfg),confirmed=True)
        auth="Bearer "+"a"*64
        raw=json.dumps({"prompt":"Synthetic question"}).encode()
        with patch.object(bridge,"_upstream",return_value="Synthetic reply") as call:
            for path,token,body in ((app.path,"bad",raw),("/arbitrary",auth,raw),
                                    (app.path,auth,b'{"prompt":"text","reference":"hidden"}'),
                                    (app.path,auth,b'{"prompt":"a","prompt":"b"}')):
                with self.assertRaises(bridge.BridgeError):
                    app.request(path,token,body)
            self.assertEqual(call.call_count,0)
            self.assertEqual(app.request(app.path,auth,raw),{"answer":"Synthetic reply"})
            sent=json.loads(call.call_args.args[1])
            self.assertEqual(sent,{"model":"approved-model","stream":False,"input":"Synthetic question"})
            call.side_effect=bridge.BridgeError("failure")
            for _ in range(2):
                with self.assertRaises(bridge.BridgeError):
                    app.request(app.path,auth,raw)
            with self.assertRaises(bridge.BridgeError):
                app.request(app.path,auth,raw)
            self.assertEqual(call.call_count,3)

    def test_real_loopback_existing_adapters(self):
        for modality in ("single_turn","conversation"):
            cfg=config(modality)
            app=bridge.Bridge(cfg,bridge.binding(cfg),confirmed=True)
            srv=bridge.server(app)
            worker=threading.Thread(target=srv.serve_forever,daemon=True)
            worker.start()
            try:
                with patch.object(bridge,"_upstream",return_value="Synthetic reply") as call:
                    doc=bridge.connector(cfg)
                    if modality=="single_turn":
                        self.assertEqual(buyer_connector._call(doc,"Synthetic prompt",adapters.expand_env(doc["headers"])),"Synthetic reply")
                        expected="Synthetic prompt"
                    else:
                        spec=adapters.AdapterSpec("test","http_conversation",url=doc["url"],
                             headers=doc["headers"],timeout=5,local_only=True)
                        adapter=adapters.build_conversation_adapter(spec)
                        hist=[{"role":"patient","text":"Synthetic question"},{"role":"system","text":"Synthetic answer"},
                              {"role":"patient","text":"Synthetic followup"}]
                        self.assertEqual(adapter(hist),"Synthetic reply")
                        expected=[{"role":"user","content":"Synthetic question"},{"role":"assistant","content":"Synthetic answer"},
                                  {"role":"user","content":"Synthetic followup"}]
                    self.assertEqual(json.loads(call.call_args.args[1])[cfg["upstream_input_field"]],expected)
            finally:
                srv.shutdown()
                srv.server_close()
                worker.join(5)

    def test_tls_pinned_connection_and_no_redirect(self):
        cfg=config()
        with patch.object(bridge.socket,"socket") as socket_factory,patch.object(bridge.ssl,"create_default_context") as tls:
            sock=socket_factory.return_value
            context=tls.return_value
            conn=bridge._PinnedHTTPS("api.example.com","8.8.8.8",5)
            conn.connect()
            sock.connect.assert_called_once_with(("8.8.8.8",443))
            context.wrap_socket.assert_called_once_with(sock,server_hostname="api.example.com")
        with patch.object(bridge,"_PinnedHTTPS") as client:
            response=client.return_value.getresponse.return_value
            response.status=302
            with self.assertRaises(bridge.BridgeError):
                bridge._upstream(cfg,b"{}","secret")
            client.return_value.request.assert_called_once()
            client.return_value.close.assert_called_once()
            response.read.assert_not_called()
        with patch.object(bridge,"_PinnedHTTPS") as client:
            response=client.return_value.getresponse.return_value
            response.status=200
            response.getheader.side_effect=lambda k,default=None: {"Content-Type":"application/json","Content-Encoding":"identity"}.get(k,default)
            response.read.return_value=b'{"choices":[{"message":{"content":"answer"}}],"other":"metadata"}'
            self.assertEqual(bridge._upstream(cfg,b"{}","secret"),"answer")
            client.return_value.request.assert_called_once_with("POST","/v1/evaluate",body=b"{}",
                headers={"Content-Type":"application/json","Accept":"application/json","Accept-Encoding":"identity","Authorization":"Bearer secret"})


if __name__=="__main__":
    unittest.main()
