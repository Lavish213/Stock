# lavish_core/trading/trade_handler.py
from __future__ import annotations
import os, json, time, threading, math
from lavish_core.utils.safety import finite_number, valid_positive
from datetime import date, datetime, timezone, timedelta
from typing import Dict, Any, Optional

from lavish_core.logger_setup import get_logger
from lavish_core.db.hybrid_store import HybridStore, DEFAULT_DB
from lavish_core.trade.trade_agent import place_trade, _dry_price  # dry-run fallback pricing only
from lavish_core.trade.broker import (
    get_account, get_order_by_client_id,
    latest_quote, get_order as broker_get_order, cancel_order as broker_cancel_order,
    get_position as broker_get_position, get_clock, resolve_and_price_contract, place_option_order,
    SUPPORTS_NATIVE_BRACKETS,
)
from lavish_core.trade.options_exit_monitor import watch_and_exit_async
from lavish_core.trade.equity_exit_monitor import watch_bracket_async, watch_and_exit_active_async
from lavish_core.trade.circuit_breaker import check_ok as circuit_breaker_check_ok, record_trade_outcome
from lavish_core.trade.reconcile import mark_watched, unmark_watched
from lavish_core.trade.portfolio_risk import check_correlation_ok
from lavish_core.trade.pdt_guard import check_pdt_ok
from lavish_core.trade.candle_gatekeeper import confirm_entry, ENABLED as GATEKEEPER_ENABLED
from lavish_core.utils.alerts import post_discord

log = get_logger("trade", log_dir="logs")

CONF_FLOOR = float(os.getenv("SIGNAL_CONFIDENCE_FLOOR", "0.55"))
TRADE_MODE = os.getenv("TRADE_MODE", "dry").lower()  # dry | paper | live

# Staged capital rollout: scales every computed position size uniformly, so
# going live can start at a fraction of intended size (0.1-0.25) and ramp up
# as real performance confirms the bot, without touching
# DEFAULT_TRADE_DOLLARS/DEFAULT_OPTION_TRADE_DOLLARS or amount_usd signals
# themselves. 1.0 = no scaling (default, unchanged behavior).
POSITION_SIZE_SCALE = float(os.getenv("POSITION_SIZE_SCALE", "1.0"))

# A real contract seen this session quoted bid $23 / ask $2,690 - a spread
# so wide the "mid price" isn't a fair fill on either side. Reject rather
# than submit a limit order into that. Expressed as spread / mid, not
# spread / bid, so a near-zero bid on a dead contract doesn't produce a
# nonsensical (or divide-by-near-zero) ratio.
OPTION_MAX_SPREAD_PCT = float(os.getenv("OPTION_MAX_SPREAD_PCT", "0.50"))

# Same fill-confirmation pattern trade_agent.place_trade uses for equity
# orders, applied to options entries too - previously an options order was
# submitted and the exit monitor started immediately after, with no check
# that the limit order (at mid, which can sit unfilled on a wide spread)
# actually filled first.
OPTIONS_FILL_POLL_ATTEMPTS = int(os.getenv("OPTIONS_FILL_POLL_ATTEMPTS", "5"))
OPTIONS_FILL_POLL_INTERVAL_SEC = float(os.getenv("OPTIONS_FILL_POLL_INTERVAL_SEC", "1.0"))

# Bracket protection for new long entries. Without this, a submitted order
# had no exit plan at all - a losing position just sat there indefinitely.
STOP_LOSS_PCT = float(os.getenv("DEFAULT_STOP_LOSS_PCT", "0.015"))
TAKE_PROFIT_PCT = float(os.getenv("DEFAULT_TAKE_PROFIT_PCT", "0.02"))

DEFAULT_OPTION_TRADE_DOLLARS = float(os.getenv("DEFAULT_OPTION_TRADE_DOLLARS", "200"))
OPTION_STRIKE_TOLERANCE = float(os.getenv("OPTION_STRIKE_TOLERANCE", "5"))

# She posts to Patreon and Discord independently, and it's not consistently
# one before the other - sometimes Patreon is first by a few minutes. Watch
# both, whichever fires first executes, and skip the same alert showing up
# again on the other source within this window.
SIGNAL_DEDUP_WINDOW_SECONDS = float(os.getenv("SIGNAL_DEDUP_WINDOW_SECONDS", "300"))

# Reject a trade if the underlying has already moved more than this much
# since the moment the alert was first seen - the reference price is
# captured once, as early as possible (execute_trade_from_post, before
# dedup/parsing/risk checks run), and compared against a fresh quote at
# the actual moment of execution. A late-processed alert (the bot was
# slow, or is catching up after downtime) shouldn't trade at a price
# meaningfully different from what she actually saw when she posted it -
# that's not "copying her call" anymore, it's a different trade that
# happens to share a ticker. 0 disables the check.
MAX_PRICE_DRIFT_SINCE_ALERT_PCT = float(os.getenv("MAX_PRICE_DRIFT_SINCE_ALERT_PCT", "0.03"))

# A new entry submitted while the market is closed doesn't fail - it sits
# queued at the broker and fills at the next open, at whatever price that
# turns out to be. Every risk gate that ran before it (price-drift-since-
# alert, sizing off a reference price, correlation/PDT checks) was computed
# against a stale last-close quote at signal time, not the price it will
# actually fill at - the exact gap the price-drift gate above exists to
# close, just reintroduced at the next open instead of right now. Alerts
# can genuinely arrive after hours (an overnight post), so this has to be
# an explicit gate, not an assumption. False disables it entirely - e.g.
# for a dry-mode-only deployment that never touches the real broker.
REQUIRE_MARKET_OPEN = os.getenv("REQUIRE_MARKET_OPEN", "true").strip().lower() in ("1", "true", "yes", "on")

# Same opt-out this codebase's other risk gates already use
# (circuit_breaker.py/pdt_guard.py/portfolio_risk.py) - kept as its own
# module-level flag rather than imported from one of them, since it's
# purely an env knob, not shared state.
RISK_CHECK_FAIL_OPEN = os.getenv("RISK_CHECK_FAIL_OPEN", "false").strip().lower() in ("1", "true", "yes", "on")

def _coerce_side(action: str) -> Optional[str]:
    a = (action or "").strip().lower()
    if a in ("buy", "long"): return "buy"
    if a in ("sell", "short"): return "sell"
    return None

def _kill_switch_reason() -> Optional[str]:
    """Checked first, before any other work, in both entry paths. Returns
    a reason string if new entries are currently halted, None otherwise.
    Remotely settable without a redeploy via scripts/kill_switch.py - see
    that script and HybridStore.set_kill_switch(). Exits are never
    affected, same philosophy as every other entry-only gate in this
    file - a kill switch that could trap you IN a position would be
    worse than no kill switch at all."""
    store = HybridStore(duckdb_path=str(DEFAULT_DB), redis_url=os.environ.get("REDIS_URL") or None)
    enabled, reason = store.get_kill_switch()
    if enabled:
        return f"kill_switch: {reason}" if reason else "kill_switch: no reason given"
    return None


def _market_closed_reason() -> Optional[str]:
    """Checked alongside the kill switch, before any other entry work -
    see REQUIRE_MARKET_OPEN above for why a new entry can't just be
    allowed to queue for the next open. Only meaningful in paper/live
    (dry mode never touches the real broker, so it never calls out for a
    clock). Exits are never affected, same philosophy as every other
    entry-only gate in this file. Fails CLOSED (blocks new entries) if the
    clock itself can't be read - same rule as every other risk check in
    this codebase; set RISK_CHECK_FAIL_OPEN=true to restore fail-open."""
    if not REQUIRE_MARKET_OPEN or TRADE_MODE not in ("paper", "live"):
        return None
    try:
        clk = get_clock()
    except Exception as e:
        if RISK_CHECK_FAIL_OPEN:
            log.warning("market_hours: could not read market clock (%s) - allowing trade "
                        "(RISK_CHECK_FAIL_OPEN=true), but this needs attention.", e)
            return None
        return f"market_hours: could not verify market is open ({e})"
    if not isinstance(clk,dict) or clk.get("is_open") is not True:
        return "market_hours: market is closed - refusing to queue a new entry for the next open"
    return None


def _price_drift_reason(alert_time_price: Optional[float], current_price: Optional[float]) -> Optional[str]:
    """Returns a rejection reason if the underlying has moved more than
    MAX_PRICE_DRIFT_SINCE_ALERT_PCT since the alert first arrived, or None
    if the move is within tolerance (or either price is unknown - this
    gate only fires when it can actually compare two real numbers)."""
    if MAX_PRICE_DRIFT_SINCE_ALERT_PCT <= 0:
        return None
    if not alert_time_price or not current_price or alert_time_price <= 0:
        return None
    if not valid_positive(alert_time_price) or not valid_positive(current_price):
        return "price_drift_unverifiable: invalid quote"
    drift = abs(current_price - alert_time_price) / alert_time_price
    if drift > MAX_PRICE_DRIFT_SINCE_ALERT_PCT:
        return (f"price_drift: underlying moved {drift:.1%} since the alert arrived "
                f"(${alert_time_price:.2f} -> ${current_price:.2f}), limit {MAX_PRICE_DRIFT_SINCE_ALERT_PCT:.1%}")
    return None

def _find_duplicate_signal(
    store: HybridStore, symbol: str, side: str, strike: Optional[float] = None, expiry: Optional[str] = None,
) -> Optional[str]:
    """Returns the source of an already-recorded matching signal within the dedup window, or None."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=SIGNAL_DEDUP_WINDOW_SECONDS)
    rows = store.fetchall(
        "SELECT source, payload FROM signals WHERE symbol=? AND side=? AND ts > ? ORDER BY ts DESC",
        (symbol.upper(), side.lower(), cutoff),
    )
    for src, payload_raw in rows:
        if strike is None and expiry is None:
            return src
        try:
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else (payload_raw or {})
        except Exception:
            payload = {}
        row_strike, row_expiry = payload.get("strike"), payload.get("expiry")
        if row_strike is not None and expiry == row_expiry and abs(float(row_strike) - float(strike)) < 0.01:
            return src
    return None

def execute_trade_from_post(signal: Dict[str, Any]) -> None:
    """
    Dispatches to the options or equity path based on the signal's shape.
    Dedupes against the *other* alert source first - see
    SIGNAL_DEDUP_WINDOW_SECONDS above.

    Options signal (from extract_signal.parse_alert/parse_text):
      { ticker, side('CALL'|'PUT'), strike, expiry('YYYY-MM-DD'),
        confidence, target_hint(underlying), stop_hint(underlying),
        amount_usd(optional), source, note }

    Equity signal (from patreon_trigger.py etc):
      { source, action('BUY'|'SELL'|etc), symbol, confidence(0..1),
        amount_usd(optional), note(optional) }
    """
    signal = dict(signal)
    try:
        confidence = finite_number(signal.get("confidence", 0))
        if not 0 <= confidence <= 1:
            return
        for key in ("amount_usd", "strike", "target_hint", "stop_hint"):
            if signal.get(key) is not None:
                finite_number(signal[key], positive=True)
    except ValueError:
        return
    if signal.get("field_conflicts"):
        return
    if str(signal.get("side", "")).upper() in ("CALL", "PUT") and not (signal.get("strike") and signal.get("expiry")):
        return
    is_option = bool(signal.get("strike") and signal.get("expiry")
                      and str(signal.get("side", "")).upper() in ("CALL", "PUT"))
    if is_option and str(signal.get("action", "BUY")).upper() not in ("BUY", "BTO", "BUY_TO_OPEN"):
        return  # options entry route cannot execute a close or short instruction
    symbol = str(signal.get("ticker") or signal.get("symbol") or "").upper().strip()
    dedup_side = str(signal.get("side") or signal.get("action") or "").lower().strip()
    source = signal.get("source", "unknown")

    # Captured here - the earliest point in the whole pipeline, before
    # dedup/parsing/risk checks run - so the price-drift-since-alert gate
    # (MAX_PRICE_DRIFT_SINCE_ALERT_PCT) compares against what the
    # underlying actually was when this alert first arrived, not a quote
    # fetched moments later after real processing time has passed.
    if symbol and "_alert_time_price" not in signal:
        try:
            signal["_alert_time_price"] = latest_quote(symbol)
        except Exception:
            signal["_alert_time_price"] = None
    if TRADE_MODE in ("paper", "live") and (is_option or _coerce_side(str(signal.get("action", ""))) == "buy"):
        if not valid_positive(signal.get("_alert_time_price")):
            log.warning("Skip entry: alert-time underlying quote unavailable")
            return

    store = None
    strike = expiry = None
    claim_key = None
    if symbol and dedup_side:
        store = HybridStore(duckdb_path=str(DEFAULT_DB), redis_url=os.environ.get("REDIS_URL") or None)
        strike = float(signal["strike"]) if is_option else None
        expiry = str(signal.get("expiry")) if is_option else None

        # Atomic first line of defense, on top of the fuzzy check right
        # below - confirmed directly (not assumed) that two threads can
        # both pass that SELECT-based duplicate check before either has
        # recorded anything, so the same real-world alert landing on
        # Patreon and Discord within milliseconds of each other could
        # otherwise both proceed to trade. A single INSERT ... ON CONFLICT
        # claim on the exact (symbol, side, strike, expiry) key closes that
        # gap; the fuzzy check still catches near-but-not-exactly-equal
        # duplicates (e.g. a strike that round-trips with tiny float drift)
        # that an exact key would miss.
        claim_key = f"{symbol}|{dedup_side}|{strike if strike is not None else ''}|{expiry or ''}"
        if not store.try_claim_signal(claim_key, SIGNAL_DEDUP_WINDOW_SECONDS):
            log.info("Skip %s %s: duplicate claim already held within %.0fs (source=%s)",
                      dedup_side.upper(), symbol, SIGNAL_DEDUP_WINDOW_SECONDS, source)
            return

        dup_source = _find_duplicate_signal(store, symbol, dedup_side, strike, expiry)
        if dup_source:
            log.info("Skip %s %s: duplicate of %s's alert within %.0fs (source=%s)",
                      dedup_side.upper(), symbol, dup_source, SIGNAL_DEDUP_WINDOW_SECONDS, source)
            store.release_claim(claim_key)
            return

    # Recorded AFTER dispatch, not before - a transient technical failure
    # (quote fetch, broker error) must not permanently occupy this alert's
    # dedup slot for SIGNAL_DEDUP_WINDOW_SECONDS. Only a signal that
    # reached a real, final outcome (traded, or deliberately skipped for a
    # business reason) is recorded; the other source gets a genuine chance
    # at a retry if this one failed for a technical reason.
    try:
        if is_option:
            handled = _execute_option_trade(signal)
        else:
            handled = _execute_equity_trade(signal)
    except Exception as e:
        log.error("Unhandled error executing %s %s: %s (not recording as seen - may be retried)",
                   dedup_side.upper() if dedup_side else "?", symbol, e)
        handled = False

    # Same "don't occupy the slot past a technical failure" rule the fuzzy
    # dedup record already follows (see above) - the atomic claim grabbed
    # above would otherwise outlive a transient error for the rest of
    # SIGNAL_DEDUP_WINDOW_SECONDS and block a legitimate retry.
    if store is not None and claim_key is not None and not handled:
        store.release_claim(claim_key)

    if store is not None and handled:
        store.log_signal(
            symbol=symbol, side=dedup_side, source=source,
            confidence=float(signal.get("confidence", 0) or 0),
            payload={"strike": strike, "expiry": expiry, "note": signal.get("note", "")},
        )

def _execute_option_trade(signal: Dict[str, Any]) -> bool:
    """
    Returns True once this signal has reached a final, deterministic
    outcome (traded, or deliberately skipped for a business reason) - the
    caller records it as seen for dedup purposes. Returns False for a
    retryable technical failure (quote/contract lookup, broker error) so a
    transient issue doesn't permanently block a legitimate retry from the
    other alert source within the dedup window.
    """
    kill_reason = _kill_switch_reason()
    if kill_reason:
        log.error("Skip options trade: %s - refusing all new entries.", kill_reason)
        return True

    closed_reason = _market_closed_reason()
    if closed_reason:
        log.error("Skip options trade: %s", closed_reason)
        return True

    ticker = str(signal.get("ticker") or signal.get("symbol") or "").upper().strip()
    option_side = str(signal.get("side", "")).upper().strip()
    strike = signal.get("strike")
    expiry_raw = signal.get("expiry")
    conf = float(signal.get("confidence", 0) or 0)
    target_hint = signal.get("target_hint")
    stop_hint = signal.get("stop_hint")
    amt = signal.get("amount_usd")
    note = signal.get("note", "")

    if not ticker or not strike or not expiry_raw:
        log.info("Skip options trade: missing ticker/strike/expiry in %s", signal)
        return True
    if conf < CONF_FLOOR:
        log.info("Skip options trade: confidence %.2f < floor %.2f (%s %s %s)",
                  conf, CONF_FLOOR, ticker, option_side, strike)
        return True

    try:
        expiry = expiry_raw if isinstance(expiry_raw, date) else datetime.fromisoformat(str(expiry_raw)).date()
    except Exception as e:
        log.warning("Skip options trade: unparseable expiry %r: %s", expiry_raw, e)
        return True

    if expiry < date.today() or option_side not in ("CALL", "PUT") or not valid_positive(strike):
        return True

    # Candle-confirmation gate - this bot only ever opens long options
    # (buy_to_open, never a new short), so every options signal that
    # reaches here is a new entry and gating applies to all of them,
    # unlike the equity path where a SELL is exempt. Same reasoning as
    # _execute_equity_trade's gate on why this can't block synchronously
    # here (handle_alert_text() runs directly inside the Discord/Patreon
    # asyncio event loops) - hands off to a background thread and returns.
    if GATEKEEPER_ENABLED and TRADE_MODE in ("paper", "live"):
        direction = "bullish" if option_side == "CALL" else "bearish"
        log.info("Gatekeeper: queuing %s %s for candle confirmation before placing an order.", ticker, option_side)
        threading.Thread(
            target=_gated_option_buy,
            args=(signal, ticker, option_side, direction, strike, expiry, conf, target_hint, stop_hint, amt, note),
            daemon=True,
        ).start()
        return True

    return _finish_option_entry(signal, ticker, option_side, strike, expiry, conf,
                                 target_hint, stop_hint, amt, note, skip_drift_check=False)


def _gated_option_buy(
    signal: Dict[str, Any], ticker: str, option_side: str, direction: str, strike: Any, expiry: date,
    conf: float, target_hint: Optional[float], stop_hint: Optional[float], amt: Optional[float], note: str,
) -> None:
    """Background-thread target for a gated options entry - see
    _execute_option_trade's comment on why this can't run inline. Same
    crash-resilience rule as every other daemon-thread target in this
    codebase: must never let an uncaught exception die silently."""
    try:
        result = confirm_entry(ticker, direction)
        if not result.get("confirmed"):
            log.info("Gatekeeper did not confirm %s %s $%.2f exp %s (%s) - no order placed.",
                      ticker, option_side, float(strike), expiry, result.get("reason"))
            return
        _finish_option_entry(signal, ticker, option_side, strike, expiry, conf,
                              target_hint, stop_hint, amt, note, skip_drift_check=True)
    except Exception as e:
        log.error("Gated options entry for %s %s %s hit an unexpected error: %s", ticker, option_side, strike, e)
        post_discord(f"🚨 Gated {option_side} entry for {ticker} ${strike} failed unexpectedly ({e}) - "
                      f"no order was placed, needs a look.")


def _finish_option_entry(
    signal: Dict[str, Any], ticker: str, option_side: str, strike: Any, expiry: date, conf: float,
    target_hint: Optional[float], stop_hint: Optional[float], amt: Optional[float], note: str,
    skip_drift_check: bool,
) -> bool:
    """
    Everything from contract resolution through order placement and exit-
    monitor start - shared by the synchronous (gatekeeper disabled) and
    gated-async (confirmed entry) paths. Contract resolution/pricing is
    deliberately done HERE, not before gating, so a gated entry prices
    and sizes off a contract quote taken right before it actually trades,
    not one that's been stale for up to 15 minutes.
    """
    if _kill_switch_reason() or _market_closed_reason() or expiry < date.today():
        return True
    if TRADE_MODE in ("paper","live"):
        from importlib import import_module
        from lavish_core.trade.broker import BROKER_PROVIDER
        from lavish_core.utils.safety import check_trading_endpoint
        provider = import_module("lavish_core.trade.broker_"+BROKER_PROVIDER)
        check_trading_endpoint(TRADE_MODE,provider.BASE_URL,BROKER_PROVIDER)
    contract = resolve_and_price_contract(
        ticker, expiry, option_side, float(strike), strike_tolerance=OPTION_STRIKE_TOLERANCE,
    )
    if not contract:
        log.warning("Skip options trade: no listed contract found near %s %s $%.2f exp %s "
                     "(may be transient - not recording as seen).",
                     ticker, option_side, float(strike), expiry)
        return False

    mid_price = contract.get("mid_price")
    if not valid_positive(mid_price):
        log.warning("Skip options trade: no usable quote for %s (may be transient - not recording as seen).",
                    contract.get("symbol"))
        return False

    # Price-drift-since-alert, against the underlying (what she actually
    # called), not the option premium itself (which moves on its own
    # schedule - gamma/theta - independent of pure underlying drift).
    # Skipped for anything that went through the candle gate above - same
    # reasoning as _finish_equity_entry's identical skip: that gate's
    # entire purpose is to WAIT FOR and REQUIRE a favorable move, which a
    # flat max-drift check would often reject right after confirming.
    if TRADE_MODE in ("paper", "live"):
        try:
            underlying_now = latest_quote(ticker)
        except Exception:
            underlying_now = None
        if not valid_positive(underlying_now):
            log.warning("Skip options entry: current underlying quote unavailable")
            return False
        drift_reason = None if skip_drift_check else _price_drift_reason(signal.get("_alert_time_price"), underlying_now)
        if drift_reason:
            store = HybridStore(duckdb_path=str(DEFAULT_DB), redis_url=os.environ.get("REDIS_URL") or None)
            store.submit_order(
                symbol=contract["symbol"], side="buy", qty=0, order_type="limit",
                limit_price=mid_price, tif="day", venue=TRADE_MODE, status="rejected",
                meta={"reason": drift_reason, "source": signal.get("source", "discord"), "note": note},
            )
            log.warning("Skip options trade: %s", drift_reason)
            return True

    # Thin/illiquid contracts can quote a "mid" that isn't actually
    # tradeable - a real example seen this session was a contract with
    # bid $23 / ask $2,690 ("mid" $1,356.50, nowhere near a fair fill on
    # either side). A limit order at that mid either doesn't fill at all
    # or, worse, does fill and burns most of the position's value on the
    # spread alone. Reject outright rather than submit into that.
    bid, ask = contract.get("bid"), contract.get("ask")
    if TRADE_MODE in ("paper", "live") and (bid is None or ask is None):
        return False  # a midpoint alone cannot establish a tradable two-sided market
    if bid is not None or ask is not None:
        if not valid_positive(bid) or not valid_positive(ask) or ask < bid:
            return True
    if bid is not None and ask is not None and bid > 0:
        spread_pct = (ask - bid) / mid_price
        if spread_pct > OPTION_MAX_SPREAD_PCT:
            log.warning("Skip options trade: %s spread too wide (bid=%.2f ask=%.2f mid=%.2f, %.0f%% > %.0f%% max)",
                        contract["symbol"], bid, ask, mid_price, spread_pct * 100, OPTION_MAX_SPREAD_PCT * 100)
            return True

    dollars = (float(amt) if amt is not None else DEFAULT_OPTION_TRADE_DOLLARS) * POSITION_SIZE_SCALE
    if not valid_positive(dollars):
        return True
    qty = int(dollars // (mid_price * 100))
    if qty < 1:
        # max(1, ...) used to force a purchase here regardless of cost -
        # a contract priced above the configured budget still bought 1,
        # no matter how far over. The budget is supposed to be a cap.
        log.warning("Skip options trade: %s costs $%.2f/contract, budget is only $%.2f - can't afford 1 contract.",
                    contract["symbol"], mid_price * 100, dollars)
        return True

    log.info("🔔 options signal → %s %s $%.2f exp %s (contract=%s qty=%s mid=%.2f mode=%s target=%s stop=%s)",
              option_side, ticker, float(strike), expiry, contract["symbol"], qty, mid_price,
              TRADE_MODE, target_hint, stop_hint)

    if TRADE_MODE not in ("paper", "live"):
        log.info("dry mode: would BUY %s x%s @ ~%.2f (no order submitted)", contract["symbol"], qty, mid_price)
        return True

    store = HybridStore(duckdb_path=str(DEFAULT_DB), redis_url=os.environ.get("REDIS_URL") or None)

    cb_ok, cb_reason = circuit_breaker_check_ok(store)
    if not cb_ok:
        store.submit_order(
            symbol=contract["symbol"], side="buy", qty=qty, order_type="limit",
            limit_price=mid_price, tif="day", venue=TRADE_MODE, status="rejected",
            meta={"reason": f"circuit_breaker: {cb_reason}", "source": signal.get("source", "discord"), "note": note},
        )
        log.error("Skip options trade: circuit_breaker: %s", cb_reason)
        return True

    corr_ok, corr_reason = check_correlation_ok(ticker)
    if not corr_ok:
        store.submit_order(
            symbol=contract["symbol"], side="buy", qty=qty, order_type="limit",
            limit_price=mid_price, tif="day", venue=TRADE_MODE, status="rejected",
            meta={"reason": corr_reason, "source": signal.get("source", "discord"), "note": note},
        )
        log.error("Skip options trade: %s", corr_reason)
        return True

    pdt_ok, pdt_reason = check_pdt_ok(store)
    if not pdt_ok:
        store.submit_order(
            symbol=contract["symbol"], side="buy", qty=qty, order_type="limit",
            limit_price=mid_price, tif="day", venue=TRADE_MODE, status="rejected",
            meta={"reason": pdt_reason, "source": signal.get("source", "discord"), "note": note},
        )
        log.error("Skip options trade: %s", pdt_reason)
        return True

    from lavish_core.trade.trade_agent import load_risk_limits, sha1_str
    client_id = "option_" + sha1_str(f"{contract['symbol']}|buy|{qty}|{int(time.time()//60)}")[:24]
    try:
        account=get_account()
        equity=finite_number(account.get("equity"),positive=True)
    except Exception:
        return False
    limits=load_risk_limits(store)
    if TRADE_MODE == "live":
        from lavish_core.trade.live_readiness import approved_entry_limits
        approved=approved_entry_limits(store,qty*mid_price*100,equity,account.get("id") or account.get("account_number"))
        limits.max_gross_exposure=min(limits.max_gross_exposure,approved["max_gross_exposure"])
    reservation=store.reserve_entry(client_id,contract["symbol"],qty,mid_price,limits,equity,multiplier=100)
    if reservation:
        return True
    oid = store.submit_order(contract["symbol"], "buy", qty, order_type="limit", limit_price=mid_price, venue=TRADE_MODE, status="pending_submit", client_id=client_id, meta={"source":signal.get("source"), "target_underlying":target_hint, "stop_underlying":stop_hint})
    if _kill_switch_reason() or _market_closed_reason():
        store.set_order_status(oid, "rejected")
        store.release_entry(client_id)
        return True
    try:
        order = place_option_order(contract_symbol=contract["symbol"], side="buy", qty=qty, order_type="limit", limit_price=mid_price, client_order_id=client_id)
    except Exception:
        try:
            order = get_order_by_client_id(client_id)
        except Exception:
            store.set_order_status(oid,"submission_unknown")
            return True
        if order is None and BROKER_PROVIDER == "tradier":
            store.set_order_status(oid,"submission_unknown")
            return True
        if order is None:
            store.set_order_status(oid,"rejected")
            store.release_entry(client_id)
            return False
        if not order:
            store.set_order_status(oid,"submission_unknown")
            return True

    if not isinstance(order,dict) or not order.get("id"):
        store.set_order_status(oid,"submission_unknown")
        return True
    log.info("Options order result: %s", order)

    # Confirm the entry actually filled before starting exit monitoring -
    # a limit order resting at mid on a wide options spread can sit open
    # for a while (or never fill). Mirrors trade_agent.place_trade's
    # equity fill-poll; without this, the exit monitor previously started
    # tracking a position that might not exist yet.
    broker_oid = order.get("id")
    final_status = order.get("status", "submitted")
    filled_qty = order.get("filled_qty")
    filled_avg_price = order.get("filled_avg_price")
    if broker_oid and TRADE_MODE in ("paper", "live"):
        for _ in range(OPTIONS_FILL_POLL_ATTEMPTS):
            if final_status in ("filled", "canceled", "expired", "rejected"):
                break
            time.sleep(OPTIONS_FILL_POLL_INTERVAL_SEC)
            try:
                polled = broker_get_order(broker_oid)
                final_status = polled.get("status", final_status)
                filled_qty = polled.get("filled_qty", filled_qty)
                filled_avg_price = polled.get("filled_avg_price", filled_avg_price)
            except Exception as e:
                log.warning("Options order status poll failed for %s: %s", broker_oid, e)
                break

    store.set_order_status(oid, final_status)
    store.execute("UPDATE orders SET meta=? WHERE id=?", (json.dumps({"broker_id":broker_oid,"raw":order,"source":signal.get("source","discord"),"ticker":ticker,"option_side":option_side,"strike":float(strike),"expiry":expiry.isoformat(),"expected_price":mid_price,"target_underlying":target_hint,"stop_underlying":stop_hint}),oid))
    if not broker_oid:
        store.set_order_status(oid,"submission_unknown")
        return True

    if final_status not in ("filled", "partially_filled"):
        log.warning("Options order for %s never confirmed filled after %d polls (status=%s) - canceling, no exit monitor started.",
                    contract["symbol"], OPTIONS_FILL_POLL_ATTEMPTS, final_status)
        if broker_oid and TRADE_MODE in ("paper", "live"):
            try:
                broker_cancel_order(broker_oid)
            except Exception as e:
                log.warning("Cancel of unfilled options order %s failed: %s", broker_oid, e)
        if final_status in ("canceled", "expired", "rejected"):
            store.release_entry(client_id)
        return True

    if final_status == "partially_filled" and broker_oid and TRADE_MODE in ("paper", "live"):
        # Real gap found and fixed: the poll loop above only waits up to
        # OPTIONS_FILL_POLL_ATTEMPTS x OPTIONS_FILL_POLL_INTERVAL_SEC
        # (~5s default) for the rest to fill too. Leaving the unfilled
        # remainder resting at the broker uncanceled meant it could fill
        # hours later, unpredictably, against market conditions none of
        # this alert's own checks (confidence floor, kill switch, price-
        # drift gate, risk gates) were ever re-validated against - and
        # until the next reconcile sweep happened to catch it, that extra
        # fill would sit with no exit monitor watching it at all. Cancel
        # the remainder; the confirmed partial fill below is final.
        try:
            broker_cancel_order(broker_oid)
            log.info("Canceled unfilled remainder of partially-filled options order for %s.", contract["symbol"])
        except Exception as e:
            log.warning("Cancel of partially-filled options order remainder %s failed: %s", contract["symbol"], e)

    # Use the real fill price (slippage vs. the mid_price we sized/quoted
    # against) for both the exit-monitor's entry baseline and P&L, not the
    # pre-fill estimate.
    try:
        entry_price = finite_number(filled_avg_price, positive=True)
        actual_qty = finite_number(filled_qty, positive=True)
        if not actual_qty.is_integer() or actual_qty > qty:
            raise ValueError("Invalid confirmed quantity")
        filled_qty_n=int(actual_qty)
        entry_fee=finite_number(order.get("filled_fee",0),nonnegative=True)
    except ValueError:
        store.set_order_status(oid, "fill_unverified")
        return True
    slippage = round(entry_price - mid_price, 4)
    log.info("Options entry filled: %s qty=%s entry=%.2f (quoted mid=%.2f, slippage=%.4f)",
              contract["symbol"], filled_qty_n, entry_price, mid_price, slippage)
    store.log_fill(
        order_id=oid, symbol=contract["symbol"], side="buy",
        qty=filled_qty_n, price=entry_price, fee=entry_fee, venue=TRADE_MODE,
        event_id=f"option-entry:{broker_oid}",
        reservation_client_id=client_id if final_status == "filled" else None,
    )

    if target_hint is None and stop_hint is None:
        log.info("No target/stop given for %s - relying on our own guardrails (stop/trail/expiry) for the exit.",
                  contract["symbol"])

    def _on_exit(result: Dict[str, Any]) -> None:
        # Runs in the monitor's background thread - use a fresh store
        # connection rather than sharing one across threads.
        unmark_watched(contract["symbol"])
        exit_store = HybridStore(duckdb_path=str(DEFAULT_DB), redis_url=os.environ.get("REDIS_URL") or None)
        exit_price = result.get("option_price")
        if result.get("status") == "exited" and exit_price is not None:
            pnl = (float(exit_price) - entry_price) * 100 * filled_qty_n
            outcome_id = (result.get("order") or {}).get("id",oid)
            applied = (True if exit_store.try_claim_outcome(f"outcome:{outcome_id}") else None) if result.get("ledger_recorded") else exit_store.log_fill(
                order_id=oid, symbol=contract["symbol"], side="sell",
                qty=filled_qty_n, price=float(exit_price), fee=0.0, venue=TRADE_MODE,
                event_id=f"option-exit:{oid}",
            )
            if applied is not None:
                record_trade_outcome(exit_store, pnl)
            log.info("Closed %s: entry=%.2f exit=%.2f realized_pnl=%.2f (%s)",
                      contract["symbol"], entry_price, exit_price, pnl, result.get("reason"))
        else:
            log.warning("Exit monitor for %s ended without a clean fill: %s", contract["symbol"], result)

    mark_watched(contract["symbol"])
    watch_and_exit_async(
        underlying_symbol=ticker,
        contract_symbol=contract["symbol"],
        qty=filled_qty_n,
        option_side=option_side,
        entry_price=entry_price,
        expiry=expiry,
        target_underlying=float(target_hint) if target_hint is not None else None,
        stop_underlying=float(stop_hint) if stop_hint is not None else None,
        on_exit=_on_exit,
    )
    return True

def _execute_equity_trade(signal: Dict[str, Any]) -> bool:
    """
    Expected signal fields:
      { source, action('BUY'|'SELL'|etc), symbol, confidence(0..1),
        amount_usd(optional), note(optional) }

    Returns True once this signal has reached a final, deterministic
    outcome (traded, or deliberately skipped for a business reason) - the
    caller records it as seen for dedup purposes. Returns False for a
    retryable technical failure (quote lookup, position check, broker
    error) so a transient issue doesn't permanently block a legitimate
    retry from the other alert source within the dedup window.
    """
    sym   = str(signal.get("symbol", "")).upper().strip()
    side  = _coerce_side(str(signal.get("action", "")))
    conf  = float(signal.get("confidence", 0) or 0)
    note  = signal.get("note", "")
    amt   = signal.get("amount_usd")  # may be None
    target_hint = signal.get("target_hint")
    stop_hint = signal.get("stop_hint")

    if not sym or not side:
        log.info("Skip trade: missing symbol/side in %s", signal)
        return True
    if conf < CONF_FLOOR:
        log.info("Skip trade: confidence %.2f < floor %.2f (%s %s)", conf, CONF_FLOOR, side, sym)
        return True

    # Only new entries (buy) are halted - a sell is reducing/closing
    # exposure, same reasoning as every other entry-only gate below. A
    # kill switch that could trap you IN a position would be worse than
    # no kill switch at all.
    if side == "buy":
        kill_reason = _kill_switch_reason()
        if kill_reason:
            log.error("Skip trade: %s - refusing all new entries.", kill_reason)
            return True

        closed_reason = _market_closed_reason()
        if closed_reason:
            log.error("Skip trade: %s", closed_reason)
            return True

    # Open HybridStore (DuckDB/Redis) for audit + risk gates
    store = HybridStore(duckdb_path=str(DEFAULT_DB), redis_url=os.environ.get("REDIS_URL") or None)

    # Fast, synchronous quote-availability check - stays synchronous (and
    # keeps its "return False to allow retry" contract) regardless of
    # whether this entry ends up gated below: there's no point watching
    # candles for a symbol with no real quote at all right now. A gated
    # BUY still re-fetches a FRESH quote after confirmation (see
    # _finish_equity_entry) rather than reusing this one - price may have
    # moved meaningfully during the wait, same reason sizing/bracket
    # levels are computed fresh there too.
    ref_price = None
    try:
        ref_price = latest_quote(sym)
    except Exception as e:
        log.warning("latest_quote failed for %s: %s", sym, e)

    if not isinstance(ref_price, (int, float)) or isinstance(ref_price, bool) or not valid_positive(ref_price):
        if TRADE_MODE in ("paper", "live"):
            store.submit_order(
                symbol=sym, side=side, qty=0, order_type="market", tif="day", venue=TRADE_MODE,
                status="rejected",
                meta={"reason": "no_real_quote_available", "source": signal.get("source", "patreon"), "note": note},
            )
            log.error("Skip trade: no real quote available for %s - refusing to size a real order off a fake price "
                      "(not recording as seen - may be retried).", sym)
            return False
        ref_price = _dry_price(sym)  # dry mode only - no real order will be submitted

    # Candle-confirmation gate: only new long entries get delayed - a sell
    # is reducing/closing exposure (never halted or slowed, same reasoning
    # as every other entry-only gate below). Dry mode is excluded too -
    # there's no slippage/real money at stake to protect there, so
    # delaying the "would trade" log by up to 15 minutes would only hurt
    # testing/demoing for no benefit.
    #
    # This can't block synchronously here: handle_alert_text() is called
    # directly (no thread, no await) from inside the Discord listener's
    # and Patreon poller's own asyncio event loops - a multi-minute wait
    # in this call stack would freeze BOTH entirely (no heartbeats, no
    # other alerts processed) for the duration. Confirmation + the actual
    # order placement run on a background thread instead, mirroring the
    # exit monitors' already-proven async pattern - this function hands
    # off and returns immediately, same as those do for exits.
    if side == "buy" and GATEKEEPER_ENABLED and TRADE_MODE in ("paper", "live"):
        log.info("Gatekeeper: queuing %s BUY for candle confirmation before placing an order.", sym)
        threading.Thread(
            target=_gated_equity_buy,
            args=(signal, sym, conf, note, amt, target_hint, stop_hint),
            daemon=True,
        ).start()
        return True

    return _finish_equity_entry(signal, store, sym, side, conf, note, amt, target_hint, stop_hint,
                                 ref_price, skip_drift_check=False)


def _gated_equity_buy(
    signal: Dict[str, Any], sym: str, conf: float, note: str, amt: Optional[float],
    target_hint: Optional[float], stop_hint: Optional[float],
) -> None:
    """
    Background-thread target for a gated BUY - see _execute_equity_trade's
    comment on why this can't run inline. Nothing upstream of this is
    watching for an exception here (it's a daemon thread kicked off and
    forgotten), so - same rule as every exit-monitor loop in this
    codebase - this must never let an uncaught exception die silently.
    """
    try:
        result = confirm_entry(sym, "bullish")
        store = HybridStore(duckdb_path=str(DEFAULT_DB), redis_url=os.environ.get("REDIS_URL") or None)

        if not result.get("confirmed"):
            store.submit_order(
                symbol=sym, side="buy", qty=0, order_type="market", tif="day", venue=TRADE_MODE,
                status="rejected",
                meta={"reason": f"gatekeeper_{result.get('reason')}",
                      "source": signal.get("source", "patreon"), "note": note},
            )
            log.info("Gatekeeper did not confirm %s BUY (%s) - no order placed.", sym, result.get("reason"))
            return

        # Fresh quote, not the pre-gate one - price has had up to
        # GATEKEEPER_MAX_WAIT_SECONDS to move since that was fetched.
        # Falls back to the confirming bar's own close price if a live
        # quote isn't available right at this instant, rather than
        # treating a momentary quote-fetch hiccup immediately after a
        # confirmed breakout as "no data" and losing the entry.
        fresh_price = None
        try:
            fresh_price = latest_quote(sym)
        except Exception as e:
            log.warning("Post-confirmation latest_quote failed for %s: %s", sym, e)
        if not fresh_price or fresh_price <= 0:
            fresh_price = result.get("price")
        if not fresh_price or fresh_price <= 0:
            store.submit_order(
                symbol=sym, side="buy", qty=0, order_type="market", tif="day", venue=TRADE_MODE,
                status="rejected",
                meta={"reason": "no_real_quote_available_post_confirmation",
                      "source": signal.get("source", "patreon"), "note": note},
            )
            log.error("Gatekeeper confirmed %s but no usable price afterward - refusing to size off nothing.", sym)
            return

        _finish_equity_entry(signal, store, sym, "buy", conf, note, amt, target_hint, stop_hint,
                              fresh_price, skip_drift_check=True)
    except Exception as e:
        log.error("Gated equity BUY for %s hit an unexpected error: %s", sym, e)
        post_discord(f"🚨 Gated BUY for {sym} failed unexpectedly ({e}) - no order was placed, needs a look.")


def _finish_equity_entry(
    signal: Dict[str, Any], store: HybridStore, sym: str, side: str, conf: float, note: str,
    amt: Optional[float], target_hint: Optional[float], stop_hint: Optional[float],
    ref_price: float, skip_drift_check: bool,
) -> bool:
    """
    Everything from the price-drift gate through order placement and exit-
    monitor start - shared by the synchronous (gatekeeper disabled, or a
    SELL) and gated-async (confirmed BUY) paths, which differ only in
    whether the drift gate still applies and where ref_price came from.
    """
    # Price-drift-since-alert: only gates new entries (a sell is reducing/
    # closing exposure, same reasoning as every other entry-only gate
    # below) - getting out of a position should never be blocked by how
    # much the underlying has moved. Also skipped for anything that went
    # through the candle gate above: that gate's entire purpose is to WAIT
    # FOR and REQUIRE a favorable price move before confirming - re-
    # applying a flat max-drift-since-alert check afterward would often
    # reject exactly the breakouts the gate just confirmed. The gate is a
    # stricter, better-informed replacement for this check on anything
    # routed through it, not an additional hurdle on top of it.
    if side == "buy" and (_kill_switch_reason() or _market_closed_reason()):
        return True
    if not valid_positive(ref_price):
        return False
    if side == "buy" and not skip_drift_check and TRADE_MODE in ("paper", "live"):
        drift_reason = _price_drift_reason(signal.get("_alert_time_price"), ref_price)
        if drift_reason:
            store.submit_order(
                symbol=sym, side=side, qty=0, order_type="market", tif="day", venue=TRADE_MODE,
                status="rejected", meta={"reason": drift_reason, "source": signal.get("source", "patreon"), "note": note},
            )
            log.warning("Skip trade: %s", drift_reason)
            return True

    # Entry budgets and scaling never prevent closing a verified holding.
    # An explicit sale amount still limits a partial exit; an unsized SELL
    # closes the remaining long position.
    qty = None
    if side == "buy" or amt is not None:
        dollars = (float(amt) if amt is not None else float(os.getenv("DEFAULT_TRADE_DOLLARS", "500")))
        if side == "buy":
            dollars *= POSITION_SIZE_SCALE
        if not valid_positive(dollars):
            return True
        sizing_price=ref_price
        if TRADE_MODE == "live" and side == "buy":
            from lavish_core.trade.trade_agent import MAX_SLIPPAGE_PCT
            sizing_price=ref_price*(1+MAX_SLIPPAGE_PCT)
        qty=float(math.floor(dollars/sizing_price))
        if qty < 1:
            return True

    meta = {"source": signal.get("source", "patreon"), "confidence": conf, "note": note}
    if side == "sell":
        try:
            broker_pos = broker_get_position(sym) if TRADE_MODE in ("paper", "live") else store.get_position(sym)
            held_qty = finite_number(broker_pos.get("qty",0),nonnegative=True) if broker_pos else 0.0
        except Exception as e:
            store.submit_order(symbol=sym,side=side,qty=0,venue=TRADE_MODE,status="rejected",meta={"reason":"position_check_failed",**meta})
            log.error("Skip SELL %s: held position could not be verified (%s)",sym,type(e).__name__)
            return False
        if held_qty <= 0:
            store.submit_order(symbol=sym,side=side,qty=0,venue=TRADE_MODE,status="rejected",meta={"reason":"reduce_only_no_position_held",**meta})
            return True
        qty = held_qty if qty is None else min(qty,held_qty)

    # Only bracket new long entries. A "sell" here is closing/shorting, not
    # opening a position, so there's nothing to attach a bracket exit to.
    # Prefer her stated target/stop when she gave one (same principle as the
    # options path) - only fall back to our fixed pct bracket for whichever
    # side she didn't specify, or if her number fails a basic sanity check
    # (a target below current price or a stop above it is a parsing miss,
    # not a real level - trust the safe default instead of that number).
    take_profit = stop_loss = None
    if side == "buy" and ref_price > 0:
        take_profit = round(ref_price * (1 + TAKE_PROFIT_PCT), 2)
        stop_loss = round(ref_price * (1 - STOP_LOSS_PCT), 2)
        if target_hint is not None:
            t = float(target_hint)
            if t > ref_price:
                take_profit = round(t, 2)
            else:
                log.warning("Ignoring target_hint %.2f for %s (not above ref %.2f) - using default TP",
                            t, sym, ref_price)
        if stop_hint is not None:
            s = float(stop_hint)
            if s < ref_price:
                stop_loss = round(s, 2)
            else:
                log.warning("Ignoring stop_hint %.2f for %s (not below ref %.2f) - using default SL",
                            s, sym, ref_price)

    # Same concentration check as the options path - only gates new long
    # entries, same reasoning as the bracket above (a sell is reducing/
    # closing exposure, not adding to it).
    if side == "buy" and TRADE_MODE in ("paper", "live"):
        corr_ok, corr_reason = check_correlation_ok(sym)
        if not corr_ok:
            store.submit_order(
                symbol=sym, side=side, qty=qty, order_type="market",
                tif="day", venue=TRADE_MODE, status="rejected",
                meta={"reason": corr_reason, **meta},
            )
            log.error("Skip trade: %s", corr_reason)
            return True

        pdt_ok, pdt_reason = check_pdt_ok(store)
        if not pdt_ok:
            store.submit_order(
                symbol=sym, side=side, qty=qty, order_type="market",
                tif="day", venue=TRADE_MODE, status="rejected",
                meta={"reason": pdt_reason, **meta},
            )
            log.error("Skip trade: %s", pdt_reason)
            return True

    log.info("🔔 signal → %s %s (qty=%.0f, conf=%.2f, mode=%s, ref=%.2f, tp=%s, sl=%s, her_target=%s, her_stop=%s)",
             side.upper(), sym, qty, conf, TRADE_MODE, ref_price, take_profit, stop_loss, target_hint, stop_hint)

    if side == "buy" and (_kill_switch_reason() or _market_closed_reason()):
        return True
    out = place_trade(
        store=store,
        symbol=sym,
        side=side,
        qty=qty,
        mode=TRADE_MODE,
        order_type="market",
        limit_price=None,
        tif="day",
        client_id=None,
        meta=meta,
        take_profit=take_profit,
        stop_loss=stop_loss,
    )
    log.info("Trade result: %s", out)

    # An exit plan (take_profit/stop_loss) only means something if
    # something watches for it and acts - without this, the entry's DB
    # order just sits "filled" forever with no linked exit, and
    # track_record.py has no way to know the position closed.
    #
    # Alpaca manages a bracket's legs server-side (whichever fills first
    # auto-cancels the other) - watch_bracket_async just watches those
    # legs and logs the result. Tradier (and any other broker without a
    # native bracket) returns no legs at all, so there's nothing to watch
    # passively - watch_and_exit_active_async actively polls price and
    # submits its own closing order instead. SUPPORTS_NATIVE_BRACKETS
    # picks which one matches the broker actually configured, rather than
    # branching on whether legs happened to come back (which would
    # silently no-op under Tradier instead of starting the real exit plan).
    if side == "buy" and out.get("status") == "filled" and (take_profit is not None or stop_loss is not None):
        entry_price = float(out.get("filled_avg_price") or ref_price)
        legs = out.get("legs") or []
        if SUPPORTS_NATIVE_BRACKETS:
            leg_ids = [leg.get("id") for leg in legs if leg.get("id")]
            if leg_ids:
                watch_bracket_async(
                    order_id=out["order_id"], symbol=sym, qty=qty,
                    entry_price=entry_price, leg_ids=leg_ids, venue=TRADE_MODE,
                )
            else:
                log.warning("No bracket legs came back for %s despite take_profit/stop_loss being set - "
                            "position has no automated exit watch.", sym)
        else:
            watch_and_exit_active_async(
                symbol=sym, qty=qty, entry_price=entry_price,
                take_profit=take_profit, stop_loss=stop_loss, venue=TRADE_MODE,
            )

    # A broker-side failure inside place_trade (unreachable, request error)
    # is the same kind of retryable technical failure as the checks above -
    # don't record this signal as seen just because the attempt happened.
    reason = str(out.get("reason", ""))
    if out.get("status") == "rejected" and (reason.startswith("broker_error") or reason == "broker_unavailable"):
        return False
    return True

def main():
    log.info("Trade handler ready (mode=%s, floor=%.2f).", TRADE_MODE, )
