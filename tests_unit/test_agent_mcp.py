"""Protocol and aggregate-only export regression tests."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer, buyer_plan, agent_snapshot as snap, agent_mcp as mcp


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"buyer"
        buyer.init(self.root,"Private organisation","Confidential intended use","ED","SE")
        buyer.add_vendor(self.root,"vendor","Confidential vendor")
        self.file=self.root.parent/"approved.json"
        self.result=snap.export(self.root,self.file)
        self.envelope=snap.load(self.file)

    def test_export_excludes_identity_and_free_text(self):
        raw=self.file.read_text()
        for value in ("Private organisation","Confidential intended use","Confidential vendor",str(self.root)):
            self.assertNotIn(value,raw)
        self.assertEqual(self.envelope["payload"]["procurement_inventory"]["candidate_count"],1)
        self.assertFalse(self.envelope["payload"]["decision_grade"])
        with self.assertRaises(FileExistsError):
            snap.export(self.root,self.file)

    def test_strict_schema_even_if_digest_recomputed(self):
        payload=self.envelope["payload"]
        payload["procurement_inventory"]["patient_name"]="private"
        self.file.write_text(json.dumps({"payload":payload,"digest":buyer_plan._digest(payload)}))
        with self.assertRaises(buyer.BuyerError):
            snap.load(self.file)

    def test_digest_mismatch_rejected(self):
        self.envelope["payload"]["procurement_inventory"]["candidate_count"]=9
        self.file.write_text(json.dumps(self.envelope))
        with self.assertRaises(buyer.BuyerError):
            snap.load(self.file)

    def test_stdio_protocol_and_fixed_tool_scope(self):
        reqs=[
            {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1"}}},
            {"jsonrpc":"2.0","method":"notifications/initialized"},
            {"jsonrpc":"2.0","id":2,"method":"tools/list"},
            {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"clinical_eval_procurement_inventory","arguments":{}}},
            {"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"clinical_eval_procurement_inventory","arguments":{"path":"/etc/passwd"}}},
            {"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"run_shell","arguments":{}}},
        ]
        inp=io.BytesIO(b"".join(json.dumps(r).encode()+b"\n" for r in reqs))
        out=io.BytesIO()
        with patch("urllib.request.urlopen",side_effect=AssertionError("network forbidden")):
            mcp.serve(self.file,inp,out)
        result={r["id"]:r for r in map(json.loads,out.getvalue().splitlines())}
        self.assertEqual(set(result),{1,2,3,4,5})
        self.assertEqual(len(result[2]["result"]["tools"]),4)
        payload=json.loads(result[3]["result"]["content"][0]["text"])
        self.assertEqual(payload["data"]["candidate_count"],1)
        self.assertFalse(payload["live"])
        self.assertFalse(payload["decision_grade"])
        self.assertIn("error",result[4])
        self.assertIn("error",result[5])

    def test_handshake_and_bad_messages(self):
        s=mcp.Session(self.envelope)
        self.assertIn("error",s.handle({"jsonrpc":"2.0","id":1,"method":"tools/list"}))
        self.assertIn("error",s.handle([]))
        self.assertIsNone(s.handle({"jsonrpc":"2.0","method":"notifications/initialized"}))
        result=s.handle({"jsonrpc":"2.0","id":2,"method":"initialize","params":{"protocolVersion":"unknown"}})
        self.assertIn(result["result"]["protocolVersion"],mcp.PROTOCOLS)
        self.assertIn("error",s.handle({"jsonrpc":"2.0","id":3,"method":"tools/list"}))
        s.handle({"jsonrpc":"2.0","method":"notifications/initialized"})
        self.assertIn("result",s.handle({"jsonrpc":"2.0","id":4,"method":"tools/list"}))


if __name__=="__main__":
    unittest.main()
