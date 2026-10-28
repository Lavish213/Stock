"""Invented native bracket executions, not authentic market replay."""
import pytest
from lavish_core.trade import equity_exit_monitor as em


@pytest.fixture
def native(store, monkeypatch):
    oid = store.submit_order('AAPL', 'buy', 2)
    store.log_fill(oid, 'AAPL', 'buy', 2, 100)
    monkeypatch.setattr(em, 'DEFAULT_DB', store.db_path)
    monkeypatch.setattr(em.time, 'sleep', lambda _: None)
    def run(snapshots):
        snapshots = iter(snapshots)
        monkeypatch.setattr(em, 'get_order', lambda _: next(snapshots))
        return em.watch_bracket(oid, 'AAPL', 2, 100, ['leg'])
    return run


def receipt(status, qty, price=110, fee=.1):
    return dict(id='leg', symbol='AAPL', side='sell', qty=2, status=status,
                filled_qty=qty, filled_avg_price=price, filled_fee=fee)


def test_partial_then_cancel_books_only_confirmed_exposure(store, native):
    result = native([receipt('canceled', 1)])
    assert result['status'] == 'partial_fill_review_required'
    assert store.get_position('AAPL')['qty'] == 1
    assert store.fetchall("SELECT qty,price,fee FROM fills WHERE side='sell'") == [(1.,110.,.1)]
    assert store.get_kill_switch()[0]
    # Restart sees the identical cumulative receipt without consuming another share.
    native([receipt('canceled', 1)])
    assert store.get_position('AAPL')['qty'] == 1


def test_partial_final_cumulative_average_and_fee_are_incremental(store, native):
    result = native([receipt('partially_filled',1), receipt('filled',2,111,.25)])
    assert result['status'] == 'exited'
    assert store.get_position('AAPL') is None
    assert store.fetchall("SELECT sum(qty),sum(qty*price),sum(fee) FROM fills WHERE side='sell'") == [(2.,222.,.25)]
    assert store.get_kill_switch()[0]  # Never silently clear the partial-fill review halt.
    assert native([receipt('filled',2,111,.25)])['status'] == 'exited'
    assert store.fetchall("SELECT sum(qty) FROM fills WHERE side='sell'") == [(2.,)]


def test_regressing_partial_snapshot_preserves_booked_execution(store, native):
    result = native([receipt('partially_filled',1),receipt('canceled',.5)])
    assert result['status'] == 'fill_unverified'
    assert store.get_position('AAPL')['qty'] == 1
    assert store.get_kill_switch()[0]


def test_missing_native_legs_halts_even_without_supervisor(store, monkeypatch):
    monkeypatch.setattr(em,'DEFAULT_DB',store.db_path)
    result = em.watch_bracket('unknown','AAPL',2,100,[])
    assert result['status'] == 'no_legs'
    assert store.get_kill_switch()[0]


@pytest.mark.parametrize('change', [dict(filled_avg_price=111),dict(filled_fee=.05),
                                    dict(filled_qty=3),dict(filled_avg_price=float('nan'))])
def test_conflicting_partial_repeat_is_quarantined(store,native,change):
    native([receipt('canceled',1)])
    changed=receipt('canceled',1)
    changed.update(change)
    assert native([changed])['status']=='fill_unverified'
    assert store.fetchall("SELECT qty,price,fee FROM fills WHERE side='sell'")==[(1.,110.,.1)]


def test_late_partial_fee_is_once_and_still_requires_protection_review(store,native):
    native([receipt('canceled',1)])
    assert native([receipt('canceled',1,fee=.25)])['status']=='fee_reconciled_review_required'
    assert native([receipt('canceled',1,fee=.25)])['status']=='partial_fill_review_required'
    assert store.fetchall("SELECT qty,fee FROM fills WHERE side='sell'")==[(1.,.25)]


def test_sibling_leg_cannot_consume_scaled_in_shares(store,native,monkeypatch):
    native([receipt('canceled',1)])
    oid=store.fetchall("SELECT id FROM orders WHERE side='buy'")[0][0]
    extra=store.submit_order('AAPL','buy',2)
    store.log_fill(extra,'AAPL','buy',2,100)
    other=receipt('filled',2);other['id']='other'
    monkeypatch.setattr(em,'get_order',lambda _:other)
    result=em.watch_bracket(oid,'AAPL',2,100,['other'])
    assert result['status']=='fill_unverified'
    assert store.get_position('AAPL')['qty']==3


def test_partial_receipt_cannot_be_applied_to_reopened_lifecycle(store,native):
    native([receipt('canceled',1)])
    close=store.submit_order('AAPL','sell',1)
    store.log_fill(close,'AAPL','sell',1,100)
    new=store.submit_order('AAPL','buy',2)
    store.log_fill(new,'AAPL','buy',2,100)
    assert native([receipt('filled',2,111,.25)])['status']=='fill_unverified'
    assert store.get_position('AAPL')['qty']==2
