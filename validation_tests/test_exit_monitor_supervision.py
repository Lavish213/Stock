from unittest.mock import Mock
import pytest
from lavish_core.trade import options_exit_monitor as om, equity_exit_monitor as em, reconcile as rc

CASES = [
    (om,'watch_and_exit','watch_and_exit_async',dict(underlying_symbol='AAPL',contract_symbol='AAPL261016C00100000',qty=1,option_side='CALL',entry_price=1)),
    (em,'watch_bracket','watch_bracket_async',dict(order_id='entry',symbol='AAPL',qty=1,entry_price=100,leg_ids=['leg'])),
    (em,'watch_and_exit_active','watch_and_exit_active_async',dict(symbol='AAPL',qty=1,entry_price=100)),
]

@pytest.mark.parametrize('module,target,launch,params',CASES)
@pytest.mark.parametrize('fault',['exception','invalid_return','abnormal_return','callback_exception'])
def test_failed_monitor_clears_protection_marker_and_halts(store,monkeypatch,module,target,launch,params,fault):
    symbol=params.get('contract_symbol',params.get('symbol'))
    monkeypatch.setattr(module,'DEFAULT_DB',store.db_path)
    monkeypatch.setattr(rc,'_watched',{symbol})
    callback=Mock(side_effect=RuntimeError('callback failure') if fault=='callback_exception' else None)
    def watch(**kwargs):
        if fault=='exception':raise RuntimeError('sensitive broker response must not be reported')
        if fault=='invalid_return':return None
        if fault=='abnormal_return':return {'status':'monitor_timeout'}
        kwargs['on_exit']({'status':'exited'})
        return {'status':'exited'}
    monkeypatch.setattr(module,target,watch)
    thread=getattr(module,launch)(**params,on_exit=callback)
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert symbol not in rc._watched
    assert store.get_kill_switch()[0]
    assert callback.call_count==1

@pytest.mark.parametrize('module,target,launch,params',CASES)
def test_successful_monitor_reports_once_without_halting(store,monkeypatch,module,target,launch,params):
    symbol=params.get('contract_symbol',params.get('symbol'))
    monkeypatch.setattr(module,'DEFAULT_DB',store.db_path)
    monkeypatch.setattr(rc,'_watched',{symbol})
    callback=Mock()
    def watch(**kwargs):
        kwargs['on_exit']({'status':'exited'})
        return {'status':'exited'}
    monkeypatch.setattr(module,target,watch)
    thread=getattr(module,launch)(**params,on_exit=callback)
    thread.join(timeout=3)
    assert callback.call_count==1 and symbol not in rc._watched
    assert not store.get_kill_switch()[0]
