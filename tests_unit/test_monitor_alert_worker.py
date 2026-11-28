import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from caeval import monitor_alert_worker as worker, monitor_alert_store as store
from tests_unit.test_monitor_alert_store import feed, alert


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass

    def respond(self,doc,status=200):
        raw=json.dumps(doc).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
        self.end_headers();self.wfile.write(raw)

    def do_GET(self):
        self.server.calls.append((self.path,self.headers.get('Authorization'),None,None))
        self.respond(self.server.feed,self.server.status)

    def do_POST(self):
        doc=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.calls.append((self.path,self.headers.get('Authorization'),doc,self.headers.get('Idempotency-Key')))
        self.respond({'schema':'monitor-alert-delivery-receipt/1','attempt_digest':doc['attempt_digest'] if self.server.good else '0'*64,'accepted':True},self.server.status)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'store';store.initialize(self.path,'m','a'*64)
        self.source=self.server();self.sink=self.server();self.source.feed=feed(alert())
        self.src=Path(self.tmp.name)/'source';self.dst=Path(self.tmp.name)/'sink'
        for p,token in ((self.src,'a'*64),(self.dst,'b'*64)):
            fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w') as f: f.write(token+'\n')
        self.config={'source_port':self.source.server_port,'sink_port':self.sink.server_port,'timeout':2}
        self.p=worker.preview(self.path,**self.config)
        worker.bind(self.path,self.config,self.p['digest'],confirmed=True)

    def server(self):
        s=HTTPServer(('127.0.0.1',0),Handler);s.calls=[];s.status=200;s.good=True
        t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
        self.addCleanup(s.server_close);self.addCleanup(t.join,5);self.addCleanup(s.shutdown)
        return s

    def run_cycle(self): return worker.cycle(self.path,self.p['digest'],self.src,self.dst)

    def test_actual_http_and_empty_transition(self):
        r=self.run_cycle();self.assertEqual(r['events'],1)
        self.assertEqual(self.source.calls[0][0:2],('/v1/monitor/check','Bearer '+'a'*64))
        path,auth,doc,key=self.sink.calls[0]
        self.assertEqual((path,auth),('/v1/alerts/deliver','Bearer '+'b'*64))
        self.assertEqual(key,r['attempt_digest']);self.assertEqual(doc['attempt_digest'],key)
        self.assertNotIn('checkpoint',doc);self.assertFalse(r['human_delivery_verified'])
        self.assertEqual(self.run_cycle()['events'],0);self.assertEqual(len(self.sink.calls),1)

    def test_wrong_receipt_then_resume_without_polling(self):
        self.sink.good=False
        with self.assertRaises(ValueError): self.run_cycle()
        attempt=store.status(self.path)['pending'];self.assertIsNotNone(attempt)
        self.sink.good=True;self.source.status=500
        r=self.run_cycle();self.assertTrue(r['resumed_pending'])
        self.assertEqual(r['attempt_digest'],attempt['digest']);self.assertEqual(len(self.source.calls),1)
        self.assertEqual(self.sink.calls[0],self.sink.calls[1])

    def test_poll_error_never_clears_checkpoint(self):
        self.run_cycle();before=store.status(self.path)
        self.source.status=302
        with self.assertRaises(ValueError): self.run_cycle()
        self.assertEqual(before,store.status(self.path));self.assertEqual(len(self.sink.calls),1)

    def test_ack_interruption_retains_same_delivery_key(self):
        with patch.object(store,'acknowledge',side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError): self.run_cycle()
        r=self.run_cycle();self.assertTrue(r['resumed_pending']);self.assertEqual(self.sink.calls[0],self.sink.calls[1])

    def test_binding_and_credentials_fail_closed(self):
        with self.assertRaises(ValueError): worker.cycle(self.path,'0'*64,self.src,self.dst)
        changed={**self.config,'timeout':3};p=worker.preview(self.path,**changed)
        with self.assertRaises(ValueError): worker.bind(self.path,changed,p['digest'],confirmed=True)
        with self.assertRaises(ValueError): worker.cycle(self.path,self.p['digest'],self.src,self.src)
        if os.name=='posix':
            self.src.chmod(0o644)
            with self.assertRaises(ValueError): self.run_cycle()
        self.assertEqual(self.source.calls,[])

    def test_scope_mismatch_and_redirect_sink(self):
        self.source.feed=feed(alert(),plan='c'*64)
        with self.assertRaises(ValueError): self.run_cycle()
        self.assertIsNone(store.status(self.path)['pending']);self.assertEqual(self.sink.calls,[])
        self.source.feed=feed(alert());self.sink.status=307
        with self.assertRaises(ValueError): self.run_cycle()
        self.assertIsNotNone(store.status(self.path)['pending']);self.assertEqual(len(self.sink.calls),1)

if __name__=='__main__': unittest.main()
