"""Standalone restricted local subgroup analysis; outputs are not publication safe."""
import argparse
import json
import os
import sys

from .subgroup_evidence import SubgroupError, freeze, analyze, contrast

LIMIT = 64 * 1024 * 1024


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise SubgroupError('duplicate JSON key')
        obj[key] = value
    return obj


def _read(path):
    with open(path, 'rb') as f:
        data = f.read(LIMIT+1)
    if len(data) > LIMIT:
        raise SubgroupError('input too large')
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
    pre.add_argument('--inventory', required=True)
    pre.add_argument('--minimum-group', type=int, default=20)
    pre.add_argument('--output', required=True)
    for name in ('analyze', 'contrast'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--plan', required=True)
        cmd.add_argument('--expected-digest', required=True)
        cmd.add_argument('--outcomes', required=True)
        cmd.add_argument('--output', required=True)
        if name == 'contrast':
            cmd.add_argument('--group-a', required=True)
            cmd.add_argument('--group-b', required=True)
            cmd.add_argument('--arm', choices=('baseline', 'candidate', 'paired_delta'), default='candidate')
    args = parser.parse_args(argv)
    try:
        if args.command == 'freeze':
            raw = _read(args.inventory)
            if not isinstance(raw, dict) or set(raw) != {'units', 'groups'}:
                raise SubgroupError('invalid inventory')
            result = freeze(raw['units'], raw['groups'], minimum_group=args.minimum_group)
            _write(args.output, result)
            print(result['plan_sha256'])
        else:
            report = analyze(_read(args.plan), args.expected_digest, _read(args.outcomes))
            if args.command == 'contrast':
                result = {'plan_sha256': args.expected_digest, 'publication_safe': False,
                          'group_a': args.group_a, 'group_b': args.group_b,
                          'contrast': contrast(report, args.group_a, args.group_b, arm=args.arm)}
            else:
                result = report
            _write(args.output, result)
        return 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print('Subgroup operation failed: invalid or inaccessible input/output.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
