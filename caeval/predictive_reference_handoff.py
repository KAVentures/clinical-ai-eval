"""Coordinator-only source-bound reviewer folders and verified reference staging."""
import argparse
import json
import os
import sys

from . import assessment_setup_web as storage, assessment_resume_web as resume
from . import predictive_reference_panel as panel, predictive_reference_review_web as reviewer
from . import predictive_research as pred, predictive_job_web as jobs


def require(ok):
    if not ok:
        raise ValueError('reference handoff scope, source or checkpoint mismatch')


def read(directory, name):
    return json.loads(storage.read_local(directory, name+'.json'), object_pairs_hook=pred._unique)


def sources(directory, plan, expected_plan):
    directory = storage.private_directory(directory)
    spec = pred._plan(plan, expected_plan)
    protocol = storage.read_local(directory, 'reference-protocol.txt', reviewer.MAX_TEXT)
    require(bool(protocol) and reviewer.sha(protocol) == spec['reference_protocol_sha256'])
    protocol.decode('utf-8')
    values = read(directory, 'reference-sources')
    require(isinstance(values, dict) and set(values) == set(spec['unit_ids']))
    inventory = {}
    for unit, text in values.items():
        require(isinstance(text, str))
        raw = text.encode('utf-8')
        require(0 < len(raw) <= reviewer.MAX_TEXT)
        inventory[unit] = reviewer.sha(raw)
    return protocol, values, inventory


def preview(directory, plan, expected_plan, actors):
    pred.fields(actors, 'coordinator reader_a reader_b adjudicator')
    _, _, inventory = sources(directory, plan, expected_plan)
    return panel.freeze(plan, expected_plan, {**actors, 'source_inventory': inventory})


def freeze(directory, plan, expected_plan, actors, expected_panel):
    directory = storage.private_directory(directory)
    p = preview(directory, plan, expected_plan, actors)
    require(p['panel_sha256'] == expected_panel)
    pred._write(directory/'reference-panel.json', p)
    return p


def material(directory, plan, expected_plan, expected_panel):
    directory = storage.private_directory(directory)
    p = panel._panel(plan, expected_plan, read(directory, 'reference-panel'), expected_panel)
    protocol, values, inventory = sources(directory, plan, expected_plan)
    require(inventory == p['source_inventory'])
    return (plan, expected_plan, p, expected_panel), protocol, values


def primary_inputs(directory, digests):
    pred.fields(digests, 'reader_a reader_b')
    require(all(pred.hex64(v) for v in digests.values()))
    return {r:read(directory, r+'-submission') for r in panel.PRIMARY}, digests


def export(directory, plan, expected_plan, expected_panel, output_root, role, *, primary_digests=None):
    directory = storage.private_directory(directory)
    output_root = storage.private_directory(output_root)
    require(directory != output_root and directory not in output_root.parents and output_root not in directory.parents)
    args, protocol, values = material(directory, plan, expected_plan, expected_panel)
    if role in panel.PRIMARY:
        require(primary_digests is None)
        packet = panel.primary_packet(*args, role)
    else:
        require(role == 'adjudicator' and primary_digests is not None)
        packet = panel.adjudication_packet(*args, *primary_inputs(directory, primary_digests))
    subset = {r['unit_id']:values[r['unit_id']] for r in packet['items']}
    target = output_root/role
    target.mkdir(mode=0o700, exist_ok=False)
    created = []
    try:
        for name, value in [('packet.json', packet), ('sources.json', subset)]:
            path = target/name
            pred._write(path, value); created.append(path)
        path = target/'protocol.txt'
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        created.append(path)
        with os.fdopen(fd, 'wb') as f:
            f.write(protocol)
        reviewer.load(target, packet['packet_sha256'], packet['reader'])
    except BaseException:
        for path in reversed(created): path.unlink()
        target.rmdir()
        raise
    return {'role':role, 'reader':packet['reader'], 'packet_sha256':packet['packet_sha256'], 'n_assigned':len(subset)}


def result(directory, plan, expected_plan, expected_panel, primary_digests, adjudication_digest=None):
    directory = storage.private_directory(directory)
    args, _, _ = material(directory, plan, expected_plan, expected_panel)
    ss, ds = primary_inputs(directory, primary_digests)
    third_path = directory/'adjudicator-submission.json'
    # Never silently ignore a staged third-reader submission.
    require(resume.present(third_path) == (adjudication_digest is not None))
    third = read(directory, 'adjudicator-submission') if adjudication_digest is not None else None
    return panel.finalize(*args, ss, ds, third, adjudication_digest)


def stage(directory, plan, expected_plan, expected_panel, primary_digests, expected_receipt, adjudication_digest=None):
    directory = storage.private_directory(directory)
    package = result(directory, plan, expected_plan, expected_panel, primary_digests, adjudication_digest)
    require(pred.hex64(expected_receipt) and package['receipt']['receipt_sha256'] == expected_receipt)
    paths = {directory/'reference-rows.json':package['bundle']['rows'], directory/'reference-panel-package.json':package}
    require(not any(resume.present(p) for p in (*paths, directory/'collection.json')))
    created = []
    try:
        for path, value in paths.items():
            pred._write(path, value); created.append(path)
    except BaseException:
        for path in reversed(created): path.unlink()
        raise
    return package


def link(child, expected_panel, primary_digests, expected_receipt, baseline_head, candidate_head, as_of, adjudication_digest=None):
    """Use live verified job packages plus independently replayed reference panel."""
    require(child.prepared is not None and child.review is None and not getattr(child,'collection_committed',False))
    plan, inputs = resume.base_inputs(child.destination, child.prepared['plan_sha256'], child.head)
    pp = inputs['predictive_plan']
    package = result(child.staging, pp, plan['predictive_plan_sha256'], expected_panel, primary_digests, adjudication_digest)
    require(package['receipt']['receipt_sha256'] == expected_receipt)
    require(pred.canonical(package) == pred.canonical(read(child.staging,'reference-panel-package')))
    require(pred.canonical(package['bundle']['rows']) == pred.canonical(read(child.staging,'reference-rows')))
    return jobs.stage_metadata(child, {'baseline_head':baseline_head, 'candidate_head':candidate_head, 'as_of':as_of, 'reference_sha256':expected_receipt})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    for name in ('preview','freeze','export','result','stage'):
        cmd = sub.add_parser(name)
        for key in ('directory','plan','expected-plan'): cmd.add_argument('--'+key,required=True)
        if name in ('preview','freeze'): cmd.add_argument('--actors',required=True)
        if name != 'preview': cmd.add_argument('--expected-panel',required=True)
        if name == 'export':
            cmd.add_argument('--output-root',required=True); cmd.add_argument('--role',choices=panel.ROLES,required=True)
        if name in ('export','result','stage'):
            for k in ('reader-a','reader-b'): cmd.add_argument('--expected-'+k,required=name!='export')
        if name in ('result','stage'): cmd.add_argument('--expected-adjudication')
        if name == 'stage': cmd.add_argument('--expected-receipt',required=True)
    a = parser.parse_args(argv)
    try:
        args=(a.directory,pred._read(a.plan),a.expected_plan)
        if a.command=='preview': out=preview(*args,pred._read(a.actors))
        elif a.command=='freeze': out=freeze(*args,pred._read(a.actors),a.expected_panel)
        else:
            args=(*args,a.expected_panel)
            ds={'reader_a':a.expected_reader_a,'reader_b':a.expected_reader_b}
            if a.command=='export':
                require(a.role=='adjudicator' or all(v is None for v in ds.values()))
                out=export(*args,a.output_root,a.role,primary_digests=ds if a.role=='adjudicator' else None)
            else:
                if a.command=='result': package=result(*args,ds,a.expected_adjudication)
                else: package=stage(*args,ds,a.expected_receipt,a.expected_adjudication)
                receipt=package['receipt']
                out={k:receipt[k] for k in ('receipt_sha256','n_planned','resolution','primary_agreement','cohen_kappa')}
                out['bundle_sha256']=package['bundle']['bundle_sha256']
        print(pred.canonical(out).decode('ascii')); return 0
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        print('Reference handoff refused. Check approved files and retained checkpoints.',file=sys.stderr); return 2


if __name__=='__main__':
    sys.exit(main())
