from types import SimpleNamespace
import pytest
from lavish_core.utils.worker_health import WorkerHealth
from lavish_core.validation.paper_market_preflight import check_market
from lavish_core.trade import broker_alpaca as broker
from lavish_core.validation.options_replay import Response

@pytest.mark.parametrize('fault',['dead','stale','clock_regressed'])
def test_worker_health_detects_failed_progress(fault):
    clock=[10.];health=WorkerHealth(lambda:clock[0]);health.register('reconcile',30)
    alive={'reconcile':True}
    if fault=='dead':alive['reconcile']=False
    elif fault=='stale':clock[0]=41
    else:clock[0]=9
    assert not health.status(alive)['healthy']

def test_worker_progress_refresh_is_thread_specific():
    clock=[0.];health=WorkerHealth(lambda:clock[0]);health.register('reconcile',10);health.register('account',20)
    clock[0]=9;health.beat('reconcile');clock[0]=15
    assert health.status({'reconcile':True,'account':True})['healthy']
    clock[0]=21
    assert set(health.status({'reconcile':True,'account':True})['reasons'])=={'reconcile:no_progress','account:no_progress'}

@pytest.mark.parametrize('age',[0,-1,float('nan'),float('inf'),True])
def test_worker_deadline_must_be_finite_positive(age):
    with pytest.raises(ValueError):WorkerHealth().register('worker',age)

def test_watchdog_halts_and_exits_when_worker_dies(tmp_path,monkeypatch):
    import run_ingest
    import lavish_core.utils.worker_health as module
    from lavish_core.utils.control_file import read_control
    from unittest.mock import Mock
    health=WorkerHealth();health.register('reconcile-loop',30);monkeypatch.setattr(module,'WORKER_HEALTH',health)
    path=tmp_path/'bot.duckdb';monkeypatch.setattr(run_ingest,'DEFAULT_DB',path)
    exits=Mock();notify=Mock();monkeypatch.setattr(run_ingest.os,'_exit',exits);monkeypatch.setattr(run_ingest.sd_notify,'notify_watchdog',notify)
    run_ingest._run_watchdog_pings({'reconcile-loop':SimpleNamespace(is_alive=lambda:False)})
    assert read_control(path)[0] is True;exits.assert_called_once_with(1);notify.assert_not_called()

@pytest.fixture
def paper_state(monkeypatch):
    monkeypatch.setenv('TRADE_MODE','paper');monkeypatch.setenv('BROKER_PROVIDER','alpaca')
    for key in ['PATREON_ACCESS_TOKEN','PATREON_CLIENT_ID','PATREON_CLIENT_SECRET','PATREON_REFRESH_TOKEN','DISCORD_USER_TOKEN','DISCORD_INGEST_ACCEPT_TOS_RISK','DEFAULT_TRADE_DOLLARS','DEFAULT_OPTION_TRADE_DOLLARS']:monkeypatch.delenv(key,raising=False)
    monkeypatch.setattr(broker,'get_account',lambda:dict(id='synthetic-account',status='ACTIVE',trading_blocked=False,account_blocked=False,options_trading_level=3,equity=100000,cash=100000))
    monkeypatch.setattr(broker,'get_clock',lambda:dict(is_open=False,next_open='2026-10-13T09:30:00-04:00'))
    monkeypatch.setattr(broker,'get_positions',lambda:[]);monkeypatch.setattr(broker.SESSION,'get',lambda *a,**k:Response([]))

def test_paper_preparation_holds_entries_without_source_and_budget(tmp_path,paper_state):
    result=check_market(tmp_path/'bot.duckdb',halt_entries=True)
    assert result['passed'] and not result['automatic_entries_ready'] and result['entry_halted']
    assert {'alert_source_credentials_missing','explicit_entry_budget_missing','entry_kill_switch_enabled'}<=set(result['blockers'])
    assert result['orders_submitted']==0 and not result['live_ready']

def test_paper_preflight_rejects_live_mode_without_broker_calls(paper_state,monkeypatch):
    monkeypatch.setenv('TRADE_MODE','live')
    with pytest.raises(ValueError):check_market()

def test_paper_preflight_account_denied(paper_state,monkeypatch):
    original=broker.get_account;monkeypatch.setattr(broker,'get_account',lambda:dict(original(),trading_blocked=True))
    result=check_market();assert not result['passed'] and 'broker_account_not_eligible' in result['blockers']

@pytest.mark.parametrize('scale',[0,.25,1])
def test_unsized_sell_closes_verified_holding_when_entry_budget_zero(tmp_path,monkeypatch,scale):
    from lavish_core.trading import trade_handler as th
    from unittest.mock import Mock
    monkeypatch.setattr(th,'TRADE_MODE','paper');monkeypatch.setattr(th,'DEFAULT_DB',tmp_path/'sell.duckdb')
    monkeypatch.setattr(th,'POSITION_SIZE_SCALE',scale);monkeypatch.setenv('DEFAULT_TRADE_DOLLARS','0')
    monkeypatch.setattr(th,'latest_quote',lambda *a:150.)
    monkeypatch.setattr(th,'broker_get_position',lambda *a:dict(qty='10'))
    submit=Mock(return_value={'status':'filled','order_id':'synthetic','legs':[]});monkeypatch.setattr(th,'place_trade',submit)
    assert th._execute_equity_trade(dict(symbol='AAPL',action='SELL',confidence=.9))
    assert submit.call_args.kwargs['qty']==10

def test_explicit_partial_sell_ignores_entry_scale(tmp_path,monkeypatch):
    from lavish_core.trading import trade_handler as th
    from unittest.mock import Mock
    monkeypatch.setattr(th,'TRADE_MODE','paper');monkeypatch.setattr(th,'DEFAULT_DB',tmp_path/'sell.duckdb')
    monkeypatch.setattr(th,'POSITION_SIZE_SCALE',0);monkeypatch.setenv('DEFAULT_TRADE_DOLLARS','0')
    monkeypatch.setattr(th,'latest_quote',lambda *a:150.)
    monkeypatch.setattr(th,'broker_get_position',lambda *a:dict(qty='10'))
    submit=Mock(return_value={'status':'filled','order_id':'synthetic','legs':[]});monkeypatch.setattr(th,'place_trade',submit)
    assert th._execute_equity_trade(dict(symbol='AAPL',action='SELL',amount_usd=300,confidence=.9))
    assert submit.call_args.kwargs['qty']==2
