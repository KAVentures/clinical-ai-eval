import copy
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from caeval import monitor_alert_relay as relay, monitor_relay_bridge as bridge
from tests_unit.test_monitor_alert_relay import envelope


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'relay'; relay.initialize(self.path,'m','b'*64)
        self.doc=envelope(); relay.accept(self.path,self.doc,self.doc['attempt_digest'])

    def test_read_only_and_cli_review_independent(self):
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        a=bridge.snapshot(self.path,'m','b'*64,{})
        self.assertEqual(a['pending_count'],1)
        self.assertFalse(a['items'][0]['local_review_recorded'])
        self.assertEqual(before,hashlib.sha256(self.path.read_bytes()).hexdigest())
        relay.review(self.path,'a'*64,relay.digest(self.doc),'d'*64,confirmed=True)
        a=bridge.snapshot(self.path,'m','b'*64,{})
        self.assertEqual(a['pending_count'],1)
        self.assertTrue(a['items'][0]['local_review_recorded'])
        self.assertEqual(bridge.snapshot(self.path,'m','b'*64,{'a'*64:relay.digest(self.doc)})['pending_count'],0)

    def test_scope_absence_and_corruption(self):
        with self.assertRaises(ValueError): bridge.snapshot(self.path,'wrong','b'*64,{})
        missing=self.path.with_name('absent')
        with self.assertRaises(FileNotFoundError): bridge.snapshot(missing,'m','b'*64,{})
        self.assertFalse(missing.exists())
        with sqlite3.connect(self.path) as conn:
            conn.execute('UPDATE batches SET payload_digest=?',('0'*64,))
        with self.assertRaises(ValueError): bridge.attempt(self.path,'m','b'*64,'a'*64)

    def test_missing_and_changed_history_visible(self):
        out=bridge.snapshot(self.path,'m','b'*64,{'a'*64:'0'*64,'f'*64:'1'*64})
        self.assertEqual((out['missing_reviewed_count'],out['changed_reviewed_count']),(1,1))
        self.assertFalse(out['source_consistent_with_review_metadata'])

    def test_exact_review_record_replay(self):
        item=bridge.attempt(self.path,'m','b'*64,'a'*64)
        d=bridge.review_record(item,item['payload_digest'],'f'*64)
        self.assertEqual(d,bridge.verify_record(d))
        self.assertFalse(d['alert_resolved'])
        self.assertFalse(d['human_delivery_verified'])
        with self.assertRaises(ValueError): bridge.review_record(item,'0'*64,'f'*64)
        bad=copy.deepcopy(d); bad['alert_resolved']=True
        with self.assertRaises(ValueError): bridge.verify_record(bad)

    def test_bounded_page_and_second_attempt(self):
        doc={**self.doc,'attempt_digest':'d'*64,'sequence':2}
        relay.accept(self.path,doc,doc['attempt_digest'])
        a=bridge.snapshot(self.path,'m','b'*64,{},limit=1)
        self.assertEqual(a['pending_count'],2)
        self.assertEqual(len(a['items']),1)
        self.assertEqual(bridge.attempt(self.path,'m','b'*64,'d'*64)['envelope'],doc)
        with self.assertRaises(ValueError): bridge.snapshot(self.path,'m','b'*64,{},limit=26)


if __name__=='__main__': unittest.main()
