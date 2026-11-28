"""Local decision-curve CLI; all output files are restricted research artifacts."""
import argparse
import json
import os
import sys

from .clinical_decision_curve import CurveError, freeze, analyze

LIMIT = 64 * 1024 * 1024


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise CurveError('duplicate JSON key')
        obj[key] = value
    return obj


def _read(path):
    with open(path, 'rb') as f:
        data = f.read(LIMIT+1)
    if len(data) > LIMIT:
        raise CurveError('input too large')
    return json.loads(data, object_pairs_hook=_unique)


def _write(path, obj):
    data = json.dumps(obj, sort_keys=True, indent=2, allow_nan=False).encode('utf-8') + b'\n'
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
    except BaseException:
        os.unlink(path)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    pre = sub.add_parser('freeze')
    pre.add_argument('--spec', required=True)
    pre.add_argument('--output', required=True)
    run = sub.add_parser('analyze')
    run.add_argument('--plan', required=True)
    run.add_argument('--expected-digest', required=True)
    run.add_argument('--outcomes', required=True)
    run.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'freeze':
            plan = freeze(_read(args.spec))
            _write(args.output, plan)
            print(plan['plan_sha256'])
        else:
            report = analyze(_read(args.plan), args.expected_digest, _read(args.outcomes))
            _write(args.output, report)
        return 0
    except (OSError, ValueError, TypeError, RecursionError):
        print('Decision-curve operation failed: invalid or inaccessible input/output.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
