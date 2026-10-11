# lavish_core/trade/candle_gatekeeper.py
# Pre-entry confirmation gate. Today, a BUY/CALL/PUT alert fires a market/
# limit order the instant it's parsed - zero delay between "she posted a
# text" and "this bot bought it." That's maximum slippage exposure: by the
# time a signal provider types an alert, sends it, this bot's listener
# parses it, and hits the broker, price has often already moved. Worse,
# a noisy/false alert gets bought identically to a real breakout - there's
# no check that the market is actually confirming the move.
#
# This gate sits between "alert parsed" and "order placed": it captures
# the current candle's high/low the moment an alert fires, then watches
# subsequent 1-minute bars for a CLOSE beyond that candle's wick in the
# alert's direction (a real breakout, not just a tick through it) before
# letting the entry proceed - with volume as a secondary corroboration,
# not a hard requirement (missing volume data degrades gracefully rather
# than silently blocking every entry on a data-quality issue). If price
# instead closes beyond the OPPOSITE wick first, the alert is invalidated
# (the move already failed) - this is deliberately NOT the same check as
# trade_handler.py's old price-drift-since-alert gate, which rejected any
# large move; this gate's entire purpose is to WAIT FOR and REQUIRE a
# favorable move, so trade_handler.py skips the drift gate for anything
# routed through here (see its own comment at the call site).
#
# This is standard retail momentum-confirmation practice, not a novel
# invention - a breakout trader waiting for a candle close above
# resistance before entering, rather than buying the first tick through
# it, is textbook technical analysis.
from __future__ import annotations
import os
import time
import logging
from typing import Any, Dict, List, Optional

from lavish_core.trade.broker import get_bars

log = logging.getLogger("candle_gatekeeper")

ENABLED = os.getenv("GATEKEEPER_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

# "within a 15-minute window" - standard retail breakout-confirmation
# practice (see module docstring); a confirmation that takes longer than
# this means the move isn't materializing on the timescale this bot
# trades, not that it's still worth waiting for.
MAX_WAIT_SECONDS = float(os.getenv("GATEKEEPER_MAX_WAIT_SECONDS", "900"))
POLL_INTERVAL_SEC = float(os.getenv("GATEKEEPER_POLL_INTERVAL_SEC", "15"))
VOLUME_CONFIRM_MULT = float(os.getenv("GATEKEEPER_VOLUME_CONFIRM_MULT", "1.5"))
VOLUME_LOOKBACK_BARS = int(os.getenv("GATEKEEPER_VOLUME_LOOKBACK_BARS", "10"))

# How many consecutive "no bar data at all" polls to tolerate as transient
# before giving up and failing CLOSED (no confirmation) - missing one
# entry during a data outage is a far smaller cost than entering blind
# with no confirmation at all, which is the entire thing this gate exists
# to prevent.
MAX_CONSECUTIVE_NO_DATA = int(os.getenv("GATEKEEPER_MAX_CONSECUTIVE_NO_DATA", "4"))


def _rolling_avg_volume(bars: List[Dict[str, Any]], before_index: int) -> float:
    lookback = bars[max(0, before_index - VOLUME_LOOKBACK_BARS):before_index]
    vols = [b["v"] for b in lookback if b.get("v")]
    return (sum(vols) / len(vols)) if vols else 0.0


def confirm_entry(
    symbol: str,
    direction: str,  # "bullish" | "bearish"
    max_wait_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Blocking poll loop (meant to run on its own thread - see
    trade_handler.py's call sites, never called from an asyncio event
    loop directly). Returns:
      {"confirmed": True,  "reason": "wick_breakout_volume_confirmed" |
                                       "wick_breakout_volume_unavailable",
       "price": float, "bar": {...}}
      {"confirmed": False, "reason": "invalidated_wrong_direction" |
                                       "timeout" | "no_data" | "gatekeeper_disabled",
       "price": float|None, "bar": dict|None}

    "gatekeeper_disabled" is reported as confirmed=False with that
    explicit reason rather than silently returning confirmed=True, so a
    caller that forgets to check ENABLED itself still gets an honest
    signal - see trade_handler.py's own ENABLED check at the call site,
    which is what actually decides whether to call this at all.
    """
    direction = direction.lower().strip()
    if direction not in ("bullish", "bearish"):
        raise ValueError("direction must be 'bullish' or 'bearish'")

    if not ENABLED:
        return {"confirmed": False, "reason": "gatekeeper_disabled", "price": None, "bar": None}

    wait_budget = max_wait_seconds if max_wait_seconds is not None else MAX_WAIT_SECONDS
    deadline = time.time() + wait_budget

    no_data_streak = 0
    ref_bar: Optional[Dict[str, Any]] = None
    last_seen_t: Optional[str] = None

    log.info("Gatekeeper watching %s (%s) for up to %.0fs before confirming entry.",
              symbol, direction, wait_budget)

    while time.time() < deadline:
        bars = get_bars(symbol, limit=VOLUME_LOOKBACK_BARS + 5)
        if not bars:
            no_data_streak += 1
            if no_data_streak >= MAX_CONSECUTIVE_NO_DATA:
                log.warning("Gatekeeper: no bar data for %s after %d attempts - failing closed, no entry.",
                            symbol, no_data_streak)
                return {"confirmed": False, "reason": "no_data", "price": None, "bar": None}
            time.sleep(POLL_INTERVAL_SEC)
            continue
        no_data_streak = 0

        if ref_bar is None:
            # First successful fetch - the current/most recently closed
            # bar at the moment the alert started being watched is the
            # fixed reference candle (its high/low don't move after this).
            ref_bar = bars[-1]
            last_seen_t = ref_bar["t"]
            log.info("Gatekeeper: %s reference candle h=%.2f l=%.2f (t=%s)",
                      symbol, ref_bar["h"], ref_bar["l"], ref_bar["t"])
            time.sleep(POLL_INTERVAL_SEC)
            continue

        new_bars = [b for b in bars if b["t"] > last_seen_t]
        for i, bar in enumerate(bars):
            if bar["t"] not in {b["t"] for b in new_bars}:
                continue
            close = bar["c"]
            breakout = (direction == "bullish" and close > ref_bar["h"]) or \
                       (direction == "bearish" and close < ref_bar["l"])
            invalidated = (direction == "bullish" and close < ref_bar["l"]) or \
                          (direction == "bearish" and close > ref_bar["h"])

            if breakout:
                avg_vol = _rolling_avg_volume(bars, i)
                if avg_vol > 0 and bar.get("v", 0) >= VOLUME_CONFIRM_MULT * avg_vol:
                    log.info("Gatekeeper: %s CONFIRMED (wick breakout + volume %.0f >= %.1fx avg %.0f) @ %.2f",
                              symbol, bar["v"], VOLUME_CONFIRM_MULT, avg_vol, close)
                    return {"confirmed": True, "reason": "wick_breakout_volume_confirmed",
                            "price": close, "bar": bar}
                log.info("Gatekeeper: %s CONFIRMED (wick breakout, volume data unavailable/flat) @ %.2f",
                          symbol, close)
                return {"confirmed": True, "reason": "wick_breakout_volume_unavailable",
                        "price": close, "bar": bar}

            if invalidated:
                log.info("Gatekeeper: %s INVALIDATED - closed %.2f through the opposite wick before confirming.",
                          symbol, close)
                return {"confirmed": False, "reason": "invalidated_wrong_direction",
                        "price": close, "bar": bar}

        if new_bars:
            last_seen_t = max(b["t"] for b in new_bars)
        time.sleep(POLL_INTERVAL_SEC)

    log.warning("Gatekeeper: %s did not confirm within %.0fs - timing out, no entry.", symbol, wait_budget)
    return {"confirmed": False, "reason": "timeout", "price": None, "bar": None}
