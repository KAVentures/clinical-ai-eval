import copy
import unittest
from caeval import unified_coverage_contract as c

PID='a'*64
KINDS=('text','conversation','rag','fhir','hl7','image_text','imaging','research','rag_grounding')
EXEC={'text':'generic','conversation':'episode','rag':'trace'}

def fixture():
    return {'programme_id':'p','plans':[{'plan_id':PID,'vendor_id':'a','target':{'name':'Product','version':'v1'},
        'profiles':['clinician_decision_support'],'required_suites':['text','rag','blocked'],
        'runnable_suites':['text','rag'],'required_but_not_run':[{'suite':'blocked','blocked_reason':'Not implemented'}]}],
        'requirements':[{'id':'r','kind':'text','plan_id':PID,'family':'text'}, {'id':'f','kind':'fhir','plan_id':PID,'family':None}],
        'unplanned_canonical_suites':[{'plan_id':PID,'suite':'rag','state':'not_planned_in_this_programme'},
                                    {'plan_id':PID,'suite':'blocked','state':'required_but_not_run'}]}

class CoverageContractTests(unittest.TestCase):
    def validate(self,p):
        return c.validate_programme(p,kinds=KINDS,executor_for=EXEC.__getitem__,executors=EXEC)

    def test_valid_and_all_required_gaps(self):
        p=fixture(); self.assertEqual(self.validate(p),{PID:p['plans'][0]})
        p['unplanned_canonical_suites'].pop()
        with self.assertRaises(ValueError): self.validate(p)

    def test_duplicate_and_uncovered_plans(self):
        p=fixture(); p['plans'].append(copy.deepcopy(p['plans'][0]))
        with self.assertRaises(ValueError): self.validate(p)
        p['plans'][1]['plan_id']='b'*64
        with self.assertRaises(ValueError): self.validate(p)

    def test_suite_partition_and_executor(self):
        p=fixture(); p['plans'][0]['required_but_not_run']=[]
        with self.assertRaises(ValueError): self.validate(p)
        p=fixture(); p['requirements'][0]['kind']='conversation'
        with self.assertRaises(ValueError): self.validate(p)
        p=fixture(); p['requirements'][1]['family']='rag'
        with self.assertRaises(ValueError): self.validate(p)

    def test_duplicate_selector_and_scope(self):
        p=fixture(); p['requirements'].append({**p['requirements'][0],'id':'other'})
        with self.assertRaises(ValueError): self.validate(p)
        p=fixture(); p['plans'][0]['target']['notes']='not target metadata'
        with self.assertRaises(ValueError): self.validate(p)

    def test_source_authority_and_types(self):
        source={'source_id':'imaging-a','mode':'experimental_imaging','claim':'not_canonical',
                'scope':'vendor_id_and_version_only','complete_roster':True,'reference_digest':None,'transport_receipt_digest':'c'*64}
        c.validate_source('imaging',source)
        for change in ({'complete_roster':1},{'scope':'exact_buyer_plan'},{'claim':'clinical_pass'},
                       {'transport_receipt_digest':'wrong'},{'source_id':'with spaces'}):
            with self.assertRaises(ValueError):c.validate_source('imaging',{**source,**change})
        c.validate_source('fhir',{'source_id':'a'*64,'mode':'synthetic_readiness','claim':'not_clinical_validity','scope':'exact_buyer_plan'})

if __name__=='__main__':unittest.main()
