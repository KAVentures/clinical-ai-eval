"""Controlled connector collection and transport restrictions."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer, buyer_connector as connector, buyer_manual as manual, adapters


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"buyer"
        buyer.init(self.root,"Pilot","CDS","ED","SE")
        self.exchange="a"*64
        self.record={"exchange_id":self.exchange,"vendor_id":"a","target":{"name":"Candidate","version":"1"},
                     "slots":{"cell1":{"prompt":"Synthetic case 1"},"cell2":{"prompt":"Synthetic case 2"}}}
        self.cfg={"vendor_id":"a","target_name":"Candidate","target_version":"1",
                  "url":"http://127.0.0.1:8769/evaluate","prompt_field":"prompt",
                  "answer_path":"answer","headers":{},"timeout_seconds":5}
        folder=self.root/"approved-connectors"
        folder.mkdir()
        self.file=folder/"local.json"
        self.file.write_text(json.dumps(self.cfg))
        self.stub=patch.object(manual,"_load",return_value=self.record)
        self.stub.start()
        self.addCleanup(self.stub.stop)

    def queue(self):
        return connector.queue(self.root,self.exchange,"local")

    def test_queue_never_calls_endpoint_and_requires_exact_approval(self):
        with patch.object(connector,"_call") as call:
            row=self.queue()
            self.assertEqual(row["state"],"queued")
            with self.assertRaises(buyer.BuyerError):
                connector.execute(self.root,row["id"],"wrong","operator")
            call.assert_not_called()
        self.assertEqual(connector.cancel(self.root,row["id"])["state"],"cancelled")
        with self.assertRaises(buyer.BuyerError):
            connector.execute(self.root,row["id"],row["binding_hash"],"operator")

    def test_complete_collection_uses_canonical_manual_handoff(self):
        row=self.queue()
        with patch.object(connector,"_call",side_effect=["Answer one","Answer two"]) as call:
            result=connector.execute(self.root,row["id"],row["binding_hash"],"operator")
        self.assertEqual(result["state"],"complete_unscored")
        self.assertEqual(result["receipt"]["n_provided"],2)
        self.assertEqual(result["receipt"]["n_attempted"],2)
        self.assertEqual({c.args[1] for c in call.call_args_list},{"Synthetic case 1","Synthetic case 2"})
        mapping=manual.responses(self.root,result["receipt"]["submission_id"])
        self.assertEqual(set(mapping.values()),{"Answer one","Answer two"})
        with self.assertRaises(buyer.BuyerError):
            connector.execute(self.root,row["id"],row["binding_hash"],"operator")

    def test_transport_error_is_missing_not_safe(self):
        row=self.queue()
        with patch.object(connector,"_call",side_effect=[adapters.AdapterError("failure"),"Answer"]):
            result=connector.execute(self.root,row["id"],row["binding_hash"],"operator")
        self.assertEqual(result["state"],"incomplete")
        self.assertEqual(result["receipt"]["n_failed"],1)
        with self.assertRaises(buyer.BuyerError):
            manual.responses(self.root,result["receipt"]["submission_id"])

    def test_changed_config_or_version_blocks_execution(self):
        row=self.queue()
        self.file.write_text(json.dumps({**self.cfg,"timeout_seconds":6}))
        with patch.object(connector,"_call") as call:
            with self.assertRaises(buyer.BuyerError):
                connector.execute(self.root,row["id"],row["binding_hash"],"operator")
            call.assert_not_called()
        self.file.write_text(json.dumps({**self.cfg,"target_version":"2"}))
        with self.assertRaises(buyer.BuyerError):
            self.queue()

    def test_only_literal_loopback_and_environment_credentials(self):
        for url in ("https://example.org/test","http://localhost:80/test",
                    "http://127.0.0.1:80@evil.example/test","http://127.0.0.1:80/a?key=secret",
                    "http://127.0.0.1:80/../test","http://127.0.0.2:80/test"):
            with self.subTest(url=url):
                with self.assertRaises(buyer.BuyerError):
                    connector._endpoint(url)
        self.file.write_text(json.dumps({**self.cfg,"headers":{"Authorization":"literal-secret"}}))
        with self.assertRaises(buyer.BuyerError):
            self.queue()

    def test_standard_protocol_uses_same_canonical_handoff(self):
        from caeval import candidate_protocol as protocol
        cfg={k:v for k,v in self.cfg.items() if k not in ("prompt_field","answer_path")}
        cfg["protocol"]=protocol.PROTOCOL
        self.file.write_text(json.dumps(cfg))
        row=self.queue()
        with patch.object(connector,"_call",return_value="Fictional answer"):
            result=connector.execute(self.root,row["id"],row["binding_hash"],"operator")
        self.assertEqual(result["state"],"complete_unscored")
        self.assertEqual(result["receipt"]["n_provided"],2)
        self.assertEqual(connector.connectors(self.root)[0]["protocol"],protocol.PROTOCOL)
        self.file.write_text(json.dumps({**cfg,"protocol":"unknown"}))
        with self.assertRaises(buyer.BuyerError):
            self.queue()

    def test_redirect_refused_and_receipt_tamper_detected(self):
        with self.assertRaises(adapters.AdapterError):
            connector._NoRedirect().redirect_request(None,None,302,"",{},"https://example.org/")
        row=self.queue()
        with patch.object(connector,"_call",return_value="Answer"):
            result=connector.execute(self.root,row["id"],row["binding_hash"],"operator")
        self.assertEqual(result["state"],"complete_unscored")
        with connector._db(self.root) as db:
            db.execute("UPDATE collections SET receipt='{}' WHERE id=?",(row["id"],))
        self.assertEqual(connector.status(self.root,row["id"])["state"],"invalid")


if __name__=="__main__":
    unittest.main()
