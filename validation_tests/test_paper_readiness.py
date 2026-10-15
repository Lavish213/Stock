import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from lavish_core.trade.live_readiness import REQUIRED, runtime_digest, source_config_digest, validate_approval
from lavish_core.trade.paper_readiness import enforce


def approval(tmp_path, monkeypatch):
    monkeypatch.setenv('TRADE_MODE','paper')
    monkeypatch.setenv('PATREON_ACCESS_TOKEN','test-fixture-not-an-actual-token')
    monkeypatch.setenv('PATREON_CAMPAIGN_ID','123')
    monkeypatch.setenv('DEFAULT_TRADE_DOLLARS','200')
    monkeypatch.setenv('DEFAULT_OPTION_TRADE_DOLLARS','200')
    now=datetime.now(timezone.utc)
    fingerprint=hashlib.sha256(b'fixture-paper').hexdigest()
    digest=runtime_digest(); source=source_config_digest()
    checks={}
    # These constructed approval fixtures test validation mechanics only.
    # They are NOT observations, operational evidence or real approvals.
    for check, sources in REQUIRED.items():
        if check in ('strategy_evaluation','key_rotation'): continue
        provenance=sorted(sources)[0]
        data=dict(check=check,source=provenance,synthetic=False,passed=True,
            observed_at=(now-timedelta(minutes=2)).isoformat(),runtime_sha256=digest,
            account_id_hash=fingerprint,source_id_hash=source)
        payload=json.dumps(data).encode(); file=tmp_path/(check+'.json'); file.write_bytes(payload)
        checks[check]=dict(status='passed',source=provenance,evidence_path=file.name,sha256=hashlib.sha256(payload).hexdigest())
    data=dict(schema_version=1,mode='paper',operator_approved=True,
        issued_at=(now-timedelta(minutes=1)).isoformat(),expires_at=(now+timedelta(days=1)).isoformat(),
        runtime_sha256=digest,source_id_hash=source,paper_account_id_hash=fingerprint,
        limits=dict(max_trade_usd=200,max_gross_exposure=1000,max_daily_loss_usd=100),checks=checks)
    file=tmp_path/'approval.json';file.write_text(json.dumps(data))
    return file,data


def store():
    result=Mock()
    result.get_risk_limits.return_value=dict(circuit_breaker_day_baseline_equity=10000,max_gross_exposure=5000,max_pos_per_symbol=500)
    return result


def test_approval_caps_and_never_clears_halt(tmp_path,monkeypatch):
    file,data=approval(tmp_path,monkeypatch); db=store()
    result=enforce(db,account=dict(id='fixture-paper',equity=10000),path=file)
    assert result['paper_ready'] and not result['live_ready']
    assert not db.set_kill_switch.called
    calls={c.args[0]:c.args[1] for c in db.set_risk_limit.call_args_list}
    assert calls['max_gross_exposure']==1000 and calls['max_pos_per_symbol']==200
    assert not validate_approval(file,account_id='fixture-paper')['live_ready']


@pytest.mark.parametrize('fault',['missing','budget','account','daily_loss','source','synthetic','expired','changed_code'])
def test_unverified_state_halts_entries(tmp_path,monkeypatch,fault):
    file,data=approval(tmp_path,monkeypatch); db=store(); account=dict(id='fixture-paper',equity=10000)
    if fault=='missing': file=tmp_path/'missing.json'
    elif fault=='budget': monkeypatch.setenv('DEFAULT_TRADE_DOLLARS','201')
    elif fault=='account': account['id']='different'
    elif fault=='daily_loss': account['equity']=9900
    elif fault=='source': monkeypatch.setenv('PATREON_CAMPAIGN_ID','124')
    elif fault=='synthetic':
        evidence=tmp_path/data['checks']['source_delivery']['evidence_path']
        payload=json.loads(evidence.read_text());payload['synthetic']=True
        body=json.dumps(payload).encode();evidence.write_bytes(body)
        data['checks']['source_delivery']['sha256']=hashlib.sha256(body).hexdigest();file.write_text(json.dumps(data))
    elif fault=='expired':
        data['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat();file.write_text(json.dumps(data))
    elif fault=='changed_code':
        data['runtime_sha256']='a'*64;file.write_text(json.dumps(data))
    assert not enforce(db,account=account,path=file)['paper_ready']
    assert db.set_kill_switch.call_args.args[0] is True
    assert not db.set_risk_limit.called


def test_broker_read_failure_halts_without_stopping_exit_supervision(monkeypatch):
    from lavish_core.trade import broker
    monkeypatch.setenv('TRADE_MODE','paper')
    monkeypatch.setattr(broker,'get_account',Mock(side_effect=ConnectionError('fixture outage')))
    db=store()
    assert not enforce(db)['paper_ready']
    assert db.set_kill_switch.call_args.args[0]


def test_kill_switch_write_failure_cannot_crash_the_watchdog(monkeypatch):
    # A DB race while recording the halt itself must still return the
    # fail-closed result instead of propagating out of enforce().
    from lavish_core.trade import broker
    monkeypatch.setenv('TRADE_MODE','paper')
    monkeypatch.setattr(broker,'get_account',Mock(side_effect=ConnectionError('fixture outage')))
    db=store(); db.set_kill_switch.side_effect=ConnectionError('fixture db race')
    result=enforce(db)
    assert not result['paper_ready']
