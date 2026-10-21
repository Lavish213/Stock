# lavish_core/trade/reconcile.py
# Every open-position exit guardrail (stop/trail/expiry-close) runs as an
# in-memory background thread in options_exit_monitor.py. That thread only
# exists while this process is alive - if run_ingest.py crashes, gets
# redeployed, or is just restarted, every open options position instantly
# has NO ONE watching it: no stop-loss, no trailing stop, no expiry-day
# close. Alpaca has no server-side dead-man's-switch of its own (checked -
# it doesn't auto-cancel/flatten on client disconnect), so this has to be
# handled here: on startup, and periodically thereafter, find any open
# option position that isn't being watched and start a guardrail monitor
# for it - using her original stated target/stop when we can recover it
# (persisted in the entry order's meta - see trade_handler.py), falling
# back to our own defaults only when that lookup comes up empty.
from __future__ import annotations
import json, os, re, time, threading, logging
from datetime import date, datetime
from typing import Optional, Dict, Any, Set, Tuple

from lavish_core.trade.broker import get_positions, get_order_by_client_id, get_order, SUPPORTS_NATIVE_BRACKETS
from lavish_core.utils.safety import finite_number
from lavish_core.trade.options_exit_monitor import watch_and_exit_async
from lavish_core.utils.alerts import post_discord
from lavish_core.db.hybrid_store import HybridStore, DEFAULT_DB, _positions_lock

log = logging.getLogger("reconcile")

RECONCILE_INTERVAL_SECONDS = float(os.getenv("RECONCILE_INTERVAL_SECONDS", "120"))

# OCC symbol: TICKER + YYMMDD + C/P + strike*1000 zero-padded to 8 digits
_OCC_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")

# In-process record of which contract symbols already have an active
# monitor, so reconciliation doesn't start a second one for a position
# this same process opened itself moments ago.
_watched: Set[str] = set()
_watched_lock = threading.Lock()


def mark_watched(contract_symbol: str) -> None:
    with _watched_lock:
        _watched.add(contract_symbol)


def unmark_watched(contract_symbol: str) -> None:
    with _watched_lock:
        _watched.discard(contract_symbol)


def parse_occ_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    m = _OCC_RE.match(symbol)
    if not m:
        return None
    ticker, yymmdd, cp, strike_digits = m.groups()
    try:
        expiry = datetime.strptime(yymmdd, "%y%m%d").date()
    except ValueError:
        return None
    return {
        "ticker": ticker,
        "expiry": expiry,
        "side": "CALL" if cp == "C" else "PUT",
        "strike": int(strike_digits) / 1000.0,
    }


def _find_original_target_stop(contract_symbol: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Looks up the most recent 'buy' order for this contract to recover her
    original stated target/stop (persisted in its meta by trade_handler.py).
    Returns (None, None) on any lookup failure or if the order predates
    this field being persisted - callers must treat that the same as
    "she didn't give one", not as an error.
    """
    try:
        store = HybridStore(duckdb_path=str(DEFAULT_DB))
        rows = store.fetchall(
            "SELECT meta FROM orders WHERE symbol = ? AND side = 'buy' "
            "AND (status IN ('filled','partially_filled') OR EXISTS "
            "(SELECT 1 FROM fills WHERE fills.order_id=orders.id AND fills.side='buy')) ORDER BY ts DESC LIMIT 1",
            (contract_symbol,),
        )
        if not rows:
            return None, None
        meta_raw = rows[0][0]
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else (meta_raw or {})
        target = meta.get("target_underlying")
        stop = meta.get("stop_underlying")
        return (finite_number(target, positive=True) if target is not None else None,
                finite_number(stop, positive=True) if stop is not None else None)
    except Exception as e:
        log.warning("reconcile: could not recover original target/stop for %s: %s", contract_symbol, e)
        return None, None
    finally:
        if 'store' in locals():
            store.con.close()


def _find_latest_entry_time(contract_symbol: str):
    """Return the latest confirmed local buy time for an option contract.

    Exit intents are keyed by OCC symbol, which can be reused after a prior
    position closes.  The timestamp lets reconciliation distinguish an intent
    belonging to that prior position from one belonging to the current entry.
    Lookup failure deliberately returns ``None`` so a possibly valid latched
    exit is preserved rather than silently discarded.
    """
    try:
        store = HybridStore(duckdb_path=str(DEFAULT_DB))
        rows = store.fetchall(
            "SELECT MIN(fills.ts) FROM orders JOIN fills ON fills.order_id=orders.id "
            "AND fills.side='buy' WHERE orders.id = ("
            "SELECT o.id FROM orders o WHERE o.symbol = ? AND o.side = 'buy' "
            "AND (o.status IN ('filled','partially_filled') OR EXISTS "
            "(SELECT 1 FROM fills f2 WHERE f2.order_id=o.id AND f2.side='buy')) "
            "ORDER BY o.ts DESC LIMIT 1)",
            (contract_symbol,),
        )
        return rows[0][0] if rows and rows[0][0] is not None else None
    except Exception as e:
        log.warning("reconcile: could not recover entry time for %s: %s", contract_symbol, e)
        return None
    finally:
        if 'store' in locals():
            store.con.close()


def reconcile_pending_orders(store=None):
    """Resolve durable pending entries without ever issuing another POST.

    Cumulative broker fills are converted into incremental ledger fills so
    a partial fill followed by a final fill neither disappears nor doubles.
    Reservations remain held throughout a failed or ambiguous lookup.
    """
    owns_store = store is None
    store = store or HybridStore(duckdb_path=str(DEFAULT_DB))
    resolved = 0
    try:
        for local in store.get_open_orders():
            client_id = local.get("client_id")
            if not client_id or local.get("venue") not in ("paper", "live"):
                continue
            try:
                remote = get_order_by_client_id(client_id)
                if not isinstance(remote, dict) or not remote.get("id"):
                    continue
                status = remote.get("status")
                terminal = ("filled", "canceled", "expired", "rejected")
                active = ("new", "pending_new", "accepted", "partially_filled", "pending_cancel")
                if status not in terminal + active:
                    raise ValueError("Unknown broker order status")
                if (('symbol' in remote and remote['symbol'] != local['symbol'])
                        or ('side' in remote and remote['side'] != local['side'])
                        or ('client_order_id' in remote and remote['client_order_id'] != client_id)
                        or ('qty' in remote and finite_number(remote['qty'], positive=True) != local['qty'])):
                    raise ValueError("Broker order identity mismatch")
                cumulative = finite_number(remote.get("filled_qty",0), nonnegative=True)
                if cumulative > local["qty"] or (status == 'filled' and cumulative != local['qty']):
                    raise ValueError("Invalid terminal or cumulative fill")
                with _positions_lock:
                    # A close monitor or another sweep may have completed the
                    # order while the REST lookup was in flight.
                    current = store.fetchall('SELECT status,meta FROM orders WHERE id=?', (local['id'],))[0]
                    if current[0] in terminal:
                        continue
                    meta = json.loads(current[1] or '{}')
                    if meta.get('broker_id') and str(meta['broker_id']) != str(remote['id']):
                        raise ValueError('Broker order ID changed')
                    rows = store.fetchall("SELECT coalesce(sum(qty),0),coalesce(sum(qty*price),0),coalesce(sum(fee),0) FROM fills WHERE order_id=? AND side=?", (local["id"],local["side"]))
                    recorded, notional, recorded_fee = rows[0]
                    delta = cumulative-recorded
                    if delta < 0:
                        raise ValueError("Broker fill regressed")
                    fee = finite_number(remote.get('filled_fee', recorded_fee), nonnegative=True) - recorded_fee
                    if fee < 0:
                        raise ValueError('Broker execution fee regressed')
                    if delta > 0:
                        average = finite_number(remote.get("filled_avg_price"), positive=True)
                        incremental_price = finite_number((cumulative*average-notional)/delta, positive=True)
                    elif cumulative > 0:
                        average = finite_number(remote.get('filled_avg_price'), positive=True)
                        if abs(cumulative*average - notional) > 1e-8:
                            raise ValueError('Broker execution price changed without new fills')
                    elif fee > 0:
                        raise ValueError('Fee without an execution')
                    # Only validated snapshots may bind identity or change
                    # exposure. Keep source/exit metadata intact.
                    meta['broker_id'] = str(remote['id'])
                    store.execute('UPDATE orders SET meta=? WHERE id=?', (json.dumps(meta), local['id']))
                    if delta > 0:
                        store.log_fill(local["id"],local["symbol"],local["side"],delta,incremental_price,fee=fee,venue=local["venue"],event_id=f"reconcile:{remote['id']}:{cumulative}")
                    elif fee > 0:
                        # A later cumulative commission may accompany the same
                        # fill total. Apply just its validated increase once.
                        fid = store.fetchall('SELECT id FROM fills WHERE order_id=? ORDER BY ts DESC LIMIT 1', (local['id'],))[0][0]
                        store.execute('UPDATE fills SET fee=fee+? WHERE id=?', (fee, fid))
                    if status in terminal:
                        store.set_order_status(local["id"],status)
                        store.release_entry(client_id)
                        resolved += 1
                    elif status in active:
                        store.set_order_status(local["id"],status)
            except ValueError:
                with _positions_lock:
                    current = store.fetchall('SELECT status FROM orders WHERE id=?', (local['id'],))[0][0]
                    if current not in ('filled', 'canceled', 'expired', 'rejected'):
                        store.set_order_status(local['id'], 'fill_unverified')
                        store.set_kill_switch(True, 'Pending order snapshot unverified; broker reconciliation required')
                log.warning("Pending order snapshot quarantined (local_id=%s)", local['id'])
            except Exception:
                log.warning("Pending order requires reconciliation (local_id=%s)",local["id"])
        return resolved
    finally:
        if owns_store:
            store.con.close()


def reconcile_open_positions() -> int:
    """
    Finds open long option positions with no active monitor in this
    process and starts one for each, using our own default guardrails.
    Returns the number recovered. Safe to call repeatedly - a position
    already in _watched is skipped.
    """
    try:
        positions = get_positions()
    except Exception as e:
        log.error("reconcile: could not fetch positions: %s", e)
        return 0

    recovered = 0
    for pos in positions:
        if pos.get('asset_class') == 'us_equity' and pos.get('side') == 'long':
            recovered += _recover_equity_position(pos)
            continue
        if pos.get("asset_class") != "us_option":
            continue
        if pos.get("side") != "long":
            continue  # only long calls/puts are ever opened by this bot

        symbol = pos.get("symbol", "")
        with _watched_lock:
            already_watched = symbol in _watched
        if already_watched:
            continue

        parsed = parse_occ_symbol(symbol)
        if not parsed:
            log.warning("reconcile: found option position %s but couldn't parse its OCC symbol - skipping.", symbol)
            continue

        try:
            entry_price=finite_number(pos.get("avg_entry_price"),positive=True)
            amount=finite_number(pos.get("qty"),positive=True)
            if not amount.is_integer():
                raise ValueError("Fractional option position")
            qty=int(amount)
        except (TypeError,ValueError,OverflowError):
            log.warning("reconcile: bad qty/avg_entry_price on position %s - skipping.", symbol)
            continue
        if entry_price <= 0 or qty <= 0:
            continue

        target_underlying, stop_underlying = _find_original_target_stop(symbol)
        recovered_her_levels = target_underlying is not None or stop_underlying is not None
        intent_store = HybridStore(DEFAULT_DB)
        try:
            exit_intent = intent_store.get_exit_intent(symbol)
            entry_time = _find_latest_entry_time(symbol)
            if (exit_intent and entry_time is not None
                    and exit_intent["triggered_at"] < entry_time):
                log.error(
                    "reconcile: stale exit intent for %s predates the latest confirmed entry; "
                    "clearing it and halting new entries while normal guardrails are restored.",
                    symbol,
                )
                intent_store.clear_exit_intent(symbol)
                intent_store.set_kill_switch(
                    True, f"Stale exit intent superseded by newer entry: {symbol}",
                )
                post_discord(
                    f"⚠️ Stale exit intent for {symbol} predates its latest confirmed entry. "
                    "The old intent was quarantined, new entries were halted, and normal "
                    "guardrails are being restored for the current position."
                )
                exit_intent = None
        finally:
            intent_store.con.close()

        log.warning(
            "reconcile: found unmanaged open position %s (%s x%s, entry=%.2f, exp=%s) - "
            "starting a guardrail monitor (%s).",
            symbol, parsed["side"], qty, entry_price, parsed["expiry"],
            f"recovered her target={target_underlying} stop={stop_underlying}" if recovered_her_levels
            else "no original target/stop found - our defaults only",
        )
        post_discord(
            f"⚠️ Recovered an unmanaged options position on startup/reconcile: "
            f"{parsed['ticker']} {parsed['side']} ${parsed['strike']:.2f} exp {parsed['expiry']} "
            f"(qty={qty}, entry={entry_price:.2f}). " +
            (f"Recovered her stated target={target_underlying} stop={stop_underlying} from the order log - "
             f"watching those plus our own guardrails."
             if recovered_her_levels else
             "No original stop/target from her was found in the order log - "
             "running our own stop-loss/trailing/expiry guardrails on it now.")
        )

        with _watched_lock:
            if symbol in _watched:
                continue
            _watched.add(symbol)

        def _on_exit(result: Dict[str, Any], _symbol=symbol) -> None:
            unmark_watched(_symbol)
            if result.get("status") not in ("exited",):
                post_discord(f"⚠️ Exit monitor for recovered position {_symbol} ended abnormally: {result}")

        try:
            watch_and_exit_async(
                underlying_symbol=parsed["ticker"],
                contract_symbol=symbol,
                qty=qty,
                option_side=parsed["side"],
                entry_price=entry_price,
                expiry=parsed["expiry"],
                target_underlying=target_underlying,
                stop_underlying=stop_underlying,
                on_exit=_on_exit,
                resume_exit_reason=exit_intent["reason"] if exit_intent else None,
                resume_underlying_price=exit_intent["underlying_price"] if exit_intent else None,
            )
        except Exception:
            unmark_watched(symbol)
            store = HybridStore(DEFAULT_DB)
            try:
                store.set_kill_switch(True, 'Recovered option protection failed to start')
            finally:
                store.con.close()
            log.error('Recovered option monitor failed to start (%s)', symbol)
            continue
        recovered += 1

    return recovered


def _recover_equity_position(position):
    """Restore the recorded equity plan; never guess ownership or new levels."""
    from lavish_core.trade.equity_exit_monitor import watch_bracket_async, watch_and_exit_active_async
    symbol = position.get('symbol')
    with _watched_lock:
        if symbol in _watched:
            return 0
    store = HybridStore(DEFAULT_DB)
    try:
        qty = finite_number(position.get('qty'), positive=True)
        entry = finite_number(position.get('avg_entry_price'), positive=True)
        rows = store.fetchall("SELECT id,qty,venue,meta FROM orders WHERE symbol=? AND side='buy' "
                              "AND status='filled' ORDER BY ts DESC LIMIT 1", (symbol,))
        if not rows or rows[0][1] != qty:
            raise ValueError('Equity ownership or original quantity unverified')
        oid, _, venue, raw_meta = rows[0]
        confirmed = store.fetchall("SELECT coalesce(sum(CASE WHEN side='buy' THEN qty ELSE -qty END),0) "
                                  "FROM fills WHERE order_id=?", (oid,))[0][0]
        if confirmed != qty:
            raise ValueError('Equity confirmed inventory does not match the recovered plan')
        meta = json.loads(raw_meta or '{}')
        if venue not in ('paper', 'live'):
            raise ValueError('Equity venue unverified')
        kwargs = dict(symbol=symbol, qty=qty, entry_price=entry, venue=venue)
        if SUPPORTS_NATIVE_BRACKETS:
            parent = get_order(meta['broker_id'])
            if (not isinstance(parent, dict) or str(parent.get('id')) != str(meta['broker_id'])
                    or ('symbol' in parent and parent['symbol'] != symbol)):
                raise ValueError('Equity bracket parent identity unverified')
            legs = parent.get('legs')
            if not isinstance(legs, list) or not legs:
                raise ValueError('Equity protection legs unavailable')
            leg_ids = [leg['id'] for leg in legs if isinstance(leg, dict) and leg.get('id')]
            if len(leg_ids) != len(legs) or len(set(leg_ids)) != len(leg_ids):
                raise ValueError('Invalid equity bracket legs')
            kwargs.update(order_id=oid, leg_ids=leg_ids)
            launch = watch_bracket_async
        else:
            target = finite_number(meta.get('take_profit'), positive=True)
            stop = finite_number(meta.get('stop_loss'), positive=True)
            if not stop < entry < target:
                raise ValueError('Recorded equity exit levels unverified')
            kwargs.update(take_profit=target, stop_loss=stop)
            launch = watch_and_exit_active_async
        def finished(result):
            unmark_watched(symbol)
            if result.get('status') != 'exited':
                s = HybridStore(DEFAULT_DB)
                try:
                    s.set_kill_switch(True, 'Recovered equity protection ended without a verified close')
                finally:
                    s.con.close()
        with _watched_lock:
            if symbol in _watched:
                return 0
            _watched.add(symbol)
        kwargs['on_exit'] = finished
        try:
            launch(**kwargs)
        except Exception:
            unmark_watched(symbol)
            raise
        return 1
    except Exception:
        store.set_kill_switch(True, 'Equity restart protection unverified; broker reconciliation required')
        log.error('Equity protection requires review (%s)', symbol)
        return 0
    finally:
        store.con.close()


def synchronize_broker_state():
    from lavish_core.trade.state_audit import audit_state
    store=HybridStore(DEFAULT_DB)
    try:
        reconcile_pending_orders(store)
        try:
            positions=get_positions()
        except Exception:
            store.set_kill_switch(True,"state_audit: broker positions unavailable")
            return {"ok":False,"reasons":["broker_positions_unavailable"]}
        return audit_state(store,positions)
    finally:
        store.con.close()


def run_periodic_reconciliation() -> None:
    """Blocking loop - run in its own background thread from run_ingest.py."""
    log.info("Reconciliation loop started (every %.0fs).", RECONCILE_INTERVAL_SECONDS)
    while True:
        try:
            synchronize_broker_state()
            n = reconcile_open_positions()
            if n:
                log.info("reconcile: recovered %d unmanaged position(s).", n)
        except Exception as e:
            log.error("reconcile: unexpected error: %s", e)
        from lavish_core.utils.worker_health import WORKER_HEALTH
        WORKER_HEALTH.beat("reconcile-loop")
        time.sleep(RECONCILE_INTERVAL_SECONDS)
