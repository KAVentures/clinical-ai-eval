"""Real loopback collection, frozen raw responses and stopped-service recovery."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from caeval import (buyer_agent as buyer, institution_access as access,
                    institution_research as studio, research_collection as collect,
                    institution_backup as backup)
from tests_unit.test_scribe_assessment import fixture
from tests_unit.test_robustness_assessment import spec as robustness_spec


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.root=self.base/"workspace"
        buyer.init(self.root,"Fictional hospital")
        for uid,role in (("operator","operator"),("other","operator"),("admin","admin")):
            access.provision(self.root,uid,role,"Fictional password 123")
        self.actor={uid:access.session(self.root,access.login(self.root,uid,"Fictional password 123"))
                    for uid in ("operator","other","admin")}
        self.calls=[]
        self.fail=False
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                outer.calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                if outer.fail:
                    self.send_error(503)
                    return
                raw=json.dumps({"answer":"Patient reports headache."}).encode()
                self.send_response(200)
                self.send_header("Content-Length",str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self,*args):
                pass
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        (self.root/"approved-connectors").mkdir()

    def create(self,kind,spec):
        target=spec["target"]
        cfg={"vendor_id":"fictional","target_name":target["name"],"target_version":target["version"],
             "url":"http://127.0.0.1:"+str(self.server.server_port)+"/evaluate",
             "prompt_field":"prompt","answer_path":"answer","headers":{},"timeout_seconds":2}
        self.config=self.root/"approved-connectors"/"local.json"
        self.config.write_text(json.dumps(cfg))
        ident=studio.create(self.root,self.actor["operator"],kind,spec)
        plan=studio.download(self.root,self.actor["operator"],ident,"plan")
        return ident,plan

    def queue(self,ident,plan):
        return collect.queue(self.root,self.actor["operator"],ident,"local",plan["plan_hash"],confirmed=True)

    def test_real_loopback_scribe_exact_raw_note_and_annotation(self):
        spec,_,_=fixture()
        ident,plan=self.create("scribe",spec)
        digest=self.queue(ident,plan)
        self.assertEqual(self.calls,[])
        with self.assertRaises(access.AccessDenied):
            collect.execute(self.root,ident,digest)
        with self.assertRaises(access.AccessDenied):
            collect.execute(self.root,ident,"wrong",allow_candidate_calls=True)
        self.assertEqual(self.calls,[])
        result=collect.execute(self.root,ident,digest,allow_candidate_calls=True)
        self.assertEqual(result["counts"]["complete"],1)
        self.assertEqual(len(self.calls),1)
        self.assertNotIn("reference_facts",self.calls[0]["prompt"])
        with self.assertRaises(access.AccessDenied):
            collect.execute(self.root,ident,digest,allow_candidate_calls=True)
        draft=studio.download(self.root,self.actor["operator"],ident,"responses")
        row=draft["responses"][0]
        self.assertEqual(row["note"],"Patient reports headache.")
        bad=json.loads(json.dumps(draft))
        bad["responses"][0]["note"]="Edited answer"
        with self.assertRaises(access.AccessDenied):
            studio.collect(self.root,self.actor["operator"],ident,plan["plan_hash"],bad,confirmed=True)
        row["claims"]=[{"claim_id":"symptom","start":0,"end":len(row["note"]),"quote":row["note"]}]
        studio.collect(self.root,self.actor["operator"],ident,plan["plan_hash"],draft,confirmed=True)
        with self.assertRaises(access.AccessDenied):
            studio.download(self.root,self.actor["other"],ident,"collection-receipt")
        receipt=studio.download(self.root,self.actor["operator"],ident,"collection-receipt")
        self.assertEqual(receipt["outcomes"][0]["answer"],row["note"])
        self.assertEqual(len(self.calls),1)

    def test_failure_circuit_breaker_and_complete_denominator(self):
        ident,plan=self.create("robustness",robustness_spec())
        digest=self.queue(ident,plan)
        self.fail=True
        result=collect.execute(self.root,ident,digest,allow_candidate_calls=True)
        self.assertEqual(len(self.calls),3)
        self.assertEqual(result["counts"]["provider_error"],3)
        self.assertEqual(result["counts"]["not_attempted"],len(plan["arms"])-3)
        draft=studio.download(self.root,self.actor["operator"],ident,"responses")
        self.assertEqual(len(draft["responses"]),len(plan["arms"]))
        self.assertTrue(all(r["status"]=="provider_error" and not r["answer"] for r in draft["responses"]))

    def test_connector_drift_cancel_and_restore(self):
        spec,_,_=fixture()
        ident,plan=self.create("scribe",spec)
        digest=self.queue(ident,plan)
        cfg=json.loads(self.config.read_text())
        cfg["timeout_seconds"]=3
        self.config.write_text(json.dumps(cfg))
        with self.assertRaises(access.AccessDenied):
            collect.execute(self.root,ident,digest,allow_candidate_calls=True)
        self.assertEqual(self.calls,[])
        checkpoint=self.base/"backup"
        restored=self.base/"restored"
        backup.create(self.root,checkpoint,services_stopped=True)
        backup.restore(checkpoint,restored,services_stopped=True)
        with access.db(restored) as conn:
            self.assertEqual(conn.execute("SELECT state FROM research_collections").fetchone()[0],"cancelled")
            self.assertEqual(conn.execute("SELECT state FROM research_studies").fetchone()[0],"collection_cancelled")
        collect.cancel(self.root,self.actor["operator"],ident)
        with self.assertRaises(access.AccessDenied):
            collect.execute(self.root,ident,digest,allow_candidate_calls=True)

    def test_running_checkpoint_refused_until_abandoned(self):
        spec,_,_=fixture()
        ident,plan=self.create("scribe",spec)
        self.queue(ident,plan)
        with access.db(self.root) as conn:
            conn.execute("UPDATE research_collections SET state='running' WHERE study=?",(ident,))
        with self.assertRaises(buyer.BuyerError):
            backup.create(self.root,self.base/"blocked",services_stopped=True)
        with self.assertRaises(access.AccessDenied):
            collect.abandon(self.root,ident)
        collect.abandon(self.root,ident,worker_stopped=True)
        self.assertEqual(studio.overview(self.root,self.actor["operator"])[0]["state"],"collection_interrupted")


if __name__=="__main__":
    unittest.main()
