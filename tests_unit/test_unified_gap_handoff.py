"""Pure handoff contract tests; callbacks here are synthetic, not native verification."""
import copy
import json
import unittest
from caeval import unified_gap_handoff as h


def work():
    return {'programme_id':'demo','programme_digest':'a'*64,'programme_state':'current','requirements':[
        {'requirement_id':'r','kind':'fhir','plan_id':'p','family':None,'vendor_id':'a',
         'target':{'name':'fictional','version':'v1'},'coverage_state':'unlinked',
         'work_inventory_state':'bounded_scan','scan_truncated':False,
         'native_work_records':[{'id':'private-native-job','state':'report_available','note':'private-note'},
                                {'state':'queued'},{'state':'failed'}]}]}


def project(coverage, w):
    assert coverage['programme_id']==w['programme_id']
    return {'counts':h.counts(w['requirements'][0]['native_work_records']),'decision_grade':False}


def check(coverage):
    if coverage != {'programme_id':'demo'}:
        raise ValueError('invalid synthetic coverage')


class HandoffTests(unittest.TestCase):
    def package(self):
        return h.build({'programme_id':'demo'},work(),project=project)

    def verify(self,p,expected=None):
        return h.verify(p,expected,project=project,verify_coverage=check)

    def test_round_trip_and_redaction(self):
        p=self.package(); r=self.verify(p,p['digest'])
        self.assertTrue(r['expected_digest_matched']); self.assertFalse(r['source_records_reverified'])
        self.assertNotIn('private-native-job',json.dumps(p));self.assertNotIn('private-note',json.dumps(p))
        self.assertEqual(p['report']['counts'],{'ready':1,'active':1,'other':1})

    def test_resealed_false_report_refused(self):
        p=self.package();p['report']['counts']['ready']=7
        p=h.seal({k:v for k,v in p.items() if k!='digest'})
        with self.assertRaises(ValueError): self.verify(p)

    def test_external_digest_and_coverage_verifier(self):
        p=self.package()
        with self.assertRaises(ValueError): self.verify(p,'0'*64)
        p['coverage']['unexpected']=True
        p=h.seal({k:v for k,v in p.items() if k!='digest'})
        with self.assertRaises(ValueError): self.verify(p)

    def test_count_shape_bounds_and_missing_scan(self):
        s=h.summarize(work())
        for key,value in [('ready',True),('other',1001),('active',-1)]:
            bad=copy.deepcopy(s);bad['requirements'][0]['counts'][key]=value
            with self.assertRaises(ValueError):h.expand(bad)
        bad=copy.deepcopy(s);bad['requirements'][0]['work_inventory_state']='unavailable'
        with self.assertRaises(ValueError):h.expand(bad)
        bad=copy.deepcopy(s);bad['requirements'].append(copy.deepcopy(bad['requirements'][0]))
        with self.assertRaises(ValueError):h.expand(bad)

    def test_projection_drift_refused(self):
        def uses_ids(c,w):return {'ids':[r.get('id') for r in w['requirements'][0]['native_work_records']]}
        with self.assertRaises(ValueError):h.build({'programme_id':'demo'},work(),project=uses_ids)

    def test_claims_and_extra_fields_refused(self):
        for field,value in [('deployment_authorized',True),('unexpected','data')]:
            p=self.package();p[field]=value;p=h.seal({k:v for k,v in p.items() if k!='digest'})
            with self.assertRaises(ValueError):self.verify(p)


if __name__=='__main__': unittest.main()
