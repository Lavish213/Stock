# Covers a severe, previously-undiscovered bug: extract_signal.py's
# confidence score is weighted for OPTIONS fields (side/strike/expiry
# worth 0.50 of it combined) - an equity-only alert structurally cannot
# earn those points, capping it at ticker+entry+target/stop (0.45 at
# best), always under CONF_FLOOR (0.55 default) no matter how clearly
# worded the buy/sell intent was. Confirmed directly, end to end: "BUY
# NVDA now, breaking out" - completely unambiguous - scored 0.25 and was
# silently skipped by trade_handler's confidence-floor check, meaning NO
# plain equity alert from either Patreon or Discord could ever actually
# execute, regardless of how many of them came in.
#
# This went unnoticed because every existing test either checked
# classification in isolation (never exercising trade_handler's real
# confidence gate - simulate_alerts.py's own dry-run preview and
# test_pipeline_integration.py's _classify() helper both skip it too) or
# constructed signal dicts by hand with confidence hardcoded (e.g.
# confidence=0.9), bypassing alert_handler.py's real computation. These
# tests go all the way from raw alert text through handle_alert_text()
# into the real execution path, with nothing mocked at that boundary.
from __future__ import annotations

import tempfile
from unittest.mock import patch

import lavish_core.trading.alert_handler as ah
import lavish_core.trading.trade_handler as th


def test_plain_equity_alert_reaches_the_broker_call(monkeypatch):
    monkeypatch.setattr(th, "DEFAULT_DB", tempfile.mktemp(suffix=".duckdb"))
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    with patch.object(th, "place_trade", return_value={"status": "filled", "order_id": "x", "legs": []}) as mock_place, \
         patch.object(th, "latest_quote", return_value=150.0), \
         patch.object(th, "check_correlation_ok", return_value=(True, "ok")), \
         patch.object(th, "check_pdt_ok", return_value=(True, "ok")), \
         patch.object(th, "get_clock", return_value={"is_open": True}):
        ah.handle_alert_text("BUY NVDA now, breaking out", source="test")

    mock_place.assert_called_once()
    assert mock_place.call_args.kwargs["side"] == "buy"
    assert mock_place.call_args.kwargs["symbol"] == "NVDA"


def test_plain_equity_alert_confidence_exceeds_the_floor():
    # Direct check of the actual number, independent of whether it's
    # dispatched - this is the number that matters for the gate.
    parsed = ah.parse_alert(text="BUY NVDA now, breaking out")
    conf = ah._equity_confidence(parsed, "BUY NVDA now, breaking out")
    assert conf >= th.CONF_FLOOR


def test_equity_confidence_baseline_matches_the_old_flat_default():
    # Pins the calibration choice: a clear ticker + clear direction, no
    # target/stop, no hedging, should land at exactly the old flat
    # DEFAULT_CONFIDENCE value - the new scoring changes the gradient
    # around that point, not the common-case baseline itself.
    parsed = ah.parse_alert(text="BUY NVDA now, breaking out")
    conf = ah._equity_confidence(parsed, "BUY NVDA now, breaking out")
    assert conf == ah.DEFAULT_CONFIDENCE


def test_equity_confidence_rises_with_an_explicit_target_or_stop():
    parsed = ah.parse_alert(text="BUY NVDA now, target 150 stop 140")
    conf = ah._equity_confidence(parsed, "BUY NVDA now, target 150 stop 140")
    assert conf > ah.DEFAULT_CONFIDENCE


def test_hedge_language_drops_a_plain_equity_alert_below_the_floor():
    # Real improvement built this round: a clear ticker/direction with
    # genuinely hedged language ("maybe", "not sure") used to score
    # identically to a decisive call - nothing before this distinguished
    # the creator's own uncertainty from a real, actionable signal.
    text = "maybe thinking about buying NVDA here, not sure"
    parsed = ah.parse_alert(text=text)
    conf = ah._equity_confidence(parsed, text)
    assert conf < th.CONF_FLOOR


def test_hedge_language_alert_is_actually_skipped_end_to_end(monkeypatch):
    import tempfile
    monkeypatch.setattr(th, "DEFAULT_DB", tempfile.mktemp(suffix=".duckdb"))
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    with patch.object(th, "place_trade") as mock_place:
        ah.handle_alert_text("maybe thinking about buying NVDA here, not sure", source="test")
    mock_place.assert_not_called()


def test_weaker_ticker_match_lowers_equity_confidence():
    # A near-miss fuzzy match (accepted - above MIN_TICKER_SCORE=75 - but
    # not exact) is less certain than an exact match and should score
    # lower, not get the same full credit either way.
    strong = ah._equity_confidence({"ticker_score": 100}, "BUY NVDA now, breaking out")
    weak = ah._equity_confidence({"ticker_score": 76}, "BUY NVDA now, breaking out")
    assert weak < strong


def test_casual_sell_phrasing_also_reaches_the_broker_call(monkeypatch):
    monkeypatch.setattr(th, "DEFAULT_DB", tempfile.mktemp(suffix=".duckdb"))
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    with patch.object(th, "place_trade", return_value={"status": "filled", "order_id": "x", "legs": []}) as mock_place, \
         patch.object(th, "latest_quote", return_value=150.0), \
         patch.object(th, "broker_get_position", return_value={"qty": "10"}), \
         patch.object(th, "get_clock", return_value={"is_open": True}):
        ah.handle_alert_text("SELL AAPL, taking profit", source="test")

    mock_place.assert_called_once()
    assert mock_place.call_args.kwargs["side"] == "sell"


def test_options_alert_confidence_is_unaffected_by_this_fix(monkeypatch):
    # The options path must keep using extract_signal.py's own weighted
    # score (not the flat equity default) - this fix only changes the
    # equity branch.
    monkeypatch.setattr(th, "DEFAULT_DB", tempfile.mktemp(suffix=".duckdb"))
    monkeypatch.setattr(th, "TRADE_MODE", "paper")
    captured = {}

    def _capture(signal):
        captured.update(signal)
        return True

    with patch.object(th, "_execute_option_trade", side_effect=_capture):
        ah.handle_alert_text("AAPL $190 calls target 200 stop 180 exp 10/16", source="test")

    assert captured.get("confidence") not in (None, ah.DEFAULT_CONFIDENCE)
    assert captured["confidence"] > 0.8  # ticker+side+strike+expiry+target/stop, options-weighted
