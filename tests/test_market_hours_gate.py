# Covers REQUIRE_MARKET_OPEN: a new entry submitted while the market is
# closed doesn't fail at the broker, it just sits queued and fills at the
# next open - at whatever price that turns out to be, after every risk
# gate upstream (price-drift-since-alert, sizing, correlation/PDT) was
# computed against a stale last-close quote at signal time, not the price
# it will actually fill at. Exits are never affected, same philosophy as
# every other entry-only gate in trade_handler.py.
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import lavish_core.trading.trade_handler as th


def _equity_signal(action="BUY", symbol="AAPL"):
    return {"source": "patreon", "action": action, "symbol": symbol, "confidence": 0.9}


def _option_signal():
    expiry = (date.today() + timedelta(days=5)).isoformat()
    return {"ticker": "AAPL", "side": "CALL", "strike": 200.0, "expiry": expiry, "confidence": 0.9}


def test_equity_buy_is_blocked_while_market_is_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    with patch.object(th, "get_clock", return_value={"is_open": False}), \
         patch.object(th, "place_trade") as mock_place:
        handled = th._execute_equity_trade(_equity_signal())

    assert handled is True  # deterministic, deliberate skip - not retryable
    mock_place.assert_not_called()


def test_equity_sell_is_never_blocked_by_market_hours(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    with patch.object(th, "get_clock", return_value={"is_open": False}), \
         patch.object(th, "latest_quote", return_value=150.0), \
         patch.object(th, "broker_get_position", return_value={"qty": "10"}), \
         patch.object(th, "place_trade", return_value={"status": "filled", "order_id": "x", "legs": []}) as mock_place:
        handled = th._execute_equity_trade(_equity_signal(action="SELL"))

    assert handled is True
    mock_place.assert_called_once()  # exit went through despite the market being closed


def test_options_buy_is_blocked_while_market_is_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    with patch.object(th, "get_clock", return_value={"is_open": False}), \
         patch.object(th, "place_option_order") as mock_place:
        handled = th._execute_option_trade(_option_signal())

    assert handled is True
    mock_place.assert_not_called()


def test_trade_proceeds_normally_when_market_is_open(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    with patch.object(th, "get_clock", return_value={"is_open": True}), \
         patch.object(th, "latest_quote", return_value=150.0), \
         patch.object(th, "check_correlation_ok", return_value=(True, "ok")), \
         patch.object(th, "check_pdt_ok", return_value=(True, "ok")), \
         patch.object(th, "place_trade", return_value={"status": "filled", "order_id": "x", "legs": []}) as mock_place:
        handled = th._execute_equity_trade(_equity_signal())

    assert handled is True
    mock_place.assert_called_once()


def test_dry_mode_never_calls_the_real_clock(tmp_path, monkeypatch):
    # Dry mode never touches the real broker - it must not even attempt a
    # network call to check market hours.
    monkeypatch.setattr(th, "TRADE_MODE", "dry")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    with patch.object(th, "get_clock", side_effect=AssertionError("dry mode must not call get_clock")), \
         patch.object(th, "place_trade", return_value={"status": "filled", "order_id": "x", "legs": []}) as mock_place:
        handled = th._execute_equity_trade(_equity_signal())

    assert handled is True
    mock_place.assert_called_once()


def test_clock_read_failure_fails_closed_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    monkeypatch.setattr(th, "RISK_CHECK_FAIL_OPEN", False)
    with patch.object(th, "get_clock", side_effect=RuntimeError("Alpaca down")), \
         patch.object(th, "place_trade") as mock_place:
        handled = th._execute_equity_trade(_equity_signal())

    assert handled is True  # blocked, deterministically - not left to retry forever
    mock_place.assert_not_called()


def test_clock_read_failure_can_be_configured_to_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    monkeypatch.setattr(th, "RISK_CHECK_FAIL_OPEN", True)
    with patch.object(th, "get_clock", side_effect=RuntimeError("Alpaca down")), \
         patch.object(th, "latest_quote", return_value=150.0), \
         patch.object(th, "check_correlation_ok", return_value=(True, "ok")), \
         patch.object(th, "check_pdt_ok", return_value=(True, "ok")), \
         patch.object(th, "place_trade", return_value={"status": "filled", "order_id": "x", "legs": []}) as mock_place:
        handled = th._execute_equity_trade(_equity_signal())

    assert handled is True
    mock_place.assert_called_once()


def test_require_market_open_false_disables_the_gate_entirely(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    monkeypatch.setattr(th, "REQUIRE_MARKET_OPEN", False)
    with patch.object(th, "get_clock", side_effect=AssertionError("must not be called when disabled")), \
         patch.object(th, "latest_quote", return_value=150.0), \
         patch.object(th, "check_correlation_ok", return_value=(True, "ok")), \
         patch.object(th, "check_pdt_ok", return_value=(True, "ok")), \
         patch.object(th, "place_trade", return_value={"status": "filled", "order_id": "x", "legs": []}) as mock_place:
        handled = th._execute_equity_trade(_equity_signal())

    assert handled is True
    mock_place.assert_called_once()
