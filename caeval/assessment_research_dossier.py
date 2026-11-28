"""Restricted aggregate procurement supplement from replayed assessment evidence."""
import argparse
import sys

from . import assessment_workbench as work, assessment_resume_web as resume
from . import assessment_setup_web as storage, predictive_research as pred
from . import predictive_assisted_coordinator as assisted, predictive_reference_panel as panel
from . import vendor_workbench as vendor

SCHEMA = 'assessment-research-dossier/1'


def freeze(plan, expected_plan, policy):
    pred.require(pred.hex64(expected_plan) and plan['plan_sha256'] == expected_plan)
    pred.require(plan['schema'] == work.SCHEMA and work.digest({k:v for k,v in plan.items() if k != 'plan_sha256'}) == expected_plan)
    pred.fields(policy, 'minimum_coverage_bp require_assisted_trial minimum_trial_coverage_bp')
    coverage = policy['minimum_coverage_bp']
    pred.fields(coverage, 'baseline candidate reference')
    pred.require(all(type(v) is int and 0 <= v <= 10000 for v in coverage.values()))
    pred.require(type(policy['require_assisted_trial']) is bool)
    t = policy['minimum_trial_coverage_bp']
    pred.require(type(t) is int and 0 <= t <= 10000)
    body = {'schema':SCHEMA,'assessment_plan_sha256':expected_plan,'policy':{**policy,'minimum_coverage_bp':dict(coverage)}}
    return {**body,'policy_sha256':pred.digest(body)}


def _gate(actual, total, minimum):
    return {'known':actual,'planned':total,'minimum_bp':minimum,
            'status':'coverage_at_or_above_minimum' if actual*10000 >= total*minimum else 'coverage_below_minimum'}


def build(plan, expected_plan, policy, expected_policy, expected_report, inputs, *, trial_package=None, expected_trial=None):
    frozen = freeze(plan, expected_plan, policy['policy'])
    pred.require(pred.hex64(expected_policy) and frozen['policy_sha256']==expected_policy and pred.canonical(frozen)==pred.canonical(policy))
    report = work.analyze(plan, expected_plan, **inputs)
    pred.require(pred.hex64(expected_report) and report['report_sha256']==expected_report)
    pred.require((trial_package is None)==(expected_trial is None))
    n = report['n_planned']; spec = policy['policy']; summary = report['summary']
    pr = report['reports']['predictive']; br = report['reports']['binary']
    coverage = {role:_gate(pr['coverage'][role]['n_known'],n,spec['minimum_coverage_bp'][role]) for role in pred.ROLES}
    gaps=[]
    if summary['mandatory_missing']: gaps.append('mandatory_vendor_documents_missing')
    if summary['unresolved_clarifications']: gaps.append('vendor_clarifications_unresolved')
    for role, result in coverage.items():
        if result['status']=='coverage_below_minimum': gaps.append(role+'_coverage_below_minimum')
    trial=None
    if trial_package is not None:
        t=assisted.verify_package(trial_package,expected_trial)
        # Same exact study, version, roster, reference protocol and native labels.
        pp=inputs['predictive_plan']
        pred.require(pred.canonical(trial_package['plan'])==pred.canonical(pp))
        ref=trial_package['panel']
        reference=panel.finalize(pp,pp['plan_sha256'],ref,ref['panel_sha256'],trial_package['primary'],
                                 trial_package['expected_primary'],trial_package['adjudication'],trial_package['expected_adjudication'])
        pred.require(pred.canonical(reference['bundle'])==pred.canonical(inputs['bundles']['reference']))
        trial={'package_sha256':expected_trial,'report_sha256':t['report_sha256'],
               'coverage':_gate(t['n_fully_observed_cases'],n,spec['minimum_trial_coverage_bp']),
               **{k:t[k] for k in ('n_readers','n_assisted_clusters','n_unassisted_clusters',
                   'assisted_minus_unassisted_equal_cluster_bounds','conditional_sharp_null_test')},
               'reference_panel_resolution':reference['receipt']['resolution'],
               'reference_primary_agreement':reference['receipt']['primary_agreement'],
               'reference_cohen_kappa':reference['receipt']['cohen_kappa']}
        if trial['coverage']['status']=='coverage_below_minimum': gaps.append('assisted_trial_coverage_below_minimum')
    elif spec['require_assisted_trial']:
        gaps.append('assisted_trial_missing')
    body={'schema':SCHEMA,'scope':report['scope'],'study':report['study'],'endpoints':report['endpoints'],
          'assessment_plan_sha256':expected_plan,'assessment_report_sha256':expected_report,'policy_sha256':expected_policy,
          'vendor_head':report['vendor_head'],'n_planned':n,'documentary_evidence':{
              'mandatory_missing_count':len(summary['mandatory_missing']),
              'unresolved_clarifications':summary['unresolved_clarifications'],'overdue_clarifications':summary['overdue_clarifications']},
          'predictive_coverage':coverage,'paired_brier_delta_bounds':pr['calibration']['paired']['cohort_brier_delta_bounds'],
          'net_benefit_delta_bounds':[{'threshold_bp':x['threshold_bp'],'candidate_minus_baseline':x['candidate_minus']['baseline']['cohort_delta_bounds']}
                                      for x in pr['decision_curve']['thresholds']],
          'binary_adverse_outcome_delta_bounds':br['multisite']['unit_weighted']['cohort_delta_bounds'],
          'population_shift_status_counts':{s:sum(f['status']==s for f in summary['population_shift_flags']) for s in sorted({f['status'] for f in summary['population_shift_flags']})},
          'assisted_trial':trial,'assisted_trial_status':'replayed' if trial is not None else 'not_supplied',
          'administrative_evidence_gaps':gaps,'source_records_reverified':False,'clinical_validation_established':False,
          'purchasing_authorized':False,'deployment_authorized':False,'decision_grade':False,'publication_safe':False}
    return {**body,'dossier_sha256':pred.digest(body)}


def verify(*args, dossier, expected_dossier, **kw):
    rebuilt=build(*args,**kw)
    return rebuilt['dossier_sha256']==expected_dossier and pred.canonical(rebuilt)==pred.canonical(dossier)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('freeze','build','verify'):
        p=sub.add_parser(name)
        p.add_argument('--workspace',required=True); p.add_argument('--expected-plan',required=True)
        p.add_argument('--policy',required=True)
        if name=='freeze': p.add_argument('--output',required=True)
        else:
            for k in ('expected-policy','expected-head','expected-report'): p.add_argument('--'+k,required=True)
            p.add_argument('--trial-package'); p.add_argument('--expected-trial')
            if name=='build': p.add_argument('--output',required=True)
            else:
                p.add_argument('--dossier',required=True); p.add_argument('--expected-dossier',required=True)
    a=parser.parse_args(argv)
    try:
        directory=storage.private_directory(a.workspace)
        if a.command=='freeze':
            result=freeze(resume.read(directory,'assessment-plan'),a.expected_plan,pred._read(a.policy))
            key='policy_sha256'
        else:
            plan, inputs, _=resume.complete_inputs(directory,a.expected_plan,a.expected_head,a.expected_report)
            args=(plan,a.expected_plan,pred._read(a.policy),a.expected_policy,a.expected_report,inputs)
            kw={'trial_package':pred._read(a.trial_package) if a.trial_package is not None else None,'expected_trial':a.expected_trial}
            if a.command=='verify':
                valid=verify(*args,dossier=pred._read(a.dossier),expected_dossier=a.expected_dossier,**kw)
                print(pred.canonical({'dossier_replayed':valid,'decision_grade':False}).decode('ascii'))
                return 0 if valid else 1
            result=build(*args,**kw); key='dossier_sha256'
        pred._write(a.output,result); print(result[key]); return 0
    except (OSError,ValueError,TypeError,KeyError,RecursionError,vendor.sqlite3.Error):
        print('Dossier operation refused: check private inputs and retained checkpoints.',file=sys.stderr); return 2


if __name__=='__main__':
    sys.exit(main())
