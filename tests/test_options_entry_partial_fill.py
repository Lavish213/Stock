# Covers a real gap found and fixed: a partially-filled options entry
# order left its unfilled remainder resting open at the broker
# indefinitely - nothing canceled it. That remainder could fill hours
# later, unpredictably, against market conditions none of this alert's
# own checks (confidence floor, kill switch, price-drift gate, risk
# gates) were ever re-validated against, and until the next reconcile
# sweep happened to catch it, that extra fill would sit with no exit
# monitor watching it at all.
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import lavish_core.trading.trade_handler as th


def _contract(symbol="AAPL260101C00200000", mid_price=1.50):
    return {"symbol": symbol, "mid_price": mid_price}


def _options_signal(ticker="AAPL", side="CALL", strike=200.0, confidence=0.9, amount_usd=500.0):
    expiry = (date.today() + timedelta(days=5)).isoformat()
    return {"ticker": ticker, "side": side, "strike": strike, "expiry": expiry,
            "confidence": confidence, "amount_usd": amount_usd}


def test_partial_fill_remainder_is_canceled(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    with patch.object(th.time, "sleep"), \
         patch.object(th, "resolve_and_price_contract", return_value=_contract()), \
         patch.object(th, "circuit_breaker_check_ok", return_value=(True, "ok")), \
         patch.object(th, "check_correlation_ok", return_value=(True, "ok")), \
         patch.object(th, "check_pdt_ok", return_value=(True, "ok")), \
         patch.object(th, "place_option_order",
                       return_value={"id": "o1", "status": "partially_filled", "filled_qty": "2",
                                      "filled_avg_price": "1.50"}), \
         patch.object(th, "broker_get_order",
                       return_value={"status": "partially_filled", "filled_qty": "2", "filled_avg_price": "1.50"}), \
         patch.object(th, "broker_cancel_order") as mock_cancel, \
         patch.object(th, "watch_and_exit_async") as mock_watch:
        handled = th._execute_option_trade(_options_signal())

    assert handled is True
    mock_cancel.assert_called_once_with("o1")  # unfilled remainder canceled
    mock_watch.assert_called_once()
    assert mock_watch.call_args.kwargs["qty"] == 2  # exit monitor watches only the confirmed filled qty


def test_full_fill_is_not_canceled(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    with patch.object(th.time, "sleep"), \
         patch.object(th, "resolve_and_price_contract", return_value=_contract()), \
         patch.object(th, "circuit_breaker_check_ok", return_value=(True, "ok")), \
         patch.object(th, "check_correlation_ok", return_value=(True, "ok")), \
         patch.object(th, "check_pdt_ok", return_value=(True, "ok")), \
         patch.object(th, "place_option_order",
                       return_value={"id": "o1", "status": "filled", "filled_qty": "3", "filled_avg_price": "1.50"}), \
         patch.object(th, "broker_cancel_order") as mock_cancel, \
         patch.object(th, "watch_and_exit_async"):
        handled = th._execute_option_trade(_options_signal())

    assert handled is True
    mock_cancel.assert_not_called()  # nothing left to cancel on a full fill
