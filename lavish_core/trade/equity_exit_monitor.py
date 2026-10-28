# lavish_core/trade/equity_exit_monitor.py
# Equity entries go out as a real Alpaca bracket order (take_profit/
# stop_loss) - unlike options, Alpaca DOES manage the OCO exit itself
# server-side (whichever leg fills first auto-cancels the other), so
# watch_bracket() below doesn't submit anything the way
# options_exit_monitor.py has to - it only watches the bracket's child leg
# orders and logs whichever one fills back into this bot's own audit
# trail. Without this, an equity buy's DB order just sat "filled" forever
# with no linked exit fill, even though the position closed correctly on
# Alpaca's side - track_record.py's "open entries with no recorded exit
# fill" bucket exists specifically because of this gap.
#
# Tradier (and any other broker without a native bracket/OCO order) has no
# child legs to watch - watch_and_exit_active() below is the real exit
# plan for those: it polls price itself and submits its own closing order
# the moment take_profit/stop_loss is hit, mirroring
# options_exit_monitor.watch_and_exit's already-proven pattern (same
# reasoning: a stated exit level only means something if something
# actively watches for it and acts). trade_handler.py picks whichever of
# the two this broker actually supports - see broker.SUPPORTS_NATIVE_BRACKETS.
from __future__ import annotations
from lavish_core.utils.safety import finite_number
import os, time, threading, logging
from typing import Optional, Dict, Any, Callable, List

from lavish_core.trade.broker import get_order, latest_quote, place_order, cancel_order
from lavish_core.trade.circuit_breaker import record_trade_outcome
from lavish_core.db.hybrid_store import HybridStore, DEFAULT_DB, _positions_lock
from lavish_core.utils.alerts import post_discord
from lavish_core.trade.exit_state import ExitState
from lavish_core.trade.broker import get_order_by_client_id
from lavish_core.trade.native_bracket_receipt import book_receipt

log = logging.getLogger("equity_exit_monitor")

POLL_INTERVAL_SEC = float(os.getenv("EQUITY_EXIT_POLL_INTERVAL_SEC", "15"))
# Longer than the options default (8h) - equity brackets are typically
# wider/slower-moving than short-dated option premium, and a day order's
# own TIF already bounds this to at most one trading session anyway.
MAX_MONITOR_HOURS = float(os.getenv("EQUITY_EXIT_MAX_MONITOR_HOURS", "12"))

# Exit-fill confirmation poll for the active monitor's own closing order -
# same names/defaults/reasoning as trade_agent.place_trade's and
# options_exit_monitor's entry/exit fill confirmation.
EXIT_FILL_POLL_ATTEMPTS = int(os.getenv("EQUITY_EXIT_FILL_POLL_ATTEMPTS", "5"))
EXIT_FILL_POLL_INTERVAL_SEC = float(os.getenv("EQUITY_EXIT_FILL_POLL_INTERVAL_SEC", "1.0"))

_TERMINAL_NO_FILL = {"canceled", "expired", "rejected"}
_TERMINAL_STATUSES = ("filled", "canceled", "expired", "rejected")


def watch_bracket(
    order_id: str,
    symbol: str,
    qty: float,
    entry_price: float,
    leg_ids: List[str],
    venue: str = "paper",
    on_exit: Optional[Callable[[Dict[str, Any]], None]] = None,
    position_epoch=...,
) -> Dict[str, Any]:
    """
    Blocking poll loop over a bracket order's take_profit/stop_loss child
    legs. Returns as soon as either leg reaches "filled", or once every
    known leg has independently reached a terminal non-fill state (which
    means the position closed some other way - manual intervention, a
    day-order expiring unfilled - without an exit fill for us to record).
    """
    if not leg_ids:
        log.warning("watch_bracket called for %s with no leg ids - nothing to watch.", symbol)
        store = HybridStore(str(DEFAULT_DB))
        try:
            store.set_kill_switch(True, 'Native bracket legs missing; protection unverified')
        finally:
            store.con.close()
        return {"status": "no_legs"}

    ownership = HybridStore(str(DEFAULT_DB))
    try:
        # A direct/restarted watcher binds the original entry receipt, not
        # whichever same-symbol position exists at restart time.
        entry_epoch = ownership.get_entry_epoch(order_id)
        if position_epoch is ...:
            position_epoch = entry_epoch
        if not position_epoch or position_epoch != entry_epoch:
            ownership.set_kill_switch(True, 'Bracket entry ownership unverified')
            return {'status': 'fill_unverified'}
    finally:
        ownership.con.close()

    deadline = time.time() + MAX_MONITOR_HOURS * 3600
    log.info("Watching equity bracket exit for %s x%s (entry=%.2f) legs=%s", symbol, qty, entry_price, leg_ids)

    last_status: Dict[str, str] = {leg_id: "unknown" for leg_id in leg_ids}

    def _finish(result: Dict[str, Any]) -> Dict[str, Any]:
        if result.get('status') != 'exited':
            store = HybridStore(duckdb_path=str(DEFAULT_DB))
            try:
                store.set_kill_switch(True, 'Equity bracket protection unverified; broker reconciliation required')
            finally:
                store.con.close()
        if on_exit:
            on_exit(result)
        return result

    while time.time() < deadline:
        for leg_id in leg_ids:
            try:
                leg = get_order(leg_id)
            except Exception as e:
                log.warning("equity_exit_monitor: poll failed for leg %s (%s): %s", leg_id, symbol, e)
                continue

            if (not isinstance(leg, dict)
                    or ('id' in leg and str(leg['id']) != str(leg_id))
                    or ('symbol' in leg and leg['symbol'] != symbol)
                    or ('side' in leg and leg['side'] != 'sell')):
                return _finish({'status': 'fill_unverified', 'leg_id': leg_id})
            try:
                if 'qty' in leg and finite_number(leg['qty'], positive=True) != qty:
                    raise ValueError('Bracket quantity mismatch')
            except ValueError:
                return _finish({'status': 'fill_unverified', 'leg_id': leg_id})
            status = leg.get("status", "unknown")
            last_status[leg_id] = status

            if status in ('filled', 'partially_filled', 'canceled', 'expired', 'rejected'):
                store = HybridStore(duckdb_path=str(DEFAULT_DB), redis_url=os.environ.get("REDIS_URL") or None)
                try:
                    with _positions_lock:
                        receipt = book_receipt(store, order_id=order_id, symbol=symbol, quantity=qty,
                            epoch=position_epoch, leg_id=leg_id, leg=leg, venue=venue)
                        if receipt['review_required']:
                            return _finish({'status': 'fee_reconciled_review_required',
                                            'leg_id': leg_id, 'fee_delta': receipt['fee_delta']})
                        if status != 'filled':
                            continue
                        exit_price = receipt['notional'] / qty
                        pnl = receipt['notional'] - entry_price * qty - receipt['fee']
                        if receipt['newly_recorded'] and store.try_claim_outcome(f'native-bracket-outcome:{order_id}'):
                            record_trade_outcome(store, pnl)
                except ValueError:
                    return _finish({'status': 'fill_unverified', 'leg_id': leg_id})
                finally:
                    store.con.close()
                log.info("Equity bracket exit: %s closed via %s leg, entry=%.2f exit=%.2f pnl=%.2f",
                          symbol, leg.get("type", "?"), entry_price, exit_price, pnl)
                return _finish({"status": "exited", "leg_id": leg_id, "leg_type": leg.get("type"),
                                "exit_price": exit_price, "pnl": pnl})

        if all(s in _TERMINAL_NO_FILL for s in last_status.values()):
            store = HybridStore(str(DEFAULT_DB))
            try:
                executed = store.fetchall(
                    "SELECT coalesce(sum(f.qty),0) FROM fill_events e JOIN fills f ON f.id=e.fill_id "
                    "WHERE f.order_id=? AND substr(e.event_id,1,12)='equity-exit:'", (order_id,))[0][0]
            finally:
                store.con.close()
            if executed:
                return _finish({'status': 'partial_fill_review_required', 'filled_qty': executed,
                                'leg_statuses': dict(last_status)})
            log.warning("equity_exit_monitor: every tracked leg for %s ended without filling (%s) - "
                        "position may have closed some other way; no exit fill recorded.", symbol, last_status)
            return _finish({"status": "no_fill", "leg_statuses": dict(last_status)})

        time.sleep(POLL_INTERVAL_SEC)

    log.warning("equity_exit_monitor: %s bracket exit not observed within %.1fh - stopping watch "
                "(position may still be open and unmanaged by this monitor going forward).",
                symbol, MAX_MONITOR_HOURS)
    return _finish({"status": "monitor_timeout"})


def watch_bracket_async(*args, **kwargs) -> threading.Thread:
    """Fire-and-forget version of watch_bracket() for callers that can't block."""
    from lavish_core.utils.exit_supervisor import start_exit_worker
    symbol = kwargs.get('symbol', args[1] if len(args)>1 else None)
    return start_exit_worker(watch_bracket, args, kwargs, symbol=symbol, db_path=DEFAULT_DB, callback_index=6)


def watch_and_exit_active(
    symbol: str,
    qty: float,
    entry_price: float,
    take_profit: Optional[float] = None,
    stop_loss: Optional[float] = None,
    venue: str = "paper",
    on_exit: Optional[Callable[[Dict[str, Any]], None]] = None,
    position_epoch=...,
) -> Dict[str, Any]:
    """
    Active equity exit monitor for brokers with no native bracket/OCO
    order (Tradier, currently) - take_profit/stop_loss here are already
    fully-resolved price levels (trade_handler.py has already merged her
    stated target/stop with this bot's own fixed-pct defaults before
    calling this), so unlike options_exit_monitor there's no separate
    "our own guardrails" layer to run underneath hers - there's exactly
    one exit plan to watch, the same one Alpaca would otherwise manage
    server-side via a bracket order's child legs.

    This bot only ever brackets new LONG entries (see trade_handler.py's
    _execute_equity_trade) - direction is always "price rising = target,
    price falling = stop", no call/put-style branching needed.
    """
    deadline = time.time() + MAX_MONITOR_HOURS * 3600
    consecutive_failures = 0
    exit_state = ExitState(qty,db_path=DEFAULT_DB,symbol=symbol,venue=venue,position_epoch=position_epoch)
    log.info("Watching active equity exit for %s x%s (entry=%.2f) target=%s stop=%s",
              symbol, qty, entry_price, take_profit, stop_loss)

    def _alert_failure(reason: str, detail: str) -> None:
        if consecutive_failures == 1 or consecutive_failures % 10 == 0:
            post_discord(
                f"\U0001F6A8 Exit attempt #{consecutive_failures} failed for {symbol} "
                f"(trigger: {reason}): {detail}. Retrying automatically - this position is NOT "
                f"unmanaged, still being watched."
            )

    def _finish(result: Dict[str, Any]) -> Dict[str, Any]:
        if on_exit:
            on_exit(result)
        return result

    def _exit(reason: str, price_at_trigger: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        One exit attempt: submit a closing market sell, then poll for a
        real fill - mirrors options_exit_monitor._exit's pattern (never
        marks "exited" on submission alone; an unconfirmed attempt is
        retried next loop iteration rather than trusted or abandoned).
        """
        nonlocal consecutive_failures
        outcome = exit_state.attempt(
            lambda remaining,client_id: place_order(symbol=symbol,side="sell",qty=remaining,type_="market",time_in_force="day",client_order_id=client_id),
            get_order_by_client_id,get_order,cancel_order,EXIT_FILL_POLL_ATTEMPTS,
            lambda:time.sleep(EXIT_FILL_POLL_INTERVAL_SEC))
        if outcome is None:
            consecutive_failures += 1
            _alert_failure(reason,"Closing order unresolved; retaining its identity")
            return None
        if outcome["status"] != "exited":
            return outcome
        exit_price, filled_amt = outcome["price"], outcome["filled_qty"]
        pnl = (exit_price-entry_price)*filled_amt
        store = HybridStore(duckdb_path=str(DEFAULT_DB),redis_url=os.environ.get("REDIS_URL") or None)
        applied = (True if store.try_claim_outcome(f"outcome:{outcome['order']['id']}") else None) if outcome.get("ledger_recorded") else store.log_fill(order_id=outcome["order"]["id"],symbol=symbol,side="sell",qty=filled_amt,price=exit_price,venue=venue,event_id=f"active-equity-exit:{outcome['order']['id']}")
        if applied is not None:
            record_trade_outcome(store,pnl)
        store.con.close()
        if reason == "max_hold_timeout":
            post_discord(f"{symbol} force-closed after max hold time at {exit_price:.2f}")
        return {"status":"exited","reason":reason,"exit_price":exit_price,"pnl":pnl,"order":outcome["order"]}

    loop_errors = 0
    while time.time() < deadline:
        try:
            triggered_reason: Optional[str] = None
            price = latest_quote(symbol)

            if price is not None:
                if take_profit is not None and price >= take_profit:
                    triggered_reason = "target"
                elif stop_loss is not None and price <= stop_loss:
                    triggered_reason = "stop"

            if triggered_reason:
                result = _exit(triggered_reason, price)
                if result is not None:
                    return _finish(result)
                # else: unconfirmed/failed attempt - already logged/alerted
                # inside _exit(); fall through and retry next iteration.
        except Exception as e:
            # Same crash-resilience requirement as options_exit_monitor's
            # loop: must never let an uncaught exception (e.g. missing/
            # invalid broker credentials mid-run) silently kill this
            # position's entire exit watch with nobody told.
            loop_errors += 1
            log.error("%s: active exit-monitor loop error (attempt %d, still watching): %s",
                      symbol, loop_errors, e)
            if loop_errors == 1 or loop_errors % 10 == 0:
                post_discord(f"🚨 {symbol}: exit monitor hit an unexpected error ({e}) - "
                              f"retrying automatically, this position is NOT unmanaged yet, but "
                              f"needs a look if this keeps happening.")

        time.sleep(POLL_INTERVAL_SEC)

    # Max hold time reached with nothing else triggering - force out rather
    # than abandon the position unmanaged. This one must actually succeed:
    # keep retrying past the deadline (already spent) rather than return a
    # failure that would falsely read as "handled".
    log.warning("%s: max hold time (%.1fh) reached with no other exit - forcing close.", symbol, MAX_MONITOR_HOURS)
    while True:
        try:
            result = _exit("max_hold_timeout")
            if result is not None:
                return _finish(result)
        except Exception as e:
            loop_errors += 1
            log.error("%s: forced-close retry error (attempt %d, still retrying): %s", symbol, loop_errors, e)
            if loop_errors == 1 or loop_errors % 10 == 0:
                post_discord(f"🚨 {symbol}: forced-close exit hit an unexpected error ({e}) - "
                              f"retrying automatically, needs a look if this keeps happening.")
        time.sleep(POLL_INTERVAL_SEC)


def watch_and_exit_active_async(*args, **kwargs) -> threading.Thread:
    """Fire-and-forget version of watch_and_exit_active() for callers that can't block."""
    from lavish_core.utils.exit_supervisor import start_exit_worker
    symbol = kwargs.get('symbol', args[0] if args else None)
    return start_exit_worker(watch_and_exit_active, args, kwargs, symbol=symbol, db_path=DEFAULT_DB, callback_index=6, bind_position_epoch=True)
