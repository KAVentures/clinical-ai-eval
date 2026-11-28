import copy
import json
import unittest
from caeval import experimental_plan_inspect as inspect


def fixture():
    return {'digest': 'a'*64, 'scope': {'target': {'name': 'Fictional', 'version': 'v1'},
            'selection': {'required_suites': [{'suite': 'omission', 'implemented': False},
                                             {'suite': 'decision_certifiability', 'implemented': False},
                                             {'suite': 'unsupported_inference', 'implemented': False}]}},
            'modules': {'scribe': {'related_suites': ['omission'], 'plan': {'plan_hash': 'b'*64,
                        'spec': {'synthetic': True, 'secret': 'private-transcript'}, 'items': [{'secret': 'private-case'}],
                        'decision_grade': False}}, 'decision': {'related_suites': ['decision_certifiability'],
                        'plan': {'plan_hash': 'c'*64, 'spec': {'synthetic': True}, 'items': [{}], 'decision_grade': False}}},
            'unplanned_related_modules': ['robustness']}


class InspectionTests(unittest.TestCase):
    def test_projection_counts_workflows_and_gaps(self):
        d = inspect.project(fixture())
        rows = {r['kind']: r for r in d['modules']}
        self.assertEqual(rows['scribe']['planned_units'], 1)
        self.assertIn('prepare-review', rows['scribe']['stages'])
        self.assertNotIn('prepare-review', rows['decision']['stages'])
        self.assertEqual(d['required_suites'][-1]['related_planned_modules'], [])
        self.assertFalse(d['native_workspaces_inspected'])
        self.assertEqual(rows['scribe']['completion_state'], 'not_inspected')
        self.assertFalse(d['deployment_authorized'])

    def test_projection_redacts_native_content(self):
        raw = json.dumps(inspect.project(fixture()))
        self.assertNotIn('private-case', raw)
        self.assertNotIn('private-transcript', raw)
        self.assertNotIn('secret', raw)

    def test_deterministic_across_module_order(self):
        f = fixture(); before = inspect.project(f)
        f['modules'] = dict(reversed(list(f['modules'].items())))
        self.assertEqual(before, inspect.project(f))

    def test_refuse_invalid_provenance(self):
        f = fixture(); f['modules']['scribe']['plan']['decision_grade'] = 0
        with self.assertRaises(ValueError): inspect.project(f)
        f = fixture(); f['modules']['scribe']['plan']['spec']['synthetic'] = 1
        with self.assertRaises(ValueError): inspect.project(f)

    def test_json_equality_not_python_numeric_coercion(self):
        for x, y in [(False, 0), (True, 1), (1, 1.0), ({'a': [False]}, {'a': [0]})]:
            self.assertEqual(x, y)
            self.assertFalse(inspect.canonical_equal(x, y))
        self.assertTrue(inspect.canonical_equal({'a': 1, 'b': False}, {'b': False, 'a': 1}))


if __name__ == '__main__': unittest.main()
