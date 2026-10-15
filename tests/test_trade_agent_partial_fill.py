# Covers a real gap found and fixed: place_trade() used to only log a
# fill when the order status was EXACTLY "filled" - a partial fill (rare
# for a liquid, small-size market order, but not impossible) was never
# recorded in the local positions/fills ledger at all, even though real
# shares were actually held at the broker. Also covers the companion fix:
# the unfilled remainder of a partially-filled order is now canceled
# rather than left resting at the broker to fill unpredictably later,
# against conditions none of this alert's own checks were ever
# re-validated against.
from __future__ import annotations

from unittest.mock import patch

import lavish_core.trade.trade_agent as ta
from lavish_core.db.hybrid_store import HybridStore


def _no_sleep(*a, **k):
    pass


def test_partial_fill_is_recorded_with_the_real_filled_quantity(tmp_path):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    with patch.object(ta, "_soft_quote", return_value=150.0), \
         patch.object(ta, "get_account", return_value={"equity":100000}), \
         patch.object(ta, "HAS_ALPACA", True), \
         patch.object(ta, "_circuit_breaker_check_ok", return_value=(True, "ok")), \
         patch.object(ta, "time") as mock_time, \
         patch.object(ta, "broker_place_order",
                       return_value={"id": "o1", "status": "partially_filled", "filled_qty": "3",
                                      "filled_avg_price": "150.00"}), \
         patch.object(ta, "broker_get_order_by_client_id", return_value=None), \
         patch.object(ta, "broker_get_order",
                       return_value={"status": "partially_filled", "filled_qty": "3", "filled_avg_price": "150.00"}), \
         patch.object(ta, "broker_cancel_order") as mock_cancel:
        mock_time.sleep.side_effect = _no_sleep
        result = ta.place_trade(store, "AAPL", "buy", 5.0, mode="paper", client_id="test-partial")

    assert result["status"] == "partially_filled"
    fills = store.fetchall("SELECT symbol, side, qty, price FROM fills WHERE symbol = 'AAPL'")
    assert fills == [("AAPL", "buy", 3.0, 150.0)]  # the real filled qty (3), not the ordered qty (5)
    mock_cancel.assert_called_once_with("o1")  # unfilled remainder (2 shares) canceled


def test_full_fill_still_works_normally(tmp_path):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    with patch.object(ta, "_soft_quote", return_value=150.0), \
         patch.object(ta, "get_account", return_value={"equity":100000}), \
         patch.object(ta, "HAS_ALPACA", True), \
         patch.object(ta, "_circuit_breaker_check_ok", return_value=(True, "ok")), \
         patch.object(ta, "time") as mock_time, \
         patch.object(ta, "broker_place_order",
                       return_value={"id": "o1", "status": "filled", "filled_qty": "5", "filled_avg_price": "150.00"}), \
         patch.object(ta, "broker_get_order_by_client_id", return_value=None), \
         patch.object(ta, "broker_cancel_order") as mock_cancel:
        mock_time.sleep.side_effect = _no_sleep
        result = ta.place_trade(store, "AAPL", "buy", 5.0, mode="paper", client_id="test-full")

    assert result["status"] == "filled"
    fills = store.fetchall("SELECT symbol, side, qty, price FROM fills WHERE symbol = 'AAPL'")
    assert fills == [("AAPL", "buy", 5.0, 150.0)]
    mock_cancel.assert_not_called()  # nothing left to cancel on a full fill


def test_fully_unfilled_order_is_not_logged_as_a_fill(tmp_path):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    with patch.object(ta, "_soft_quote", return_value=150.0), \
         patch.object(ta, "get_account", return_value={"equity":100000}), \
         patch.object(ta, "HAS_ALPACA", True), \
         patch.object(ta, "_circuit_breaker_check_ok", return_value=(True, "ok")), \
         patch.object(ta, "time") as mock_time, \
         patch.object(ta, "broker_place_order",
                       return_value={"id": "o1", "status": "pending_new", "filled_qty": None, "filled_avg_price": None}), \
         patch.object(ta, "broker_get_order_by_client_id", return_value=None), \
         patch.object(ta, "broker_get_order",
                       return_value={"status": "pending_new", "filled_qty": None, "filled_avg_price": None}), \
         patch.object(ta, "broker_cancel_order") as mock_cancel:
        mock_time.sleep.side_effect = _no_sleep
        result = ta.place_trade(store, "AAPL", "buy", 5.0, mode="paper", client_id="test-unfilled")

    assert result["status"] == "pending_new"
    fills = store.fetchall("SELECT symbol FROM fills WHERE symbol = 'AAPL'")
    assert fills == []
    mock_cancel.assert_not_called()  # place_trade itself doesn't cancel a never-filled order - that's the caller's job
