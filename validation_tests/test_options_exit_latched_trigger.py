from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from lavish_core.trade import options_exit_monitor as monitor


@pytest.mark.parametrize('level,first,field,reason', [
    (95, 90, 'stop_underlying', 'her_stop'),
    (105, 110, 'target_underlying', 'her_target'),
])
def test_failed_exit_remains_requested_after_underlying_recovers(monkeypatch, level, first, field, reason):
    clock = iter(range(100))
    monkeypatch.setattr(monitor, 'time', SimpleNamespace(time=lambda:next(clock), sleep=lambda _:None))
    monkeypatch.setattr(monitor, 'MAX_MONITOR_HOURS', 3/3600)
    monkeypatch.setattr(monitor, 'latest_quote', Mock(side_effect=[first, 100]))
    monkeypatch.setattr(monitor, '_option_limit_price', lambda _:1.)
    monkeypatch.setattr(monitor, 'post_discord', Mock())
    attempt = Mock(side_effect=[None, dict(status='exited',price=1.,filled_qty=1,order={},ledger_recorded=True)])
    monkeypatch.setattr(monitor, 'ExitState', lambda *a,**k:SimpleNamespace(attempt=attempt))
    result = monitor.watch_and_exit('SPY','SPY261016C00770000',1,'call',1.,**{field:level})
    assert result['reason'] == reason
    assert attempt.call_count == 2


def test_latched_exit_does_not_depend_on_recovering_underlying_feed(monkeypatch):
    clock = iter(range(100))
    monkeypatch.setattr(monitor, 'time', SimpleNamespace(time=lambda:next(clock), sleep=lambda _:None))
    monkeypatch.setattr(monitor, 'MAX_MONITOR_HOURS', 3/3600)
    quotes = Mock(side_effect=[90, RuntimeError('underlying unavailable')])
    monkeypatch.setattr(monitor, 'latest_quote', quotes)
    monkeypatch.setattr(monitor, '_option_limit_price', lambda _:1.)
    monkeypatch.setattr(monitor, 'post_discord', Mock())
    attempt = Mock(side_effect=[None, dict(status='exited',price=1.,filled_qty=1,order={},ledger_recorded=True)])
    monkeypatch.setattr(monitor, 'ExitState', lambda *a,**k:SimpleNamespace(attempt=attempt))
    result = monitor.watch_and_exit('SPY','SPY261016C00770000',1,'call',1.,stop_underlying=95)
    assert result['reason'] == 'her_stop'
    assert quotes.call_count == 1


def test_lost_ack_recovery_keeps_one_order_and_books_one_exit(store, monkeypatch):
    symbol = 'SPY261016C00770000'
    buy = store.submit_order(symbol, 'buy', 1)
    store.log_fill(buy, symbol, 'buy', 1, 1.)
    monkeypatch.setattr(monitor, 'DEFAULT_DB', store.db_path)
    clock = iter(range(100))
    monkeypatch.setattr(monitor, 'time', SimpleNamespace(time=lambda:next(clock), sleep=lambda _:None))
    monkeypatch.setattr(monitor, 'MAX_MONITOR_HOURS', 3/3600)
    monkeypatch.setattr(monitor, 'latest_quote', Mock(side_effect=[90, 100]))
    monkeypatch.setattr(monitor, '_option_limit_price', lambda _:1.)
    monkeypatch.setattr(monitor, 'post_discord', Mock())
    submitted = []
    def lost_ack(**kwargs):
        submitted.append(kwargs)
        raise TimeoutError('Accepted externally, response lost')
    def lookup(client_id):
        assert client_id == submitted[0]['client_order_id']
        return dict(id='synthetic-accepted-exit', symbol=symbol, side='sell', qty=1,
                    client_order_id=client_id, status='filled', filled_qty=1,
                    filled_avg_price=1., filled_fee=.65)
    monkeypatch.setattr(monitor, 'place_option_order', lost_ack)
    monkeypatch.setattr(monitor, 'get_order_by_client_id', lookup)
    callback = Mock()
    result = monitor.watch_and_exit('SPY',symbol,1,'call',1.,stop_underlying=95,on_exit=callback)
    assert result['reason'] == 'her_stop' and result['ledger_recorded']
    assert len(submitted) == 1
    assert store.get_position(symbol) is None
    assert store.fetchall("SELECT count(*),sum(qty),sum(fee) FROM fills WHERE side='sell'") == [(1,1.,.65)]
    assert callback.call_count == 1
