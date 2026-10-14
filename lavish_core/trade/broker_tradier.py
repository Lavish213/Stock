# lavish_core/trade/broker_tradier.py
# Tradier equivalent of broker_alpaca.py - same function names/signatures,
# translating Tradier's own response shapes into the Alpaca-shaped dicts
# the rest of this codebase already reads (acct["equity"], pos["qty"],
# pos["asset_class"], order["filled_qty"], etc.), so trade_handler.py,
# circuit_breaker.py, reconcile.py, and everything else can run against
# either broker through lavish_core.trade.broker's dispatch without
# knowing which one is live.
#
# API shapes here are from Tradier's public developer docs (sandbox base
# https://sandbox.tradier.com/v1, production https://api.tradier.com/v1,
# bearer-token auth) - researched directly this session, not guessed, but
# NOT verified against a real sandbox call (this environment can't reach
# Tradier's API any more than it can reach Alpaca's - confirmed the same
# network block applies). Treat this the same way as every other piece of
# this codebase that's correct-by-research rather than confirmed-by-real-
# call: the first real sandbox test is still the one that actually proves
# it, same as it would be for a change to the Alpaca side.
from __future__ import annotations
import os, json, logging, re, time, uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from lavish_core.utils.safety import finite_number, valid_positive, check_trading_endpoint
import requests
from lavish_core.utils.broker_transport import BrokerSession
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("broker_tradier")

API_TOKEN = os.getenv("TRADIER_API_KEY", "")
BASE_URL = os.getenv("TRADIER_BASE_URL", "https://sandbox.tradier.com/v1").rstrip("/")
# Tradier scopes every account/order call under /accounts/{account_id}/... -
# resolved once from /user/profile (see _account_id()) unless explicitly
# overridden, since a token can be tied to more than one account.
_ACCOUNT_ID_OVERRIDE = os.getenv("TRADIER_ACCOUNT_ID", "").strip()

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Accept": "application/json",
}

# Same reasoning as broker_alpaca.py's retry policy: GET-only, POST (order
# submission) is never blindly retried here - that's handled via a stable
# idempotency tag instead (see place_order).
_retry = Retry(
    total=3, connect=3, read=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,
    respect_retry_after_header=True,
)
SESSION = BrokerSession('tradier')
SESSION.mount("https://", HTTPAdapter(max_retries=_retry))
SESSION.mount("http://", HTTPAdapter(max_retries=_retry))

# Option symbols are OCC format (e.g. AAPL260116C00200000) - same pattern
# reconcile.py already uses to tell an option position's symbol apart
# from a plain equity ticker. Tradier doesn't return an explicit
# "asset_class" field the way Alpaca does, so this is how it's inferred
# here instead.
_OCC_RE = re.compile(r"^[A-Z]+\d{6}[CP]\d{8}$")


def _check_keys():
    check_trading_endpoint(os.getenv("TRADE_MODE", "dry"), BASE_URL, "tradier")
    if not API_TOKEN:
        raise RuntimeError("Missing TRADIER_API_KEY")


_account_id_cache: Optional[str] = None


def _account_id() -> str:
    """
    Resolves and caches the account number tied to this token, from
    GET /user/profile. profile.account can be a single object or a list
    depending on how many accounts the token has access to (confirmed
    via Tradier's own community examples, not just assumed) - this
    always normalizes to a list and requires an unambiguous account unless
    TRADIER_ACCOUNT_ID explicitly picks one.
    """
    global _account_id_cache
    if _ACCOUNT_ID_OVERRIDE:
        return _ACCOUNT_ID_OVERRIDE
    if _account_id_cache:
        return _account_id_cache
    _check_keys()
    r = SESSION.get(f"{BASE_URL}/user/profile", headers=HEADERS, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Tradier user/profile error {r.status_code}: {r.text}")
    accounts = r.json().get("profile", {}).get("account")
    if accounts is None:
        raise RuntimeError("Tradier user/profile returned no accounts for this token")
    if not isinstance(accounts, list):
        accounts = [accounts]
    if not accounts:
        raise RuntimeError("Tradier user/profile returned an empty account list")
    if len(accounts) != 1:
        raise RuntimeError('Multiple Tradier accounts; configure TRADIER_ACCOUNT_ID explicitly')
    if not isinstance(accounts[0], dict) or not accounts[0].get('account_number'):
        raise RuntimeError('Tradier account identity unavailable')
    _account_id_cache = accounts[0]["account_number"]
    return _account_id_cache


def get_account() -> Dict[str, Any]:
    """
    GET /accounts/{id}/balances, translated into Alpaca's field names
    (equity/cash/buying_power) so every caller (circuit_breaker.py,
    pdt_guard.py, account_monitor.py) works unmodified. buying_power maps
    to stock_buying_power - option_buying_power is usually lower/more
    conservative and is a separate, options-specific limit Alpaca doesn't
    split out the same way; stock_buying_power is the closer match to
    what Alpaca's single buying_power field represents for this bot's
    purposes (equity position sizing).
    """
    _check_keys()
    acct_id = _account_id()
    r = SESSION.get(f"{BASE_URL}/accounts/{acct_id}/balances", headers=HEADERS, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Tradier balances error {r.status_code}: {r.text}")
    bal = r.json().get("balances", {})
    return {
        "id": str(acct_id),
        "equity": bal.get("total_equity"),
        "cash": bal.get("total_cash"),
        "buying_power": bal.get("stock_buying_power") or bal.get("option_buying_power"),
        # No Tradier field confirmed yet for "prior day's closing equity" -
        # account_monitor.py already falls back to current equity (0 day
        # P&L for that snapshot) when this key is absent, so omitting it
        # rather than guessing is the safe choice.
    }


def get_clock() -> Dict[str, Any]:
    """
    GET /markets/clock, translated from Tradier's state enum (premarket/
    open/postmarket/closed) into Alpaca's boolean is_open - every caller
    (trade_handler.py's market-hours gate, options_exit_monitor.py's
    minutes-to-close calc) reads is_open/next_close, not a state string.
    """
    _check_keys()
    r = SESSION.get(f"{BASE_URL}/markets/clock", headers=HEADERS, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Tradier clock error {r.status_code}: {r.text}")
    clk = r.json().get("clock", {})
    return {
        "is_open": clk.get("state") == "open",
        "next_open": clk.get("next_change"),
        "next_close": clk.get("next_change"),
        "state": clk.get("state"),
    }


def _normalize_position(p: Dict[str, Any]) -> Dict[str, Any]:
    symbol = str(p.get("symbol", ""))
    qty = float(p.get("quantity", 0) or 0)
    is_option = bool(_OCC_RE.match(symbol))
    # cost_basis is Tradier's TOTAL dollar cost for the position, not a
    # per-share/per-contract average - divide back out to match Alpaca's
    # avg_entry_price semantics. Multiplier is 100 for options (standard
    # contract size), 1 for equity. Flagged here specifically because
    # this exact semantic (total vs. per-unit) wasn't confirmed against a
    # real Tradier response this session - verify against the real
    # sandbox before trusting avg_entry_price-derived P&L math under
    # Tradier.
    multiplier = 100.0 if is_option else 1.0
    cost_basis = float(p.get("cost_basis", 0) or 0)
    avg_entry_price = abs(cost_basis / (qty * multiplier)) if qty else 0.0
    return {
        "symbol": symbol,
        "qty": qty,
        "asset_class": "us_option" if is_option else "us_equity",
        "side": "long" if qty > 0 else "short",
        "avg_entry_price": avg_entry_price,
    }


def get_positions() -> List[Dict[str, Any]]:
    _check_keys()
    acct_id = _account_id()
    r = SESSION.get(f"{BASE_URL}/accounts/{acct_id}/positions", headers=HEADERS, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Tradier positions error {r.status_code}: {r.text}")
    pos = r.json().get("positions")
    if not pos or pos == "null":
        return []
    raw = pos.get("position")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raw = [raw]
    return [_normalize_position(p) for p in raw]


def get_position(symbol: str) -> Optional[Dict[str, Any]]:
    symbol = symbol.upper()
    for p in get_positions():
        if p["symbol"].upper() == symbol:
            return p
    return None


def _normalize_order(o: Dict[str, Any]) -> Dict[str, Any]:
    """Alpaca-shaped order dict from Tradier's order fields (status,
    avg_fill_price, exec_quantity, remaining_quantity) - field names
    confirmed from Tradier's official order response reference."""
    result = {
        "id": str(o.get("id") or ""),
        "status": {'pending':'pending_new','open':'new','ok':'pending_new'}.get(o.get('status'), o.get('status')),
        "filled_qty": o.get("exec_quantity"),
        "filled_avg_price": o.get("avg_fill_price"),
        "legs": [],  # Tradier has no native bracket/OCO equivalent surfaced here - see equity_exit_monitor.py's active monitor
    }
    if 'tag' in o:
        result['client_order_id'] = o['tag']
    if 'option_symbol' in o:
        result['symbol'] = o['option_symbol']
    elif 'symbol' in o:
        result['symbol'] = o['symbol']
    if 'quantity' in o:
        result['qty'] = o['quantity']
    if 'side' in o:
        result['side'] = {'buy_to_open': 'buy', 'sell_to_close': 'sell'}.get(o['side'], o['side'])
    return result


def get_order(order_id: str) -> Dict[str, Any]:
    _check_keys()
    acct_id = _account_id()
    r = SESSION.get(f"{BASE_URL}/accounts/{acct_id}/orders/{order_id}", headers=HEADERS, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Tradier get_order error {r.status_code}: {r.text}")
    order = r.json().get("order", {})
    return _normalize_order(order)


def get_order_by_client_id(client_order_id: str) -> Optional[Dict[str, Any]]:
    """
    Tradier has no dedicated "get order by client id" endpoint the way
    Alpaca does - this lists the account's recent orders and matches on
    the `tag` field (set to our client_order_id-equivalent at submission
    time, see place_order). Same purpose as Alpaca's version: resolving a
    submission whose response was lost, without blindly resubmitting.
    """
    _check_keys()
    acct_id = _account_id()
    matches = []
    # Tags are omitted by default and the endpoint otherwise returns only
    # 25 current-session orders. Both defaults defeat timeout recovery.
    for page in range(1, 6):
        r = SESSION.get(f"{BASE_URL}/accounts/{acct_id}/orders", headers=HEADERS,
                        params={'includeTags': 'true', 'limit': 1000, 'page': page}, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"Tradier list orders error {r.status_code}")
        orders = r.json().get('orders')
        if orders in (None, 'null'):
            raw = []
        elif isinstance(orders, dict):
            raw = orders.get('order')
            raw = [] if raw is None else (raw if isinstance(raw, list) else [raw])
        else:
            raise RuntimeError('Malformed Tradier orders response')
        if any(not isinstance(o, dict) for o in raw):
            raise RuntimeError('Malformed Tradier order row')
        matches.extend(o for o in raw if o.get('tag') == client_order_id)
        if len(matches) > 1:
            raise RuntimeError('Ambiguous Tradier order tag; broker reconciliation required')
        if len(raw) < 1000:
            return _normalize_order(matches[0]) if matches else None
    raise RuntimeError('Incomplete Tradier order history; broker reconciliation required')


def cancel_order(order_id: str) -> bool:
    """
    DELETE /accounts/{id}/orders/{id} - standard REST pattern for this
    resource, consistent with every third-party Tradier client found
    researching this, but not confirmed against the official reference
    page directly this session (that page didn't come back in search
    results). Verify against the real sandbox before depending on this.
    """
    _check_keys()
    acct_id = _account_id()
    r = SESSION.delete(f"{BASE_URL}/accounts/{acct_id}/orders/{order_id}", headers=HEADERS, timeout=15)
    if r.status_code in (200, 201, 204):
        return True
    if r.status_code in (404, 400, 422):
        log.info("cancel_order %s: already terminal or not cancelable (%s)", order_id, r.status_code)
        return False
    raise RuntimeError(f"Tradier cancel_order error {r.status_code}: {r.text}")


def latest_quote(symbol: str) -> Optional[float]:
    """Last-trade price from /markets/quotes, with a bid/ask midpoint
    fallback - mirrors broker_alpaca.latest_quote's fallback chain
    (never raises, returns None on total failure)."""
    _check_keys()
    symbol = symbol.upper()
    try:
        r = SESSION.get(f"{BASE_URL}/markets/quotes", headers=HEADERS,
                         params={"symbols": symbol, "greeks": "false"}, timeout=10)
        if r.status_code == 200:
            q = r.json().get("quotes", {}).get("quote")
            if isinstance(q, list):
                q = q[0] if q else None
            if q:
                last = q.get("last")
                if isinstance(last, (int, float)) and not isinstance(last, bool) and valid_positive(last):
                    return float(last)
                bid, ask = q.get("bid"), q.get("ask")
                if all(isinstance(p, (int,float)) and not isinstance(p,bool) and valid_positive(p) for p in (bid,ask)) and bid <= ask:
                    return float((bid+ask)/2)
    except Exception as e:
        log.warning(f"latest_quote failed for {symbol}: {e}")
    return None


def get_bars(symbol: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Recent 1-minute OHLCV bars via /markets/timesales, oldest first -
    same purpose/contract as broker_alpaca.get_bars (candle_gatekeeper.py
    reads this generically through the dispatcher). Never raises; returns
    [] on failure so the gatekeeper fails open rather than crashing the
    entry thread.

    Field names (time/open/high/low/close/volume under series.data) are
    from Tradier's documented timesales response - same "researched, not
    yet confirmed against a real sandbox call" caveat as the rest of this
    module. interval="1min" has a documented 20-day lookback limit for
    open-market data, far more than the few minutes this ever needs.
    """
    _check_keys()
    try:
        now = datetime.now(timezone.utc)
        start = (now - timedelta(minutes=max(limit * 2, 30))).strftime("%Y-%m-%d %H:%M")
        r = SESSION.get(
            f"{BASE_URL}/markets/timesales", headers=HEADERS,
            params={"symbol": symbol.upper(), "interval": "1min",
                    "start": start, "session_filter": "all"},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        series = r.json().get("series")
        if not series or series == "null":
            return []
        data = series.get("data")
        if data is None:
            return []
        if not isinstance(data, list):
            data = [data]
        bars = [
            {"t": d.get("time"), "o": float(d["open"]), "h": float(d["high"]),
             "l": float(d["low"]), "c": float(d["close"]), "v": float(d.get("volume", 0))}
            for d in data
        ]
        return bars[-limit:]
    except Exception as e:
        log.warning(f"get_bars failed for {symbol}: {e}")
        return []


def place_order(symbol: str, side: str, qty: str,
                 type_: str = "market", time_in_force: str = "day",
                 limit_price: Optional[float] = None,
                 take_profit: Optional[float] = None,
                 stop_loss: Optional[float] = None,
                 client_order_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Plain equity order only - Tradier has no single-call bracket
    equivalent to Alpaca's take_profit/stop_loss order_class=bracket (its
    closest analog, OTOCO, is a different multi-leg order shape). If the
    caller asks for a bracket here, this submits the plain entry anyway
    and logs a warning - trade_handler.py is responsible for routing to
    the active equity exit monitor (equity_exit_monitor.py) instead of
    expecting bracket legs back, when BROKER_PROVIDER=tradier.
    """
    _check_keys()
    if os.getenv("TRADE_MODE","dry") == "live" and side.lower() == "buy":
        from lavish_core.trade.live_readiness import assert_live_ready
        assert_live_ready()
        if type_ != "limit" or limit_price is None:
            raise RuntimeError("Live entries require bounded limit prices")
        account = get_account()
        account_id = account.get("id") or account.get("account_number")
        if not account_id:
            raise RuntimeError("Live account identity unavailable")
        assert_live_ready(notional=finite_number(qty,positive=True)*finite_number(limit_price,positive=True),account_id=account_id)

    finite_number(qty, positive=True)
    if side not in ("buy", "sell"):
        raise ValueError("Invalid order side")
    for value in (limit_price, take_profit, stop_loss):
        if value is not None:
            finite_number(value, positive=True)

    if take_profit is not None or stop_loss is not None:
        log.warning("place_order: take_profit/stop_loss requested but Tradier has no native bracket order here - "
                    "submitting a plain entry; the caller must use an active exit monitor instead.")

    if not client_order_id:
        minute_bucket = int(time.time() // 60)
        raw = f"{symbol}|{side}|{qty}|{type_}|{minute_bucket}"
        client_order_id = "lavish_" + uuid.uuid5(uuid.NAMESPACE_OID, raw).hex[:20]

    acct_id = _account_id()
    payload = {
        "class": "equity",
        "symbol": symbol.upper(),
        "side": side,
        "quantity": str(qty),
        "type": type_,
        "duration": time_in_force,
        "tag": client_order_id,
    }
    if type_ in ("limit", "stop_limit") and limit_price is not None:
        payload["price"] = str(limit_price)

    log.info(f"[Tradier] place_order {json.dumps(payload)}")
    r = SESSION.post(f"{BASE_URL}/accounts/{acct_id}/orders", headers=HEADERS, data=payload, timeout=20)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Tradier order error {r.status_code}: {r.text}")
    order = r.json().get("order", {})
    result = _normalize_order(order)
    result.setdefault("client_order_id", client_order_id)
    return result
