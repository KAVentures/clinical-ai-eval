"""Resume a local assessment only from independently retained checkpoints."""
import argparse
import base64
import json
import secrets
import sys
from threading import Lock
from wsgiref.simple_server import make_server

from . import assessment_setup_web as setup, assessment_workbench_web as web
from . import assessment_workbench as work, predictive_research as pred
from . import prospective_research as binary, vendor_workbench as vendor

BASE = ('request', 'assessment-plan', 'predictive-plan', 'binary-plan', 'manifest')
COLLECTION = (*pred.ROLES, 'outcomes', 'reference-counts', 'target-counts', 'config')


def require(ok):
    if not ok:
        raise ValueError('workspace or retained checkpoint mismatch')


def read(directory, name):
    return json.loads(setup.read_local(directory, name+'.json', pred.LIMIT), object_pairs_hook=pred._unique)


def present(path):
    return path.exists() or path.is_symlink()


def base_inputs(directory, expected_plan, head):
    require(pred.hex64(expected_plan) and pred.hex64(head))
    values = {name: read(directory, name) for name in BASE}
    plan, bp, pp = (values[k] for k in ('assessment-plan', 'binary-plan', 'predictive-plan'))
    require(plan['plan_sha256'] == expected_plan)
    key, sites = binary._read_keys(directory/'keys.json')
    binary._check(bp, plan['binary_plan_sha256'], values['manifest'], key, sites)
    db = directory/'procurement.db'
    with vendor._connect(db) as conn:
        request, *_ = vendor._load(conn, plan['vendor_root'], head)
    require(pred.canonical(request) == pred.canonical(values['request']))
    require(pred.canonical(work.freeze(request, plan['request_sha256'], plan['vendor_root'], pp, bp)) == pred.canonical(plan))
    return plan, {'db': str(db), 'head': head, 'predictive_plan': pp, 'binary_plan': bp,
                  'manifest': values['manifest'], 'audit_key': key, 'site_keys': sites}


def complete_inputs(directory, expected_plan, head, expected_report):
    require(pred.hex64(expected_report))
    plan, inputs = base_inputs(directory, expected_plan, head)
    cfg = read(directory, 'config')
    pred.fields(cfg, 'db head as_of predictive_plan bundles expected_bundles binary_plan manifest keys outcomes reference_counts target_counts')
    pred.fields(cfg['bundles'], 'baseline candidate reference')
    pred.fields(cfg['expected_bundles'], 'baseline candidate reference')
    # Never follow paths from a recovered configuration until verified.
    paths = {'db':'procurement.db', 'predictive_plan':'predictive-plan.json', 'binary_plan':'binary-plan.json',
             'manifest':'manifest.json', 'keys':'keys.json', 'outcomes':'outcomes.json',
             'reference_counts':'reference-counts.json', 'target_counts':'target-counts.json'}
    require(all(cfg[k] == str(directory/v) for k,v in paths.items()) and cfg['head'] == head)
    require(cfg['bundles'] == {r:str(directory/(r+'.json')) for r in pred.ROLES})
    inputs.update(as_of=cfg['as_of'], expected_bundles=cfg['expected_bundles'],
                  bundles={r:read(directory,r) for r in pred.ROLES},
                  outcomes=read(directory,'outcomes'), reference_counts=read(directory,'reference-counts'),
                  target_counts=read(directory,'target-counts'))
    report = work.analyze(plan, expected_plan, **inputs)
    require(report['report_sha256'] == expected_report)
    return plan, inputs, report


class PinnedReview(web.Operator):
    """Retain the external report checkpoint across runs and downloads."""
    def __init__(self, directory, expected_plan, head, expected_report):
        super().__init__(str(directory/'assessment-plan.json'), expected_plan, str(directory/'config.json'))
        self.directory, self.head, self.expected_report = directory, head, expected_report
        self.inputs()

    def inputs(self):
        plan, inputs, _ = complete_inputs(self.directory, self.expected_plan, self.head, self.expected_report)
        return plan, inputs


class Resume(setup.Setup):
    def __init__(self, staging, destination, expected_plan, head, expected_report=None):
        self.staging = setup.private_directory(staging)
        self.destination = setup.private_directory(destination)
        require(self.staging != self.destination)
        require(not any(present(self.destination/('procurement.db'+suffix)) for suffix in ('-journal','-wal','-shm')))
        self.prepared, _ = base_inputs(self.destination, expected_plan, head)
        self.head = head
        found = [present(self.destination/(name+'.json')) for name in COLLECTION]
        require(all(found) or not any(found))
        require((expected_report is not None) == all(found))
        self.password = secrets.token_urlsafe(32)
        self.auth = 'Basic '+base64.b64encode(('operator:'+self.password).encode('ascii')).decode('ascii')
        self.csrf = secrets.token_hex(32)
        self.host = None
        self.lock = Lock()
        self.preview = self.collection = self.review = None
        self.collection_committed = all(found)
        if self.collection_committed:
            self.review = self._review(expected_report)

    def _review(self, checkpoint):
        app = PinnedReview(self.destination, self.prepared['plan_sha256'], self.head, checkpoint)
        app.host, app.auth, app.csrf = self.host, self.auth, self.csrf
        return app

    def import_vendor(self):
        require(not self.collection_committed)
        super().import_vendor()

    def clarify(self):
        require(not self.collection_committed)
        super().clarify()

    def preview_collection(self):
        require(not self.collection_committed)
        super().preview_collection()

    def commit_collection(self):
        require(not self.collection_committed and self.collection is not None)
        checkpoint = self.collection[1]
        super().commit_collection()
        self.collection_committed = True
        # If persisted collection validation fails, do not offer a review
        # session; the complete files need supervised checkpoint recovery.
        try:
            self.review = self._review(checkpoint)
        except Exception:
            self.review = None
            raise

    def __call__(self, env, start):
        if self.review is not None:
            self.review.host = self.host
        return super().__call__(env,start)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('staging','destination','expected-plan','expected-head'):
        parser.add_argument('--'+key, required=True)
    parser.add_argument('--expected-report')
    a = parser.parse_args(argv)
    try:
        app = Resume(a.staging,a.destination,a.expected_plan,a.expected_head,a.expected_report)
        with make_server('127.0.0.1',0,app,handler_class=web.QuietHandler) as server:
            app.host = '127.0.0.1:'+str(server.server_port)
            print('Open http://'+app.host+' in a local browser. Username: operator',flush=True)
            print('Session password: '+app.password,flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError,ValueError,TypeError,KeyError,RecursionError,vendor.sqlite3.Error):
        print('Workspace resume refused. Reconcile against independently retained checkpoints.',file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
