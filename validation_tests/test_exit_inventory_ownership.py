"""Synthetic stale-monitor/creator-close races through durable ExitState."""
from unittest.mock import Mock

import pytest

from lavish_core.trade.exit_state import ExitState


def buy(store, qty):
    oid = store.submit_order("AAPL", "buy", qty)
    store.log_fill(oid, "AAPL", "buy", qty, 100)


def attempt(state, submit):
    return state.attempt(submit, Mock(), Mock(), Mock(), 1, lambda: None)


def close_reply(qty, client_id):
    return dict(id="close", status="filled", side="sell", symbol="AAPL",
                qty=qty, client_order_id=client_id, filled_qty=qty,
                filled_avg_price=110, filled_fee=0)


@pytest.mark.parametrize("remaining", [0, 1])
def test_stale_exit_plan_never_submits_more_than_current_inventory(store, remaining):
    buy(store, 2)
    external = store.submit_order("AAPL", "sell", 2-remaining)
    store.log_fill(external, "AAPL", "sell", 2-remaining, 105)
    submit = Mock(side_effect=close_reply)
    result = attempt(ExitState(2, db_path=store.db_path, symbol="AAPL"), submit)
    submit.assert_not_called()
    assert result["status"] == "fill_unverified"
    assert store.get_kill_switch()[0]


def test_inventory_changed_during_broker_call_is_not_booked_negative(store):
    buy(store, 2)
    def submit(qty, client_id):
        other = store.submit_order("AAPL", "sell", 1)
        store.log_fill(other, "AAPL", "sell", 1, 105)
        return close_reply(qty, client_id)
    result = attempt(ExitState(2, db_path=store.db_path, symbol="AAPL"), submit)
    assert result["status"] == "fill_unverified"
    assert store.get_position("AAPL")["qty"] == 1
    assert store.fetchall("SELECT sum(qty) FROM fills WHERE side='sell'")[0][0] == 1
    assert store.get_kill_switch()[0]


def test_valid_trim_then_new_runner_close_remains_allowed(store):
    buy(store, 3)
    for qty in (2, 1):
        result = attempt(ExitState(qty, db_path=store.db_path, symbol="AAPL"), close_reply)
        assert result["status"] == "exited"
    assert store.get_position("AAPL") is None
    assert not store.get_kill_switch()[0]


def test_fractional_exit_residue_closes_without_false_halt(store):
    for _ in range(8):
        buy(store, .1)
    state = ExitState(.8, db_path=store.db_path, symbol="AAPL")
    pending = dict(id="fractional", status="partially_filled", filled_qty=.7,
                   filled_avg_price=110, filled_fee=0)
    assert state.attempt(lambda *args: pending, Mock(), lambda *args: pending,
                         Mock(), 1, lambda: None) is None
    final = dict(pending, status="filled", filled_qty=.8)
    result = state.attempt(Mock(), Mock(), lambda *args: final, Mock(), 1, lambda: None)
    assert result["status"] == "exited"
    assert store.get_position("AAPL") is None
    assert not store.get_kill_switch()[0]
