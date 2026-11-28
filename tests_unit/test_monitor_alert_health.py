import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from caeval import monitor_alert_health as health


class HealthTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'health.sqlite3';self.clock=1000
        p=patch.object(health,'_now',side_effect=lambda:self.clock);p.start();self.addCleanup(p.stop)
        health.initialize(self.path,'a'*64,120,30)

    def test_initial_success_and_stale(self):
        self.assertIn('no_success_recorded',health.status(self.path)['issues'])
        self.assertEqual(health.run_cycle(self.path,'a'*64,lambda:42),42)
        self.assertFalse(health.status(self.path)['attention_required'])
        self.clock+=120
        self.assertIn('success_overdue',health.status(self.path)['issues'])

    def test_failure_and_recovery(self):
        def fail(): raise RuntimeError('private detail')
        with self.assertRaises(RuntimeError): health.run_cycle(self.path,'a'*64,fail)
        s=health.status(self.path);self.assertIn('last_cycle_failed',s['issues']);self.assertEqual(s['state']['failures'],1)
        self.assertNotIn('private detail',str(s))
        health.run_cycle(self.path,'a'*64,lambda:None)
        self.assertEqual(health.status(self.path)['state']['failures'],0)

    def test_overlap_and_explicit_recovery(self):
        rid=health.start(self.path,'a'*64)
        self.clock+=30
        self.assertIn('cycle_overdue',health.status(self.path)['issues'])
        with self.assertRaises(ValueError): health.start(self.path,'a'*64)
        with self.assertRaises(ValueError): health.finish(self.path,rid,'interrupted')
        with self.assertRaises(ValueError): health.finish(self.path,'b'*64,'interrupted',process_stopped=True)
        health.finish(self.path,rid,'interrupted',process_stopped=True)
        self.assertIn('last_cycle_interrupted',health.status(self.path)['issues'])
        health.run_cycle(self.path,'a'*64,lambda:None)
        with self.assertRaises(ValueError): health.finish(self.path,rid,'succeeded')

    def test_concurrent_start_only_one(self):
        def attempt(_):
            try: return health.start(self.path,'a'*64)
            except ValueError: return None
        with ThreadPoolExecutor(4) as pool: ids=list(pool.map(attempt,range(8)))
        self.assertEqual(sum(i is not None for i in ids),1)

    def test_binding_and_clock_regression(self):
        with self.assertRaises(ValueError): health.start(self.path,'b'*64)
        self.clock-=1
        self.assertIn('clock_regression',health.status(self.path)['issues'])
        with self.assertRaises(ValueError): health.start(self.path,'a'*64)

    def test_killed_cycle_and_tamper(self):
        def killed(): raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): health.run_cycle(self.path,'a'*64,killed)
        self.assertIn('cycle_in_progress',health.status(self.path)['issues'])
        with sqlite3.connect(self.path) as c: c.execute("UPDATE heartbeat SET payload='{}'")
        with self.assertRaises(ValueError): health.status(self.path)


if __name__=='__main__': unittest.main()
