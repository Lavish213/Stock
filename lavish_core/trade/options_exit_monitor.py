# lavish_core/trade/options_exit_monitor.py
# Alpaca doesn't support bracket/OCO orders on options positions, so any
# exit plan - hers or ours - only means something if this actively watches
# for it and submits a closing order when a level is hit.
#
# She doesn't always post a sell/exit alert. Her stated target/stop (when
# given) are followed first, since that's the actual call being copied -
# but this always ALSO runs its own independent guardrails underneath
# hers, so a position is never left completely unmanaged just because she
# went quiet:
#   - a hard stop-loss on the option's own premium (she trades short-dated
#     options; a gap can blow through an underlying-price stop before this
#     loop's next poll, so a premium-based backstop matters)
#   - a trailing stop once meaningfully in profit, so a round-trip back to
#     a loss can't happen silently just because no one said "sell"
#   - a forced close near market close on the contract's expiration day,
#     regardless of P&L - theta/pin risk goes non-linear into the close on
#     0-5 DTE contracts, which is what she trades
#   - a max-hold-time cutoff as a last resort if none of the above fire
#
# Standard retail options risk management (this is not a novel invention -
# see e.g. E*TRADE's and Schwab's own writeups on automating options exits):
# stop around 40-50% of premium, take-profit/trail around 50-100%+, and
# always have a time-based exit near expiration.
#
# Exit confirmation: an earlier version marked a position "exited" the
# instant the closing order was SUBMITTED, with no check that it actually
# FILLED - a resting limit order on a thin contract can sit open (or never
# fill at all) while the rest of the system believed the position was
# closed. It also treated a failed submission as terminal, ending the
# monitor thread entirely on one transient error. Both fixed here: every
# exit attempt polls for a real fill (mirrors trade_handler.py's entry
# fill-confirmation - same config vars, same pattern) before returning
# "exited", and any attempt that doesn't confirm filled is retried on the
# next loop iteration instead of abandoning the position - reconcile.py
# remains the outer backstop for an actual process crash/restart, not for
# a single failed order.
from __future__ import annotations
import os, time, logging, threading
from datetime import date, datetime, timezone
from typing import Optional, Dict, Any, Callable

from lavish_core.trade.broker import latest_quote, get_clock, get_order, cancel_order, latest_option_quote, place_option_order
from lavish_core.utils.alerts import post_discord
from lavish_core.trade.exit_state import ExitState
from lavish_core.db.hybrid_store import DEFAULT_DB
from lavish_core.trade.broker import get_order_by_client_id
from lavish_core.utils.safety import finite_number

log = logging.getLogger("options_exit_monitor")

POLL_INTERVAL_SEC = float(os.getenv("OPTIONS_EXIT_POLL_INTERVAL_SEC", "5"))
MAX_MONITOR_HOURS = float(os.getenv("OPTIONS_EXIT_MAX_MONITOR_HOURS", "8"))

# Our own guardrails - always active, independent of anything she posts.
OUR_STOP_LOSS_PCT = float(os.getenv("OPTIONS_OWN_STOP_LOSS_PCT", "0.50"))       # exit if premium down 50%
OUR_TRAIL_TRIGGER_PCT = float(os.getenv("OPTIONS_OWN_TRAIL_TRIGGER_PCT", "0.30"))  # start trailing once up 30%
OUR_TRAIL_DRAWDOWN_PCT = float(os.getenv("OPTIONS_OWN_TRAIL_DRAWDOWN_PCT", "0.20"))  # give back at most 20% off the high
EXPIRY_FORCE_CLOSE_MINUTES = float(os.getenv("OPTIONS_EXPIRY_FORCE_CLOSE_MINUTES", "30"))  # force out N min before close, on expiry day

# Exit-fill confirmation poll - same names/defaults as trade_handler.py's
# entry-side confirmation, deliberately: same kind of operation (confirm a
# submitted limit order actually filled), same config surface.
EXIT_FILL_POLL_ATTEMPTS = int(os.getenv("OPTIONS_FILL_POLL_ATTEMPTS", "5"))
EXIT_FILL_POLL_INTERVAL_SEC = float(os.getenv("OPTIONS_FILL_POLL_INTERVAL_SEC", "1.0"))

_TERMINAL_STATUSES = ("filled", "canceled", "expired", "rejected")


def _option_limit_price(contract_symbol: str) -> Optional[float]:
    """
    None means "no usable quote data" (missing/unreachable - genuinely
    retryable). A confirmed bid of exactly 0.0 is different information
    entirely - the market saying this contract is worthless, not that
    data is missing - and is returned as 0.0, not None, so callers can
    tell the two apart. Conflating them used to make _exit() retry
    forever trying to "sell" an option that will never have a positive
    price again (a real, deep-OTM/near-expiry scenario, not contrived) -
    looping past the position's actual expiration and alerting every
    10th attempt indefinitely.
    """
    q = latest_option_quote(contract_symbol)
    if not q or q.get("bid") is None or q.get("ask") is None:
        return None
    try:
        bid = finite_number(q["bid"], nonnegative=True)
        ask = finite_number(q["ask"], nonnegative=True)
        if ask < bid:
            return None
    except ValueError:
        return None
    if bid == 0.0:
        return 0.0
    if ask == 0.0:
        return None  # ask=0 with a real bid is bad/stale data, not "worthless"
    return round((bid + ask) / 2, 2)


def _minutes_to_close() -> Optional[float]:
    try:
        clk = get_clock()
        if not clk.get("is_open"):
            return None
        next_close = datetime.fromisoformat(clk["next_close"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return max(0.0, (next_close - now).total_seconds() / 60.0)
    except Exception as e:
        log.warning("Could not read market clock: %s", e)
        return None


def watch_and_exit(
    underlying_symbol: str,
    contract_symbol: str,
    qty: int,
    option_side: str,  # "call" | "put" - determines which direction is target vs stop
    entry_price: float,
    expiry: Optional[date] = None,
    target_underlying: Optional[float] = None,
    stop_underlying: Optional[float] = None,
    on_exit: Optional[Callable[[Dict[str, Any]], None]] = None,
    resume_exit_reason: Optional[str] = None,
    resume_underlying_price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Blocking poll loop: watches both her stated underlying levels (if any)
    AND our own premium-based guardrails, and submits a closing 'sell'
    order on the option contract the moment anything triggers. Intended to
    run one thread per open options position (see watch_and_exit_async).

    Direction for her levels depends on option_side: a CALL profits as the
    underlying rises (target = price >= target_underlying, stop = price <=
    stop_underlying); a PUT is the reverse.
    """
    is_call = option_side.lower().startswith("c")
    deadline = time.time() + MAX_MONITOR_HOURS * 3600
    high_water_pct = 0.0
    trailing_active = False
    consecutive_failures = 0
    exit_state = ExitState(qty,db_path=DEFAULT_DB,symbol=contract_symbol,venue=os.getenv("TRADE_MODE","paper"))

    log.info(
        "Watching %s (%s x%s, entry=%.2f) via underlying %s: her_target=%s her_stop=%s "
        "| our guardrails: stop=-%.0f%% trail_trigger=+%.0f%% trail_drawdown=%.0f%%",
        contract_symbol, option_side.upper(), qty, entry_price, underlying_symbol,
        target_underlying, stop_underlying,
        OUR_STOP_LOSS_PCT * 100, OUR_TRAIL_TRIGGER_PCT * 100, OUR_TRAIL_DRAWDOWN_PCT * 100,
    )

    def _alert_failure(reason: str, detail: str) -> None:
        # Always alert on the first failure; throttle after that (every
        # 10th) so a sustained outage retrying every POLL_INTERVAL_SEC
        # doesn't spam the channel into being ignored.
        if consecutive_failures == 1 or consecutive_failures % 10 == 0:
            post_discord(
                f"\U0001F6A8 Exit attempt #{consecutive_failures} failed for {contract_symbol} "
                f"(trigger: {reason}): {detail}. Retrying automatically - this position is NOT "
                f"unmanaged, still being watched."
            )

    def _exit(reason: str, underlying_price: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        One exit attempt: submit a closing order, then poll for a real
        fill. Returns the terminal result only once actually filled;
        returns None if this attempt should be retried (submission
        failed, or never confirmed filled) - the caller loops and tries
        again rather than believing the position is closed when it might
        not be.
        """
        nonlocal consecutive_failures
        # Order-status recovery does not require a new executable quote.
        # Never submit a replacement without one, even after cancellation.
        in_flight = getattr(exit_state, "pending", None) is not None or getattr(exit_state, "unknown", False)
        try:
            limit_price = _option_limit_price(contract_symbol)
        except Exception as exc:
            if not in_flight:
                raise
            # A quote transport/configuration fault must not prevent status
            # lookup of an order that may already have filled. Lookup itself
            # retains its normal failure handling; this grants no submit path.
            limit_price = None
            log.warning("%s: option quote failed (%s); reconciling existing exit only",
                        contract_symbol, type(exc).__name__)

        if limit_price == 0.0 and not in_flight:
            # A zero bid can recover; only an expired contract can settle.
            from lavish_core.trade.reconcile import parse_occ_symbol
            parsed = parse_occ_symbol(contract_symbol)
            expiry_day = expiry or (parsed["expiry"] if parsed else None)
            if expiry_day is None or expiry_day >= date.today():
                return {"status":"no_liquidity", "reason":"manual_review_required"}
            post_discord(f"Expired {contract_symbol} has zero bid; verify broker settlement.")
            return {"status":"settlement_unverified", "reason":f"{reason}_worthless_requires_broker_confirmation", "underlying_price":underlying_price,
                    "option_price":None, "quoted_bid":0.0, "order":None}

        if limit_price is None and not in_flight:
            return None
        outcome = exit_state.attempt(
            lambda remaining, client_id: place_option_order(contract_symbol=contract_symbol, side="sell", qty=remaining, order_type="limit", limit_price=limit_price, client_order_id=client_id),
            get_order_by_client_id, get_order, cancel_order, EXIT_FILL_POLL_ATTEMPTS,
            lambda: time.sleep(EXIT_FILL_POLL_INTERVAL_SEC),
            **({"allow_submit": False} if limit_price is None or limit_price == 0.0 else {}))
        if outcome is None:
            consecutive_failures += 1
            _alert_failure(reason,"Closing order unresolved; retaining its identity")
            return None
        if outcome["status"] != "exited":
            return outcome
        return {"status":"exited", "reason":reason, "underlying_price":underlying_price,
                "option_price":outcome["price"], "filled_qty":outcome["filled_qty"], "order":outcome["order"], "ledger_recorded":outcome.get("ledger_recorded",False)}

    allowed_resume_reasons = {
        "her_target", "her_stop", "our_stop_loss", "our_trailing_stop",
        "expiry_force_close", "max_hold_timeout",
    }
    if resume_exit_reason is not None and resume_exit_reason not in allowed_resume_reasons:
        raise ValueError("Invalid resumed exit reason")

    def _persist_intent(reason, underlying_price):
        from lavish_core.db.hybrid_store import HybridStore
        store = HybridStore(DEFAULT_DB)
        try:
            store.set_exit_intent(contract_symbol, reason, underlying_price)
        finally:
            store.con.close()

    def _clear_intent():
        from lavish_core.db.hybrid_store import HybridStore
        store = HybridStore(DEFAULT_DB)
        try:
            store.clear_exit_intent(contract_symbol)
        finally:
            store.con.close()

    loop_errors = 0
    latched_reason = resume_exit_reason
    latched_underlying_price = resume_underlying_price
    while time.time() < deadline:
        try:
            triggered_reason: Optional[str] = latched_reason
            underlying_price = latched_underlying_price if latched_reason else latest_quote(underlying_symbol)

            # --- her stated levels (checked first - this is the call being copied) ---
            if triggered_reason is None and underlying_price is not None:
                if is_call:
                    if target_underlying is not None and underlying_price >= target_underlying:
                        triggered_reason = "her_target"
                    elif stop_underlying is not None and underlying_price <= stop_underlying:
                        triggered_reason = "her_stop"
                else:
                    if target_underlying is not None and underlying_price <= target_underlying:
                        triggered_reason = "her_target"
                    elif stop_underlying is not None and underlying_price >= stop_underlying:
                        triggered_reason = "her_stop"

            # --- our own guardrails (always run, regardless of whether she gave levels) ---
            if triggered_reason is None:
                premium = _option_limit_price(contract_symbol)
                if premium is not None and entry_price > 0:
                    pnl_pct = (premium - entry_price) / entry_price
                    high_water_pct = max(high_water_pct, pnl_pct)
                    if not trailing_active and high_water_pct >= OUR_TRAIL_TRIGGER_PCT:
                        trailing_active = True
                        log.info("%s: trailing stop armed at high-water +%.1f%%", contract_symbol, high_water_pct * 100)

                    if pnl_pct <= -OUR_STOP_LOSS_PCT:
                        triggered_reason = "our_stop_loss"
                    elif trailing_active and (high_water_pct - pnl_pct) >= OUR_TRAIL_DRAWDOWN_PCT:
                        triggered_reason = "our_trailing_stop"

            # --- forced close near expiration, regardless of P&L ---
            if triggered_reason is None and expiry is not None and datetime.now(timezone.utc).date() >= expiry:
                mins_left = _minutes_to_close()
                if mins_left is not None and mins_left <= EXPIRY_FORCE_CLOSE_MINUTES:
                    triggered_reason = "expiry_force_close"

            if triggered_reason:
                # Once an exit is requested, a recovered price (or an
                # unavailable underlying feed) must not cancel retries of
                # that same ExitState/order identity after a failed attempt.
                if latched_reason is None:
                    # Persist before contacting the broker. A crash after the
                    # trigger but before/while submitting must not let a
                    # recovered price silently cancel the requested close.
                    _persist_intent(triggered_reason, underlying_price)
                    latched_reason = triggered_reason
                    latched_underlying_price = underlying_price
                result = _exit(triggered_reason, underlying_price)
                if result is not None:
                    if result.get("status") == "exited":
                        _clear_intent()
                    if on_exit:
                        on_exit(result)
                    return result
                # else: this attempt failed/unconfirmed - already logged/alerted
                # inside _exit(); fall through and retry next iteration.
        except Exception as e:
            # Real gap found and fixed: every call above is supposed to
            # swallow its own errors (latest_quote/_option_limit_price are
            # documented "never raises"), but that chain shares
            # _check_keys(), which DOES raise unguarded if Alpaca
            # credentials ever go missing/invalid mid-run - and nothing
            # upstream of this loop was catching that. An uncaught
            # exception here used to kill this one position's entire exit
            # monitor silently - no stop, no target, no expiry-force-close,
            # nobody told, until the next reconcile_open_positions() sweep
            # (if even that could reach the broker). Must never let this
            # loop die; log, alert, and keep retrying instead.
            loop_errors += 1
            log.error("%s: exit-monitor loop error (attempt %d, still watching): %s",
                      contract_symbol, loop_errors, e)
            if loop_errors == 1 or loop_errors % 10 == 0:
                post_discord(f"🚨 {contract_symbol}: exit monitor hit an unexpected error ({e}) - "
                              f"retrying automatically, this position is NOT unmanaged yet, but "
                              f"needs a look if this keeps happening.")

        time.sleep(POLL_INTERVAL_SEC)

    # Max hold time reached with nothing else triggering - force out rather
    # than abandon the position unmanaged. This one must actually succeed:
    # keep retrying past the deadline (which has already been spent) rather
    # than returning a failure that would falsely read as "handled".
    log.warning("%s: max monitor window (%.1fh) reached; continuing %s exit.",
                contract_symbol, MAX_MONITOR_HOURS, latched_reason or "max_hold_timeout")
    if latched_reason is None:
        _persist_intent("max_hold_timeout", latched_underlying_price)
        latched_reason = "max_hold_timeout"
    while True:
        try:
            result = _exit(latched_reason or "max_hold_timeout", latched_underlying_price)
            if result is not None:
                if result.get("status") == "exited":
                    _clear_intent()
                if on_exit:
                    on_exit(result)
                return result
        except Exception as e:
            # Same unguarded-_check_keys() gap as the main loop above -
            # _exit() itself calls _option_limit_price() before its own
            # try block, so this must not let an uncaught exception end
            # this "must actually succeed" retry loop either.
            loop_errors += 1
            log.error("%s: forced-close retry error (attempt %d, still retrying): %s",
                      contract_symbol, loop_errors, e)
            if loop_errors == 1 or loop_errors % 10 == 0:
                post_discord(f"🚨 {contract_symbol}: forced-close exit hit an unexpected error ({e}) - "
                              f"retrying automatically, needs a look if this keeps happening.")
        time.sleep(POLL_INTERVAL_SEC)


def watch_and_exit_async(*args, **kwargs) -> threading.Thread:
    """Fire-and-forget version of watch_and_exit() for callers that can't block."""
    from lavish_core.utils.exit_supervisor import start_exit_worker
    symbol = kwargs.get('contract_symbol', args[1] if len(args)>1 else None)
    return start_exit_worker(watch_and_exit, args, kwargs, symbol=symbol, db_path=DEFAULT_DB, callback_index=8)
