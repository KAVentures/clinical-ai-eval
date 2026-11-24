"""Live scoped agent metadata through a real Unix socket, not a workspace mount."""
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from caeval import agent_grants as grants, agent_live as live, agent_snapshot
from caeval import buyer_agent as buyer, institution_access as access

INVENTORY="clinical_eval_procurement_inventory"
JOBS="clinical_eval_job_summary"


class GrantTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.root=self.base/"workspace"
        buyer.init(self.root,"Private Hospital","Private intended use","ED","SE")
        buyer.add_vendor(self.root,"candidate","Private Candidate")
        for user,role in (("operator","operator"),("reviewer","reviewer"),("observer","observer")):
            access.provision(self.root,user,role,"Synthetic test password!")
        self.epoch="a"*64

    def issue(self,**changes):
        return grants.issue(self.root,**dict({"owner":"operator","scopes":[INVENTORY],"ttl":60,
                    "calls":3,"generation":self.epoch,"confirmed":True},**changes))

    def test_explicit_scope_budget_and_live_refresh(self):
        g=self.issue(calls=2)
        token=g["token"]
        self.assertEqual(grants.permitted(self.root,token,self.epoch),[INVENTORY])
        with self.assertRaises(access.AccessDenied):
            grants.query(self.root,token,self.epoch,JOBS)
        first=grants.query(self.root,token,self.epoch,INVENTORY)
        self.assertEqual(first["data"]["candidate_count"],1)
        self.assertTrue(first["live"])
        self.assertFalse(first["decision_grade"])
        self.assertNotIn("Private",json.dumps(first))
        buyer.add_vendor(self.root,"second","Private second candidate")
        second=grants.query(self.root,token,self.epoch,INVENTORY)
        self.assertEqual(second["data"]["candidate_count"],2)
        self.assertNotEqual(first["view_digest"],second["view_digest"])
        with self.assertRaises(access.AccessDenied):
            grants.query(self.root,token,self.epoch,INVENTORY)
        self.assertNotIn(token,json.dumps(grants.inventory(self.root)))
        with access.db(self.root) as conn:
            self.assertNotIn(token,str([dict(r) for r in conn.execute("SELECT * FROM agent_grants")]))
            self.assertNotIn("Private",str([dict(r) for r in conn.execute("SELECT * FROM audit")]))

    def test_invalid_issuance_and_expiration(self):
        for changes in ({"confirmed":False},{"owner":"reviewer"},{"scopes":["shell"]},
                        {"scopes":[INVENTORY,INVENTORY]},{"ttl":86401},{"calls":0}):
            with self.subTest(changes=changes),self.assertRaises(access.AccessDenied):
                self.issue(**changes)
        g=self.issue(ttl=1)
        with patch.object(grants.time,"time",return_value=g["expires"]):
            with self.assertRaises(access.AccessDenied):
                grants.permitted(self.root,g["token"],self.epoch)

    def test_revoke_owner_disable_and_generation_rotation(self):
        g=self.issue()
        with self.assertRaises(access.AccessDenied):
            grants.permitted(self.root,g["token"],"b"*64)
        grants.revoke(self.root,g["grant_id"])
        with self.assertRaises(access.AccessDenied):
            grants.query(self.root,g["token"],self.epoch,INVENTORY)
        g=self.issue()
        access.disable(self.root,"operator")
        with self.assertRaises(access.AccessDenied):
            grants.permitted(self.root,g["token"],self.epoch)

    def test_revocation_during_read_refuses_output(self):
        g=self.issue()
        original=agent_snapshot.build
        def revoke(directory):
            result=original(directory)
            grants.revoke(directory,g["grant_id"])
            return result
        with patch.object(agent_snapshot,"build",side_effect=revoke):
            with self.assertRaises(access.AccessDenied):
                grants.query(self.root,g["token"],self.epoch,INVENTORY)

    def test_schema_rejects_injected_clinical_text(self):
        g=self.issue()
        original=agent_snapshot.build
        def injected(directory):
            result=original(directory)
            result["payload"]["procurement_inventory"]["case_text"]="Private clinical text"
            return result
        with patch.object(agent_snapshot,"build",side_effect=injected):
            with self.assertRaises(access.AccessDenied):
                grants.query(self.root,g["token"],self.epoch,INVENTORY)

    @unittest.skipUnless(os.name=="posix" and hasattr(socket,"AF_UNIX"),"POSIX broker required")
    def test_real_socket_mcp_scope_revocation_and_protocol(self):
        g=self.issue()
        path=self.base/"broker.sock"
        server=live.Server(str(path),live.Handler)
        server.directory=self.root
        server.generation=self.epoch
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        def close():
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
        self.addCleanup(close)
        session=live.LiveSession(path,g["token"])
        self.assertIn("error",session.handle([]))
        self.assertIn("error",session.handle({"jsonrpc":"2.0","id":1,"method":"tools/list"}))
        init=session.handle({"jsonrpc":"2.0","id":2,"method":"initialize","params":{"protocolVersion":"2025-03-26"}})
        self.assertEqual(init["result"]["serverInfo"]["name"],"clinical-ai-eval-live-metadata")
        session.handle({"jsonrpc":"2.0","method":"notifications/initialized"})
        listing=session.handle({"jsonrpc":"2.0","id":3,"method":"tools/list"})
        self.assertEqual([t["name"] for t in listing["result"]["tools"]],[INVENTORY])
        def call(name,arguments=None):
            return session.handle({"jsonrpc":"2.0","id":4,"method":"tools/call",
                    "params":{"name":name,"arguments":arguments or {}}})
        self.assertIn("error",call(JOBS))
        self.assertIn("error",call(INVENTORY,{"path":"/etc/passwd"}))
        result=json.loads(call(INVENTORY)["result"]["content"][0]["text"])
        self.assertEqual(result["data"]["candidate_count"],1)
        grants.revoke(self.root,g["grant_id"])
        self.assertIn("error",call(INVENTORY))
        self.assertIn("error",session.handle({"jsonrpc":"2.0","id":5,"method":"tools/list"}))

    def test_protected_files_and_workspace_boundary(self):
        key=self.base/"token"
        g=self.issue()
        grants.write_private(key,g["token"])
        self.assertEqual(grants.read_private(key,grants.TOKEN),g["token"])
        with self.assertRaises(FileExistsError):
            grants.write_private(key,g["token"])
        with self.assertRaises(access.AccessDenied):
            grants.outside_workspace(self.root,self.root/"token")
        if os.name=="posix":
            key.chmod(0o644)
            with self.assertRaises(access.AccessDenied):
                grants.read_private(key,grants.TOKEN)


if __name__=="__main__":
    unittest.main()
