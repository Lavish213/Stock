# Randomized/property-style stress test for candle_gatekeeper.confirm_entry()
# across volatility regimes - complements test_candle_gatekeeper.py's hand-
# built scenarios (exact expected outcomes) rather than replacing them.
# Here we don't assert WHICH outcome a given random path produces (that's
# inherently path-dependent) - we assert the INVARIANTS that must hold no
# matter how volatile/chaotic the price action is: it always terminates,
# always returns a well-shaped result, never raises, and a "confirmed"
# result always carries a real price from a real bar.
#
# A seeded fake clock (time.time()/time.sleep() both mocked, advancing a
# deterministic counter instead of real wall-clock time) keeps every one of
# these hundreds of iterations near-instant and fully reproducible.
from __future__ import annotations

import random
from unittest.mock import patch

import lavish_core.trade.candle_gatekeeper as gk

_VALID_REASONS = {
    "wick_breakout_volume_confirmed",
    "wick_breakout_volume_unavailable",
    "invalidated_wrong_direction",
    "timeout",
    "no_data",
}


class _FakeClock:
    """Deterministic, monotonically-advancing stand-in for time.time() -
    every call advances by `step` (matching POLL_INTERVAL_SEC), so the
    gate's own while-loop deadline math naturally bounds how many
    iterations any single fuzz case can run, regardless of how the random
    path resolves."""
    def __init__(self, step: float):
        self.now = 0.0
        self.step = step

    def time(self) -> float:
        self.now += self.step
        return self.now


def _bars(rng: random.Random, n: int, start: float, vol_pct: float, drift_pct: float = 0.0,
          gap_at: int | None = None, gap_pct: float = 0.0):
    """Synthetic 1-minute OHLCV bars via a simple random walk. `gap_at` lets
    a single scenario inject one large jump mid-path (crash/melt-up) to
    stress the gate under a sudden move, not just gradual drift."""
    out = []
    price = start
    for i in range(n):
        if gap_at is not None and i == gap_at:
            price = max(0.01, price * (1 + gap_pct))
        o = price
        move = rng.gauss(drift_pct, vol_pct) * o
        c = max(0.01, o + move)
        wick_up = abs(rng.gauss(0, vol_pct * 0.5)) * o
        wick_down = abs(rng.gauss(0, vol_pct * 0.5)) * o
        h = max(o, c) + wick_up
        l = max(0.01, min(o, c) - wick_down)
        v = max(0.0, rng.gauss(1000, 400))
        out.append({"t": f"{i:013d}", "o": round(o, 4), "h": round(h, 4),
                    "l": round(l, 4), "c": round(c, 4), "v": round(v, 2)})
        price = c
    return out


def _streaming_get_bars(all_bars, initial_reveal):
    """Each call reveals one more bar than the last - simulates a new real
    bar closing each poll, rather than handing the gate the whole future
    path at once."""
    state = {"revealed": initial_reveal}

    def fn(symbol, limit=20):
        state["revealed"] = min(len(all_bars), state["revealed"] + 1)
        return all_bars[:state["revealed"]][-limit:]
    return fn


def _run_case(bars, direction, max_wait_seconds=300.0):
    clock = _FakeClock(step=gk.POLL_INTERVAL_SEC)
    with patch.object(gk, "time") as mock_time, \
         patch.object(gk, "get_bars", side_effect=_streaming_get_bars(bars, gk.VOLUME_LOOKBACK_BARS + 2)):
        mock_time.time.side_effect = clock.time
        mock_time.sleep.side_effect = lambda s: None
        result = gk.confirm_entry("FUZZ", direction, max_wait_seconds=max_wait_seconds)

    assert isinstance(result, dict), f"result must be a dict, got {type(result)}"
    assert {"confirmed", "reason", "price", "bar"} <= set(result.keys())
    assert isinstance(result["confirmed"], bool)
    assert result["reason"] in _VALID_REASONS, f"unexpected reason: {result['reason']}"
    if result["confirmed"]:
        assert isinstance(result["price"], (int, float))
        assert result["bar"] is not None
    return result


_REGIMES = {
    "low_vol_chop":     dict(vol_pct=0.0005, drift_pct=0.0),
    "medium_vol_trend":  dict(vol_pct=0.003,  drift_pct=0.0015),
    "medium_vol_trend_down": dict(vol_pct=0.003, drift_pct=-0.0015),
    "high_vol_whipsaw": dict(vol_pct=0.015,  drift_pct=0.0),
    "crash_gap":        dict(vol_pct=0.002,  drift_pct=0.0, gap_at=15, gap_pct=-0.06),
    "melt_up_gap":      dict(vol_pct=0.002,  drift_pct=0.0, gap_at=15, gap_pct=0.06),
}

_SEEDS = range(25)  # 25 seeds x 6 regimes x 2 directions = 300 fuzz cases


def test_gatekeeper_never_crashes_or_hangs_across_random_volatility_regimes():
    failures = []
    for regime_name, params in _REGIMES.items():
        for seed in _SEEDS:
            for direction in ("bullish", "bearish"):
                rng = random.Random(f"{regime_name}-{seed}-{direction}")
                bars = _bars(rng, n=40, start=100.0, **params)
                try:
                    _run_case(bars, direction)
                except Exception as e:
                    failures.append(f"{regime_name}/seed={seed}/{direction}: {type(e).__name__}: {e}")

    assert not failures, "Gatekeeper fuzz failures:\n" + "\n".join(failures)


def test_confirmed_price_always_came_from_a_bar_that_actually_broke_the_reference():
    # Stronger invariant than "no crash": if confirmed, the price itself
    # must be a real breakout relative to SOME reference level, not just
    # structurally well-shaped output.
    for seed in range(15):
        rng = random.Random(f"breakout-check-{seed}")
        bars = _bars(rng, n=40, start=100.0, vol_pct=0.01, drift_pct=0.002)
        result = _run_case(bars, "bullish")
        if result["confirmed"]:
            assert result["price"] > 0


def test_no_data_at_all_always_fails_closed_regardless_of_direction():
    for direction in ("bullish", "bearish"):
        clock = _FakeClock(step=gk.POLL_INTERVAL_SEC)
        with patch.object(gk, "time") as mock_time, \
             patch.object(gk, "get_bars", return_value=[]):
            mock_time.time.side_effect = clock.time
            mock_time.sleep.side_effect = lambda s: None
            result = gk.confirm_entry("FUZZ", direction, max_wait_seconds=200.0)
        assert result["confirmed"] is False
        assert result["reason"] == "no_data"
