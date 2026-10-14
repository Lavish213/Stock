"""Release evidence and closing-fee regressions; no external I/O."""
from unittest.mock import Mock
import pytest
from lavish_core.trade.exit_state import ExitState
from lavish_core.trade.live_readiness import validate_approval
from validation_tests.test_readiness_and_options_simulation import approval

@pytest.fixture(autouse=True)
def configured_test_creator(monkeypatch):
    monkeypatch.setenv('PATREON_CAMPAIGN_ID','123')
    monkeypatch.setenv('PATREON_ACCESS_TOKEN','synthetic-fixture-token')

def test_source_change_invalidates_release_approval(tmp_path,monkeypatch):
    path,_=approval(tmp_path)
    assert validate_approval(path,account_id='test-account')['live_ready']
    monkeypatch.setenv('PATREON_CAMPAIGN_ID','456')
    assert not validate_approval(path,account_id='test-account')['live_ready']

def test_missing_source_credentials_invalidates_release_approval(tmp_path,monkeypatch):
    path,_=approval(tmp_path)
    for key in ('PATREON_ACCESS_TOKEN','PATREON_CLIENT_ID','PATREON_CLIENT_SECRET','PATREON_REFRESH_TOKEN','DISCORD_USER_TOKEN'):
        monkeypatch.delenv(key,raising=False)
    assert not validate_approval(path,account_id='test-account')['live_ready']


@pytest.mark.parametrize('missing',['source_delivery','cash_reconciliation'])
def test_release_requires_source_and_cash_evidence(tmp_path,missing):
    import json
    path,data=approval(tmp_path)
    data['checks'].pop(missing,None)
    path.write_text(json.dumps(data))
    assert not validate_approval(path,account_id='test-account')['live_ready']


def attempt(state,submit,poll):
    return state.attempt(submit,Mock(),poll,Mock(return_value=True),1,lambda:None)


def prepare(store):
    oid=store.submit_order('AAPL','buy',2)
    store.log_fill(oid,'AAPL','buy',2,100)
    state=ExitState(2,db_path=store.db_path,symbol='AAPL')
    submit=Mock(return_value={'id':'closing','status':'partially_filled','filled_qty':1,'filled_avg_price':110,'filled_fee':.01})
    assert attempt(state,submit,lambda *a:{'status':'partially_filled','filled_qty':1,'filled_avg_price':110,'filled_fee':.01}) is None
    return state,submit


def test_closing_fee_only_increment_is_booked_once(store):
    state,submit=prepare(store)
    for _ in range(2):
        assert attempt(state,submit,lambda *a:{'status':'partially_filled','filled_qty':1,'filled_avg_price':110,'filled_fee':.03}) is None
    assert store.fetchall("SELECT sum(fee) FROM fills WHERE side='sell'")[0][0]==pytest.approx(.03)
    assert store.get_position('AAPL')['qty']==1
    assert submit.call_count==1


@pytest.mark.parametrize('fee',[-1,'nan',.005])
def test_invalid_or_regressing_fee_without_new_quantity_halts(store,fee):
    state,submit=prepare(store)
    result=attempt(state,submit,lambda *a:{'status':'partially_filled','filled_qty':1,'filled_avg_price':110,'filled_fee':fee})
    assert result['status']=='fill_unverified'
    assert store.get_position('AAPL')['qty']==1
    assert store.get_kill_switch()[0]
    assert submit.call_count==1
