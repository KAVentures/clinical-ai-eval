"""Exercise the actual portable handoff and gap projector together."""
import copy
import json
import unittest
from caeval import unified_gap_handoff as handoff, unified_evidence_gaps as gaps
from tests_unit.test_unified_evidence_gaps import fixture, row, set_source


class GapProjectionAcceptanceTests(unittest.TestCase):
    def package(self):
        status, work = fixture()
        row(work, 'fhir')['native_work_records'] = [
            {'id': 'private-candidate', 'state': 'report_available', 'note': 'private-note'},
            {'id': 'private-running', 'state': 'running'}, {'state': 'failed'}]
        set_source(status, work, 'text', {'mode': 'internal_regression', 'claim': 'internal_regression', 'scope': 'exact_buyer_plan'})
        set_source(status, work, 'imaging', {'mode': 'experimental_imaging', 'complete_roster': False})
        row(work, 'rag').update(work_inventory_state='partial', scan_truncated=True)
        return handoff.build(status, work, project=gaps.project)

    def verify(self, package, expected=None):
        # Coverage fixture is synthetic; native source replay is deliberately absent.
        return handoff.verify(package, expected, project=gaps.project, verify_coverage=lambda c: None)

    def test_actual_projection_replays_and_redacts(self):
        package = self.package()
        report = package['report']
        self.assertEqual(row(report, 'fhir')['potential_ready_records'], 1)
        self.assertEqual(row(report, 'fhir')['active_work_records'], 1)
        self.assertEqual(row(report, 'text')['condition'], 'internal_regression_only')
        self.assertEqual(row(report, 'imaging')['condition'], 'incomplete_imaging_roster')
        self.assertTrue(report['partial_work_inventory'])
        self.assertNotIn('private-candidate', json.dumps(package))
        self.assertNotIn('private-note', json.dumps(package))
        self.assertTrue(self.verify(package, package['digest'])['expected_digest_matched'])

    def test_changed_counts_or_mismatched_scope_refused_even_if_resealed(self):
        for change in ('count', 'scope', 'scan'):
            package = self.package()
            summary = row(package['work_summary'], 'fhir')
            if change == 'count':
                summary['counts']['ready'] += 1
            elif change == 'scope':
                summary['target'] = {'name': 'fictional', 'version': 'v2'}
            else:
                summary['scan_truncated'] = True
            package = handoff.seal({k: v for k, v in package.items() if k != 'digest'})
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.verify(package)

    def test_json_type_substitution_refused(self):
        for field, value in [('deployment_authorized', 0), ('partial_work_inventory', 1)]:
            package = self.package()
            package['report'][field] = value
            package = handoff.seal({k:v for k,v in package.items() if k!='digest'})
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(package)

    def test_inconsistent_scan_metadata_refused(self):
        package = self.package()
        for state, truncated in [('bounded_scan', True), ('bounded_scan', None),
                                 ('bounded_legacy_scan', False), ('partial', None),
                                 ('unavailable', True), ('stale_plan', None)]:
            summary = copy.deepcopy(package['work_summary'])
            r = row(summary, 'rag')
            r.update(work_inventory_state=state, scan_truncated=truncated)
            with self.subTest(state=state, truncated=truncated), self.assertRaises(ValueError):
                handoff.expand(summary)


if __name__ == '__main__':
    unittest.main()
