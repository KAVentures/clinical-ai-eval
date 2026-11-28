import json
import sqlite3
import unittest
from caeval import monitor_grant_policy as policy


class GrantPolicyTests(unittest.TestCase):
    def grant(self):
        return {'monitor':'m','scopes':'["check"]','expires':1000,'revoked_seq':None,'token_hash':'secret-hash','owner':'private-owner'}

    def check(self, grant=None, now=900, minimum=100, actual='a'*64):
        return policy.check_polling_grant(self.grant() if grant is None else grant,'m','a'*64,actual,
                                         now=now,minimum_remaining=minimum)

    def test_exact_scope_and_no_secrets(self):
        result=self.check()
        self.assertEqual(result['remaining_seconds'],100)
        self.assertFalse(result['live_http_checked'])
        self.assertNotIn('secret-hash',json.dumps(result))
        self.assertNotIn('private-owner',json.dumps(result))

    def test_wrong_monitor_plan_or_overbroad_scope(self):
        for change in ({'monitor':'other'},{'scopes':'["check","report"]'},{'scopes':'["incident"]'},{'revoked_seq':1}):
            with self.subTest(change=change),self.assertRaises(ValueError): self.check({**self.grant(),**change})
        with self.assertRaises(ValueError): self.check(actual='b'*64)

    def test_expiry_boundary_and_invalid_policy(self):
        for now,minimum in ((901,100),(1000,1),(900,0),(900,True),(900,86401)):
            with self.subTest(now=now,minimum=minimum),self.assertRaises(ValueError):self.check(now=now,minimum=minimum)

    def test_actual_audit_time_boundary(self):
        with sqlite3.connect(':memory:') as conn:
            conn.execute('CREATE TABLE audit(seq INTEGER PRIMARY KEY, at INTEGER)')
            conn.executemany('INSERT INTO audit VALUES(?,?)',[(1,999),(2,1000),(3,1001)])
            policy.assert_receipt_lifetime(conn,1,1000)
            for seq in (2,3,4):
                with self.assertRaises(ValueError): policy.assert_receipt_lifetime(conn,seq,1000)

    def test_expired_audit_rolls_back_entire_transaction(self):
        with sqlite3.connect(':memory:') as conn:
            conn.execute('CREATE TABLE audit(seq INTEGER PRIMARY KEY, at INTEGER)')
            conn.execute('CREATE TABLE events(id INTEGER)')
            with self.assertRaises(ValueError):
                with conn:
                    conn.execute('INSERT INTO events VALUES(1)')
                    conn.execute('INSERT INTO audit VALUES(1,1000)')
                    policy.assert_receipt_lifetime(conn,1,1000)
            self.assertEqual(conn.execute('SELECT count(*) FROM events').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT count(*) FROM audit').fetchone()[0],0)

if __name__=='__main__': unittest.main()
