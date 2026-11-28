"""Matched multi-product procurement evidence, without ranking or approval."""
import argparse
from itertools import combinations
import sys

from . import assessment_research_dossier as dossier, assessment_resume_web as resume
from . import assessment_setup_web as storage, assessment_workbench as work
from . import predictive_research as pred, prospective_research as binary
from . import risk_probability as risk, clinical_decision_curve as curve
from . import vendor_workbench as vendor, vendor_submission as submission

SCHEMA = 'assessment-multivendor/1'
MAX_PRODUCTS = 5


def require(ok):
    if not ok:
        raise ValueError('comparison scope, evidence or checkpoint mismatch')


def _without_candidate(spec):
    return {k:v for k,v in spec.items() if k != 'candidate_version'}


def _member(member):
    pred.fields(member, 'plan expected_plan inputs policy expected_policy')
    plan, expected, inputs = (member[k] for k in ('plan','expected_plan','inputs'))
    require(pred.hex64(expected) and plan['plan_sha256'] == expected)
    pp, bp = inputs['predictive_plan'], inputs['binary_plan']
    ps = pred._plan(pp, plan['predictive_plan_sha256'])
    binary._check(bp,plan['binary_plan_sha256'],inputs['manifest'],inputs['audit_key'],inputs['site_keys'])
    with vendor._connect(inputs['db']) as conn:
        request, *_ = vendor._load(conn,plan['vendor_root'],inputs['head'])
    require(work.freeze(request,plan['request_sha256'],plan['vendor_root'],pp,bp)==plan)
    req = submission._request(request,plan['request_sha256'])
    policy = dossier.freeze(plan,expected,member['policy']['policy'])
    require(policy==member['policy'] and pred.hex64(member['expected_policy']) and policy['policy_sha256']==member['expected_policy'])
    common = {'buyer':req['buyer'],'predictive':_without_candidate(ps),
              'binary':_without_candidate(bp['spec']),'manifest':binary.data.validate(inputs['manifest']),
              'subgroup':bp['subgroup'],'requirements':req['requirements'],'policy':policy['policy']}
    scope = {'scope':plan['scope'],'plan_sha256':expected,'policy_sha256':member['expected_policy']}
    return scope,common


def freeze(members):
    """Freeze exact common conditions and the full version-specific product roster."""
    require(isinstance(members,dict) and 2 <= len(members) <= MAX_PRODUCTS and all(risk._token(k) for k in members))
    scopes, identities, common_digest = {}, set(), None
    for name in sorted(members):
        scope,common = _member(members[name])
        digest = pred.digest(common)
        require(common_digest is None or common_digest==digest)
        common_digest = digest
        identity = tuple(scope['scope'][k] for k in ('vendor','product','version'))
        require(identity not in identities)
        identities.add(identity); scopes[name]=scope
    body={'schema':SCHEMA,'common_conditions_sha256':common_digest,'products':scopes}
    return {**body,'comparison_sha256':pred.digest(body)}


def _shared(bundle):
    # Native bundle hashes are different across candidate-specific plans. The
    # underlying common scope, exact rows and retained source pointer must agree.
    return {k:bundle[k] for k in ('scope','source_receipt_sha256','rows')}


def _pair(spec, left, right, labels, a, b):
    # Reuse the native matched engines with distinct internal arm tokens; the
    # actual product identities remain in the surrounding comparison roster.
    common={k:spec[k] for k in ('endpoint','horizon','unit_ids')}
    common.update(baseline_version='left',candidate_version='right')
    cal=risk.freeze({**common,'bin_edges_bp':spec['bin_edges_bp']})
    dec=curve.freeze({**common,'action':spec['action'],'thresholds_bp':spec['thresholds_bp']})
    rows=[{'unit_id':u,'label':labels.get(u),'baseline_bp':a.get(u),'candidate_bp':b.get(u)} for u in spec['unit_ids']]
    cr=risk.analyze(cal,cal['plan_sha256'],rows)
    dr=curve.analyze(dec,dec['plan_sha256'],rows)
    return {'left':left,'right':right,'direction':'right_minus_left','n_planned':len(rows),
            'brier_difference':cr['paired'],
            'net_benefit_differences':[{'threshold_bp':r['threshold_bp'],**r['candidate_minus']['baseline']} for r in dr['thresholds']]}


def analyze(comparison, expected, members, results):
    require(pred.hex64(expected) and comparison['comparison_sha256']==expected and pred.canonical(freeze(members))==pred.canonical(comparison))
    require(isinstance(results,dict) and set(results)==set(members))
    entries, scores, common_bundles = {}, {}, None
    labels = spec = as_of = None
    for name in sorted(members):
        member=members[name]; result=results[name]
        if result is None:
            entries[name]={'status':'not_supplied','dossier':None}; continue
        pred.fields(result,'expected_report dossier expected_dossier trial_package expected_trial')
        plan, expected_plan, inputs, policy, expected_policy=(member[k] for k in ('plan','expected_plan','inputs','policy','expected_policy'))
        expected_dossier=result['expected_dossier']
        require(pred.hex64(expected_dossier))
        rebuilt=dossier.build(plan,expected_plan,policy,expected_policy,result['expected_report'],inputs,
                              trial_package=result['trial_package'],expected_trial=result['expected_trial'])
        require(rebuilt['dossier_sha256']==expected_dossier and pred.canonical(rebuilt)==pred.canonical(result['dossier']))
        pp=inputs['predictive_plan']; bundles=inputs['bundles']; checkpoints=inputs['expected_bundles']
        ps=pred._plan(pp,plan['predictive_plan_sha256'])
        for role in pred.ROLES:
            pred._bundle(pp,pp['plan_sha256'],role,bundles[role],checkpoints[role])
        shared={r:_shared(bundles[r]) for r in ('baseline','reference')}
        require(common_bundles is None or pred.canonical(common_bundles)==pred.canonical(shared))
        require(as_of is None or as_of==inputs['as_of'])
        common_bundles,as_of=shared,inputs['as_of']
        labels=pred._bundle(pp,pp['plan_sha256'],'reference',bundles['reference'],checkpoints['reference'])
        scores[name]=pred._bundle(pp,pp['plan_sha256'],'candidate',bundles['candidate'],checkpoints['candidate'])
        spec=ps
        entries[name]={'status':'replayed','dossier':rebuilt}
    pairs=[_pair(spec,a,b,labels,scores[a],scores[b]) for a,b in combinations(sorted(scores),2)]
    body={'schema':SCHEMA,'comparison_sha256':expected,'products':entries,'as_of':as_of,
          'n_declared':len(members),'n_replayed':len(scores),'all_products_replayed':len(scores)==len(members),
          'shared_baseline_and_reference_sha256':pred.digest(common_bundles) if common_bundles is not None else None,
          'predictive_pairwise_contrasts':pairs,'binary_and_assisted_findings_compared':False,
          'source_records_reverified':False,'clinical_validation_established':False,'purchasing_authorized':False,
          'deployment_authorized':False,'decision_grade':False,'publication_safe':False}
    return {**body,'report_sha256':pred.digest(body)}


def verify(comparison, expected, members, results, report, expected_report):
    rebuilt=analyze(comparison,expected,members,results)
    return rebuilt['report_sha256']==expected_report and pred.canonical(rebuilt)==pred.canonical(report)


def _load(config, *, completed):
    """Trusted local configuration; never accept arbitrary paths from a browser."""
    require(isinstance(config,dict) and 2 <= len(config) <= MAX_PRODUCTS)
    members,results,seen={}, {}, set()
    for name, row in config.items():
        pred.fields(row,'workspace expected_plan expected_head expected_policy result')
        path=storage.private_directory(row['workspace'])
        require(path not in seen); seen.add(path)
        result=row['result']
        if completed and result is not None:
            pred.fields(result,'expected_report expected_dossier trial_package expected_trial')
            plan,inputs,_=resume.complete_inputs(path,row['expected_plan'],row['expected_head'],result['expected_report'])
            require((result['trial_package'] is None)==(result['expected_trial'] is None))
            results[name]={**result,'dossier':resume.read(path,'research-dossier'),
                           'trial_package':pred._read(result['trial_package']) if result['trial_package'] is not None else None}
        else:
            require(completed or result is None)
            if completed: require(not resume.present(path/'research-dossier.json'))
            plan,inputs=resume.base_inputs(path,row['expected_plan'],row['expected_head'])
            results[name]=None
        members[name]={'plan':plan,'expected_plan':row['expected_plan'],'inputs':inputs,
                       'policy':resume.read(path,'research-dossier-policy'),'expected_policy':row['expected_policy']}
    return members,results


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('freeze','analyze','verify'):
        p=sub.add_parser(name); p.add_argument('--config',required=True)
        if name!='freeze':
            p.add_argument('--comparison',required=True); p.add_argument('--expected-comparison',required=True)
        if name=='verify':
            p.add_argument('--report',required=True); p.add_argument('--expected-report',required=True)
        else: p.add_argument('--output',required=True)
    a=parser.parse_args(argv)
    try:
        members,results=_load(pred._read(a.config),completed=a.command!='freeze')
        if a.command=='freeze': result=freeze(members); key='comparison_sha256'
        else:
            args=(pred._read(a.comparison),a.expected_comparison,members,results)
            if a.command=='verify':
                ok=verify(*args,pred._read(a.report),a.expected_report)
                print(pred.canonical({'report_replayed':ok,'decision_grade':False}).decode('ascii')); return 0 if ok else 1
            result=analyze(*args); key='report_sha256'
        pred._write(a.output,result); print(result[key]); return 0
    except (OSError,ValueError,TypeError,KeyError,RecursionError,vendor.sqlite3.Error):
        print('Multi-product comparison refused. Check approved conditions and retained checkpoints.',file=sys.stderr); return 2


if __name__=='__main__':
    sys.exit(main())
