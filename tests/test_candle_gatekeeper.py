# Covers candle_gatekeeper.confirm_entry() - the pre-entry confirmation
# gate that watches 1-minute bars for a real wick-breakout (with volume as
# a secondary, non-blocking corroboration) before trade_handler.py is
# allowed to place an order. Driven directly with mocked get_bars() and
# time.sleep patched to a no-op, same style as test_options_exit_monitor.py.
from __future__ import annotations

from unittest.mock import patch

import lavish_core.trade.candle_gatekeeper as gk


def _no_sleep(*a, **k):
    pass


def _bar(t, o, h, l, c, v=1000.0):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def test_disabled_returns_not_confirmed_with_explicit_reason():
    with patch.object(gk, "ENABLED", False):
        result = gk.confirm_entry("AAPL", "bullish")
    assert result["confirmed"] is False
    assert result["reason"] == "gatekeeper_disabled"


def test_rejects_invalid_direction():
    import pytest
    with pytest.raises(ValueError):
        gk.confirm_entry("AAPL", "sideways")


def test_bullish_wick_breakout_with_volume_confirms():
    ref = _bar("t0", 100.0, 101.0, 99.0, 100.5)
    lookback = [_bar(f"t-{i}", 100, 100.5, 99.5, 100.0, v=500.0) for i in range(10)]
    breakout = _bar("t1", 100.5, 102.0, 100.4, 101.5, v=1000.0)  # closes above ref high, 2x volume

    with patch.object(gk, "time") as mock_time, \
         patch.object(gk, "get_bars", side_effect=[
             lookback + [ref],
             lookback + [ref, breakout],
         ]):
        mock_time.sleep.side_effect = _no_sleep
        mock_time.time.side_effect = list(range(20))

        result = gk.confirm_entry("AAPL", "bullish")

    assert result["confirmed"] is True
    assert result["reason"] == "wick_breakout_volume_confirmed"
    assert result["price"] == 101.5


def test_bullish_breakout_without_volume_data_still_confirms_on_price_alone():
    # Volume confirmation is a secondary corroboration, not a hard
    # requirement - missing/flat volume data must not block an otherwise
    # real wick breakout.
    ref = _bar("t0", 100.0, 101.0, 99.0, 100.5, v=0.0)
    lookback = [_bar(f"t-{i}", 100, 100.5, 99.5, 100.0, v=0.0) for i in range(10)]
    breakout = _bar("t1", 100.5, 102.0, 100.4, 101.5, v=0.0)

    with patch.object(gk, "time") as mock_time, \
         patch.object(gk, "get_bars", side_effect=[
             lookback + [ref],
             lookback + [ref, breakout],
         ]):
        mock_time.sleep.side_effect = _no_sleep
        mock_time.time.side_effect = list(range(20))

        result = gk.confirm_entry("AAPL", "bullish")

    assert result["confirmed"] is True
    assert result["reason"] == "wick_breakout_volume_unavailable"


def test_bearish_wick_breakdown_confirms():
    ref = _bar("t0", 100.0, 101.0, 99.0, 99.5)
    lookback = [_bar(f"t-{i}", 100, 100.5, 99.5, 100.0, v=500.0) for i in range(10)]
    breakdown = _bar("t1", 99.5, 99.6, 97.0, 98.0, v=1000.0)  # closes below ref low

    with patch.object(gk, "time") as mock_time, \
         patch.object(gk, "get_bars", side_effect=[
             lookback + [ref],
             lookback + [ref, breakdown],
         ]):
        mock_time.sleep.side_effect = _no_sleep
        mock_time.time.side_effect = list(range(20))

        result = gk.confirm_entry("AAPL", "bearish")

    assert result["confirmed"] is True
    assert result["price"] == 98.0


def test_price_closing_through_the_opposite_wick_invalidates_a_bullish_alert():
    ref = _bar("t0", 100.0, 101.0, 99.0, 100.5)
    lookback = [_bar(f"t-{i}", 100, 100.5, 99.5, 100.0) for i in range(10)]
    wrong_way = _bar("t1", 100.0, 100.2, 97.0, 98.0)  # closes below the ref LOW - alert is dead

    with patch.object(gk, "time") as mock_time, \
         patch.object(gk, "get_bars", side_effect=[
             lookback + [ref],
             lookback + [ref, wrong_way],
         ]):
        mock_time.sleep.side_effect = _no_sleep
        mock_time.time.side_effect = list(range(20))

        result = gk.confirm_entry("AAPL", "bullish")

    assert result["confirmed"] is False
    assert result["reason"] == "invalidated_wrong_direction"


def test_neither_breakout_nor_invalidation_times_out():
    ref = _bar("t0", 100.0, 101.0, 99.0, 100.5)
    chopping = _bar("t1", 100.4, 100.8, 99.8, 100.3)  # inside the range both ways

    with patch.object(gk, "time") as mock_time, \
         patch.object(gk, "get_bars", return_value=[ref, chopping]):
        mock_time.sleep.side_effect = _no_sleep
        # First call establishes deadline; everything after stays under it
        # except the final check, which pushes past.
        mock_time.time.side_effect = [0] + [100] * 10 + [10**9]

        result = gk.confirm_entry("AAPL", "bullish", max_wait_seconds=500)

    assert result["confirmed"] is False
    assert result["reason"] == "timeout"


def test_no_bar_data_at_all_fails_closed_not_open():
    # Missing one entry during a data outage is far cheaper than entering
    # blind with zero confirmation - the entire point of this gate.
    with patch.object(gk, "time") as mock_time, \
         patch.object(gk, "get_bars", return_value=[]):
        mock_time.sleep.side_effect = _no_sleep
        mock_time.time.side_effect = list(range(30))

        result = gk.confirm_entry("AAPL", "bullish")

    assert result["confirmed"] is False
    assert result["reason"] == "no_data"


def test_transient_no_data_recovers_once_bars_come_back():
    ref = _bar("t0", 100.0, 101.0, 99.0, 100.5, v=500.0)
    lookback = [_bar(f"t-{i}", 100, 100.5, 99.5, 100.0, v=500.0) for i in range(10)]
    breakout = _bar("t1", 100.5, 102.0, 100.4, 101.5, v=500.0)

    with patch.object(gk, "time") as mock_time, \
         patch.object(gk, "get_bars", side_effect=[
             [],  # transient outage
             [],
             lookback + [ref],
             lookback + [ref, breakout],
         ]):
        mock_time.sleep.side_effect = _no_sleep
        mock_time.time.side_effect = list(range(30))

        result = gk.confirm_entry("AAPL", "bullish")

    assert result["confirmed"] is True
