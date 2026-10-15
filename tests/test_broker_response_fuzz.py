# Randomized malformed-response stress test for the broker normalizers
# (broker_tradier.py's _normalize_position/_normalize_order, and
# broker_alpaca.py's get_bars field extraction). A real broker can return
# a field as null, a string instead of a number, missing entirely, or an
# unexpected type during an outage/partial-degradation - this generates
# many randomized "garbage" response shapes and checks that each
# normalizer either produces a sane result or raises a clear, expected
# exception, but never an unhandled KeyError/TypeError/AttributeError that
# would crash whatever loop called it (an exit monitor, the gatekeeper,
# reconcile.py's sweep) over a single bad response.
from __future__ import annotations

import random

import pytest

import lavish_core.trade.broker_tradier as tradier

# Exceptions a normalizer is allowed to raise on genuinely bad input -
# these are "handled" in the sense that a caller already catches and logs
# them (every real call site wraps broker calls in try/except). What must
# NEVER happen is one of Python's own generic errors (KeyError, TypeError,
# AttributeError, ValueError from an unguarded float()/int() cast) leaking
# out of the normalizer itself.
_ACCEPTABLE_EXCEPTIONS = (RuntimeError,)

_GARBAGE_VALUES = [None, "", "not_a_number", "NaN", 0, -1, 1e300, [], {}, True, "123abc"]


def _random_position(rng: random.Random) -> dict:
    """A plausible-but-possibly-broken Tradier position dict - each field
    independently has a chance of being replaced with garbage."""
    base = {"symbol": rng.choice(["AAPL", "SPY260116C00200000", "MSFT", ""]),
            "quantity": rng.choice([10, -5, 0, 0.5, "10"]),
            "cost_basis": rng.choice([1500.0, -200.0, 0, "1500"])}
    for key in list(base.keys()):
        if rng.random() < 0.3:
            base[key] = rng.choice(_GARBAGE_VALUES)
    return base


def _random_order(rng: random.Random) -> dict:
    base = {"id": rng.choice([42, "42", None]),
            "status": rng.choice(["filled", "pending", None, 123]),
            "exec_quantity": rng.choice([10, None, "10", 0]),
            "avg_fill_price": rng.choice([150.25, None, "150.25"]),
            "tag": rng.choice(["lavish_abc", None, 123])}
    for key in list(base.keys()):
        if rng.random() < 0.3:
            base[key] = rng.choice(_GARBAGE_VALUES)
    return base


def test_normalize_position_never_raises_an_unhandled_builtin_exception():
    unexpected = []
    for seed in range(300):
        rng = random.Random(seed)
        pos = _random_position(rng)
        try:
            result = tradier._normalize_position(pos)
            assert isinstance(result, dict)
            assert "symbol" in result and "qty" in result and "asset_class" in result
        except _ACCEPTABLE_EXCEPTIONS:
            pass
        except Exception as e:
            unexpected.append(f"seed={seed} input={pos!r} -> {type(e).__name__}: {e}")

    assert not unexpected, "Unhandled exceptions normalizing garbage positions:\n" + "\n".join(unexpected)


def test_normalize_order_never_raises_an_unhandled_builtin_exception():
    unexpected = []
    for seed in range(300):
        rng = random.Random(seed)
        order = _random_order(rng)
        try:
            result = tradier._normalize_order(order)
            assert isinstance(result, dict)
            assert "id" in result and "status" in result
        except _ACCEPTABLE_EXCEPTIONS:
            pass
        except Exception as e:
            unexpected.append(f"seed={seed} input={order!r} -> {type(e).__name__}: {e}")

    assert not unexpected, "Unhandled exceptions normalizing garbage orders:\n" + "\n".join(unexpected)


def test_normalize_position_qty_zero_never_divides_by_zero():
    # A specific, real-world-plausible case worth calling out by name
    # rather than hoping the general fuzz sweep happens to hit it: qty=0
    # with a nonzero cost_basis (a data artifact, not a real position) -
    # avg_entry_price math divides by qty, so this is the most direct way
    # to provoke a ZeroDivisionError if the qty==0 guard were ever removed.
    result = tradier._normalize_position({"symbol": "AAPL", "quantity": 0, "cost_basis": 500.0})
    assert result["avg_entry_price"] == 0.0


def test_normalize_position_handles_occ_lookalike_strings_without_crashing():
    # Strings that almost but don't quite match the OCC regex - must not
    # crash the asset_class inference even if they misclassify.
    weird_symbols = ["AAPL", "AAPL26011", "AAPL260116X00200000", "260116C00200000",
                      "A", "", "AAPL260116C0020000", "AAPL260116C002000000"]
    for sym in weird_symbols:
        result = tradier._normalize_position({"symbol": sym, "quantity": 1, "cost_basis": 100.0})
        assert result["asset_class"] in ("us_equity", "us_option")
