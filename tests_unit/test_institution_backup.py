"""Offline workspace checkpoint and recovery, no clinical claim elevation."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from caeval import buyer_agent as buyer, buyer_plan, institution_access as access
from caeval import institution_backup as backup, procurement_workflow as procurement


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.root=self.base/"workspace"
        buyer.init(self.root,"Test hospital","Decision support","ED","SE")
        buyer.add_vendor(self.root,"candidate","Candidate")
        self.doc=self.base/"evidence.txt"
        self.doc.write_text("Synthetic evidence")
        self.evidence=buyer.add_evidence(self.root,"candidate","security",str(self.doc))
        self.destination=self.base/"checkpoint"
        self.restored=self.base/"restored"

    def create(self):
        return backup.create(self.root,self.destination,services_stopped=True)

    def test_roundtrip_and_session_revocation(self):
        access.provision(self.root,"operator","operator","Synthetic test password!")
        old=access.login(self.root,"operator","Synthetic test password!")
        out=self.create()
        self.assertEqual(out,backup.check(self.destination))
        self.assertEqual(out["integrity"],"verified")
        result=backup.restore(self.destination,self.restored,services_stopped=True)
        self.assertTrue(result["sessions_revoked"])
        self.assertFalse(result["decision_grade"])
        self.assertEqual(buyer.load(self.root),buyer.load(self.restored))
        self.assertEqual(buyer.status(self.restored)["vendors"][0]["documents"][0]["integrity"],"intact")
        with self.assertRaises(access.AccessDenied):
            access.session(self.restored,old)
        new=access.login(self.restored,"operator","Synthetic test password!")
        self.assertEqual(access.session(self.restored,new)["user_id"],"operator")
        # Backup remains immutable, and the original service's session is not revoked.
        self.assertEqual(backup.check(self.destination),out)
        self.assertEqual(access.session(self.root,old)["user_id"],"operator")

    def test_explicit_confirmation_and_no_overwrite(self):
        with self.assertRaises(buyer.BuyerError):
            backup.create(self.root,self.destination)
        self.assertFalse(self.destination.exists())
        with self.assertRaises(buyer.BuyerError):
            backup.create(self.root,self.root/"inside",services_stopped=True)
        self.create()
        with self.assertRaises(FileExistsError):
            self.create()
        with self.assertRaises(buyer.BuyerError):
            backup.restore(self.destination,self.restored)
        self.restored.mkdir()
        with self.assertRaises(FileExistsError):
            backup.restore(self.destination,self.restored,services_stopped=True)

    def test_tampered_content_and_inventory_rejected(self):
        self.create()
        data=self.destination/"workspace"/"buyer.json"
        data.write_text("{}")
        with self.assertRaises(buyer.BuyerError):
            backup.check(self.destination)
        with self.assertRaises(buyer.BuyerError):
            backup.restore(self.destination,self.restored,services_stopped=True)
        self.assertFalse(self.restored.exists())

    def test_rehashed_traversal_manifest_rejected(self):
        self.create()
        path=self.destination/backup.MANIFEST
        doc=json.loads(path.read_text())
        doc["files"]["../outside"]={"bytes":0,"sha256":"0"*64}
        doc["backup_id"]=buyer_plan._digest({k:v for k,v in doc.items() if k!="backup_id"})
        path.write_text(json.dumps(doc))
        with self.assertRaises(buyer.BuyerError):
            backup.check(self.destination)

    def test_running_jobs_block_backup(self):
        p=self.root/"evaluation-jobs"
        p.mkdir()
        conn=sqlite3.connect(p/"jobs.sqlite3")
        try:
            conn.execute("CREATE TABLE jobs(status TEXT)")
            conn.execute("INSERT INTO jobs VALUES('running')")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(buyer.BuyerError):
            self.create()
        self.assertFalse(self.destination.exists())

    def test_links_and_transient_files_refused(self):
        p=self.root/"link"
        try:
            p.symlink_to(self.doc)
        except (OSError,NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(buyer.BuyerError):
            self.create()
        p.unlink()
        (self.root/"orphan.sqlite3-wal").write_bytes(b"stale")
        with self.assertRaises(buyer.BuyerError):
            self.create()

    def test_relocate_internal_package_reference_without_touching_original(self):
        study=self.root/"assessment"
        st=procurement.init(study,"Study",["missing_information"],{"content_hash":"pack"},
                "panel.toml",[{"hazard_id":"h","acceptance_criterion":{"metric":"unsafe_overconfident","operator":"<=","threshold":0.1}}])
        procurement.add_vendor(study,"candidate",{"name":"Candidate","version":"1"},"Product A")
        pkg=study/"packages"/"run"
        pkg.mkdir(parents=True)
        state=procurement.load(study)
        state["results"]={"candidate":{"missing_information":{"ingestion":"verified_package","package":{"workspace":str(pkg)}}}}
        procurement.save(study,state)
        out=self.create()
        backup.restore(self.destination,self.restored,services_stopped=True)
        new=procurement.load(self.restored/"assessment")
        self.assertEqual(new["results"]["candidate"]["missing_information"]["package"]["workspace"],"assessment/packages/run")
        self.assertEqual(procurement.load(study),state)
        self.assertEqual(backup.check(self.destination),out)

    def test_external_package_refused(self):
        study=self.root/"assessment"
        procurement.init(study,"Study",["missing_information"],{"content_hash":"pack"},"panel.toml",[])
        procurement.add_vendor(study,"candidate",{"name":"Candidate","version":"1"},"Product A")
        state=procurement.load(study)
        state["results"]={"candidate":{"missing_information":{"ingestion":"verified_package","package":{"workspace":str(self.base)}}}}
        procurement.save(study,state)
        with self.assertRaises(buyer.BuyerError):
            self.create()


if __name__=="__main__":
    unittest.main()
