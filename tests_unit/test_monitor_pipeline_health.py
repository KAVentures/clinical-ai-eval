import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from caeval import monitor_pipeline_health as pipeline, monitor_alert_relay as relay
from tests_unit.test_monitor_alert_relay import envelope

NOW=int(datetime(2026,11,26,tzinfo=timezone.utc).timestamp())
SCOPE={'monitor_id':'m','plan_digest':'b'*64}


def state():
    return {'scope':SCOPE,'sequence':1,'checkpoint':{'feed':{'as_of':'2026-11-26'}},'pending':None,
            'last_ack':{'attempt_digest':'a'*64,'delivery_attested':True}}


def heartbeat():
    return {'issues':[],'state':{'policy':{'binding':'f'*64}}}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'relay';relay.initialize(self.path,'m','b'*64)
        self.doc=envelope();relay.accept(self.path,self.doc,self.doc['attempt_digest'])
        with sqlite3.connect(self.path) as c: c.execute('UPDATE batches SET accepted_at=?',(NOW-120,))

    def snapshot(self): return pipeline.relay_snapshot(self.path,SCOPE,['a'*64])

    def assess(self,s=None,h=None,r=None):
        return pipeline.assess({'digest':'f'*64},state() if s is None else s,heartbeat() if h is None else h,
                               self.snapshot() if r is None else r,now=NOW,backlog_limit=2,backlog_age=600)

    def test_reconciled_last_ack(self):
        result=self.assess();self.assertFalse(result['attention_required']);self.assertEqual(result['last_receipt_state'],'matching_metadata')
        self.assertFalse(result['live_source_polled']);self.assertFalse(result['human_delivery_verified'])
        self.assertNotIn('selected',result['relay'])

    def test_restore_missing_last_ack(self):
        with sqlite3.connect(self.path) as c: c.execute('DELETE FROM batches')
        self.assertIn('last_ack_relay_missing_or_mismatched',self.assess()['issues'])

    def test_duplicate_and_ahead_sequence(self):
        for key,seq in [('c',1),('d',3)]:
            doc={**self.doc,'attempt_digest':key*64,'sequence':seq}
            # Simulate a damaged or older relay database, bypassing admission.
            with sqlite3.connect(self.path) as conn:
                conn.execute('INSERT INTO batches VALUES(?,?,?,?,?,NULL,NULL)',(doc['attempt_digest'],seq,relay.canonical(doc),relay.digest(doc),NOW-120))
        issues=self.assess()['issues']
        self.assertIn('relay_sequence_ahead_of_outbox',issues);self.assertIn('relay_duplicate_sequences',issues)
        self.assertIn('local_cli_backlog_limit',issues)

    def test_backlog_and_old_checkpoint(self):
        s=state();s['checkpoint']['feed']['as_of']='2026-11-25'
        with sqlite3.connect(self.path) as c: c.execute('UPDATE batches SET accepted_at=?',(NOW-600,))
        issues=self.assess(s=s)['issues'];self.assertIn('local_cli_backlog_overdue',issues);self.assertIn('checkpoint_date_older_than_utc_today',issues)
        relay.review(self.path,'a'*64,relay.digest(self.doc),'f'*64,confirmed=True)
        self.assertNotIn('local_cli_backlog_overdue',self.assess()['issues'])

    def test_unavailable_never_healthy(self):
        r=pipeline.assess(None,None,None,None,now=NOW,backlog_limit=2,backlog_age=600)
        self.assertEqual(set(r['issues']),{'outbox_or_binding_unavailable','heartbeat_unavailable','relay_unavailable'})
        self.assertTrue(r['attention_required'])

    def test_pending_exact_relay_or_conflicting_next_sequence(self):
        s=state();doc={**self.doc,'attempt_digest':'d'*64,'sequence':2}
        s['pending']={'digest':'d'*64,'sequence':2,'batch':{'delivery_required':True,
            'next_checkpoint':{'feed':{'monitor_id':'m','plan_digest':'b'*64,'as_of':'2026-11-26'}},'events':doc['events']}}
        relay.accept(self.path,doc,doc['attempt_digest'])
        snap=pipeline.relay_snapshot(self.path,SCOPE,['a'*64,'d'*64])
        r=self.assess(s=s,r=snap);self.assertEqual(r['pending_state'],'accepted')
        self.assertIn('pending_outbox_attempt',r['issues'])
        s['pending']['digest']='e'*64
        r=self.assess(s=s,r=snap);self.assertEqual(r['pending_state'],'not_accepted')
        self.assertIn('relay_next_sequence_not_exact_pending',r['issues'])

    def test_selected_payload_replay_and_binding_mismatch(self):
        h=heartbeat();h['state']['policy']['binding']='0'*64
        self.assertIn('heartbeat_binding_mismatch',self.assess(h=h)['issues'])
        with sqlite3.connect(self.path) as c: c.execute('UPDATE batches SET payload_digest=?',('0'*64,))
        with self.assertRaises(ValueError): self.snapshot()


if __name__=='__main__': unittest.main()
