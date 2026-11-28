"""Real native evaluator rehearsal; no mocked assessment or job service."""
import json
import pytest
from caeval import evaluator_native_mcp_acceptance as a


def test_native_remote_mcp_acceptance():
    result=a.rehearse()
    assert result['native_grant']=='passed'
    assert result['explicit_local_approval']=='passed'
    assert result['mock_evaluation']=='passed'
    assert result['metadata_receipt_integrity']=='passed'
    assert result['grant_isolation_and_revocation']=='passed'
    assert result['n_verified_packages']>0
    assert not result['real_client_tested'] and not result['tls_proxy_tested']
    assert not result['clinical_validation_established'] and not result['decision_grade']


def test_cli_safe_failure(monkeypatch,capsys):
    def fail(): raise ValueError('private exception text')
    monkeypatch.setattr(a,'rehearse',fail)
    assert a.main()==2 and 'private exception' not in capsys.readouterr().out


def test_cli_success(monkeypatch,capsys):
    monkeypatch.setattr(a,'rehearse',lambda:{'schema':'test'})
    assert a.main()==0
    assert json.loads(capsys.readouterr().out)=={'schema':'test'}
