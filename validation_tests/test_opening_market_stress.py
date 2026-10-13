"""Synthetic opening conditions through the production parser and entry gates."""
from datetime import date, datetime, timedelta, timezone
from itertools import product
from unittest.mock import Mock
import random
import math

import pytest

from lavish_core.trading import trade_handler as th, alert_handler
from lavish_core.trade import options_broker as ob
from lavish_core.validation.options_replay import Response, replay
from lavish_core.validation.options_exchange import OptionsExchange, Contract


@pytest.mark.parametrize("mode", ["paper", "live"])
def test_money_candle_confirmation_never_uses_unverified_fallback(mode, monkeypatch):
    from lavish_core.trade import broker_alpaca as broker
    monkeypatch.setenv("TRADE_MODE", mode)
    monkeypatch.setattr(broker, "_check_keys", lambda: None)
    monkeypatch.setattr(broker.SESSION, "get", lambda *a, **k: Response({}, 503))
    fallback = Mock(return_value=[{"c": 100}])
    monkeypatch.setattr(broker, "_yfinance_fallback_bars", fallback)
    assert broker.get_bars("SPY") == []
    fallback.assert_not_called()


OPEN_CASES = list(product(["CALL", "PUT"], [-.2, -.1, -.04, -.03, 0, .02, .04, .1, .3],
                         [0, .04, .19, .8], [0, 99, 100, 200], [False, True]))


@pytest.mark.parametrize("side,gap,spread,budget,is_open", OPEN_CASES)
def test_market_open_alert_entry_rules(side, gap, spread, budget, is_open, paper_boundary, monkeypatch):
    """Real parser -> entry handler; only the broker boundary is synthetic."""
    expiry = date.today() + timedelta(days=7)
    symbol = ob.build_occ_symbol("AAPL", expiry, side, 200)
    monkeypatch.setattr(th, "POSITION_SIZE_SCALE", 1)
    monkeypatch.setattr(th, "MAX_PRICE_DRIFT_SINCE_ALERT_PCT", .03)
    monkeypatch.setattr(th, "OPTION_MAX_SPREAD_PCT", .2)
    monkeypatch.setattr(th, "get_clock", lambda: {"is_open": is_open})
    prices = iter([100., 100. * (1 + gap)])
    monkeypatch.setattr(th, "latest_quote", lambda *a: next(prices, 100. * (1 + gap)))
    monkeypatch.setattr(th, "resolve_and_price_contract", lambda *a, **k: {
        "symbol": symbol, "mid_price": 1., "bid": 1 - spread / 2, "ask": 1 + spread / 2})
    submit = Mock(return_value={"id": "synthetic-opening", "status": "rejected", "filled_qty": "0"})
    monkeypatch.setattr(th, "place_option_order", submit)
    monkeypatch.setattr(th, "broker_cancel_order", Mock(return_value=True))
    monkeypatch.setattr(th, "broker_get_order", lambda *a: {"status": "rejected"})
    text = f"BTO AAPL 200 {side} exp {expiry:%m/%d/%Y}"
    alert_handler.handle_alert_text(text, source="synthetic-opening", amount_usd=budget)
    expected = is_open and abs(gap) <= .03 and spread <= .2 and budget >= 100
    assert bool(submit.call_count) is expected
    if expected:
        assert submit.call_args.kwargs["qty"] == budget // 100
        assert submit.call_args.kwargs["side"] == "buy"
        assert not th.watch_and_exit_async.called  # rejection is never treated as a fill


@pytest.mark.parametrize("missing", [None, 0, -1, float("nan"), float("inf"), True])
def test_underlying_quote_outage_never_opens_options(missing, paper_boundary, monkeypatch):
    quote = iter([100., missing])
    monkeypatch.setattr(th, "latest_quote", lambda *a: next(quote, missing))
    monkeypatch.setattr(th, "resolve_and_price_contract", lambda *a, **k: {
        "symbol": "synthetic-option", "mid_price": 1., "bid": .99, "ask": 1.01})
    submit = Mock()
    monkeypatch.setattr(th, "place_option_order", submit)
    th.execute_trade_from_post({"ticker": "AAPL", "side": "CALL", "strike": 200,
                               "expiry": (date.today() + timedelta(days=7)).isoformat(),
                               "confidence": .9, "amount_usd": 200})
    submit.assert_not_called()


@pytest.mark.parametrize("initial", [None, 0, -1, float("nan"), float("inf"), True])
def test_missing_alert_time_baseline_cannot_bypass_drift(initial, paper_boundary, monkeypatch):
    values = iter([initial, 100.])
    monkeypatch.setattr(th, "latest_quote", lambda *a: next(values, 100.))
    resolve = Mock()
    monkeypatch.setattr(th, "resolve_and_price_contract", resolve)
    th.execute_trade_from_post({"ticker": "AAPL", "side": "CALL", "strike": 200,
                               "expiry": (date.today() + timedelta(days=7)).isoformat(), "confidence": .9})
    resolve.assert_not_called()


@pytest.mark.parametrize("bid,ask", [(None, None), (.99, None), (None, 1.01)])
def test_midpoint_without_two_sided_market_is_not_tradable(bid, ask, paper_boundary, monkeypatch):
    monkeypatch.setattr(th, "resolve_and_price_contract", lambda *a, **k: {
        "symbol": "synthetic-option", "mid_price": 1., "bid": bid, "ask": ask})
    submit = Mock()
    monkeypatch.setattr(th, "place_option_order", submit)
    th.execute_trade_from_post({"ticker": "AAPL", "side": "CALL", "strike": 200,
                               "expiry": (date.today() + timedelta(days=7)).isoformat(), "confidence": .9})
    submit.assert_not_called()


@pytest.mark.parametrize("bid,ask", [(float("nan"), 1), (1, float("inf")), (True, 1), (-1, 1), (2, 1), (1, 0)])
def test_corrupt_quote_rejected_at_adapter(bid, ask, monkeypatch):
    monkeypatch.setenv("TRADE_MODE", "paper")
    monkeypatch.setattr(ob, "_check_keys", lambda: None)
    monkeypatch.setattr(ob.SESSION, "get", lambda *a, **k: Response({"quotes": {
        "synthetic": {"bp": bid, "ap": ask, "t": datetime.now(timezone.utc).isoformat()}}}))
    assert ob.latest_option_quote("synthetic") is None


@pytest.mark.parametrize("seed", range(100))
def test_seeded_volatility_and_liquidity_paths(seed, tmp_path):
    """100 reproducible 60-tick paths; fees, no shorts and cash conservation."""
    rng = random.Random(seed)
    side = "CALL" if seed % 2 == 0 else "PUT"
    premium = 1.
    x = OptionsExchange()
    x.add_contract(Contract("path", "SPY", date.today() + timedelta(days=7), side, 200), 1, 1)
    x.submit("path", "BUY", 3, 1, "entry")
    for tick in range(60):
        # Synthetic jumps and diffusion; no assertion of predictive realism.
        premium *= math.exp(rng.gauss(0, .035) + (rng.choice([-.35, .35]) if tick in (0, 30) else 0))
        premium = max(.01, min(20, premium))
        spread = rng.choice([.02, .1, .4])
        bid = max(.01, round(premium * (1 - spread / 2), 2))
        ask = max(bid, round(premium * (1 + spread / 2), 2))
        x.quote("path", bid, ask, rng.choice([0, 1, 3]))
        if tick == 20:
            x.submit("path", "SELL", 3, bid, "exit")
        if "exit" in x.clients:
            x.match(x.clients["exit"])
        assert x.positions.get("path", 0) >= 0
        expected_cash = 100000 + sum((1 if e["side"] == "SELL" else -1) * e["qty"] * e["price"] * 100 - e["qty"] * .65 for e in x.events)
        assert x.cash == pytest.approx(expected_cash)
    # A confirmed cancellation precedes any replacement close.
    if x.positions:
        x.cancel(x.clients["exit"])
        x.quote("path", bid, ask, 100)
        x.submit("path", "SELL", x.positions["path"], bid, "replacement")
    assert not x.positions
    with pytest.raises(ValueError):
        x.submit("path", "SELL", 1, bid, "duplicate-close")
    # Independent replay through actual adapter, durable ExitState and reporting.
    result = replay(tmp_path / "replay.db", side=side, qty=3, exit_price=bid)
    assert result["passed"] and result["orders"] == 2 and result["fills"] == 2
