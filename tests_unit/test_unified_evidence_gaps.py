import copy
import unittest
from caeval import unified_evidence_gaps as gaps


def fixture():
    plan={'plan_id':'p','vendor_id':'a','target':{'name':'fictional','version':'v1'},
          'required_suites':['text','rag','blocked','omitted'],'runnable_suites':['text','rag','omitted']}
    reqs=[{'id':'r-'+kind,'kind':kind,'plan_id':'p','family':family} for kind,family in
          [('text','text'),('rag','rag'),('fhir',None),('imaging',None)]]
    p={'programme_id':'demo','digest':'a'*64,'plans':[plan],'requirements':reqs,
       'unplanned_canonical_suites':[{'plan_id':'p','suite':'blocked','state':'required_but_not_run'},
                                   {'plan_id':'p','suite':'omitted','state':'not_planned_in_this_programme'}]}
    status={'programme':p,'state':'current','requirements':[
        {'requirement':r,'vendor_id':'a','target':plan['target'],'state':'unlinked','locked_source':None} for r in reqs]}
    work={'programme_id':'demo','programme_digest':p['digest'],'programme_state':'current','requirements':[
        {'requirement_id':r['id'],**{k:r[k] for k in ('kind','plan_id','family')},'vendor_id':'a','target':plan['target'],
         'coverage_state':'unlinked','work_inventory_state':'bounded_scan','scan_truncated':False,'native_work_records':[]} for r in reqs]}
    return status,work


def row(doc,kind):
    return next(r for r in doc['requirements'] if r['kind']==kind)


def set_source(status,work,kind,source,state='source_available'):
    a=next(r for r in status['requirements'] if r['requirement']['kind']==kind)
    a.update(state=state,locked_source=source)
    row(work,kind)['coverage_state']=state


class GapTests(unittest.TestCase):
    def test_ready_and_active_are_not_associated(self):
        s,w=fixture(); row(w,'fhir')['native_work_records']=[{'state':'report_available'},{'state':'collecting'}]
        result=gaps.project(s,w); r=row(result,'fhir')
        self.assertEqual(r['condition'],'not_associated')
        self.assertEqual([t['code'] for t in r['tasks']],['verify_candidate_association','continue_native_work'])
        self.assertFalse(result['deployment_authorized'])

    def test_canonical_and_imaging_limits(self):
        s,w=fixture()
        set_source(s,w,'text',{'mode':'internal_regression','claim':'internal_regression','scope':'exact_buyer_plan'})
        set_source(s,w,'rag',{'mode':'demonstration','claim':'synthetic_context_rehearsal_only','scope':'exact_buyer_plan'})
        set_source(s,w,'imaging',{'mode':'experimental_imaging','complete_roster':False})
        result=gaps.project(s,w)
        self.assertEqual(row(result,'text')['condition'],'internal_regression_only')
        self.assertEqual(row(result,'rag')['condition'],'canonical_evidence_gap')
        self.assertEqual(row(result,'imaging')['condition'],'incomplete_imaging_roster')
        self.assertEqual({r['suite']:r['condition'] for r in result['canonical_suites']},
                         {'text':'internal_regression_only','rag':'canonical_evidence_gap','blocked':'required_but_not_run','omitted':'not_planned_in_this_programme'})
        self.assertFalse(result['canonical_requirements_satisfied'])

    def test_unavailable_and_changed(self):
        s,w=fixture(); row(w,'fhir')['work_inventory_state']='unavailable'
        set_source(s,w,'imaging',{'mode':'experimental_imaging'},'source_changed')
        result=gaps.project(s,w)
        self.assertTrue(result['partial_work_inventory'])
        self.assertEqual([t['code'] for t in row(result,'fhir')['tasks']],['investigate_native_inventory'])
        self.assertEqual(row(result,'imaging')['tasks'][0]['code'],'investigate_locked_source')

    def test_stale_disallows_work_tasks(self):
        s,w=fixture(); s['state']=w['programme_state']='stale_plan'
        for r in s['requirements']:r['state']='stale_plan'
        for r in w['requirements']:r.update(coverage_state='stale_plan',work_inventory_state='stale_plan')
        self.assertTrue(all(r['tasks'][0]['code']=='review_stale_programme' for r in gaps.project(s,w)['requirements']))

    def test_scope_and_omission_mismatch_rejected(self):
        for change in ('scope','missing','duplicate','gap'):
            s,w=fixture()
            if change=='scope': row(w,'fhir')['target']={'version':'v2'}
            elif change=='missing': w['requirements'].pop()
            elif change=='duplicate': w['requirements'].append(copy.deepcopy(w['requirements'][0]))
            else: s['programme']['unplanned_canonical_suites'].pop()
            with self.assertRaises(ValueError): gaps.project(s,w)

    def test_digest_deterministic(self):
        s,w=fixture(); result=gaps.project(s,w)
        w['requirements'].reverse();s['requirements'].reverse()
        self.assertEqual(result,gaps.project(s,w))


if __name__=='__main__': unittest.main()
