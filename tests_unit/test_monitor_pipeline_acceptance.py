"""Real local HTTP and SQLite integration; the source feed is synthetic."""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch
from wsgiref.simple_server import make_server

from caeval import (monitor_alert_health as health, monitor_alert_relay as relay,
                    monitor_alert_store as store, monitor_alert_worker as worker,
                    monitor_pipeline_health as pipeline, monitor_relay_bridge as bridge)


class FeedHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.calls += 1
        if (self.path != '/v1/monitor/check' or
                self.headers.get('Authorization') != 'Bearer ' + 'a'*64):
            self.send_error(403)
            return
        raw = json.dumps(self.server.feed).encode('utf-8')
        self.send_response(self.server.code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class PipelineAcceptanceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.outbox, self.heartbeat, self.relay = (root / k for k in ('outbox', 'heartbeat', 'relay'))
        self.src, self.dst = root / 'source-token', root / 'relay-token'
        for path, char in ((self.src, 'a'), (self.dst, 'b')):
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(char*64 + '\n')
        self.scope = {'monitor_id': 'fictional', 'plan_digest': 'c'*64}
        self.day = datetime.now(timezone.utc).date().isoformat()
        store.initialize(self.outbox, **self.scope)
        relay.initialize(self.relay, **self.scope)
        self.source = HTTPServer(('127.0.0.1', 0), FeedHandler)
        self.source.calls = 0
        self.source.code = 200
        self.source.feed = self.feed('d')
        self.start_server(self.source)
        self.sink = make_server('127.0.0.1', 0, lambda env, start: app(env, start),
                                handler_class=relay.QuietHandler)
        app = relay.App(self.relay, self.sink.server_port, self.dst)
        self.start_server(self.sink)
        config = {'source_port': self.source.server_port, 'sink_port': self.sink.server_port, 'timeout': 2}
        self.approval = worker.preview(self.outbox, **config)['digest']
        worker.bind(self.outbox, config, self.approval, confirmed=True)
        health.initialize(self.heartbeat, self.approval, 120, 30)

    def start_server(self, server):
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)

    def feed(self, *letters, severity='serious'):
        return {'schema': 'lifecycle-monitor-feed/1', **self.scope, 'as_of': self.day,
                'alerts': [{'id': c*64, 'kind': 'threshold', 'severity': severity} for c in letters],
                'attention_required': bool(letters), 'decision_grade': False, 'deployment_authorized': False}

    def cycle(self):
        return health.run_cycle(self.heartbeat, self.approval, lambda: worker.cycle(
            self.outbox, self.approval, self.src, self.dst))

    def check(self):
        return pipeline.check(self.outbox, self.heartbeat, self.relay, self.approval,
                              backlog_limit=5, backlog_age=600)

    def test_complete_lifecycle_and_event_free_sequence_gap(self):
        result = self.cycle()
        self.assertEqual(result['events'], 1)
        self.assertFalse(self.check()['attention_required'])
        self.assertEqual(self.cycle()['events'], 0)
        self.source.feed = self.feed('d', severity='critical')
        self.cycle()
        self.source.feed = self.feed()
        self.cycle()
        inbox = relay.inbox(self.relay)['batches']
        self.assertEqual([r['envelope']['sequence'] for r in sorted(inbox, key=lambda r: r['envelope']['sequence'])], [1, 3, 4])
        events = {r['envelope']['sequence']: r['envelope']['events'][0]['transition'] for r in inbox}
        self.assertEqual(events, {1: 'newly_reported', 3: 'severity_changed', 4: 'no_longer_reported'})
        self.assertEqual(store.status(self.outbox)['sequence'], 4)
        self.assertFalse(self.check()['attention_required'])
        self.assertFalse(self.check()['clinical_safety_established'])
        self.assertEqual(bridge.snapshot(self.relay, **self.scope, reviewed={})['pending_count'], 3)

    def test_sink_acceptance_before_outbox_ack_interruption(self):
        with patch.object(store, 'acknowledge', side_effect=RuntimeError('simulated interruption')):
            with self.assertRaises(RuntimeError):
                self.cycle()
        pending = store.status(self.outbox)['pending']
        self.assertEqual(self.check()['pending_state'], 'accepted')
        self.assertEqual(relay.inbox(self.relay)['pending_count'], 1)
        self.source.code = 503
        result = self.cycle()
        self.assertTrue(result['resumed_pending'])
        self.assertEqual(result['attempt_digest'], pending['digest'])
        self.assertEqual(self.source.calls, 1)
        self.assertEqual(relay.inbox(self.relay)['pending_count'], 1)
        before = store.status(self.outbox)
        with self.assertRaises(ValueError):
            self.cycle()
        self.assertEqual(before, store.status(self.outbox))
        self.assertIn('heartbeat_last_cycle_failed', self.check()['issues'])

    def test_relay_restore_missing_ack_and_review_history(self):
        self.cycle()
        row = relay.inbox(self.relay)['batches'][0]
        attempt = row['envelope']['attempt_digest']
        review = bridge.review_record(bridge.attempt(self.relay, **self.scope, attempt_id=attempt),
                                      row['payload_digest'], 'e'*64)
        self.assertEqual(bridge.verify_record(review), review)
        with closing(sqlite3.connect(self.relay)) as conn, conn:
            conn.execute('DELETE FROM batches')
        self.assertIn('last_ack_relay_missing_or_mismatched', self.check()['issues'])
        snap = bridge.snapshot(self.relay, **self.scope, reviewed={attempt: row['payload_digest']})
        self.assertEqual(snap['missing_reviewed_count'], 1)
        self.assertFalse(snap['source_consistent_with_review_metadata'])

    def test_event_free_ack_conflict_hidden_by_next_pending(self):
        self.cycle()
        self.cycle()  # sequence 2 requires no delivery
        self.source.feed = self.feed('f')
        with patch.object(store, 'acknowledge', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                self.cycle()  # sequence 3 accepted, but not acknowledged
        rogue = {**relay.inbox(self.relay)['batches'][0]['envelope'], 'attempt_digest': '1'*64, 'sequence': 2}
        relay.accept(self.relay, rogue, rogue['attempt_digest'])
        self.assertIn('event_free_ack_has_relay_record', self.check()['issues'])

    def test_last_ack_date_conflict(self):
        self.cycle()
        row = relay.inbox(self.relay)['batches'][0]['envelope']
        row['as_of'] = '2000-01-01'
        with closing(sqlite3.connect(self.relay)) as conn, conn:
            conn.execute('UPDATE batches SET payload=?,payload_digest=? WHERE id=?',
                         (relay.canonical(row), relay.digest(row), row['attempt_digest']))
        self.assertIn('last_ack_relay_missing_or_mismatched', self.check()['issues'])


if __name__ == '__main__':
    unittest.main()
