import tempfile
import unittest
from pathlib import Path
from caeval import unified_legacy_work as legacy


class LegacyInventoryTests(unittest.TestCase):
    def test_prepared_scan_includes_malformed_record_and_sentinel(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            (path / 'unrelated.txt').touch()
            for char in 'abc':
                (path / (char * 64)).touch()
            ids = legacy.prepared_ids(path, 2)
            self.assertEqual(ids, [c * 64 for c in 'abc'])
            self.assertEqual(legacy.prepared_ids(path / 'missing'), [])
            with self.assertRaises(ValueError):
                legacy.prepared_ids(path / ('a' * 64))

    def test_scope_exact_candidate_and_family(self):
        plan = {'plan_id': 'p', 'vendor_id': 'a', 'target': {'name': 'test', 'version': 'v1'}}
        exchange = {**plan, 'family_ids': ['missing_information']}
        self.assertTrue(legacy.text_scope(exchange, plan, 'missing_information'))
        self.assertFalse(legacy.text_scope(exchange, plan, 'conflicting_evidence'))
        self.assertFalse(legacy.text_scope({**exchange, 'plan_id': 'other'}, plan, 'missing_information'))
        for change in ({'vendor_id': 'b'}, {'target': {'name': 'test', 'version': 'v2'}},
                       {'family_ids': ['missing_information', 'missing_information']}):
            with self.assertRaises(ValueError):
                legacy.text_scope({**exchange, **change}, plan, 'missing_information')

    def test_merge_suppresses_only_verified_job_preparation_and_preserves_errors(self):
        prepared = {'records': [{'id': 'p1'}, {'id': 'p2'}], 'unverified_records': 1, 'scan_truncated': False}
        jobs = {'records': [{'id': 'j1', 'assessment_id': 'p1'}], 'unverified_records': 2, 'scan_truncated': True}
        result = legacy.merge_text(prepared, jobs)
        self.assertEqual([r['id'] for r in result['records']], ['p2', 'j1'])
        self.assertEqual(result['state'], 'partial')
        self.assertEqual(result['unverified_records'], 3)
        self.assertTrue(result['scan_truncated'])

    def test_empty_merge_is_bounded(self):
        empty = {'records': [], 'unverified_records': 0, 'scan_truncated': False}
        self.assertEqual(legacy.merge_text(empty, empty), {**empty, 'state': 'bounded_scan'})


if __name__ == '__main__':
    unittest.main()
