"""Standalone local dataset integrity command; no central CLI integration needed."""
import argparse
import json
import os
import sys

from .dataset_integrity import IntegrityError, audit, canonical, plan_grouped, verify

MAX_INPUT = 64 * 1024 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IntegrityError('duplicate JSON object key')
        result[key] = value
    return result


def _read(path, limit):
    with open(path, 'rb') as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise IntegrityError('input size limit exceeded')
    return data


def _json(path):
    return json.loads(_read(path, MAX_INPUT), object_pairs_hook=_unique)


def _private_new(path, data):
    # Never overwrite an existing manifest or follow an existing symlink.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(canonical(data) + b'\n')
    except BaseException:
        os.unlink(path)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('audit', 'verify', 'plan'):
        command = sub.add_parser(name)
        command.add_argument('--manifest', required=True)
        command.add_argument('--key-file', required=True, help='local binary secret, at least 32 bytes')
        if name == 'audit':
            command.add_argument('--design', choices=('grouped', 'temporal', 'site', 'temporal_site'), default='grouped')
        elif name == 'verify':
            command.add_argument('--receipt', required=True)
        else:
            command.add_argument('--output-manifest', required=True, help='new private local file; never overwritten')
            command.add_argument('--train', type=int, default=60)
            command.add_argument('--tune', type=int, default=20)
    args = parser.parse_args(argv)
    try:
        rows = _json(args.manifest)
        key = _read(args.key_file, 4096)
        if args.command == 'verify':
            valid = verify(rows, key, _json(args.receipt))
            print(json.dumps({'receipt_matches': valid, 'decision_grade': False}))
            return 0 if valid else 1
        if args.command == 'plan':
            planned, report = plan_grouped(rows, key, train=args.train, tune=args.tune)
            _private_new(args.output_manifest, planned)
            print(canonical(report).decode('ascii'))
            return 0
        report = audit(rows, key, design=args.design)
        print(canonical(report).decode('ascii'))
        return 0 if report['status'] == 'NO_DETECTED_OVERLAP' else 1
    except (OSError, ValueError, TypeError, RecursionError):
        # Do not echo paths, parser exceptions, record values or secrets.
        print('Dataset integrity operation failed: invalid or inaccessible input/output.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
