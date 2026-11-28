import copy
import io
import json
import stat
import zipfile

import pytest
from caeval.vendor_submission import freeze, build, verify, SubmissionError, main


def plan():
    return freeze(dict(request_id='r1', buyer='hospital', vendor='vendor', product='ai', version='v1',
                       requirements=[dict(id='safety', mandatory=True, description='Safety report'),
                                     dict(id='extra', mandatory=False, description='Extra documentation')]))


def package(p=None, missing=False):
    p = p or plan()
    responses = {'extra': {'status': 'not_applicable_claimed', 'explanation': 'vendor claim'},
                 'safety': {'status': 'unavailable' if missing else 'supplied', 'explanation': 'pending' if missing else ''}}
    return build(p, p['request_sha256'], responses, {} if missing else {'safety': b'opaque evidence'})


def checked(raw, p=None):
    p = p or plan()
    return verify(p, p['request_sha256'], raw)


def rewrite(raw, transform, compression=zipfile.ZIP_STORED):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        members = [(name, z.read(name)) for name in z.namelist()]
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', compression=compression) as z:
        for name, data in transform(members):
            z.writestr(name, data)
    return result.getvalue()


def test_deterministic_and_complete():
    raw = package()
    assert raw == package()
    r = checked(raw)
    assert r['coverage'] == 'MANDATORY_FILES_SUPPLIED'
    assert r['integrity'] == 'VERIFIED_BYTES_ONLY'
    assert not r['decision_grade'] and not r['publication_safe']
    assert 'vendor claim' not in str(r) and 'opaque evidence' not in str(r)
    assert not r['mandatory_missing']


def test_missing_requirement_not_waived():
    r = checked(package(missing=True))
    assert r['mandatory_missing'] == ['safety']
    assert r['coverage'] == 'MANDATORY_FILES_MISSING'


def test_claimed_nonapplicability_does_not_waive_mandatory():
    p = plan()
    responses = {i: {'status': 'not_applicable_claimed', 'explanation': 'vendor claim'} for i in ('extra', 'safety')}
    assert checked(build(p, p['request_sha256'], responses, {}), p)['mandatory_missing'] == ['safety']


def test_plan_scope_and_digest():
    p = plan()
    q = freeze({**p['request'], 'version': 'v2'})
    with pytest.raises(SubmissionError):
        checked(package(), q)
    with pytest.raises(SubmissionError):
        verify(p, 'bad', package())
    p['request']['requirements'][0]['mandatory'] = True
    with pytest.raises(SubmissionError):
        checked(package(), p)


@pytest.mark.parametrize('change', [lambda xs: xs+[('../bad', b'x')],
                                    lambda xs: xs+[('evidence/extra.bin', b'x')],
                                    lambda xs: [(k,v) for k,v in xs if k!='evidence/safety.bin'],
                                    lambda xs: [(k,b'changed' if k=='evidence/safety.bin' else v) for k,v in xs],
                                    lambda xs: [(k,v+b' ' if k=='submission.json' else v) for k,v in xs]])
def test_bad_members(change):
    with pytest.raises(SubmissionError):
        checked(rewrite(package(), change))


def test_duplicate_members():
    with pytest.warns(UserWarning):
        raw = rewrite(package(), lambda xs: xs+xs[:1])
    with pytest.raises(SubmissionError):
        checked(raw)


def test_compressed_and_symlink_members():
    with pytest.raises(SubmissionError):
        checked(rewrite(package(), lambda xs: xs, zipfile.ZIP_DEFLATED))
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        info = zipfile.ZipInfo('submission.json')
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, b'target')
    with pytest.raises(SubmissionError):
        checked(out.getvalue())


def test_duplicate_json_and_corruption():
    with pytest.raises(SubmissionError):
        checked(rewrite(package(), lambda xs: [(k,b'{"x":1,"x":2}' if k=='submission.json' else v) for k,v in xs]))
    with pytest.raises(SubmissionError):
        checked(b'not a zip')
    with pytest.raises(SubmissionError):
        checked(rewrite(package(), lambda xs: [(k,b'{' if k=='submission.json' else v) for k,v in xs]))


@pytest.mark.parametrize('responses,blobs', [({}, {}),
    ({'extra': {'status':'unavailable','explanation':'x'}, 'safety': {'status':'supplied','explanation':''}}, {}),
    ({'extra': {'status':'unavailable','explanation':'x'}, 'safety': {'status':'unavailable','explanation':''}}, {}),
    ({'extra': {'status':'unavailable','explanation':'x'}, 'safety': {'status':'unavailable','explanation':'x'}}, {'safety': b'x'})])
def test_invalid_builder(responses, blobs):
    p = plan()
    with pytest.raises(SubmissionError):
        build(p, p['request_sha256'], responses, blobs)


def test_cli_roundtrip(tmp_path, capsys):
    p = plan()
    spec = tmp_path/'spec.json'
    spec.write_text(json.dumps(p['request']))
    request = tmp_path/'request.json'
    assert main(['freeze','--spec',str(spec),'--output',str(request)]) == 0
    digest = capsys.readouterr().out.strip()
    assert digest == p['request_sha256']
    archive = tmp_path/'submission.zip'
    archive.write_bytes(package(missing=True))
    report = tmp_path/'report.json'
    args = ['verify','--request',str(request),'--expected-digest',digest,'--archive',str(archive),'--output',str(report)]
    assert main(args) == 1
    assert json.loads(report.read_text()) == checked(archive.read_bytes())
    assert main(args) == 2
