# lavish_core/trading/trade_agent.py
# Lavish Trade Agent v3 — Hardened Execution + Risk Gate (HybridStore + Alpaca)
#
# ✅ Backward compatible with your current place_trade() usage
# ✅ Adds TradeAgent class so SuperCore/TradeBridge can call place_order()
# ✅ Idempotent order submits (retry-safe)
# ✅ Broker retry + 429/5xx backoff
# ✅ Price sanity + slippage guard when real quote is available (soft-fail if not)
# ✅ Safer DB writes + richer meta/audit trail
# ✅ Optional EventBus hooks (duck-typed)
# ✅ Still runs clean dry/paper/live modes
#
# ENV (in addition to existing):
#  LIVE_TRADE=true|false              (SuperCore already gates; agent respects too)
#  ORDER_SLIPPAGE_PCT_MAX=0.006       (0.6%)
#  ORDER_PRICE_SANITY_PCT_MAX=0.02    (2%)
#  ORDER_RETRY_MAX=4
#  ORDER_RETRY_BASE_SEC=0.75
#  ORDER_DEFAULT_TIF=day
#  ORDER_DEFAULT_TYPE=market
#
# NOTE:
#  This file assumes HybridStore has:
#    - get_position(symbol)
#    - fetchall(sql)
#    - submit_order(...)
#    - log_fill(...)
#    - get_risk_limits(), set_risk_limits()
#  Same as your current version.

from __future__ import annotations

from lavish_core.utils.safety import finite_number, check_trading_endpoint
import os
import time
import json
import math
import uuid
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, Callable

from lavish_core.db.hybrid_store import HybridStore, DEFAULT_DB
from lavish_core.trade.circuit_breaker import check_ok as _circuit_breaker_check_ok
from lavish_core.logger_setup import configure_root_logging

try:
    from lavish_core.trade.broker import (
        get_account,
        get_position as broker_position,
        place_order as broker_place_order,
        get_order as broker_get_order,
        get_order_by_client_id as broker_get_order_by_client_id,
        cancel_order as broker_cancel_order,
    )
    HAS_ALPACA = True
except Exception:
    HAS_ALPACA = False

# Configures the ROOT logger (stdout + logs/lavish.log) exactly once - see
# configure_root_logging()'s docstring. This used to be a local
# logging.basicConfig() call here that only added a stdout handler; every
# other module in lavish_core/trade/ that does plain logging.getLogger(name)
# (circuit_breaker, broker_alpaca, reconcile, options_broker, etc.) relies
# on that root config via propagation, so those modules' logs were never
# reaching a file, only container stdout.
configure_root_logging()
LOG = logging.getLogger("trade_agent")

# ───────────────────────────────────────────────────────────────
# Helpers / Env
# ───────────────────────────────────────────────────────────────

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if v not in (None, "") else default

DEFAULT_TIF = (env("ORDER_DEFAULT_TIF", "day") or "day").lower()
DEFAULT_TYPE = (env("ORDER_DEFAULT_TYPE", "market") or "market").lower()

MAX_SLIPPAGE_PCT = float(env("ORDER_SLIPPAGE_PCT_MAX", "0.006") or 0.006)
MAX_SANITY_PCT = float(env("ORDER_PRICE_SANITY_PCT_MAX", "0.02") or 0.02)

RETRY_MAX = int(env("ORDER_RETRY_MAX", "4") or 4)
RETRY_BASE = float(env("ORDER_RETRY_BASE_SEC", "0.75") or 0.75)

FILL_POLL_ATTEMPTS = int(env("ORDER_FILL_POLL_ATTEMPTS", "5") or 5)
FILL_POLL_INTERVAL_SEC = float(env("ORDER_FILL_POLL_INTERVAL_SEC", "1.0") or 1.0)

def _dry_price(symbol: str, fallback: float = 100.0) -> float:
    """
    Deterministic pseudo-price for dry runs and rough risk checks.
    """
    base = sum(ord(c) for c in symbol) % 50
    return round(fallback + base + 0.17, 2)

def _stable_client_id(symbol: str, side: str, qty: float) -> str:
    """
    Idempotency key for retries.
    Stable per (sym, side, qty, minute bucket) to avoid duplicates on transient failures.
    """
    minute_bucket = int(time.time() // 60)
    raw = f"{symbol}|{side}|{qty}|{minute_bucket}"
    return "lavish_" + uuid.UUID(hashlib_md5(raw)).hex[:20]

def hashlib_md5(s: str) -> str:
    import hashlib
    return hashlib.md5(s.encode("utf-8", "ignore"), usedforsecurity=False).hexdigest()

def _soft_quote(symbol: str) -> Optional[float]:
    """
    Optional real quote if broker layer provides it.
    Soft-fails to None if not available.
    """
    if not HAS_ALPACA:
        return None
    try:
        from lavish_core.trade.broker import latest_quote
        q = latest_quote(symbol)
        return float(q) if q else None
    except Exception:
        pass
    return None

# ───────────────────────────────────────────────────────────────
# Risk Limits
# ───────────────────────────────────────────────────────────────

@dataclass
class RiskLimits:
    max_gross_exposure: float = 250000.0
    max_pos_per_symbol: float = 5000.0
    max_leverage: float = 2.0

@dataclass
class RiskContext:
    symbol: str
    side: str
    qty: float
    price: float
    prev_qty: float
    new_qty: float
    prev_gross_exposure: float
    new_gross_exposure: float
    added_gross_exposure: float
    account_equity: float | None
    violated: Optional[str] = None
    sanity_ref_price: Optional[float] = None
    sanity_deviation_pct: Optional[float] = None
    slippage_pct_max: float = MAX_SLIPPAGE_PCT

def load_risk_limits(store: HybridStore) -> RiskLimits:
    kv = store.get_risk_limits()
    return RiskLimits(
        max_gross_exposure=float(kv.get("max_gross_exposure", 250000.0)),
        max_pos_per_symbol=float(kv.get("max_pos_per_symbol", 5000.0)),
        max_leverage=float(kv.get("max_leverage", 2.0)),
    )

def ensure_risk_limits(store: HybridStore) -> None:
    if not store.get_risk_limits():
        store.set_risk_limits(
            {
                "max_gross_exposure": 250000.0,
                "max_pos_per_symbol": 5000.0,
                "max_leverage": 2.0,
            }
        )

# ───────────────────────────────────────────────────────────────
# Exposure estimation
# ───────────────────────────────────────────────────────────────

def _estimate_exposure_by_symbol(store: HybridStore) -> Dict[str, float]:
    rows = store.fetchall("SELECT symbol, qty, avg_price FROM positions")
    out: Dict[str, float] = {}
    for sym, qty, avg in rows:
        sym_u = str(sym).upper()
        import re
        multiplier = 100 if re.search(r"\d{6}[CP]\d{8}$", sym_u) else 1
        out[sym_u] = abs(float(qty) * float(avg)) * multiplier
    return out

def estimate_equity_and_exposure(store: HybridStore) -> Dict[str, float]:
    by_sym = _estimate_exposure_by_symbol(store)
    gross = sum(by_sym.values())
    return {"gross_exposure": gross}

def would_violate_limits(
    store: HybridStore,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    limits: RiskLimits,
    account_equity: Optional[float] = None,
) -> Optional[str]:
    sym = symbol.upper()
    try:
        qty = finite_number(qty, positive=True)
        price = finite_number(price, positive=True)
        for value in (limits.max_gross_exposure, limits.max_pos_per_symbol, limits.max_leverage):
            finite_number(value, positive=True)
        if account_equity is not None:
            finite_number(account_equity, positive=True)
    except ValueError:
        return "invalid_risk_input"
    side = side.lower()

    pos = store.get_position(sym) or {"qty": 0.0, "avg_price": 0.0}
    prev_qty = float(pos.get("qty", 0.0))
    new_qty = prev_qty + (qty if side == "buy" else -qty)

    if side == "sell" and prev_qty > 0 and 0 <= new_qty < prev_qty:
        return None
    if abs(new_qty) > limits.max_pos_per_symbol:
        return f"max_pos_per_symbol exceeded: {abs(new_qty)} > {limits.max_pos_per_symbol}"

    by_sym = _estimate_exposure_by_symbol(store)
    prev_symbol_exposure = by_sym.get(sym, 0.0)
    baseline_gross = sum(by_sym.values()) - prev_symbol_exposure

    new_symbol_exposure = abs(new_qty * price)
    new_gross = baseline_gross + new_symbol_exposure

    if account_equity is not None and account_equity > 0:
        if new_gross > limits.max_leverage * account_equity:
            return (
                f"max_leverage exceeded: gross {new_gross:.2f} "
                f"> {limits.max_leverage}× equity {account_equity:.2f}"
            )

    if new_gross > limits.max_gross_exposure:
        return f"max_gross_exposure exceeded: {new_gross:.2f} > {limits.max_gross_exposure:.2f}"

    return None

# ───────────────────────────────────────────────────────────────
# Broker retry wrapper
# ───────────────────────────────────────────────────────────────

def _with_retries(fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """Retries a broker call that carries no client_order_id to look up
    (e.g. a GET). For order *submission*, use _submit_with_retries instead -
    a bare retry-on-exception here can't tell "the POST timed out before
    reaching Alpaca" from "the POST succeeded and only the response was
    lost", and blindly resubmitting risks a real duplicate order."""
    last_err: Optional[Exception] = None
    for attempt in range(RETRY_MAX + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            sleep_s = RETRY_BASE * (2 ** attempt)
            LOG.warning("Broker call failed (attempt %d/%d): %s. Sleeping %.2fs",
                        attempt + 1, RETRY_MAX + 1, e, sleep_s)
            time.sleep(sleep_s)
    raise RuntimeError(f"Broker retries exhausted: {last_err}")


class SubmissionUnknown(RuntimeError):
    """A submission outcome requires reconciliation before another POST."""


def _submit_with_retries(submit_fn: Callable[[], Dict[str, Any]], client_order_id: str) -> Dict[str, Any]:
    """
    Order submission's own retry wrapper. The gap a bare retry-on-exception
    leaves open: a POST that times out or drops its response after the
    order actually reached Alpaca either opens a second real position (if
    Alpaca somehow accepted a resubmit) or gets rejected as a duplicate
    client_order_id and - with nothing to distinguish that from any other
    failure - eventually gives up and marks the trade "rejected" locally
    even though a real order exists at the broker. Before every retry,
    this checks whether client_order_id already resolves to a real order
    first; only submits again if it genuinely doesn't exist yet.
    """
    last_err: Optional[Exception] = None
    for attempt in range(RETRY_MAX + 1):
        try:
            return submit_fn()
        except Exception as e:
            last_err = e
            LOG.warning("Order submission failed (attempt %d/%d): %s", attempt + 1, RETRY_MAX + 1, e)
            try:
                existing = broker_get_order_by_client_id(client_order_id)
                if existing:
                    LOG.warning(
                        "Found an existing order %s for client_order_id=%s after a failed submit - "
                        "using it instead of resubmitting.", existing.get("id"), client_order_id,
                    )
                    return existing
            except Exception as lookup_err:
                raise SubmissionUnknown("Order outcome unknown; reconcile client_order_id") from lookup_err
            from lavish_core.trade.broker import BROKER_PROVIDER
            if BROKER_PROVIDER == "tradier":
                raise SubmissionUnknown("Tradier tag cannot prove order idempotency") from e
            sleep_s = RETRY_BASE * (2 ** attempt)
            time.sleep(sleep_s)
    raise RuntimeError(f"Broker retries exhausted: {last_err}")

# ───────────────────────────────────────────────────────────────
# Unified Trade Entry
# ───────────────────────────────────────────────────────────────

def place_trade(
    store: HybridStore,
    symbol: str,
    side: str,
    qty: float,
    mode: str = "dry",
    order_type: str = DEFAULT_TYPE,
    limit_price: Optional[float] = None,
    tif: str = DEFAULT_TIF,
    client_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    take_profit: Optional[float] = None,
    stop_loss: Optional[float] = None,
) -> Dict[str, Any]:
    symbol = symbol.upper().strip()
    side = side.lower().strip()
    tif = tif.lower().strip()
    mode = mode.lower().strip()
    order_type = (order_type or DEFAULT_TYPE).lower().strip()
    meta = dict(meta or {})

    if side not in ("buy", "sell"):
        raise ValueError(f"Invalid side: {side}")
    qty = finite_number(qty, positive=True)
    if mode not in ("dry", "paper", "live"):
        raise ValueError("Unknown trading mode")
    for value in (limit_price, take_profit, stop_loss):
        if value is not None:
            finite_number(value, positive=True)
    if mode in ("paper", "live"):
        from lavish_core.trade.broker import BROKER_PROVIDER
        from importlib import import_module
        provider = import_module("lavish_core.trade.broker_" + BROKER_PROVIDER)
        check_trading_endpoint(mode, provider.BASE_URL, BROKER_PROVIDER)

    if side == "buy" and mode == "live":
        from lavish_core.trade.live_readiness import assert_live_ready
        assert_live_ready()

    # Circuit breaker only blocks new risk-taking (buys), never closes/sells -
    # you should always be able to get out of a position, even mid-halt.
    if side == "buy" and mode in ("paper", "live"):
        cb_ok, cb_reason = _circuit_breaker_check_ok(store)
        if not cb_ok:
            oid = store.submit_order(
                symbol=symbol, side=side, qty=qty, order_type=order_type,
                limit_price=limit_price, tif=tif, venue=mode, status="rejected",
                client_id=client_id, meta={"reason": f"circuit_breaker: {cb_reason}", **meta},
            )
            LOG.error("REJECTED %s %s×%s: circuit_breaker: %s", side.upper(), qty, symbol, cb_reason)
            return {"status": "rejected", "order_id": oid, "reason": f"circuit_breaker: {cb_reason}"}

    # Stable client order id for idempotency
    if not client_id:
        # stable within 1-minute buckets
        minute_bucket = int(time.time() // 60)
        client_id = sha1_str(f"{symbol}|{side}|{qty}|{minute_bucket}")[:24]

    # Reference price for risk + dry fills
    if order_type == "limit" and limit_price is not None:
        ref_price = float(limit_price)
    else:
        ref_price = _dry_price(symbol)

    # If broker quote exists, use it for sanity
    real_quote = _soft_quote(symbol)
    if real_quote is not None:
        try:
            ref_price = finite_number(real_quote, positive=True)
        except ValueError:
            real_quote = None
    if mode in ("paper", "live") and real_quote is None:
        oid = store.submit_order(symbol, side, qty, venue=mode, status="rejected", client_id=client_id, meta={"reason":"no_real_quote_available"})
        return {"status":"rejected", "order_id":oid, "reason":"no_real_quote_available"}

    if take_profit is not None or stop_loss is not None:
        meta.setdefault("take_profit", take_profit)
        meta.setdefault("stop_loss", stop_loss)

    if side == "buy" and mode == "live":
        if order_type == "market":
            order_type="limit"
            limit_price=round(ref_price*(1+MAX_SLIPPAGE_PCT),2)
        ref_price=finite_number(limit_price,positive=True)
        assert_live_ready(notional=qty*ref_price)
    limits = load_risk_limits(store)
    acct_equity = None
    if HAS_ALPACA and mode in ("paper", "live"):
        try:
            acct = get_account()
            acct_equity = finite_number(acct.get("equity"), positive=True)
        except Exception as e:
            if side == "buy":
                oid = store.submit_order(symbol, side, qty, venue=mode, status="rejected", client_id=client_id, meta={"reason":"account_unavailable"})
                return {"status":"rejected", "order_id":oid, "reason":"account_unavailable"}

    if side == "buy" and mode == "live":
        from lavish_core.trade.live_readiness import approved_entry_limits
        approved=approved_entry_limits(store,qty*ref_price,acct_equity,acct.get("id") or acct.get("account_number"))
        limits.max_gross_exposure=min(limits.max_gross_exposure,approved["max_gross_exposure"])

    # Build risk ctx
    by_sym = _estimate_exposure_by_symbol(store)
    prev_symbol_exposure = by_sym.get(symbol, 0.0)
    baseline_gross = sum(by_sym.values()) - prev_symbol_exposure

    pos = store.get_position(symbol) or {"qty": 0.0, "avg_price": 0.0}
    prev_qty = float(pos.get("qty", 0.0))
    new_qty = prev_qty + (qty if side == "buy" else -qty)
    new_symbol_exposure = abs(new_qty * ref_price)
    new_gross = baseline_gross + new_symbol_exposure
    added_gross = max(0.0, new_gross - (baseline_gross + prev_symbol_exposure))

    risk_ctx = RiskContext(
        symbol=symbol,
        side=side,
        qty=qty,
        price=ref_price,
        prev_qty=prev_qty,
        new_qty=new_qty,
        prev_gross_exposure=sum(by_sym.values()),
        new_gross_exposure=new_gross,
        added_gross_exposure=added_gross,
        account_equity=acct_equity,
    )

    # Price sanity vs limit price if both exist
    if limit_price is not None and real_quote is not None:
        dev_pct = abs(real_quote - float(limit_price)) / max(1e-9, real_quote)
        risk_ctx.sanity_ref_price = real_quote
        risk_ctx.sanity_deviation_pct = dev_pct
        if dev_pct > MAX_SANITY_PCT:
            violation = (
                f"price_sanity exceeded: deviation {dev_pct:.4f} "
                f"> {MAX_SANITY_PCT:.4f}"
            )
            risk_ctx.violated = violation
            meta.setdefault("risk_context", asdict(risk_ctx))
            oid = store.submit_order(
                symbol=symbol,
                side=side,
                qty=qty,
                order_type=order_type,
                limit_price=limit_price,
                tif=tif,
                venue=mode,
                status="rejected",
                client_id=client_id,
                meta={"reason": violation, **meta},
            )
            LOG.error("REJECTED %s %s×%s: %s", side.upper(), qty, symbol, violation)
            return {"status": "rejected", "order_id": oid, "reason": violation}

    violation = would_violate_limits(
        store, symbol, side, qty, ref_price, limits, account_equity=acct_equity
    )
    risk_ctx.violated = violation
    meta.setdefault("risk_context", asdict(risk_ctx))

    if violation:
        oid = store.submit_order(
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
            tif=tif,
            venue=mode,
            status="rejected",
            client_id=client_id,
            meta={"reason": violation, **meta},
        )
        LOG.error("REJECTED %s %s×%s: %s", side.upper(), qty, symbol, violation)
        return {"status": "rejected", "order_id": oid, "reason": violation}

    if side == "buy":
        reservation_reason = store.reserve_entry(client_id, symbol, qty, ref_price, limits, acct_equity)
        if reservation_reason:
            oid = store.submit_order(symbol, side, qty, venue=mode, status="rejected", client_id=client_id, meta={"reason":reservation_reason})
            return {"status":"rejected", "order_id":oid, "reason":reservation_reason}

    # ── DRY MODE ────────────────────────────────────────────────
    if mode == "dry":
        oid = store.submit_order(
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
            tif=tif,
            venue="dry_run",
            status="submitted",
            client_id=client_id,
            meta=meta,
        )
        price = ref_price
        fid = store.log_fill(
            order_id=oid,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            fee=0.00,
            venue="dry_run",
            reservation_client_id=client_id,
        )
        LOG.info("DRY FILL %s %s×%s @ %.2f (order=%s fill=%s)",
                 side.upper(), qty, symbol, price, oid, fid)
        return {"status": "filled", "order_id": oid, "fill_id": fid, "price": price}

    # ── REAL BROKER ─────────────────────────────────────────────
    if mode in ("paper", "live"):
        if not HAS_ALPACA:
            LOG.error("Alpaca broker unavailable.")
            oid = store.submit_order(
                symbol=symbol,
                side=side,
                qty=qty,
                order_type=order_type,
                limit_price=limit_price,
                tif=tif,
                venue=mode,
                status="rejected",
                client_id=client_id,
                meta={"reason": "broker_unavailable", **meta},
            )
            return {"status": "rejected", "order_id": oid, "reason": "broker_unavailable"}

        if side == "buy" and store.get_kill_switch()[0]:
            store.release_entry(client_id)
            oid = store.submit_order(symbol, side, qty, venue=mode, status="rejected", client_id=client_id, meta={"reason":"kill_switch"})
            return {"status":"rejected", "order_id":oid, "reason":"kill_switch"}

        def _submit():
            return broker_place_order(
                symbol=symbol,
                side=side,
                qty=qty,
                type_=order_type,
                time_in_force=tif,
                limit_price=limit_price,
                take_profit=take_profit,
                stop_loss=stop_loss,
                client_order_id=client_id or None,
            )

        oid = store.submit_order(symbol, side, qty, order_type=order_type, limit_price=limit_price, tif=tif, venue=mode, status="pending_submit", client_id=client_id, meta=meta)
        broker_resp = None
        try:
            broker_resp = _submit_with_retries(_submit, client_id)
            def verify_identity(snapshot, expected_id=None):
                if (not isinstance(snapshot, dict) or not snapshot.get('id')
                        or (expected_id is not None and str(snapshot['id']) != str(expected_id))
                        or ('symbol' in snapshot and snapshot['symbol'] != symbol)
                        or ('side' in snapshot and snapshot['side'] != side)
                        or ('client_order_id' in snapshot and snapshot['client_order_id'] != client_id)
                        or ('qty' in snapshot and finite_number(snapshot['qty'], positive=True) != qty)):
                    raise ValueError('Equity execution identity mismatch')
            if not isinstance(broker_resp, dict) or not broker_resp.get('id'):
                raise SubmissionUnknown('Broker response lacks order ID')
            verify_identity(broker_resp)
            broker_oid = broker_resp.get("id")
            if not broker_oid:
                raise SubmissionUnknown("Broker response lacks order ID")
            client_ord_id = client_id or broker_resp.get("client_order_id")

            # Market orders usually fill within a couple seconds; poll briefly
            # so we record what actually happened instead of assuming "submitted".
            final_status = broker_resp.get("status", "submitted")
            filled_qty = broker_resp.get("filled_qty")
            filled_avg_price = broker_resp.get("filled_avg_price")
            filled_fee = broker_resp.get('filled_fee', 0)
            if broker_oid:
                for _ in range(FILL_POLL_ATTEMPTS):
                    if final_status in ("filled", "canceled", "expired", "rejected"):
                        break
                    time.sleep(FILL_POLL_INTERVAL_SEC)
                    try:
                        polled = broker_get_order(broker_oid)
                        if not isinstance(polled, dict):
                            raise ValueError('Malformed equity order poll')
                        verify_identity(dict(broker_resp, **polled), broker_oid)
                        final_status = polled.get("status", final_status)
                        filled_qty = polled.get("filled_qty", filled_qty)
                        filled_avg_price = polled.get("filled_avg_price", filled_avg_price)
                        filled_fee = polled.get('filled_fee', filled_fee)
                        broker_resp = dict(broker_resp, **polled)
                    except ValueError:
                        raise
                    except Exception as e:
                        LOG.warning("Order status poll failed for %s: %s", broker_oid, e)
                        break

            if final_status not in ('filled','partially_filled','canceled','expired','rejected',
                                    'new','pending_new','accepted','submitted','pending_cancel','pending'):
                raise ValueError('Unknown equity execution status')
            actual_qty = finite_number(filled_qty if filled_qty is not None else 0, nonnegative=True)
            execution_fee = finite_number(filled_fee, nonnegative=True)
            if actual_qty > qty or (final_status == 'filled' and actual_qty != qty):
                raise ValueError('Equity cumulative fill does not match request')
            if actual_qty > 0:
                actual_price = finite_number(filled_avg_price, positive=True)
            elif execution_fee > 0:
                raise ValueError('Equity fee without a fill')
            store.set_order_status(oid, final_status)
            store.execute("UPDATE orders SET meta=? WHERE id=?", (json.dumps({"broker_id":broker_oid,"raw":broker_resp,"expected_price":ref_price,**meta}), oid))
            if actual_qty > 0:
                # Real gap found and fixed: this used to only log a fill
                # when status was EXACTLY "filled" - a partial fill (rare
                # for a liquid, small-size market order, but not
                # impossible) was never recorded in the local positions/
                # fills ledger at all, even though real shares were
                # actually held at the broker.
                actual_price = finite_number(filled_avg_price, positive=True)
                slippage = round(actual_price - ref_price, 4)
                store.log_fill(
                    order_id=oid,
                    symbol=symbol,
                    side=side,
                    qty=actual_qty,
                    price=actual_price,
                    fee=execution_fee,
                    venue=mode,
                    event_id=f"entry:{broker_oid or client_id}:{side}",
                    reservation_client_id=client_id if final_status == "filled" else None,
                )
                store.set_order_status(oid, final_status)
                if abs(slippage) > 0:
                    LOG.info("Slippage %s %s: expected %.2f actual %.2f (%.4f)",
                             side.upper(), symbol, ref_price, actual_price, slippage)
                if final_status == "partially_filled" and broker_oid:
                    # A resting remainder left open after this bot's own
                    # poll budget could fill hours later, unpredictably,
                    # against conditions none of this alert's own checks
                    # (confidence floor, kill switch, price-drift gate,
                    # risk gates) were ever re-validated against. Cancel
                    # it; the confirmed partial fill above is final.
                    try:
                        if broker_cancel_order(broker_oid):
                            store.set_order_status(oid, 'pending_cancel')
                        LOG.info("Requested cancellation of unfilled remainder for %s; awaiting terminal confirmation.", symbol)
                    except Exception as e:
                        LOG.warning("Cancel of partially-filled order remainder for %s failed: %s", symbol, e)
            if final_status in ("canceled", "expired", "rejected"):
                store.release_entry(client_id)
            LOG.info("%s %s %s×%s (order=%s broker_id=%s client_id=%s status=%s)",
                     mode.upper(), final_status.upper(), qty, symbol, oid, broker_oid, client_ord_id, final_status)
            return {
                "status": final_status,
                "order_id": oid,
                "broker_id": broker_oid,
                "client_order_id": client_ord_id,
                "filled_qty": filled_qty,
                "filled_avg_price": filled_avg_price,
                # Bracket orders (take_profit/stop_loss set) come back with
                # child leg orders under broker_resp["legs"] - the caller
                # needs their IDs to watch for which leg eventually fills
                # (see equity_exit_monitor.py). Not present on a plain order.
                "legs": broker_resp.get("legs") or [],
            }

        except Exception as e:
            unknown = isinstance(e, SubmissionUnknown) or broker_resp is not None
            status = "submission_unknown" if isinstance(e, SubmissionUnknown) else ("fill_unverified" if unknown else "rejected")
            store.set_order_status(oid, status)
            if status == 'fill_unverified':
                store.set_kill_switch(True, 'Equity execution snapshot unverified; broker reconciliation required')
            if not unknown:
                store.release_entry(client_id)
            return {"status":status, "order_id":oid, "reason":"reconciliation_required" if unknown else f"broker_error: {e}"}

    raise ValueError("mode must be one of: dry, paper, live")

def sha1_str(s: str) -> str:
    import hashlib
    return hashlib.sha1(s.encode("utf-8", "ignore"), usedforsecurity=False).hexdigest()

# ───────────────────────────────────────────────────────────────
# TradeAgent class (SuperCore-compatible)
# ───────────────────────────────────────────────────────────────

class TradeAgent:
    """
    SuperCore expects:
        agent.place_order(symbol, side, qty, meta)
    This wraps place_trade() cleanly.

    Default mode:
      - if LIVE_TRADE=true -> "live"
      - else -> "dry"
    """
    def __init__(
        self,
        store: Optional[HybridStore] = None,
        bus: Optional[Any] = None
    ):
        self.store = store or HybridStore(
            duckdb_path=str(DEFAULT_DB),
            redis_url=os.environ.get("REDIS_URL") or None
        )
        ensure_risk_limits(self.store)
        self.bus = bus
        self.default_mode = "live" if env("LIVE_TRADE", "false").lower() == "true" else "dry"

    def place_order(self, symbol: str, side: str, qty: float, meta: Optional[Dict[str, Any]] = None):
        out = place_trade(
            store=self.store,
            symbol=symbol,
            side=side,
            qty=qty,
            mode=self.default_mode,
            order_type=DEFAULT_TYPE,
            tif=DEFAULT_TIF,
            meta=meta or {},
        )
        if self.bus:
            try:
                self.bus.publish("trade_agent_update", {"symbol": symbol, "side": side, "qty": qty, "result": out})
            except Exception:
                pass
        return out

    # Emergency flatten (manual use)
    def flatten_all(self, reason: str = "manual_kill"):
        """
        Emergency: close every open position. In dry mode, "closes"
        whatever this process's own local ledger (the `positions` table)
        shows - no real broker call, same as every other dry-mode path in
        this bot. In paper/live mode, reads the REAL broker position list
        instead: previously this always read the local `positions` table,
        which is equity-only (options fills are never written there -
        reconcile.py already treats the broker's own list as the only
        real source of truth for options) and could itself be stale
        relative to the broker. An emergency "close everything" that
        silently skipped every open option contract, or closed against a
        stale qty, would be worse than useless. One position failing to
        close doesn't abort the rest - each is attempted independently.
        """
        if self.default_mode == "dry":
            rows = self.store.fetchall("SELECT symbol, qty FROM positions")
            closed = []
            for sym, q in rows:
                sym = str(sym).upper()
                q = float(q)
                if q == 0:
                    continue
                side = "sell" if q > 0 else "buy"
                closed.append(place_trade(self.store, sym, side, abs(q), mode=self.default_mode,
                                         order_type="market", meta={"reason": reason, "flatten": True}))
            return {"status": "flattened", "n": len(closed), "details": closed}

        if not HAS_ALPACA:
            LOG.error("flatten_all: Alpaca client unavailable - cannot read real broker positions.")
            return {"status": "error", "reason": "broker unavailable", "n": 0, "details": []}

        from lavish_core.trade.broker import get_positions, place_option_order, latest_option_quote

        try:
            positions = get_positions()
        except Exception as e:
            LOG.error("flatten_all: could not read broker positions: %s", e)
            return {"status": "error", "reason": f"could not read broker positions: {e}", "n": 0, "details": []}

        closed = []
        for pos in positions:
            symbol = str(pos.get("symbol", "")).upper()
            qty = float(pos.get("qty", 0) or 0)
            if not symbol or qty == 0:
                continue
            side = "sell" if qty > 0 else "buy"
            try:
                if pos.get("asset_class") == "us_option":
                    quote = latest_option_quote(symbol)
                    if not quote or not quote.get("bid") or not quote.get("ask"):
                        LOG.error("flatten_all: no usable quote for %s - could not close, needs manual attention.", symbol)
                        closed.append({"status": "error", "symbol": symbol, "reason": "no usable quote"})
                        continue
                    limit_price = round((quote["bid"] + quote["ask"]) / 2, 2)
                    closed.append(place_option_order(symbol, side, abs(qty), order_type="limit",
                                                       limit_price=limit_price))
                else:
                    closed.append(place_trade(self.store, symbol, side, abs(qty), mode=self.default_mode,
                                               order_type="market", meta={"reason": reason, "flatten": True}))
            except Exception as e:
                LOG.error("flatten_all: failed to close %s: %s", symbol, e)
                closed.append({"status": "error", "symbol": symbol, "reason": str(e)})
        return {"status": "flattened", "n": len(closed), "details": closed}

# ───────────────────────────────────────────────────────────────
# CLI test harness
# ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Lavish Trade Agent v3")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell", "BUY", "SELL"])
    p.add_argument("--qty", required=True, type=float)
    p.add_argument("--mode", default=None, choices=["dry", "paper", "live"])
    p.add_argument("--duckdb", default=str(DEFAULT_DB))
    args = p.parse_args()

    store = HybridStore(duckdb_path=args.duckdb, redis_url=os.environ.get("REDIS_URL") or None)
    ensure_risk_limits(store)

    out = place_trade(
        store=store,
        symbol=args.symbol,
        side=args.side.lower(),
        qty=args.qty,
        mode=args.mode or ("live" if env("LIVE_TRADE", "false") == "true" else "dry"),
    )
    print(out)
