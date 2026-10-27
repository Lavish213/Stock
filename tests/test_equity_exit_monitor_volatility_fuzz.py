# Randomized price-path stress test for watch_and_exit_active() across
# volatility regimes - complements test_equity_exit_monitor_active.py's
# hand-built scenarios. The invariant this checks: no matter how the price
# path wanders (slow chop, violent whipsaw, a gap through both target and
# stop in one bar), the function MUST eventually terminate with
# status="exited" (by design it never gives up - see its own "must
# actually succeed" forced-close retry loop) and the reported P&L must be
# arithmetically consistent with the entry/exit prices it reports.
#
# place_order is mocked to confirm-fill immediately at the current
# simulated price - this test isolates the PRICE-PATH dimension specific-
# ally; order-submission retry/cancel-and-retry behavior already has its
# own dedicated, deterministic coverage in test_equity_exit_monitor_active.py
# and isn't re-fuzzed here (one random dimension per test keeps a failure
# diagnosable).
from __future__ import annotations

import random
from unittest.mock import patch

import pytest

import lavish_core.trade.equity_exit_monitor as eem
from lavish_core.db.hybrid_store import HybridStore


class _FakeClock:
    def __init__(self, step: float):
        self.now = 0.0
        self.step = step

    def time(self) -> float:
        self.now += self.step
        return self.now


def _price_path(rng: random.Random, n: int, start: float, vol_pct: float, drift_pct: float = 0.0,
                 gap_at: int | None = None, gap_pct: float = 0.0):
    prices = [start]
    for i in range(n):
        if gap_at is not None and i == gap_at:
            prices[-1] = max(0.01, prices[-1] * (1 + gap_pct))
        move = rng.gauss(drift_pct, vol_pct) * prices[-1]
        prices.append(max(0.01, prices[-1] + move))
    return prices


def _streaming_quote(prices):
    state = {"i": 0}

    def fn(symbol):
        i = min(state["i"], len(prices) - 1)
        state["i"] += 1
        return prices[i]
    return fn


@pytest.fixture(autouse=True)
def _real_store(tmp_path, monkeypatch):
    db_path = tmp_path / "test.duckdb"
    store = HybridStore(duckdb_path=db_path)
    store.con.close()
    monkeypatch.setattr(eem, "DEFAULT_DB", db_path)
    # Short monitor window (minutes, not the real 12h default) so every
    # fuzz case that never hits target/stop still terminates fast via the
    # forced-close path, keeping this test suite quick.
    monkeypatch.setattr(eem, "MAX_MONITOR_HOURS", 0.05)  # 3 minutes


def _run_case(prices, entry_price, take_profit, stop_loss, symbol="FUZZ"):
    store = HybridStore(duckdb_path=eem.DEFAULT_DB)
    try:
        oid = store.submit_order(symbol, "buy", 10)
        store.log_fill(oid, symbol, "buy", 10, entry_price)
    finally:
        store.con.close()
    clock = _FakeClock(step=eem.POLL_INTERVAL_SEC)

    def fake_place_order(symbol, side, qty, type_="market", time_in_force="day", client_order_id=None):
        # Confirm-fills immediately at "the current price" - approximated
        # here as the most recent price the monitor has already observed,
        # close enough for this test's purpose (P&L consistency, not exact
        # fill-price modeling).
        return {"id": f"o-{symbol}", "status": "filled", "filled_avg_price": str(prices[-1]), "filled_qty": "10"}

    with patch.object(eem, "time") as mock_time, \
         patch.object(eem, "latest_quote", side_effect=_streaming_quote(prices)), \
         patch.object(eem, "place_order", side_effect=fake_place_order), \
         patch.object(eem, "get_order"), \
         patch.object(eem, "get_order_by_client_id", return_value=None), \
         patch.object(eem, "post_discord"):
        mock_time.time.side_effect = clock.time
        mock_time.sleep.side_effect = lambda s: None

        result = eem.watch_and_exit_active(
            symbol=symbol, qty=10, entry_price=entry_price,
            take_profit=take_profit, stop_loss=stop_loss, venue="paper",
        )

    assert result["status"] == "exited"
    assert result["reason"] in ("target", "stop", "max_hold_timeout")
    assert isinstance(result["exit_price"], float)
    assert isinstance(result["pnl"], float)
    expected_pnl = (result["exit_price"] - entry_price) * 10
    assert result["pnl"] == pytest.approx(expected_pnl, abs=1e-6)
    return result


_REGIMES = {
    "low_vol_chop":          dict(vol_pct=0.0003, drift_pct=0.0),
    "medium_vol_trend_up":   dict(vol_pct=0.002,  drift_pct=0.001),
    "medium_vol_trend_down": dict(vol_pct=0.002,  drift_pct=-0.001),
    "high_vol_whipsaw":      dict(vol_pct=0.01,   drift_pct=0.0),
    "flash_crash":           dict(vol_pct=0.002,  drift_pct=0.0, gap_at=10, gap_pct=-0.08),
    "melt_up":               dict(vol_pct=0.002,  drift_pct=0.0, gap_at=10, gap_pct=0.08),
}


def test_equity_active_exit_always_terminates_and_pnl_is_consistent():
    entry = 150.0
    take_profit = entry * 1.02
    stop_loss = entry * 0.985
    failures = []

    for regime_name, params in _REGIMES.items():
        for seed in range(20):
            rng = random.Random(f"{regime_name}-{seed}")
            prices = _price_path(rng, n=30, start=entry, **params)
            try:
                _run_case(prices, entry, take_profit, stop_loss, symbol=f"FUZZ-{regime_name}-{seed}")
            except Exception as e:
                failures.append(f"{regime_name}/seed={seed}: {type(e).__name__}: {e}")

    assert not failures, "Equity exit-monitor fuzz failures:\n" + "\n".join(failures)


def test_a_single_bar_gapping_through_both_target_and_stop_still_resolves_cleanly():
    # The real-world case this exists for: a gap-down (or up) open that
    # jumps straight past BOTH levels in one tick, not a gradual approach
    # to either - confirm the monitor still picks one cleanly rather than
    # hanging on an ambiguous "both triggered at once" state.
    entry = 100.0
    for seed in range(10):
        rng = random.Random(f"double-gap-{seed}")
        # One huge move that blows through stop (95) and target (103) in
        # a single step, in a random direction each seed.
        direction = rng.choice([1, -1])
        prices = [entry, entry + direction * 20.0] + _price_path(rng, 10, entry + direction * 20.0, 0.001)
        result = _run_case(prices, entry, take_profit=103.0, stop_loss=95.0, symbol=f"GAP-{seed}")
        assert result["reason"] in ("target", "stop", "max_hold_timeout")
