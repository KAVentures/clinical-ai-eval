"""Coordinator workflow for a source-bound, randomized assisted-reader study."""
import argparse
import os
import sys

from . import predictive_assisted_trial as trial, predictive_assisted_reader as reader
from . import predictive_reference_handoff as handoff, predictive_reference_panel as panel
from . import predictive_research as pred, assessment_setup_web as storage
from . import assessment_resume_web as resume

SCHEMA = 'predictive-assisted-coordinator/1'
DESIGN = 'assisted-design'
ALLOCATION = 'assisted-allocation'
PACKAGE = 'assisted-trial-package'


def require(ok):
    if not ok:
        raise ValueError('assisted trial scope or retained checkpoint mismatch')


def _scope(directory, plan, expected_plan, expected_panel):
    d = storage.private_directory(directory)
    ref = panel._panel(plan, expected_plan, handoff.read(d, 'reference-panel'), expected_panel)
    return d, (plan, expected_plan, ref, expected_panel)


def _commitments(d, units):
    inventory = {}
    for role, filename in [('decision', 'decisions.json'), ('assistance', 'assistance.json')]:
        raw = storage.read_local(d, role+'-protocol.txt', reader.ref.MAX_TEXT)
        require(bool(raw)); raw.decode('utf-8')
        inventory[role+'_protocol_sha256'] = reader.ref.sha(raw)
        texts = reader._json(d, filename)
        require(isinstance(texts, dict) and set(texts) == set(units))
        hashes = {}
        for u, text in texts.items():
            require(isinstance(text, str))
            data = text.encode('utf-8')
            require(0 < len(data) <= reader.ref.MAX_TEXT)
            hashes[u] = reader.ref.sha(data)
        inventory[role+'_input_sha256'] = hashes
    return inventory


def preview(directory, plan, expected_plan, expected_panel, settings):
    d, args = _scope(directory, plan, expected_plan, expected_panel)
    pred.fields(settings, 'clinicians cluster_by_unit n_assisted randomization_test')
    return trial.freeze(*args, {**settings, **_commitments(d, plan['spec']['unit_ids'])})


def freeze(directory, plan, expected_plan, expected_panel, settings, expected_design):
    d = storage.private_directory(directory)
    design = preview(d, plan, expected_plan, expected_panel, settings)
    require(design['design_sha256'] == expected_design)
    pred._write(d/(DESIGN+'.json'), design)
    return design


def _design(directory, plan, expected_plan, expected_panel, expected_design):
    d, args = _scope(directory, plan, expected_plan, expected_panel)
    design = trial.check(*args, handoff.read(d, DESIGN), expected_design)
    inventory = _commitments(d, design['unit_ids'])
    require(all(design['spec'][k] == v for k, v in inventory.items()))
    return d, (*args, design, expected_design)


def draw(directory, plan, expected_plan, expected_panel, expected_design, *, confirmed=False):
    """Reserve the single allocation slot before drawing. Never remove an ambiguous slot."""
    require(confirmed is True)
    d, args = _design(directory, plan, expected_plan, expected_panel, expected_design)
    path = d/(ALLOCATION+'.json')
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    # Unlike normal exclusive outputs, failure must not make a new draw possible.
    with os.fdopen(fd, 'wb') as f:
        f.flush(); os.fsync(f.fileno())
        if os.name == 'posix':
            dirfd = os.open(d, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try: os.fsync(dirfd)
            finally: os.close(dirfd)
        allocation = trial.draw(*args, confirmed=True)
        f.write(pred.canonical(allocation)+b'\n'); f.flush(); os.fsync(f.fileno())
    return allocation


def _allocated(directory, plan, expected_plan, expected_panel, expected_design, expected_allocation):
    d, args = _design(directory, plan, expected_plan, expected_panel, expected_design)
    allocation = trial._allocation(args, handoff.read(d, ALLOCATION), expected_allocation)
    return d, args, allocation


def export(directory, plan, expected_plan, expected_panel, expected_design, expected_allocation, clinician, output_root):
    d, args, allocation = _allocated(directory, plan, expected_plan, expected_panel, expected_design, expected_allocation)
    return reader.export(*args, allocation=allocation, allocation_digest=expected_allocation,
                         clinician=clinician, source_directory=d, output_root=output_root)


def _report(package):
    pred.fields(package, 'schema plan panel design allocation primary expected_primary adjudication expected_adjudication expected_reference submissions expected_submissions randomization_assumptions_declared report package_sha256')
    require(package['schema'] == SCHEMA)
    p, ref, design, alloc = (package[k] for k in ('plan','panel','design','allocation'))
    args = (p,p['plan_sha256'],ref,ref['panel_sha256'],design,design['design_sha256'])
    keys = ('primary','expected_primary','adjudication','expected_adjudication','expected_reference',
            'submissions','expected_submissions','randomization_assumptions_declared')
    return trial.analyze(*args, allocation=alloc, allocation_digest=alloc['allocation_sha256'],
                         **{k:package[k] for k in keys})


def result(directory, plan, expected_plan, expected_panel, expected_design, expected_allocation, checkpoints):
    d, args, allocation = _allocated(directory, plan, expected_plan, expected_panel, expected_design, expected_allocation)
    pred.fields(checkpoints, 'expected_primary expected_adjudication expected_reference expected_submissions randomization_assumptions_declared')
    # Recheck source evidence and the full independent reference panel at handoff.
    reference = handoff.result(d, plan, expected_plan, expected_panel, checkpoints['expected_primary'], checkpoints['expected_adjudication'])
    require(reference['receipt']['receipt_sha256'] == checkpoints['expected_reference'])
    primary, _ = handoff.primary_inputs(d, checkpoints['expected_primary'])
    adjudication = handoff.read(d, 'adjudicator-submission') if checkpoints['expected_adjudication'] is not None else None
    require(resume.present(d/'adjudicator-submission.json') == (adjudication is not None))
    clinicians = args[-2]['spec']['clinicians']
    require(isinstance(checkpoints['expected_submissions'], dict) and set(checkpoints['expected_submissions']) == set(clinicians))
    submissions = {c:handoff.read(d, 'trial-reader-'+c+'-submission') for c in clinicians}
    body = {'schema':SCHEMA,'plan':args[0],'panel':args[2],'design':args[4], 'allocation':allocation,
            'primary':primary,'adjudication':adjudication,'submissions':submissions,**checkpoints}
    # The report does not include raw sources, but the package contains restricted labels.
    report = _report({**body,'report':None,'package_sha256':None})
    body['report'] = report
    return {**body,'package_sha256':pred.digest(body)}


def seal(directory, plan, expected_plan, expected_panel, expected_design, expected_allocation, checkpoints, expected_package):
    d = storage.private_directory(directory)
    package = result(d, plan, expected_plan, expected_panel, expected_design, expected_allocation, checkpoints)
    require(pred.hex64(expected_package) and package['package_sha256'] == expected_package)
    pred._write(d/(PACKAGE+'.json'), package)
    return package


def verify_package(package, expected_package):
    """Offline replay checks pinned evidence and computation, not the original source bytes."""
    require(pred.hex64(expected_package) and package['package_sha256'] == expected_package)
    require(pred.digest({k:v for k,v in package.items() if k != 'package_sha256'}) == expected_package)
    require(pred.canonical(_report(package)) == pred.canonical(package['report']))
    return package['report']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('preview','freeze','draw','export','result','seal','verify'):
        cmd = sub.add_parser(name)
        if name == 'verify':
            cmd.add_argument('--package', required=True); cmd.add_argument('--expected-package', required=True)
            continue
        for key in ('directory','plan','expected-plan','expected-panel'): cmd.add_argument('--'+key, required=True)
        if name in ('preview','freeze'): cmd.add_argument('--settings',required=True)
        if name != 'preview': cmd.add_argument('--expected-design',required=True)
        if name == 'draw': cmd.add_argument('--confirm-single-draw',action='store_true')
        if name in ('export','result','seal'): cmd.add_argument('--expected-allocation',required=True)
        if name == 'export':
            cmd.add_argument('--clinician',required=True); cmd.add_argument('--output-root',required=True)
        if name in ('result','seal'): cmd.add_argument('--checkpoints',required=True)
        if name == 'seal': cmd.add_argument('--expected-package',required=True)
    a = parser.parse_args(argv)
    try:
        if a.command == 'verify': out = verify_package(pred._read(a.package),a.expected_package)
        else:
            args = (a.directory,pred._read(a.plan),a.expected_plan,a.expected_panel)
            if a.command == 'preview': out = preview(*args,pred._read(a.settings))
            elif a.command == 'freeze': out = freeze(*args,pred._read(a.settings),a.expected_design)
            elif a.command == 'draw': out = draw(*args,a.expected_design,confirmed=a.confirm_single_draw)
            elif a.command == 'export': out = export(*args,a.expected_design,a.expected_allocation,a.clinician,a.output_root)
            else:
                args = (*args,a.expected_design,a.expected_allocation,pred._read(a.checkpoints))
                package = result(*args) if a.command == 'result' else seal(*args,a.expected_package)
                out = {'package_sha256':package['package_sha256'],'report':package['report']}
        print(pred.canonical(out).decode('ascii')); return 0
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        print('Trial operation refused. Verify retained checkpoints and private inputs; never redraw an uncertain allocation.',file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
