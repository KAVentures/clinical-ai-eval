import copy
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from caeval import monitor_alert_store as store


def feed(*alerts, day='2026-11-25', plan='a'*64):
    return {'schema':'lifecycle-monitor-feed/1','monitor_id':'m','plan_digest':plan,'as_of':day,
            'alerts':list(alerts),'attention_required':bool(alerts),'decision_grade':False,'deployment_authorized':False}


def alert(letter='b'):
    return {'id':letter*64,'kind':'threshold','severity':'serious'}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'store.sqlite3'
        store.initialize(self.path,'m','a'*64)

    def test_restart_and_pending_replay(self):
        a=store.enqueue(self.path,feed(alert()))
        self.assertEqual(store.status(self.path)['pending'],a)
        self.assertEqual(store.enqueue(self.path,feed(alert())),a)
        with self.assertRaises(ValueError): store.enqueue(self.path,feed())
        with self.assertRaises(ValueError): store.acknowledge(self.path,a['digest'])
        receipt=store.acknowledge(self.path,a['digest'],delivered=True)
        self.assertEqual(store.acknowledge(self.path,a['digest'],delivered=True),receipt)
        self.assertIsNone(store.status(self.path)['pending'])

    def test_concurrent_same_feed(self):
        with ThreadPoolExecutor(4) as pool:
            results=list(pool.map(lambda _:store.enqueue(self.path,feed(alert())),range(8)))
        self.assertTrue(all(a==results[0] for a in results))
        with ThreadPoolExecutor(4) as pool:
            receipts=list(pool.map(lambda _:store.acknowledge(self.path,results[0]['digest'],delivered=True),range(8)))
        self.assertTrue(all(r==receipts[0] for r in receipts))
        self.assertEqual(store.status(self.path)['sequence'],1)

    def test_repeat_identical_batch_has_distinct_attempt(self):
        def advance(f):
            a=store.enqueue(self.path,f)
            store.acknowledge(self.path,a['digest'],delivered=a['batch']['delivery_required'])
            return a
        advance(feed()); first=advance(feed(alert())); advance(feed()); second=store.enqueue(self.path,feed(alert()))
        self.assertEqual(first['batch'],second['batch'])
        self.assertNotEqual(first['digest'],second['digest'])
        with self.assertRaises(ValueError): store.acknowledge(self.path,first['digest'],delivered=True)
        self.assertEqual(store.status(self.path)['pending'],second)

    def test_atomic_rollback(self):
        original=store._write
        def fail(conn,state):
            original(conn,state)
            raise RuntimeError('simulated interruption before commit')
        with patch.object(store,'_write',side_effect=fail):
            with self.assertRaises(RuntimeError): store.enqueue(self.path,feed(alert()))
        self.assertIsNone(store.status(self.path)['pending'])
        a=store.enqueue(self.path,feed(alert()))
        with patch.object(store,'_write',side_effect=fail):
            with self.assertRaises(RuntimeError): store.acknowledge(self.path,a['digest'],delivered=True)
        self.assertEqual(store.status(self.path)['pending'],a)
        self.assertEqual(store.status(self.path)['sequence'],0)

    def test_bad_scope_and_corrupt_state(self):
        with self.assertRaises(ValueError): store.enqueue(self.path,feed(plan='b'*64))
        with sqlite3.connect(self.path) as conn:
            state=json.loads(conn.execute('SELECT payload FROM state').fetchone()[0]);state['sequence']=5
            conn.execute('UPDATE state SET payload=?',(json.dumps(state),))
        with self.assertRaises(ValueError): store.status(self.path)

    def test_no_overwrite_or_auto_create(self):
        with self.assertRaises(FileExistsError): store.initialize(self.path,'m','a'*64)
        missing=self.path.with_name('missing')
        with self.assertRaises(FileNotFoundError): store.status(missing)
        self.assertFalse(missing.exists())
        if os.name=='posix':
            self.path.chmod(0o644)
            with self.assertRaises(ValueError): store.status(self.path)

    def test_event_free_and_prior_ack_does_not_clear_new_pending(self):
        a=store.enqueue(self.path,feed())
        receipt=store.acknowledge(self.path,a['digest'])
        b=store.enqueue(self.path,feed(alert()))
        self.assertEqual(store.acknowledge(self.path,a['digest']),receipt)
        self.assertEqual(store.status(self.path)['pending'],b)
        with self.assertRaises(ValueError): store.acknowledge(self.path,a['digest'],delivered=True)

if __name__=='__main__': unittest.main()
