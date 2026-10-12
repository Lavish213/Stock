# Covers watch_and_exit_active() - the active equity exit monitor used
# when the configured broker has no native bracket/OCO order (Tradier,
# currently - see broker.SUPPORTS_NATIVE_BRACKETS). Unlike watch_bracket()
# (which only watches Alpaca-managed child leg orders), this submits its
# own closing order and must confirm it actually filled before declaring
# the position exited - same exit-confirmation rigor options_exit_monitor
# already has (see test_options_exit_monitor.py), proven here for the
# equity path.
from __future__ import annotations

import pytest
from unittest.mock import patch

import lavish_core.trade.equity_exit_monitor as eem
from lavish_core.db.hybrid_store import HybridStore


def _no_sleep(*a, **k):
    pass


@pytest.fixture(autouse=True)
def _real_store(tmp_path, monkeypatch):
    # record_trade_outcome()/log_fill() need a real HybridStore - same
    # pattern test_reconcile_target_stop_recovery.py uses, not a mock,
    # since this exercises the actual DB read/write round trip
    # watch_and_exit_active performs on a confirmed fill.
    db_path = tmp_path / "test.duckdb"
    HybridStore(duckdb_path=db_path)
    monkeypatch.setattr(eem, "DEFAULT_DB", db_path)


def test_target_triggers_and_confirms_real_fill_price():
    with patch.object(eem, "time") as mock_time, \
         patch.object(eem, "latest_quote", return_value=160.0), \
         patch.object(eem, "place_order", return_value={"id": "o1", "status": "pending_new"}) as mock_place, \
         patch.object(eem, "get_order", side_effect=[
             {"status": "pending_new"},
             {"status": "filled", "filled_avg_price": "160.10", "filled_qty": "10"},
         ]) as mock_get, \
         patch.object(eem, "cancel_order") as mock_cancel, \
         patch.object(eem, "post_discord"):
        mock_time.sleep.side_effect = _no_sleep
        mock_time.time.side_effect = list(range(20))

        result = eem.watch_and_exit_active(
            symbol="AAPL", qty=10, entry_price=150.0, take_profit=159.0, stop_loss=145.0,
        )

    assert result["status"] == "exited"
    assert result["reason"] == "target"
    assert result["exit_price"] == 160.10
    assert result["pnl"] == pytest.approx((160.10 - 150.0) * 10)
    mock_place.assert_called_once()
    args = mock_place.call_args.kwargs
    assert args["side"] == "sell"
    assert args["symbol"] == "AAPL"
    mock_cancel.assert_not_called()


def test_stop_loss_triggers_exit():
    with patch.object(eem, "time") as mock_time, \
         patch.object(eem, "latest_quote", return_value=140.0), \
         patch.object(eem, "place_order", return_value={
             "id": "o1", "status": "filled", "filled_avg_price": "140.0", "filled_qty": "10",
         }), \
         patch.object(eem, "get_order") as mock_get, \
         patch.object(eem, "post_discord"):
        mock_time.sleep.side_effect = _no_sleep
        mock_time.time.side_effect = list(range(20))

        result = eem.watch_and_exit_active(
            symbol="AAPL", qty=10, entry_price=150.0, take_profit=160.0, stop_loss=145.0,
        )

    assert result["status"] == "exited"
    assert result["reason"] == "stop"
    mock_get.assert_not_called()  # filled on first response, no polling needed


def test_neither_level_fires_until_price_moves():
    # Price sits between stop and target for two iterations, then crosses
    # the target on the third - must not exit early.
    with patch.object(eem, "time") as mock_time, \
         patch.object(eem, "latest_quote", side_effect=[150.0, 151.0, 161.0]), \
         patch.object(eem, "place_order", return_value={
             "id": "o1", "status": "filled", "filled_avg_price": "161.0", "filled_qty": "10",
         }), \
         patch.object(eem, "get_order"), \
         patch.object(eem, "post_discord"):
        mock_time.sleep.side_effect = _no_sleep
        mock_time.time.side_effect = list(range(20))

        result = eem.watch_and_exit_active(
            symbol="AAPL", qty=10, entry_price=150.0, take_profit=160.0, stop_loss=145.0,
        )

    assert result["status"] == "exited"
    assert result["reason"] == "target"


def test_exit_retries_after_submission_failure_instead_of_dying():
    with patch.object(eem, "time") as mock_time, \
         patch.object(eem, "latest_quote", return_value=160.0), \
         patch.object(eem, "place_order", side_effect=[
             RuntimeError("broker 503"),
             RuntimeError("broker 503"),
             {"id": "o1", "status": "filled", "filled_avg_price": "160.0", "filled_qty": "10"},
         ]) as mock_place, \
         patch.object(eem, "get_order") as mock_get, \
         patch.object(eem, "post_discord") as mock_discord:
        mock_time.sleep.side_effect = _no_sleep
        mock_time.time.side_effect = list(range(20))

        result = eem.watch_and_exit_active(
            symbol="AAPL", qty=10, entry_price=150.0, take_profit=159.0, stop_loss=145.0,
        )

    assert result["status"] == "exited"
    assert mock_place.call_count == 3
    mock_get.assert_not_called()
    assert mock_discord.called


def test_unconfirmed_fill_is_canceled_and_retried():
    with patch.object(eem, "time") as mock_time, \
         patch.object(eem, "latest_quote", return_value=160.0), \
         patch.object(eem, "place_order", side_effect=[
             {"id": "stale-1", "status": "pending_new"},
             {"id": "o2", "status": "filled", "filled_avg_price": "160.0", "filled_qty": "10"},
         ]) as mock_place, \
         patch.object(eem, "get_order", side_effect=[{"status":"pending_new"}]*5+[{"status":"canceled", "filled_qty":"0"}]), \
         patch.object(eem, "cancel_order") as mock_cancel, \
         patch.object(eem, "post_discord"):
        mock_time.sleep.side_effect = _no_sleep
        mock_time.time.side_effect = list(range(30))

        result = eem.watch_and_exit_active(
            symbol="AAPL", qty=10, entry_price=150.0, take_profit=159.0, stop_loss=145.0,
        )

    assert result["status"] == "exited"
    assert mock_place.call_count == 2
    mock_cancel.assert_called_once_with("stale-1")


def test_an_unexpected_error_in_the_loop_body_does_not_kill_the_monitor():
    # Same gap options_exit_monitor had and fixed: latest_quote() is
    # documented "never raises" but shares _check_keys(), which DOES raise
    # unguarded if broker credentials ever go missing/invalid mid-run.
    with patch.object(eem, "time") as mock_time, \
         patch.object(eem, "latest_quote", side_effect=[RuntimeError("Missing API key"), 160.0, 160.0]), \
         patch.object(eem, "place_order", return_value={
             "id": "o1", "status": "filled", "filled_avg_price": "160.0", "filled_qty": "10",
         }), \
         patch.object(eem, "get_order"), \
         patch.object(eem, "post_discord") as mock_discord:
        mock_time.sleep.side_effect = _no_sleep
        mock_time.time.side_effect = list(range(20))

        result = eem.watch_and_exit_active(
            symbol="AAPL", qty=10, entry_price=150.0, take_profit=159.0, stop_loss=145.0,
        )

    assert result["status"] == "exited"
    assert mock_discord.called


def test_forced_close_after_max_hold_time_with_nothing_else_triggering():
    with patch.object(eem, "time") as mock_time, \
         patch.object(eem, "latest_quote", return_value=152.0), \
         patch.object(eem, "place_order", return_value={
             "id": "o1", "status": "filled", "filled_avg_price": "152.0", "filled_qty": "10",
         }), \
         patch.object(eem, "get_order"), \
         patch.object(eem, "post_discord") as mock_discord:
        mock_time.sleep.side_effect = _no_sleep
        # First time.time() calls already exceed the deadline -> drops
        # straight into the forced-close retry loop.
        mock_time.time.side_effect = [0, 10**9]

        result = eem.watch_and_exit_active(
            symbol="AAPL", qty=10, entry_price=150.0, take_profit=200.0, stop_loss=100.0,
        )

    assert result["status"] == "exited"
    assert result["reason"] == "max_hold_timeout"
    assert mock_discord.called


def test_on_exit_callback_is_invoked_with_the_result():
    with patch.object(eem, "time") as mock_time, \
         patch.object(eem, "latest_quote", return_value=160.0), \
         patch.object(eem, "place_order", return_value={
             "id": "o1", "status": "filled", "filled_avg_price": "160.0", "filled_qty": "10",
         }), \
         patch.object(eem, "get_order"), \
         patch.object(eem, "post_discord"):
        mock_time.sleep.side_effect = _no_sleep
        mock_time.time.side_effect = list(range(20))

        seen = {}
        eem.watch_and_exit_active(
            symbol="AAPL", qty=10, entry_price=150.0, take_profit=159.0, stop_loss=145.0,
            on_exit=lambda r: seen.update(r),
        )

    assert seen["status"] == "exited"
