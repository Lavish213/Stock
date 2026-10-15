# Randomized premium-path stress test for watch_and_exit() - complements
# test_options_exit_monitor.py's hand-built scenarios. Focuses specifically
# on the "our own guardrails" (stop/trail) + worthless-option paths across
# randomized premium volatility, since that's the dimension most exposed to
# real market chaos (a short-dated option's premium can move far more
# violently, percentage-wise, than the underlying itself). her_target/
# her_stop (the underlying-price-based levels) already have dedicated
# deterministic coverage elsewhere and aren't re-fuzzed here.
#
# Invariant checked: regardless of how violent/chaotic the premium path is
# (including occasionally going to zero mid-path, a real deep-OTM/near-
# expiry scenario), the function always eventually terminates with
# status="exited" and a reason from the known set - never hangs, never
# raises, never returns an ambiguous/partial result.
from __future__ import annotations

import random
from unittest.mock import patch

import lavish_core.trade.options_exit_monitor as oem


class _FakeClock:
    def __init__(self, step: float):
        self.now = 0.0
        self.step = step

    def time(self) -> float:
        self.now += self.step
        return self.now


_VALID_REASONS_PREFIXES = (
    "our_stop_loss", "our_trailing_stop", "her_target", "her_stop",
    "expiry_force_close", "max_hold_timeout",
)


def _premium_path(rng: random.Random, n: int, start: float, vol_pct: float,
                   worthless_at: int | None = None):
    path = [start]
    for i in range(n):
        if worthless_at is not None and i >= worthless_at:
            path.append(0.0)
            continue
        move = rng.gauss(0, vol_pct) * path[-1]
        path.append(max(0.0, path[-1] + move))
    return path


def _streaming_option_quote(premiums):
    state = {"i": 0}

    def fn(symbol):
        i = min(state["i"], len(premiums) - 1)
        state["i"] += 1
        p = premiums[i]
        if p <= 0.0:
            return {"bid": 0.0, "ask": 0.01}  # confirmed-worthless shape, not "missing data"
        return {"bid": round(p * 0.95, 2), "ask": round(p * 1.05, 2)}
    return fn


def _run_case(premiums, entry_price, option_side="call", max_hold_hours=0.02):
    clock = _FakeClock(step=oem.POLL_INTERVAL_SEC)

    def fake_place_option_order(contract_symbol, side, qty, order_type="limit", limit_price=None):
        return {"id": "o1", "status": "filled", "filled_avg_price": str(limit_price), "filled_qty": str(qty)}

    with patch.object(oem, "time") as mock_time, \
         patch.object(oem, "latest_quote", return_value=100.0), \
         patch.object(oem, "latest_option_quote", side_effect=_streaming_option_quote(premiums)), \
         patch.object(oem, "place_option_order", side_effect=fake_place_option_order), \
         patch.object(oem, "get_order"), \
         patch.object(oem, "post_discord"), \
         patch.object(oem, "MAX_MONITOR_HOURS", max_hold_hours):
        mock_time.time.side_effect = clock.time
        mock_time.sleep.side_effect = lambda s: None

        result = oem.watch_and_exit(
            underlying_symbol="FUZZ", contract_symbol="FUZZ260101C00100000",
            qty=1, option_side=option_side, entry_price=entry_price,
        )

    assert result["status"] == "exited"
    assert any(result["reason"].startswith(p) for p in _VALID_REASONS_PREFIXES), \
        f"unexpected reason: {result['reason']}"
    return result


_REGIMES = {
    "low_vol":   dict(vol_pct=0.03),
    "med_vol":   dict(vol_pct=0.15),
    "high_vol":  dict(vol_pct=0.5),   # options premium can genuinely move this violently
}


def test_options_exit_always_terminates_across_random_premium_volatility():
    failures = []
    for regime_name, params in _REGIMES.items():
        for seed in range(20):
            rng = random.Random(f"{regime_name}-{seed}")
            premiums = _premium_path(rng, n=25, start=4.0, **params)
            try:
                _run_case(premiums, entry_price=4.0)
            except Exception as e:
                failures.append(f"{regime_name}/seed={seed}: {type(e).__name__}: {e}")

    assert not failures, "Options exit-monitor fuzz failures:\n" + "\n".join(failures)


def test_premium_going_to_zero_mid_path_always_resolves_as_worthless_not_a_hang():
    for seed in range(15):
        rng = random.Random(f"worthless-{seed}")
        worthless_at = rng.randint(2, 8)
        premiums = _premium_path(rng, n=20, start=4.0, vol_pct=0.1, worthless_at=worthless_at)
        result = _run_case(premiums, entry_price=4.0)
        # Either it genuinely hit a worthless premium (reason suffix) or
        # one of our own guardrails fired first on the way down - both are
        # valid, correct outcomes; what must NOT happen is a hang or crash,
        # already asserted inside _run_case.
        assert result["option_price"] is not None


def test_put_side_premium_fuzz_also_terminates_cleanly():
    # option_side flips which underlying direction is "good" for her
    # levels (not exercised here directly), but the premium-based
    # guardrails below should behave identically regardless of call/put.
    for seed in range(10):
        rng = random.Random(f"put-{seed}")
        premiums = _premium_path(rng, n=25, start=3.0, vol_pct=0.2)
        _run_case(premiums, entry_price=3.0, option_side="put")
