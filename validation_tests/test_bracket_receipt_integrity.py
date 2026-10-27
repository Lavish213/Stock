"""Synthetic repeated native-bracket receipts, not market executions."""
import pytest
from lavish_core.trade import equity_exit_monitor as em


@pytest.fixture
def bracket(store, monkeypatch):
    oid = store.submit_order('AAPL', 'buy', 2)
    store.log_fill(oid, 'AAPL', 'buy', 2, 100)
    monkeypatch.setattr(em, 'DEFAULT_DB', store.db_path)
    snapshot = dict(id='leg', symbol='AAPL', side='sell', qty=2,
                    status='filled', filled_qty=2, filled_avg_price=110, filled_fee=.1)
    monkeypatch.setattr(em, 'get_order', lambda _: dict(snapshot))
    def watch():
        return em.watch_bracket(oid, 'AAPL', 2, 100, ['leg'])
    assert watch()['status'] == 'exited'
    return snapshot, watch


@pytest.mark.parametrize('change', [dict(filled_avg_price=111), dict(filled_fee=.05)])
def test_conflicting_duplicate_bracket_receipt_is_not_a_verified_exit(store, bracket, change):
    snapshot, watch = bracket
    snapshot.update(change)
    assert watch()['status'] == 'fill_unverified'
    assert store.fetchall("SELECT qty,price,fee FROM fills WHERE side='sell'") == [(2., 110., .1)]
    assert store.get_kill_switch()[0]


def test_late_cumulative_bracket_fee_is_booked_once_without_inventory_change(store, bracket):
    snapshot, watch = bracket
    snapshot['filled_fee'] = .25
    assert watch()['status'] == 'fee_reconciled_review_required'
    assert watch()['status'] == 'exited'
    assert store.fetchall("SELECT qty,price,fee FROM fills WHERE side='sell'") == [(2., 110., .25)]
    assert store.get_position('AAPL') is None
    assert store.get_kill_switch()[0]  # Outcome/loss-streak review, not a new trade.


def test_sparse_duplicate_fee_preserves_recorded_commission(store, bracket):
    snapshot, watch = bracket
    del snapshot['filled_fee']
    assert watch()['status'] == 'exited'
    assert store.fetchall("SELECT fee FROM fills WHERE side='sell'") == [(.1,)]
    assert not store.get_kill_switch()[0]
