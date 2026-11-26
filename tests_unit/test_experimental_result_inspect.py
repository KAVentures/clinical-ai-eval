import copy
import json
import unittest
from caeval import experimental_plan_inspect as inspect
from tests_unit.test_experimental_plan_inspect import fixture


def result_fixture():
    frozen = fixture()
    plan = inspect.project(frozen)
    result = {'digest': 'd'*64, 'project_plan_digest': frozen['digest'], 'decision_grade': False,
              'module_receipts': {'decision': {'plan_hash': 'c'*64, 'synthetic': True,
                  'related_suites': ['decision_certifiability'], 'decision_grade': False,
                  'package_digest': 'e'*64, 'report_digest': 'f'*64, 'summary': {'secret': 'private-score'}}},
              'packages': {'decision': {'private': 'raw-response'}},
              'uncompleted_planned_modules': ['scribe'], 'unplanned_related_modules': ['robustness'],
              'all_planned_modules_replayed': False, 'coverage': []}
    for row in plan['required_suites']:
        related = row['related_planned_modules']
        result['coverage'].append({'suite': row['suite'], 'canonical_runnable': row['canonical_runnable'],
            'canonical_execution_verified': False, 'canonical_requirement_satisfied': False,
            'planned_experimental_modules': related, 'replayed_experimental_modules': [x for x in related if x == 'decision']})
    receipt = {k: copy.deepcopy(result[k]) for k in ('digest','project_plan_digest','decision_grade','coverage',
               'all_planned_modules_replayed','uncompleted_planned_modules','unplanned_related_modules')}
    receipt.update(status='REPLAY_VERIFIED', expected_digest_matched=True)
    return frozen, result, receipt


class ResultInspectionTests(unittest.TestCase):
    def test_partial_replayed_projection_redacts_native_content(self):
        doc = inspect.project_result(*result_fixture())
        self.assertTrue(doc['result_replayed'])
        self.assertFalse(doc['all_planned_modules_replayed'])
        rows = {r['kind']:r for r in doc['modules']}
        self.assertEqual(rows['decision']['completion_state'],'replayed')
        self.assertEqual(rows['decision']['report_digest'],'f'*64)
        self.assertEqual(rows['scribe']['completion_state'],'not_in_result')
        self.assertIsNone(rows['scribe']['package_digest'])
        for secret in ('private-score','private-case','private-transcript','raw-response'):
            self.assertNotIn(secret,json.dumps(doc))
        self.assertFalse(doc['scientific_scores_included'])
        self.assertFalse(doc['deployment_authorized'])

    def test_replay_binding_and_completion_mismatch_refused(self):
        for field,value in [('status','NOT_VERIFIED'),('digest','0'*64),('expected_digest_matched',1),
                            ('all_planned_modules_replayed',True),('uncompleted_planned_modules',[])]:
            frozen,result,receipt=result_fixture();receipt[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):inspect.project_result(frozen,result,receipt)

    def test_source_receipt_scope_and_digest_checked(self):
        for field,value in [('plan_hash','0'*64),('synthetic',1),('decision_grade',0),('package_digest','wrong')]:
            frozen,result,receipt=result_fixture();result['module_receipts']['decision'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):inspect.project_result(frozen,result,receipt)

    def test_forged_canonical_coverage_even_if_receipt_matches_refused(self):
        frozen,result,receipt=result_fixture()
        result['coverage'][0]['canonical_requirement_satisfied']=True
        receipt['coverage']=copy.deepcopy(result['coverage'])
        with self.assertRaises(ValueError):inspect.project_result(frozen,result,receipt)

    def test_deterministic(self):
        f,r,v=result_fixture();before=inspect.project_result(f,r,v)
        f['modules']=dict(reversed(list(f['modules'].items())))
        self.assertEqual(before,inspect.project_result(f,r,v))


if __name__=='__main__':unittest.main()
