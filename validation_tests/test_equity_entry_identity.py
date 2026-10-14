from unittest.mock import Mock
import pytest
from lavish_core.trade import trade_agent as ta

@pytest.fixture
def boundary(monkeypatch):
    monkeypatch.setattr(ta, '_soft_quote', lambda _: 100)
    monkeypatch.setattr(ta, 'get_account', lambda: {'equity':100000})
    monkeypatch.setattr(ta, '_circuit_breaker_check_ok', lambda *a: (True,'ok'))
    monkeypatch.setattr(ta.time, 'sleep', lambda _: None)

@pytest.mark.parametrize('poll', [False, True])
@pytest.mark.parametrize('change', [
    {'symbol':'NVDA'}, {'side':'sell'}, {'client_order_id':'foreign'},
    {'qty':4}, {'filled_qty':1}, {'filled_fee':-1},
])
def test_conflicting_equity_entry_snapshot_never_books(store, monkeypatch, boundary, poll, change):
    response = dict(id='remote', symbol='AAPL', side='buy', client_order_id='entry',
                    qty=3, status='filled', filled_qty=3, filled_avg_price=100, filled_fee=0)
    response.update(change)
    if poll:
        monkeypatch.setattr(ta, 'broker_place_order', lambda **k: dict(id='remote',status='new'))
        monkeypatch.setattr(ta, 'broker_get_order', lambda _: response)
    else:
        monkeypatch.setattr(ta, 'broker_place_order', lambda **k: response)
    result = ta.place_trade(store, 'AAPL', 'buy', 3, mode='paper', client_id='entry')
    assert result['status'] == 'fill_unverified'
    assert not store.fetchall('SELECT * FROM fills')
    assert store.get_kill_switch()[0]
    assert store.fetchall('SELECT count(*) FROM risk_reservations')[0][0] == 1

def test_cancel_acceptance_does_not_release_partial_entry_budget(store, monkeypatch, boundary):
    monkeypatch.setattr(ta, 'broker_place_order', lambda **k: dict(id='remote',status='partially_filled',filled_qty=1,filled_avg_price=100))
    monkeypatch.setattr(ta, 'broker_get_order', lambda _: dict(id='remote',status='partially_filled',filled_qty=1,filled_avg_price=100))
    monkeypatch.setattr(ta, 'broker_cancel_order', Mock(return_value=True))
    result = ta.place_trade(store, 'AAPL', 'buy', 3, mode='paper', client_id='entry')
    assert result['status'] == 'partially_filled'
    assert store.get_position('AAPL')['qty'] == 1
    assert store.fetchall('SELECT count(*) FROM risk_reservations')[0][0] == 1

def test_canceled_partial_execution_and_fee_are_not_lost(store, monkeypatch, boundary):
    monkeypatch.setattr(ta, 'broker_place_order', lambda **k: dict(id='remote',status='canceled',filled_qty=1,filled_avg_price=100,filled_fee=.03))
    result = ta.place_trade(store, 'AAPL', 'buy', 3, mode='paper', client_id='entry')
    assert result['status'] == 'canceled'
    assert store.get_position('AAPL')['qty'] == 1
    assert store.fetchall('SELECT sum(fee) FROM fills')[0][0] == pytest.approx(.03)
