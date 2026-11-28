import copy
import unittest
from caeval.monitor_alert_outbox import prepare, verify, acknowledge


def feed(*alerts, day='2026-11-25', plan='a'*64):
    return {'schema':'lifecycle-monitor-feed/1','monitor_id':'m','plan_digest':plan,'as_of':day,'alerts':list(alerts),'attention_required':bool(alerts),'decision_grade':False,'deployment_authorized':False}


def alert(letter, kind='threshold', severity='serious'):
    return {'id':letter*64,'kind':kind,'severity':severity}


class TestOutbox(unittest.TestCase):
    def test_new_and_exact_retry(self):
        f=feed(alert('b'),alert('c'))
        b=prepare(f)
        self.assertEqual(b,prepare(feed(*reversed(f['alerts']))))
        self.assertEqual(len(b['events']),2)
        self.assertEqual(b,verify(b))
        with self.assertRaises(ValueError): acknowledge(b,b['digest'])
        cp=acknowledge(b,b['digest'],delivered=True)
        unchanged=prepare(f,cp)
        self.assertFalse(unchanged['delivery_required'])
        self.assertEqual(unchanged['persistent_count'],2)
        self.assertEqual(cp,acknowledge(unchanged,unchanged['digest']))

    def test_transitions(self):
        a,b,c=alert('b'),alert('c'),alert('d')
        prior=prepare(feed(a,b,c))['next_checkpoint']
        changed=prepare(feed(a,alert('c',severity='critical'),alert('e')),prior)
        self.assertEqual([r['transition'] for r in changed['events']],['newly_reported','severity_changed','no_longer_reported'])
        self.assertEqual(changed['persistent_count'],1)
        self.assertFalse(changed['clinical_safety_established'])

    def test_refuses_bad_inputs(self):
        a=alert('b'); old=prepare(feed(a))['next_checkpoint']
        for bad in (feed(a,a),feed(a,day='2026-11-24'),feed(a,plan='b'*64),feed(alert('b',kind='incident'))):
            with self.assertRaises(ValueError): prepare(bad,old)
        with self.assertRaises(ValueError): prepare(None,old)
        bad=feed(a);bad['attention_required']=False
        with self.assertRaises(ValueError): prepare(bad)

    def test_tamper_and_wrong_ack(self):
        b=prepare(feed(alert('b')))
        wrong=copy.deepcopy(b);wrong['events']=[]
        with self.assertRaises(ValueError): verify(wrong)
        with self.assertRaises(ValueError): acknowledge(b,'0'*64,delivered=True)

if __name__=='__main__': unittest.main()
