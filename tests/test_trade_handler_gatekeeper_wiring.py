# Covers the wiring between trade_handler.py and candle_gatekeeper.py -
# not the gate's own confirmation logic (see test_candle_gatekeeper.py),
# but that a BUY/CALL/PUT entry actually gets routed through it, that a
# SELL (reduce-only, never a new entry) bypasses it entirely, and that a
# non-confirmed gate result results in no order being placed.
#
# threading.Thread is replaced with a synchronous stand-in so the gated
# path's background thread runs immediately and deterministically in the
# test, instead of the test having to sleep/poll for a real thread.
from __future__ import annotations

from unittest.mock import patch

import lavish_core.trading.trade_handler as th


class _SyncThread:
    """Runs the target immediately on .start() instead of spawning a real
    thread - makes the gated (background-thread) path deterministic to
    test without sleeping/polling for a real thread to finish."""
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def test_equity_buy_is_routed_through_the_gate_and_places_order_once_confirmed(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    monkeypatch.setattr(th, "GATEKEEPER_ENABLED", True)
    signal = {"source": "patreon", "action": "BUY", "symbol": "AAPL", "confidence": 0.9}

    with patch.object(th.threading, "Thread", _SyncThread), \
         patch.object(th, "latest_quote", return_value=150.0), \
         patch.object(th, "confirm_entry", return_value={"confirmed": True, "reason": "wick_breakout_volume_confirmed", "price": 151.0}) as mock_confirm, \
         patch.object(th, "check_correlation_ok", return_value=(True, "ok")), \
         patch.object(th, "check_pdt_ok", return_value=(True, "ok")), \
         patch.object(th, "place_trade", return_value={"status": "filled", "order_id": "x", "legs": []}) as mock_place:
        handled = th._execute_equity_trade(signal)

    assert handled is True
    mock_confirm.assert_called_once_with("AAPL", "bullish")
    mock_place.assert_called_once()


def test_equity_buy_places_no_order_when_gate_does_not_confirm(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    monkeypatch.setattr(th, "GATEKEEPER_ENABLED", True)
    signal = {"source": "patreon", "action": "BUY", "symbol": "AAPL", "confidence": 0.9}

    with patch.object(th.threading, "Thread", _SyncThread), \
         patch.object(th, "latest_quote", return_value=150.0), \
         patch.object(th, "confirm_entry", return_value={"confirmed": False, "reason": "timeout", "price": None}), \
         patch.object(th, "place_trade") as mock_place:
        handled = th._execute_equity_trade(signal)

    # The top-level return is still True (hand-off to the gate thread is
    # itself the final, deterministic outcome for dedup purposes) even
    # though no order was placed - same contract exit-monitor hand-offs
    # already use.
    assert handled is True
    mock_place.assert_not_called()


def test_equity_sell_bypasses_the_gate_entirely(tmp_path, monkeypatch):
    # A sell is reduce-only/closing exposure, never a new entry - gating
    # it would mean "getting out of a position can be delayed up to 15
    # minutes," which is never the intent.
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    monkeypatch.setattr(th, "GATEKEEPER_ENABLED", True)
    signal = {"source": "patreon", "action": "SELL", "symbol": "AAPL", "confidence": 0.9}

    with patch.object(th.threading, "Thread", _SyncThread), \
         patch.object(th, "latest_quote", return_value=150.0), \
         patch.object(th, "broker_get_position", return_value={"qty": 10}), \
         patch.object(th, "confirm_entry") as mock_confirm, \
         patch.object(th, "place_trade", return_value={"status": "filled", "order_id": "x", "legs": []}) as mock_place:
        handled = th._execute_equity_trade(signal)

    assert handled is True
    mock_confirm.assert_not_called()
    mock_place.assert_called_once()


def test_equity_buy_skips_the_gate_in_dry_mode(tmp_path, monkeypatch):
    # No real money/slippage at stake in dry mode - delaying the "would
    # trade" log for up to 15 minutes serves no purpose there.
    monkeypatch.setattr(th, "TRADE_MODE", "dry")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    monkeypatch.setattr(th, "GATEKEEPER_ENABLED", True)
    signal = {"source": "patreon", "action": "BUY", "symbol": "AAPL", "confidence": 0.9}

    with patch.object(th.threading, "Thread", _SyncThread), \
         patch.object(th, "latest_quote", return_value=150.0), \
         patch.object(th, "confirm_entry") as mock_confirm, \
         patch.object(th, "place_trade", return_value={"status": "filled", "order_id": "x", "legs": []}):
        handled = th._execute_equity_trade(signal)

    assert handled is True
    mock_confirm.assert_not_called()


def test_gated_equity_buy_skips_the_price_drift_check():
    # The gate's own confirmation requirement replaces the flat max-drift-
    # since-alert check for anything routed through it - re-applying that
    # check after a confirmed breakout would often reject exactly the
    # moves the gate just confirmed.
    signal = {"source": "patreon", "action": "BUY", "symbol": "AAPL", "confidence": 0.9,
              "_alert_time_price": 100.0}

    with patch.object(th, "confirm_entry", return_value={"confirmed": True, "reason": "x", "price": 200.0}), \
         patch.object(th, "TRADE_MODE", "paper"), \
         patch.object(th, "latest_quote", return_value=200.0), \
         patch.object(th, "_price_drift_reason") as mock_drift, \
         patch.object(th, "check_correlation_ok", return_value=(True, "ok")), \
         patch.object(th, "check_pdt_ok", return_value=(True, "ok")), \
         patch.object(th, "place_trade", return_value={"status": "filled", "order_id": "x", "legs": []}):
        th._gated_equity_buy(signal, "AAPL", 0.9, "", None, None, None)

    mock_drift.assert_not_called()


def test_gated_equity_buy_survives_an_unexpected_error():
    # Nothing upstream is watching a daemon thread for exceptions - must
    # never die silently, same crash-resilience rule as every exit monitor.
    signal = {"source": "patreon", "action": "BUY", "symbol": "AAPL", "confidence": 0.9}

    with patch.object(th, "confirm_entry", side_effect=RuntimeError("broker down")), \
         patch.object(th, "post_discord") as mock_discord:
        th._gated_equity_buy(signal, "AAPL", 0.9, "", None, None, None)  # must not raise

    assert mock_discord.called


def test_options_entry_is_routed_through_the_gate_with_correct_direction(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    monkeypatch.setattr(th, "GATEKEEPER_ENABLED", True)
    signal = {
        "source": "discord", "ticker": "AAPL", "symbol": "AAPL", "side": "PUT",
        "strike": 200.0, "expiry": (__import__("datetime").date.today()+__import__("datetime").timedelta(days=7)).isoformat(), "confidence": 0.9, "amount_usd": 1000.0,
    }
    contract = {"symbol": "AAPL260116P00200000", "mid_price": 4.0, "bid": 3.9, "ask": 4.1}

    with patch.object(th.threading, "Thread", _SyncThread), \
         patch.object(th, "confirm_entry", return_value={"confirmed": True, "reason": "x", "price": 195.0}) as mock_confirm, \
         patch.object(th, "resolve_and_price_contract", return_value=contract), \
         patch.object(th, "circuit_breaker_check_ok", return_value=(True, "ok")), \
         patch.object(th, "check_correlation_ok", return_value=(True, "ok")), \
         patch.object(th, "check_pdt_ok", return_value=(True, "ok")), \
         patch.object(th, "place_option_order",
                       return_value={"id": "o1", "status": "filled", "filled_qty": "1", "filled_avg_price": "4.0"}) as mock_place, \
         patch.object(th, "broker_get_order"), \
         patch.object(th, "watch_and_exit_async"):
        handled = th._execute_option_trade(signal)

    assert handled is True
    mock_confirm.assert_called_once_with("AAPL", "bearish")
    mock_place.assert_called_once()


def test_options_entry_places_no_order_when_gate_does_not_confirm(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    monkeypatch.setattr(th, "GATEKEEPER_ENABLED", True)
    signal = {
        "source": "discord", "ticker": "AAPL", "symbol": "AAPL", "side": "CALL",
        "strike": 200.0, "expiry": (__import__("datetime").date.today()+__import__("datetime").timedelta(days=7)).isoformat(), "confidence": 0.9,
    }

    with patch.object(th.threading, "Thread", _SyncThread), \
         patch.object(th, "confirm_entry", return_value={"confirmed": False, "reason": "invalidated_wrong_direction"}), \
         patch.object(th, "resolve_and_price_contract") as mock_resolve, \
         patch.object(th, "place_option_order") as mock_place:
        handled = th._execute_option_trade(signal)

    assert handled is True
    mock_resolve.assert_not_called()  # contract resolution is deferred to post-confirmation - never reached
    mock_place.assert_not_called()


def test_gated_option_buy_survives_an_unexpected_error():
    signal = {"source": "discord", "ticker": "AAPL", "symbol": "AAPL", "side": "CALL", "confidence": 0.9}
    with patch.object(th, "confirm_entry", side_effect=RuntimeError("broker down")), \
         patch.object(th, "post_discord") as mock_discord:
        th._gated_option_buy(signal, "AAPL", "CALL", "bullish", 200.0, (__import__("datetime").date.today()+__import__("datetime").timedelta(days=7)),
                              0.9, None, None, None, "")  # must not raise

    assert mock_discord.called
