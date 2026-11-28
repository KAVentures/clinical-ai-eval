"""Replay a common reference panel and baseline into frozen candidate-specific plans."""
import argparse
import sys

from . import predictive_research as pred, predictive_reference_panel as panel
from . import risk_probability as risk

SCHEMA = 'predictive-shared-evidence/1'
MAX_PRODUCTS = 5


def freeze(plans, expected_plans, anchor):
    pred.require(isinstance(plans,dict) and isinstance(expected_plans,dict))
    pred.require(2 <= len(plans) <= MAX_PRODUCTS and set(plans)==set(expected_plans))
    pred.require(anchor in plans and all(risk._token(k) for k in plans))
    common = None
    for name in sorted(plans):
        spec = pred._plan(plans[name],expected_plans[name])
        current = {k:v for k,v in spec.items() if k!='candidate_version'}
        pred.require(common is None or pred.canonical(current)==pred.canonical(common))
        common = current
    body = {'schema':SCHEMA,'anchor':anchor,'plans':dict(expected_plans),
            'common_spec_sha256':pred.digest(common)}
    return {**body,'design_sha256':pred.digest(body)}


def check(design, expected_design, plans, expected_plans):
    rebuilt = freeze(plans,expected_plans,design['anchor'])
    pred.require(pred.hex64(expected_design) and rebuilt['design_sha256']==expected_design)
    pred.require(pred.canonical(rebuilt)==pred.canonical(design))
    return rebuilt


def build(design, expected_design, plans, expected_plans, evidence):
    """Use one pinned source panel and baseline; never copy candidate predictions."""
    d = check(design,expected_design,plans,expected_plans)
    pred.fields(evidence,'panel expected_panel primary expected_primary adjudication expected_adjudication expected_reference baseline expected_baseline')
    anchor=d['anchor']; plan=plans[anchor]; expected=expected_plans[anchor]
    reference = panel.finalize(plan,expected,evidence['panel'],evidence['expected_panel'],
                               evidence['primary'],evidence['expected_primary'],
                               evidence['adjudication'],evidence['expected_adjudication'])
    receipt = reference['receipt']
    pred.require(pred.hex64(evidence['expected_reference']) and receipt['receipt_sha256']==evidence['expected_reference'])
    baseline = evidence['baseline']
    pred._bundle(plan,expected,'baseline',baseline,evidence['expected_baseline'])
    bundles = {}
    for name in sorted(plans):
        args=(plans[name],expected_plans[name])
        bundles[name] = {
            'baseline':pred.seal(*args,'baseline',baseline['source_receipt_sha256'],baseline['rows']),
            'reference':pred.seal(*args,'reference',receipt['receipt_sha256'],receipt['rows'])}
    body = {'schema':SCHEMA,'design_sha256':expected_design,'anchor':anchor,
            'anchor_reference_receipt_sha256':receipt['receipt_sha256'],
            'anchor_baseline_bundle_sha256':evidence['expected_baseline'],
            'reference_resolution':receipt['resolution'],'reference_primary_agreement':receipt['primary_agreement'],
            'reference_cohen_kappa':receipt['cohen_kappa'],'bundles':bundles,
            'source_records_reverified':False,'external_blinding_verified':False,
            'independent_identity_verified':False,'decision_grade':False,'publication_safe':False}
    return {**body,'package_sha256':pred.digest(body)}


def verify(design, expected_design, plans, expected_plans, evidence, package, expected_package):
    pred.require(pred.hex64(expected_package))
    rebuilt=build(design,expected_design,plans,expected_plans,evidence)
    return rebuilt['package_sha256']==expected_package and pred.canonical(rebuilt)==pred.canonical(package)


def export(design, expected_design, plans, expected_plans, evidence, package, expected_package, product):
    """Return one exact native pair only after replay of the retained source evidence."""
    pred.require(verify(design,expected_design,plans,expected_plans,evidence,package,expected_package))
    pred.require(product in plans)
    pair=package['bundles'][product]
    for role in ('baseline','reference'):
        b=pair[role]
        pred._bundle(plans[product],expected_plans[product],role,b,b['bundle_sha256'])
    body={'schema':SCHEMA,'package_sha256':expected_package,'product':product,
          'plan_sha256':expected_plans[product],'bundles':pair}
    return {**body,'export_sha256':pred.digest(body)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('freeze','build','verify','export'):
        p=sub.add_parser(name); p.add_argument('--plans',required=True)
        if name=='freeze': p.add_argument('--anchor',required=True)
        else:
            for k in ('design','expected-design','evidence'): p.add_argument('--'+k,required=True)
        if name in ('verify','export'):
            p.add_argument('--package',required=True); p.add_argument('--expected-package',required=True)
        if name=='export': p.add_argument('--product',required=True)
        if name!='verify': p.add_argument('--output',required=True)
    a=parser.parse_args(argv)
    try:
        roster=pred._read(a.plans)
        pred.fields(roster,'plans expected_plans')
        plans,heads=roster['plans'],roster['expected_plans']
        if a.command=='freeze': result=freeze(plans,heads,a.anchor); key='design_sha256'
        else:
            args=(pred._read(a.design),a.expected_design,plans,heads,pred._read(a.evidence))
            if a.command=='build': result=build(*args); key='package_sha256'
            else:
                args=(*args,pred._read(a.package),a.expected_package)
                if a.command=='verify':
                    ok=verify(*args); print(pred.canonical({'package_replayed':ok,'decision_grade':False}).decode('ascii'))
                    return 0 if ok else 1
                result=export(*args,a.product); key='export_sha256'
        pred._write(a.output,result); print(result[key]); return 0
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        print('Shared evidence operation refused. Check frozen plans and retained source evidence.',file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
