import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch
from caeval import monitor_grant_capacity as c


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.addCleanup(self.conn.close)
        self.conn.row_factory=sqlite3.Row
        self.conn.execute('CREATE TABLE lifecycle_ingestion_grants(id TEXT PRIMARY KEY, expires INTEGER, revoked_seq INTEGER, audit_seq INTEGER)')

    def add(self,ident,expires,revoked=None):
        self.conn.execute('INSERT INTO lifecycle_ingestion_grants VALUES(?,?,?,?)',(str(ident),expires,revoked,ident))

    def test_expired_and_revoked_do_not_consume_live_capacity(self):
        for i in range(201): self.add(i,99)
        self.add(201,1000,1);self.add(202,101)
        status=c.require_available(self.conn,100)
        self.assertEqual((status['total'],status['active'],status['expired'],status['revoked']),(203,1,201,1))
        self.assertTrue(status['history_retained'])

    def test_active_cap_and_exact_expiry_boundary(self):
        for i in range(c.MAX_ACTIVE): self.add(i,101)
        with self.assertRaises(ValueError): c.require_available(self.conn,100)
        self.assertTrue(c.require_available(self.conn,101)['issuance_available'])
        self.assertEqual(c.capacity(self.conn,101)['total'],c.MAX_ACTIVE)

    def test_history_cap_remains_fail_closed(self):
        with patch.object(c,'MAX_HISTORY',3):
            for i in range(3): self.add(i,99)
            self.assertFalse(c.capacity(self.conn,100)['issuance_available'])
            with self.assertRaises(ValueError): c.require_available(self.conn,100)

    def test_all_active_and_bounded_recent_history(self):
        for i in range(150): self.add(i,99)
        self.add(151,101);self.add(152,102);self.add(153,1000,2)
        status,rows=c.visible_rows(self.conn,100)
        self.assertEqual(len(rows),102)
        self.assertEqual([r['id'] for r in rows[:3]],['152','151','153'])
        self.assertEqual(status['total'],153)
        self.assertEqual(c.capacity(self.conn,100),status)

    def test_invalid_clock_and_oversized_inventory(self):
        for now in (True,-1,1.1):
            with self.assertRaises(ValueError): c.capacity(self.conn,now)
        with patch.object(c,'MAX_ACTIVE',1):
            self.add(1,101);self.add(2,101)
            with self.assertRaises(ValueError): c.visible_rows(self.conn,100)


if __name__=='__main__': unittest.main()
