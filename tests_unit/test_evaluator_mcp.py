"""Exercise the scoped MCP client against an actual local WSGI service."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from wsgiref.simple_server import make_server

from caeval import buyer_agent as buyer, institution_access as access, evaluator_service as api
from caeval import evaluator_mcp as mcp


class MCPTests(unittest.TestCase):
    def test_real_local_service_and_mcp_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"workspace"
            buyer.init(root,"Fictional assessment")
            access.provision(root,"operator","operator","Synthetic test password")
            with patch.object(api.assessment,"check",return_value={}):
                grant=api.issue(root,"operator","a"*64,3600,confirmed=True)
            with make_server("127.0.0.1",0,api.App(root),handler_class=api.QuietHandler) as server:
                thread=threading.Thread(target=server.serve_forever,daemon=True)
                thread.start()
                try:
                    client=mcp.Client(server.server_port,grant["token"])
                    session=mcp.Session(client)
                    def call(method,params=None):
                        return session.handle({"jsonrpc":"2.0","id":1,"method":method,"params":params or {}})
                    self.assertIn("error",call("tools/list"))
                    self.assertIn("result",call("initialize",{"protocolVersion":"2025-03-26"}))
                    session.handle({"jsonrpc":"2.0","method":"notifications/initialized"})
                    tools=call("tools/list")["result"]["tools"]
                    self.assertEqual(len(tools),5)
                    self.assertFalse(next(t for t in tools if t["name"]=="request_evaluation")["annotations"]["readOnlyHint"])
                    with patch.object(api.jobs,"execute",side_effect=AssertionError("must not execute")):
                        req=client.call("request_evaluation",{"idempotency_key":"synthetic-key-0001"})
                        self.assertEqual(req["status"],"awaiting_local_approval")
                        self.assertEqual(client.call("list_evaluation_requests",{})["requests"],[req])
                        self.assertEqual(client.call("evaluation_status",{"request_id":req["request_id"]}),req)
                    self.assertIn("error",call("tools/call",{"name":"execute","arguments":{}}))
                    self.assertIn("error",call("tools/call",{"name":"evaluation_status","arguments":{"request_id":"../other"}}))
                    api.revoke(root,grant["grant_id"])
                    self.assertIn("error",call("tools/list"))
                finally:
                    server.shutdown()
                    thread.join(timeout=5)


if __name__=="__main__":
    unittest.main()
