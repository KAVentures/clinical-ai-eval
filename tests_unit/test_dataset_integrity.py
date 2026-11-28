import copy
import hashlib
import pytest
from caeval.dataset_integrity import audit, verify, plan_grouped, IntegrityError, canonical

KEY = b'k' * 32


def row(i, partition='train', **changes):
    r = dict(record_id='record-' + str(i), partition=partition, patient_key='patient-' + str(i),
             lineage_key='case-' + str(i), content_sha256=hashlib.sha256(str(i).encode()).hexdigest(),
             site_key='site-a' if partition != 'test' else 'site-b',
             observed_at='2025-01-01' if partition != 'test' else '2025-02-01')
    r.update(changes)
    return r


def test_clean_receipt_and_tamper():
    rows = [row(1), row(2, 'test')]
    report = audit(rows, KEY, design='temporal_site')
    assert report['status'] == 'NO_DETECTED_OVERLAP'
    assert report['decision_grade'] is False
    assert verify(rows[::-1], KEY, report)
    assert not verify(rows, b'x' * 32, report)
    assert not verify([row(1), row(3, 'test')], KEY, report)
    tampered = copy.deepcopy(report)
    tampered['counts']['test'] = 999
    assert not verify(rows, KEY, tampered)
    for secret in ('record-1', 'patient-1', 'case-1', 'site-a', '2025-01-01'):
        assert secret.encode() not in canonical(report)


@pytest.mark.parametrize('field', ['patient_key', 'lineage_key', 'content_sha256'])
def test_leakage_across_all_partitions(field):
    a = row(1)
    b = row(2, 'tune', **{field: a[field]})
    c = row(3, 'test', **{field: a[field]})
    report = audit([a, b, c], KEY)
    assert report['status'] == 'FAIL'
    assert report['overlap'][field] == {'groups': 1, 'records': 3}


def test_site_and_temporal_holdouts():
    rows = [row(1), row(2, 'test', site_key='site-a', observed_at='2025-01-01')]
    assert audit(rows, KEY)['status'] == 'NO_DETECTED_OVERLAP'
    report = audit(rows, KEY, design='temporal_site')
    assert set(report['violations']) == {'site_holdout_overlap', 'temporal_holdout_overlap'}
    assert report['status'] == 'FAIL'


def test_missing_not_a_pass_and_failure_precedence():
    rows = [row(1, patient_key=None), row(2, 'test')]
    assert audit(rows, KEY)['status'] == 'INCOMPLETE'
    assert audit([row(1)], KEY)['status'] == 'INCOMPLETE'
    rows[1]['lineage_key'] = rows[0]['lineage_key']
    assert audit(rows, KEY)['status'] == 'FAIL'
    assert audit([row(1), row(2, 'test', observed_at=None)], KEY, design='temporal')['status'] == 'INCOMPLETE'


@pytest.mark.parametrize('changes', [{'record_id': ''}, {'partition': 'validation'}, {'observed_at': '2025-02-30'}, {'content_sha256': 'x'}, {'unknown': 1}, {'site_key': []}])
def test_invalid_records(changes):
    with pytest.raises(IntegrityError):
        audit([row(1, **changes), row(2, 'test')], KEY)


def test_duplicate_and_short_key():
    with pytest.raises(IntegrityError):
        audit([row(1), row(1, 'test')], KEY)
    with pytest.raises(IntegrityError):
        audit([row(1)], b'weak')


def test_transitive_split_planning_and_order_independence():
    rows = [row(i, None) for i in range(10)]
    rows[1]['patient_key'] = rows[0]['patient_key']
    rows[2]['lineage_key'] = rows[1]['lineage_key']
    rows[3]['content_sha256'] = rows[2]['content_sha256']
    before = copy.deepcopy(rows)
    planned, receipt = plan_grouped(rows, KEY)
    assert rows == before
    assert len({r['partition'] for r in planned[:4]}) == 1
    assert {r['partition'] for r in planned} == {'train', 'tune', 'test'}
    assert receipt['component_count'] == 7
    assert receipt['largest_component'] == 4
    assert receipt['audit']['status'] == 'NO_DETECTED_OVERLAP'
    assert plan_grouped(rows[::-1], KEY) == (planned, receipt)
    assert verify(planned, KEY, receipt['audit'])


def test_impossible_split_and_missing_metadata():
    with pytest.raises(IntegrityError):
        plan_grouped([row(1, None), row(2, None)], KEY)
    with pytest.raises(IntegrityError):
        plan_grouped([row(1, None, patient_key=None), row(2, None), row(3, None)], KEY)
    with pytest.raises(IntegrityError):
        plan_grouped([row(1, None), row(2, None), row(3, None)], KEY, train=90, tune=20)


def test_cli_roundtrip_and_private_output(tmp_path, capsys):
    import json
    import os
    from caeval.dataset_integrity_cli import main
    key = tmp_path / 'key'
    key.write_bytes(KEY)
    manifest = tmp_path / 'input.json'
    manifest.write_text(json.dumps([row(i, None) for i in range(5)]))
    output = tmp_path / 'planned.json'
    args = ['--manifest', str(manifest), '--key-file', str(key)]
    assert main(['plan', *args, '--output-manifest', str(output)]) == 0
    report = json.loads(capsys.readouterr().out)
    if os.name == 'posix':
        assert output.stat().st_mode & 0o777 == 0o600
    assert main(['plan', *args, '--output-manifest', str(output)]) == 2
    capsys.readouterr()
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps(report['audit']))
    assert main(['verify', '--manifest', str(output), '--key-file', str(key), '--receipt', str(receipt)]) == 0
    assert json.loads(capsys.readouterr().out)['receipt_matches'] is True
    assert main(['audit', '--manifest', str(output), '--key-file', str(key)]) == 0
    assert json.loads(capsys.readouterr().out) == report['audit']


def test_cli_duplicate_json_keys_not_echoed(tmp_path, capsys):
    from caeval.dataset_integrity_cli import main
    key = tmp_path / 'key'
    key.write_bytes(KEY)
    manifest = tmp_path / 'input.json'
    manifest.write_text('[{"private-value": 1, "private-value": 2}]')
    assert main(['audit', '--manifest', str(manifest), '--key-file', str(key)]) == 2
    assert 'private-value' not in capsys.readouterr().err
