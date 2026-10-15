from unittest.mock import Mock
import pytest
from lavish_core.trade import equity_exit_monitor as em, reconcile as rc

@pytest.mark.parametrize('change', [
    {'id': 'foreign'}, {'symbol': 'NVDA'}, {'side': 'buy'},
    {'qty': 3}, {'filled_qty': 1}, {'filled_fee': -1},
])
def test_native_bracket_conflicting_exit_preserves_exposure_and_halts(store, monkeypatch, change):
    oid = store.submit_order('AAPL', 'buy', 2)
    store.log_fill(oid, 'AAPL', 'buy', 2, 100)
    monkeypatch.setattr(em, 'DEFAULT_DB', store.db_path)
    leg = dict(id='leg', symbol='AAPL', side='sell', qty=2,
               status='filled', filled_qty=2, filled_avg_price=110, filled_fee=0)
    leg.update(change)
    monkeypatch.setattr(em, 'get_order', lambda _: leg)
    result = em.watch_bracket(oid, 'AAPL', 2, 100, ['leg'])
    assert result['status'] == 'fill_unverified'
    assert store.get_position('AAPL')['qty'] == 2
    assert store.get_kill_switch()[0]

def test_latest_rejected_alert_cannot_replace_confirmed_exit_levels(store, monkeypatch):
    monkeypatch.setattr(rc, 'DEFAULT_DB', store.db_path)
    symbol = 'AAPL261016C00100000'
    store.submit_order(symbol, 'buy', 1, status='filled', meta={'target_underlying':110,'stop_underlying':90})
    store.submit_order(symbol, 'buy', 1, status='rejected', meta={'target_underlying':999,'stop_underlying':1})
    assert rc._find_original_target_stop(symbol) == (110,90)

def test_failed_monitor_launch_is_not_marked_as_watched(store, monkeypatch):
    symbol = 'AAPL261016C00100000'
    monkeypatch.setattr(rc, 'DEFAULT_DB', store.db_path)
    monkeypatch.setattr(rc, '_watched', set())
    monkeypatch.setattr(rc, 'get_positions', lambda: [dict(symbol=symbol, side='long',asset_class='us_option',qty=1,avg_entry_price=1)])
    monkeypatch.setattr(rc, 'post_discord', Mock())
    monkeypatch.setattr(rc, 'watch_and_exit_async', Mock(side_effect=RuntimeError('thread start failed')))
    try:
        rc.reconcile_open_positions()
    except RuntimeError:
        pass
    assert symbol not in rc._watched
    assert store.get_kill_switch()[0]
