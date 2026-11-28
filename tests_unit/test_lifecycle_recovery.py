"""Synthetic offline recovery and lifecycle audit linkage regressions."""
import hashlib
import json
import unittest
from pathlib import Path

from caeval import institution_access as access, institution_backup as backup
from caeval import institution_lifecycle_preflight as preflight, monitor_ingestion as api
from tests_unit.test_monitor_ingestion import IngestionTests as Fixture
from tests_unit.test_lifecycle_monitor import report


class LifecycleRecoveryTests(unittest.TestCase):
    setUp=Fixture.setUp
    plan=Fixture.plan
    freeze=Fixture.freeze
    prepare=Fixture.prepare
    batch=Fixture.batch

    def _state(self,result,ledger):
        return next(r["state"] for r in result["ledgers"] if r["ledger"]==ledger)

    def _checkpoint(self):
        destination=Path(self.temp.name)/"checkpoint"
        backup.create(self.root,destination,services_stopped=True)
        return destination

    def test_empty_workspace_preflight_is_read_only(self):
        path=self.root/"institution"/"access.sqlite3"
        original=hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaises(access.AccessDenied):
            preflight.check(self.root)
        result=preflight.check(self.root,services_stopped=True)
        self.assertTrue(result["ok"])
        self.assertFalse(result["source_payloads_replayed"])
        self.assertFalse(result["deployment_authorized"])
        self.assertTrue(all(r["state"]=="not_initialized" for r in result["ledgers"]))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),original)
        self.assertFalse(preflight.check(self.root,services_stopped=True,
                                         expect_restored=True)["ok"])

    def test_restore_revokes_monitor_credentials_and_keeps_receipt_history(self):
        self.prepare()
        request=self.batch(("report",report(self.p)))
        receipt=api.submit(self.root,self.token,request)
        checkpoint=self._checkpoint()
        dest=Path(self.temp.name)/"restored"
        restored=backup.restore(checkpoint,dest,services_stopped=True)
        self.assertTrue(restored["monitoring_ingestion_grants_revoked"])
        result=preflight.check(dest,services_stopped=True,expect_restored=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["restored_authorization_cleared"])
        self.assertEqual(self._state(result,"monitor_ingestion"),"inventory_verified")
        with self.assertRaises(access.AccessDenied):
            api.check(dest,self.token)
        with self.assertRaises(access.AccessDenied):
            api.submit(dest,self.token,request)
        with access.db(dest) as conn:
            row=conn.execute("SELECT payload FROM lifecycle_ingestion_requests").fetchone()
            self.assertEqual(json.loads(row["payload"]),receipt)
            self.assertEqual(api.revoke_restored_grants(conn),0)
        # Restoring a copy does not revoke authorization at the source.
        self.assertEqual(api.submit(self.root,self.token,request),receipt)

    def test_deleted_audit_event_detected_and_restore_refused(self):
        self.prepare()
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM audit WHERE action=?",(api.ISSUE,))
        result=preflight.check(self.root,services_stopped=True)
        self.assertFalse(result["ok"])
        self.assertEqual(self._state(result,"monitor_ingestion"),
                         "inventory_mismatch_or_unavailable")
        checkpoint=self._checkpoint()
        dest=Path(self.temp.name)/"refused"
        with self.assertRaises(access.AccessDenied):
            backup.restore(checkpoint,dest,services_stopped=True)
        self.assertFalse(dest.exists())

    def test_missing_table_with_audit_is_not_uninitialized(self):
        self.prepare()
        with access.db(self.root) as conn:
            conn.execute("DROP TABLE lifecycle_ingestion_requests")
        result=preflight.check(self.root,services_stopped=True)
        self.assertFalse(result["ok"])
        self.assertEqual(self._state(result,"monitor_ingestion"),"incomplete_schema")


if __name__=="__main__":
    unittest.main()
