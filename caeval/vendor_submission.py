"""Offline vendor evidence request and bounded submission package verification."""
import argparse
import hashlib
import io
import json
import os
import re
import stat
import struct
import sys
import zipfile

SCHEMA = 'vendor-submission/1'
MAX_ITEMS = 100
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_ARCHIVE = MAX_TOTAL + 1024 * 1024
MAX_MANIFEST = 256 * 1024
STATES = ('supplied', 'unavailable', 'not_applicable_claimed')


class SubmissionError(ValueError):
    pass


def _fields(obj, keys):
    if not isinstance(obj, dict) or set(obj) != set(keys.split()):
        raise SubmissionError('unexpected fields')


def _token(x):
    return isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', x) is not None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def _unique(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise SubmissionError('duplicate JSON key')
        out[k] = v
    return out


def freeze(spec):
    """Lock a buyer's request scope and evidence inventory before submission."""
    _fields(spec, 'request_id buyer vendor product version requirements')
    if not all(_token(spec[k]) for k in ('request_id', 'buyer', 'vendor', 'product', 'version')):
        raise SubmissionError('invalid request scope')
    requirements = spec['requirements']
    if not isinstance(requirements, list) or not 1 <= len(requirements) <= MAX_ITEMS:
        raise SubmissionError('invalid requirement inventory')
    seen = set()
    clean = []
    for r in requirements:
        _fields(r, 'id mandatory description')
        if not _token(r['id']) or r['id'] in seen or type(r['mandatory']) is not bool:
            raise SubmissionError('invalid or duplicate requirement')
        if not isinstance(r['description'], str) or not 1 <= len(r['description'].strip()) <= 500 or len(r['description']) > 500:
            raise SubmissionError('invalid requirement description')
        seen.add(r['id'])
        clean.append(dict(r))
    plan = {**spec, 'requirements': sorted(clean, key=lambda x: x['id'])}
    return {'schema': SCHEMA, 'request': plan, 'request_sha256': _sha(_canonical([SCHEMA, plan]))}


def _request(frozen, expected):
    _fields(frozen, 'schema request request_sha256')
    if freeze(frozen['request']) != frozen or frozen['request_sha256'] != expected:
        raise SubmissionError('request digest mismatch')
    return frozen['request']


def _name(i):
    return 'evidence/' + i + '.bin'


def _manifest(request, expected, responses, blobs):
    if not isinstance(responses, dict) or set(responses) != {r['id'] for r in request['requirements']}:
        raise SubmissionError('response inventory differs from request')
    if not isinstance(blobs, dict):
        raise SubmissionError('invalid artifact inventory')
    items = []
    needed = set()
    total = 0
    for i, response in sorted(responses.items()):
        _fields(response, 'status explanation')
        status, explanation = response['status'], response['explanation']
        if status not in STATES or not isinstance(explanation, str) or len(explanation) > 500:
            raise SubmissionError('invalid response')
        if status != 'supplied' and not explanation.strip():
            raise SubmissionError('missing explanation')
        item = {'id': i, 'status': status, 'explanation': explanation, 'artifact': None}
        if status == 'supplied':
            needed.add(i)
            raw = blobs.get(i)
            if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_FILE:
                raise SubmissionError('missing or oversized artifact')
            total += len(raw)
            if total > MAX_TOTAL:
                raise SubmissionError('submission size exceeded')
            item['artifact'] = {'path': _name(i), 'bytes': len(raw), 'sha256': _sha(raw)}
        items.append(item)
    if set(blobs) != needed:
        raise SubmissionError('unexpected artifact')
    return {'schema': SCHEMA, 'request_sha256': expected,
            'scope': {k: request[k] for k in ('request_id', 'buyer', 'vendor', 'product', 'version')}, 'items': items}


def build(frozen, expected, responses, blobs):
    """Construct an opaque, deterministic ZIP; never interpret evidence bytes."""
    req = _request(frozen, expected)
    manifest = _manifest(req, expected, responses, blobs)
    manifest_raw = _canonical(manifest)
    if len(manifest_raw) > MAX_MANIFEST:
        raise SubmissionError('manifest too large')
    members = {'submission.json': manifest_raw, **{_name(i): raw for i, raw in blobs.items()}}
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_STORED) as z:
        for name, raw in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            z.writestr(info, raw)
    result = out.getvalue()
    if len(result) > MAX_ARCHIVE:
        raise SubmissionError('archive too large')
    return result


def verify(frozen, expected, raw):
    """Validate byte binding and completeness; never extract or open artifacts."""
    req = _request(frozen, expected)
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_ARCHIVE:
        raise SubmissionError('invalid archive size')
    allowed = {'submission.json', *(_name(r['id']) for r in req['requirements'])}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            members = z.infolist()
            names = [m.filename for m in members]
            if len(names) != len(set(names)) or not set(names) <= allowed or 'submission.json' not in names:
                raise SubmissionError('unexpected or duplicate archive member')
            total = 0
            for m in members:
                limit = MAX_MANIFEST if m.filename == 'submission.json' else MAX_FILE
                mode = stat.S_IFMT(m.external_attr >> 16)
                if (m.orig_filename != m.filename or m.is_dir() or mode not in (0, stat.S_IFREG)
                        or m.compress_type != zipfile.ZIP_STORED or m.flag_bits & 1
                        or not 1 <= m.file_size <= limit or m.compress_size != m.file_size):
                    raise SubmissionError('unsupported or oversized archive member')
                if m.filename != 'submission.json':
                    total += m.file_size
            if total > MAX_TOTAL:
                raise SubmissionError('submission size exceeded')
            manifest_raw = z.read('submission.json')
            manifest = json.loads(manifest_raw, object_pairs_hook=_unique)
            _fields(manifest, 'schema request_sha256 scope items')
            if not isinstance(manifest['items'], list) or len(manifest['items']) != len(req['requirements']):
                raise SubmissionError('invalid item inventory')
            responses, blobs = {}, {}
            for item in manifest['items']:
                _fields(item, 'id status explanation artifact')
                i = item['id']
                if not _token(i) or i in responses or _name(i) not in allowed:
                    raise SubmissionError('unexpected or duplicate item')
                responses[i] = {k: item[k] for k in ('status', 'explanation')}
                if item['status'] == 'supplied':
                    if _name(i) not in names:
                        raise SubmissionError('artifact absent')
                    blobs[i] = z.read(_name(i))
            if set(names) != {'submission.json', *(_name(i) for i in blobs)}:
                raise SubmissionError('unreferenced member')
            rebuilt = _manifest(req, expected, responses, blobs)
            if _canonical(rebuilt) != manifest_raw:
                raise SubmissionError('manifest or artifact binding mismatch')
    except (zipfile.BadZipFile, json.JSONDecodeError, RuntimeError, NotImplementedError, OSError, UnicodeError,
            RecursionError, TypeError, KeyError, EOFError, struct.error, OverflowError) as exc:
        raise SubmissionError('invalid submission archive') from exc
    gaps = [r['id'] for r in req['requirements'] if r['mandatory'] and responses[r['id']]['status'] != 'supplied']
    return {'schema': SCHEMA, 'request_sha256': expected, 'archive_sha256': _sha(raw),
            'integrity': 'VERIFIED_BYTES_ONLY', 'mandatory_missing': gaps,
            'coverage': 'MANDATORY_FILES_SUPPLIED' if not gaps else 'MANDATORY_FILES_MISSING',
            'items': [{'id': i['id'], 'status': i['status'], 'artifact': i['artifact']} for i in rebuilt['items']],
            'decision_grade': False, 'publication_safe': False}


def _read(path, limit):
    with open(path, 'rb') as f:
        raw = f.read(limit + 1)
    if len(raw) > limit:
        raise SubmissionError('input too large')
    return raw


def _write(path, raw):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
    except BaseException:
        os.unlink(path)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    pre = sub.add_parser('freeze')
    pre.add_argument('--spec', required=True)
    pre.add_argument('--output', required=True)
    check = sub.add_parser('verify')
    check.add_argument('--request', required=True)
    check.add_argument('--expected-digest', required=True)
    check.add_argument('--archive', required=True)
    check.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'freeze':
            plan = freeze(json.loads(_read(args.spec, MAX_MANIFEST), object_pairs_hook=_unique))
            _write(args.output, _canonical(plan) + b'\n')
            print(plan['request_sha256'])
            return 0
        plan = json.loads(_read(args.request, MAX_MANIFEST), object_pairs_hook=_unique)
        report = verify(plan, args.expected_digest, _read(args.archive, MAX_ARCHIVE))
        _write(args.output, _canonical(report) + b'\n')
        return 1 if report['mandatory_missing'] else 0
    except (ValueError, TypeError, OSError, RecursionError):
        print('Vendor submission operation failed: invalid or inaccessible input/output.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
