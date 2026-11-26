import copy
import http.client
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from wsgiref.simple_server import make_server
from caeval import monitor_alert_relay as relay


def envelope():
    return {'schema':'monitor-alert-delivery/1','attempt_digest':'a'*64,'monitor_id':'m','plan_digest':'b'*64,
            'as_of':'2026-11-26','sequence':1,'events':[{'transition':'newly_reported',
            'alert':{'id':'c'*64,'kind':'threshold','severity':'serious'}}]}


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'relay';relay.initialize(self.path,'m','b'*64)

    def test_accept_restart_and_conflict(self):
        d=envelope();key=d['attempt_digest'];a=relay.accept(self.path,d,key)
        self.assertEqual(a,relay.accept(self.path,d,key))
        self.assertEqual(relay.inbox(self.path)['pending_count'],1)
        d['events'][0]['alert']['severity']='critical'
        with self.assertRaises(ValueError): relay.accept(self.path,d,key)

    def test_concurrent_acceptance(self):
        d=envelope()
        with ThreadPoolExecutor(4) as pool:
            rows=list(pool.map(lambda _:relay.accept(self.path,d,d['attempt_digest']),range(8)))
        self.assertTrue(all(x==rows[0] for x in rows));self.assertEqual(relay.inbox(self.path)['pending_count'],1)

    def test_scope_and_invalid_transitions(self):
        for change in ({'plan_digest':'d'*64},{'events':[]},{'sequence':True},{'as_of':'20261126'}):
            d={**envelope(),**change}
            with self.assertRaises(ValueError): relay.accept(self.path,d,d['attempt_digest'])
        d=envelope();d['events'].append(copy.deepcopy(d['events'][0]))
        with self.assertRaises(ValueError): relay.accept(self.path,d,d['attempt_digest'])
        self.assertEqual(relay.inbox(self.path)['pending_count'],0)

    def test_review_does_not_resolve_alert(self):
        d=envelope();relay.accept(self.path,d,d['attempt_digest']);p=relay.inbox(self.path)['batches'][0]['payload_digest']
        with self.assertRaises(ValueError): relay.review(self.path,d['attempt_digest'],p,'f'*64)
        r=relay.review(self.path,d['attempt_digest'],p,'f'*64,confirmed=True)
        self.assertFalse(r['alert_resolved']);self.assertFalse(r['human_delivery_verified'])
        self.assertEqual(relay.review(self.path,d['attempt_digest'],p,'f'*64,confirmed=True),r)
        self.assertEqual(relay.inbox(self.path)['pending_count'],0)
        self.assertTrue(relay.accept(self.path,d,d['attempt_digest'])['accepted'])
        self.assertEqual(relay.inbox(self.path)['pending_count'],0)
        with self.assertRaises(ValueError): relay.review(self.path,d['attempt_digest'],p,'e'*64,confirmed=True)

    def test_tamper_refused(self):
        d=envelope();relay.accept(self.path,d,d['attempt_digest'])
        with sqlite3.connect(self.path) as c: c.execute("UPDATE batches SET payload_digest=?",('0'*64,))
        with self.assertRaises(ValueError): relay.inbox(self.path)
        with self.assertRaises(ValueError): relay.accept(self.path,d,d['attempt_digest'])

    def test_real_http_authorization_and_retry(self):
        secret=Path(self.tmp.name)/'secret';fd=os.open(secret,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f: f.write('f'*64+'\n')
        s=make_server('127.0.0.1',0,lambda e,r:app(e,r),handler_class=relay.QuietHandler)
        app=relay.App(self.path,s.server_port,secret)
        t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
        self.addCleanup(s.server_close);self.addCleanup(t.join,5);self.addCleanup(s.shutdown)
        d=envelope();body=json.dumps(d).encode()
        def call(**extra):
            headers={'Authorization':'Bearer '+'f'*64,'Content-Type':'application/json','Idempotency-Key':d['attempt_digest'],**extra}
            c=http.client.HTTPConnection('127.0.0.1',s.server_port,timeout=2)
            try:
                c.request('POST','/v1/alerts/deliver',body,headers);r=c.getresponse();return r.status,json.loads(r.read())
            finally: c.close()
        self.assertEqual(call(Authorization='Bearer bad')[0],403)
        self.assertEqual(call(Origin='https://example.org')[0],403)
        self.assertEqual(call(**{'Idempotency-Key':'0'*64})[0],403)
        a=call();self.assertEqual(a[0],200);self.assertEqual(a,call())
        self.assertEqual(relay.inbox(self.path)['pending_count'],1)


if __name__=='__main__': unittest.main()
