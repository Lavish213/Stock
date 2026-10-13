# lavish_core/trade/options_broker_tradier.py
# Tradier equivalent of options_broker.py - same function names/
# signatures (find_contract, latest_option_quote, place_option_order,
# resolve_and_price_contract), so trade_handler.py and the exit monitors
# work against either broker through lavish_core.trade.broker's dispatch.
#
# API shapes from Tradier's public developer docs, researched directly
# this session (sandbox base, bearer auth, /markets/options/chains,
# /accounts/{id}/orders with class=option) - same "correct by research,
# not yet confirmed by a real sandbox call" caveat as broker_tradier.py.
from __future__ import annotations
import os
import json, logging, re, time, uuid
from lavish_core.utils.safety import finite_number
from datetime import date
from typing import Optional, Dict, Any, List

from lavish_core.trade.broker_tradier import HEADERS, BASE_URL, SESSION, _check_keys, _account_id
from lavish_core.trade.options_broker import build_occ_symbol  # pure OCC-format helper, not Alpaca-specific

log = logging.getLogger("options_broker_tradier")


def find_contract(
    ticker: str,
    expiry: date,
    side: str,
    strike: float,
    strike_tolerance: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """
    Looks up the real tradable contract via Tradier's options chain for
    this underlying+expiry, picking the closest listed strike to the
    requested one - same reasoning as options_broker.py's Alpaca version
    (a parsed/OCR'd strike won't always match a real listed increment).
    """
    _check_keys()
    strike = finite_number(strike, positive=True)
    tolerance = finite_number(strike_tolerance, nonnegative=True)
    option_type = "call" if side.lower().startswith("c") else "put"
    r = SESSION.get(
        f"{BASE_URL}/markets/options/chains", headers=HEADERS,
        params={"symbol": ticker.upper(), "expiration": expiry.isoformat(), "greeks": "false"},
        timeout=20,
    )
    if r.status_code != 200:
        log.warning("options chain lookup failed %s: %s", r.status_code, r.text[:300])
        return None

    chain = r.json().get("options")
    if not isinstance(chain, dict):
        return None
    contracts = chain.get("option")
    if contracts is None:
        return None
    if not isinstance(contracts, list):
        contracts = [contracts]
    def matches(c):
        if not isinstance(c, dict) or c.get('option_type') != option_type:
            return False
        try:
            listed = finite_number(c.get('strike'), positive=True)
            if abs(listed - strike) > tolerance:
                return False
            if c.get('symbol') != build_occ_symbol(ticker, expiry, side, listed):
                return False
            if 'expiration_date' in c and c['expiration_date'] != expiry.isoformat():
                return False
            if 'underlying' in c and c['underlying'] != ticker.upper():
                return False
            return True
        except (ValueError, TypeError, OverflowError):
            return False
    contracts = [c for c in contracts if matches(c)]
    if not contracts:
        return None

    best = min(contracts, key=lambda c: abs(float(c.get("strike", 0)) - strike))
    deviation = abs(float(best.get("strike", 0)) - strike)
    if deviation > tolerance:
        log.warning("Closest Tradier contract for %s %s $%.2f is $%.2f away (tolerance %.2f) - rejecting.",
                    ticker, side, strike, deviation, strike_tolerance)
        return None

    return {
        "symbol": best['symbol'],
        "strike_price": float(best.get("strike", strike)),
    }


def latest_option_quote(contract_symbol: str) -> Optional[Dict[str, float]]:
    """Best bid/ask for a contract via /markets/quotes (same endpoint as
    equity quotes - Tradier doesn't split options onto a separate quote
    path the way Alpaca's data API does)."""
    _check_keys()
    try:
        r = SESSION.get(f"{BASE_URL}/markets/quotes", headers=HEADERS,
                         params={"symbols": contract_symbol, "greeks": "false"}, timeout=10)
        if r.status_code != 200:
            return None
        q = r.json().get("quotes", {}).get("quote")
        if isinstance(q, list):
            q = q[0] if q else None
        if not q:
            return None
        bid, ask = q.get("bid"), q.get("ask")
        if bid is None or ask is None:
            return None
        bid = finite_number(bid, nonnegative=True)
        ask = finite_number(ask, positive=True)
        if ask < bid:
            return None
        return {"bid": bid, "ask": ask}
    except Exception as e:
        log.warning("latest_option_quote failed for %s: %s", contract_symbol, e)
        return None


def place_option_order(
    contract_symbol: str,
    side: str,
    qty: int,
    order_type: str = "limit",
    limit_price: Optional[float] = None,
    time_in_force: str = "day",
    client_order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Single-leg option order via POST /accounts/{id}/orders, class=option.
    side here is this bot's own "buy"/"sell" (open/close a long only -
    this bot never opens a short option position) - translated to
    Tradier's buy_to_open/sell_to_close, the only two this bot ever
    actually needs given its long-only design.
    """
    _check_keys()
    if os.getenv("TRADE_MODE","dry") == "live" and side.lower() == "buy":
        from lavish_core.trade.live_readiness import assert_live_ready
        assert_live_ready()
        if order_type != "limit" or limit_price is None:
            raise RuntimeError("Live option entries require bounded limit prices")
        assert_live_ready(notional=finite_number(qty,positive=True)*finite_number(limit_price,positive=True)*100,account_id=_account_id())

    quantity = finite_number(qty, positive=True)
    if not quantity.is_integer():
        raise ValueError("Option quantities must be whole contracts")
    if limit_price is not None:
        finite_number(limit_price, positive=True)
    side = side.lower()
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    if order_type == "limit" and limit_price is None:
        raise ValueError("limit_price is required for limit orders")
    tradier_side = "buy_to_open" if side == "buy" else "sell_to_close"

    if not client_order_id:
        minute_bucket = int(time.time() // 60)
        raw = f"{contract_symbol}|{side}|{qty}|{order_type}|{minute_bucket}"
        client_order_id = "lavish_opt_" + uuid.uuid5(uuid.NAMESPACE_OID, raw).hex[:16]

    # Leading letters ONLY - an OCC symbol is TICKER+YYMMDD+C/P+strike, so
    # a naive "every alpha character" grab (the original approach here)
    # also picks up the C/P call/put letter itself: "AAPL260116C00200000"
    # -> "AAPLC", not "AAPL". Caught by test_options_broker_tradier.py
    # before this ever reached a real order submission.
    underlying_match = re.match(r"^[A-Z]+", contract_symbol)
    underlying = underlying_match.group(0) if underlying_match else contract_symbol
    acct_id = _account_id()
    payload = {
        "class": "option",
        "symbol": underlying,
        "option_symbol": contract_symbol,
        "side": tradier_side,
        "quantity": str(int(qty)),
        "type": order_type,
        "duration": time_in_force,
        "tag": client_order_id,
    }
    if order_type == "limit":
        payload["price"] = str(limit_price)

    log.info("[Tradier options] place_order %s", json.dumps(payload))
    r = SESSION.post(f"{BASE_URL}/accounts/{acct_id}/orders", headers=HEADERS, data=payload, timeout=20)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Tradier options order error {r.status_code}: {r.text}")
    order = r.json().get("order", {})
    return {
        "id": str(order.get("id", "")),
        "status": order.get("status"),
        "filled_qty": order.get("exec_quantity"),
        "filled_avg_price": order.get("avg_fill_price"),
        "client_order_id": client_order_id,
    }


def resolve_and_price_contract(
    ticker: str, expiry: date, side: str, strike: float, strike_tolerance: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """Convenience: find the real contract, then attach a current quote/
    midpoint to it - same shape as options_broker.py's Alpaca version."""
    contract = find_contract(ticker, expiry, side, strike, strike_tolerance=strike_tolerance)
    if not contract:
        return None
    quote = latest_option_quote(contract["symbol"])
    if quote and quote["bid"] and quote["ask"]:
        contract["mid_price"] = round((quote["bid"] + quote["ask"]) / 2, 2)
        contract["bid"] = quote["bid"]
        contract["ask"] = quote["ask"]
    return contract
