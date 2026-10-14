# Covers options_broker_tradier.py - the Tradier equivalent of
# options_broker.py's find_contract/latest_option_quote/place_option_order.
# Same caveat as test_broker_tradier.py: mocked HTTP only, never run
# against a real Tradier sandbox call from this environment.
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import lavish_core.trade.options_broker_tradier as obt


def _resp(status_code=200, body=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = body or {}
    r.text = str(body)
    return r


def test_find_contract_picks_closest_strike_for_calls():
    chain = {"options": {"option": [
        {"symbol": "AAPL260116C00195000", "strike": 195.0, "option_type": "call"},
        {"symbol": "AAPL260116C00200000", "strike": 200.0, "option_type": "call"},
        {"symbol": "AAPL260116P00200000", "strike": 200.0, "option_type": "put"},
    ]}}
    with patch.object(obt, "_check_keys"), \
         patch.object(obt.SESSION, "get", return_value=_resp(body=chain)):
        contract = obt.find_contract("AAPL", date(2026, 1, 16), "call", 199.0, strike_tolerance=1.0)

    assert contract["symbol"] == "AAPL260116C00200000"
    assert contract["strike_price"] == 200.0


def test_find_contract_filters_out_wrong_option_type():
    chain = {"options": {"option": [
        {"symbol": "AAPL260116P00200000", "strike": 200.0, "option_type": "put"},
    ]}}
    with patch.object(obt, "_check_keys"), \
         patch.object(obt.SESSION, "get", return_value=_resp(body=chain)):
        contract = obt.find_contract("AAPL", date(2026, 1, 16), "call", 200.0)

    assert contract is None


def test_find_contract_rejects_strike_outside_tolerance():
    chain = {"options": {"option": [
        {"symbol": "AAPL260116C00250000", "strike": 250.0, "option_type": "call"},
    ]}}
    with patch.object(obt, "_check_keys"), \
         patch.object(obt.SESSION, "get", return_value=_resp(body=chain)):
        contract = obt.find_contract("AAPL", date(2026, 1, 16), "call", 200.0, strike_tolerance=5.0)

    assert contract is None


def test_find_contract_returns_none_on_non_200():
    with patch.object(obt, "_check_keys"), \
         patch.object(obt.SESSION, "get", return_value=_resp(status_code=500)):
        assert obt.find_contract("AAPL", date(2026, 1, 16), "call", 200.0) is None


def test_find_contract_returns_none_when_chain_is_null():
    with patch.object(obt, "_check_keys"), \
         patch.object(obt.SESSION, "get", return_value=_resp(body={"options": "null"})):
        assert obt.find_contract("AAPL", date(2026, 1, 16), "call", 200.0) is None


def test_latest_option_quote_returns_bid_ask():
    with patch.object(obt, "_check_keys"), \
         patch.object(obt.SESSION, "get", return_value=_resp(body={
             "quotes": {"quote": {"bid": 3.9, "ask": 4.1}}
         })):
        q = obt.latest_option_quote("AAPL260116C00200000")

    assert q == {"bid": 3.9, "ask": 4.1}


def test_latest_option_quote_returns_none_on_missing_fields():
    with patch.object(obt, "_check_keys"), \
         patch.object(obt.SESSION, "get", return_value=_resp(body={
             "quotes": {"quote": {"ask": 4.1}}
         })):
        assert obt.latest_option_quote("AAPL260116C00200000") is None


def test_place_option_order_translates_buy_to_buy_to_open():
    with patch.object(obt, "_check_keys"), \
         patch.object(obt, "_account_id", return_value="ACC123"), \
         patch.object(obt.SESSION, "post", return_value=_resp(body={
             "order": {"id": 5, "status": "filled", "exec_quantity": 1, "avg_fill_price": 4.0}
         })) as mock_post:
        result = obt.place_option_order("AAPL260116C00200000", side="buy", qty=1,
                                          order_type="limit", limit_price=4.0)

    payload = mock_post.call_args.kwargs["data"]
    assert payload["side"] == "buy_to_open"
    assert payload["class"] == "option"
    # Underlying must be JUST the ticker, not "AAPLC" - a naive "every
    # alpha character in the OCC symbol" extraction also grabs the C/P
    # call/put letter itself.
    assert payload["symbol"] == "AAPL"
    assert payload["option_symbol"] == "AAPL260116C00200000"
    assert result["status"] == "filled"


def test_place_option_order_underlying_extraction_excludes_put_letter_too():
    with patch.object(obt, "_check_keys"), \
         patch.object(obt, "_account_id", return_value="ACC123"), \
         patch.object(obt.SESSION, "post", return_value=_resp(body={"order": {"id": 7, "status": "filled"}})) as mock_post:
        obt.place_option_order("SPY260116P00450000", side="buy", qty=1, order_type="limit", limit_price=2.0)

    assert mock_post.call_args.kwargs["data"]["symbol"] == "SPY"


def test_place_option_order_translates_sell_to_sell_to_close():
    with patch.object(obt, "_check_keys"), \
         patch.object(obt, "_account_id", return_value="ACC123"), \
         patch.object(obt.SESSION, "post", return_value=_resp(body={
             "order": {"id": 6, "status": "filled"}
         })) as mock_post:
        obt.place_option_order("AAPL260116C00200000", side="sell", qty=1, order_type="limit", limit_price=5.0)

    assert mock_post.call_args.kwargs["data"]["side"] == "sell_to_close"


def test_place_option_order_rejects_invalid_side():
    import pytest
    with patch.object(obt, "_check_keys"), pytest.raises(ValueError):
        obt.place_option_order("AAPL260116C00200000", side="short", qty=1, order_type="limit", limit_price=4.0)


def test_place_option_order_requires_limit_price_for_limit_orders():
    import pytest
    with patch.object(obt, "_check_keys"), pytest.raises(ValueError):
        obt.place_option_order("AAPL260116C00200000", side="buy", qty=1, order_type="limit")


def test_place_option_order_raises_on_broker_error():
    import pytest
    with patch.object(obt, "_check_keys"), \
         patch.object(obt, "_account_id", return_value="ACC123"), \
         patch.object(obt.SESSION, "post", return_value=_resp(status_code=400, body={"errors": "bad"})):
        with pytest.raises(RuntimeError):
            obt.place_option_order("AAPL260116C00200000", side="buy", qty=1, order_type="limit", limit_price=4.0)


def test_resolve_and_price_contract_attaches_midpoint():
    with patch.object(obt, "find_contract", return_value={"symbol": "AAPL260116C00200000", "strike_price": 200.0}), \
         patch.object(obt, "latest_option_quote", return_value={"bid": 3.9, "ask": 4.1}):
        contract = obt.resolve_and_price_contract("AAPL", date(2026, 1, 16), "call", 200.0)

    assert contract["mid_price"] == 4.0
    assert contract["bid"] == 3.9
    assert contract["ask"] == 4.1


def test_resolve_and_price_contract_returns_none_when_contract_not_found():
    with patch.object(obt, "find_contract", return_value=None):
        assert obt.resolve_and_price_contract("AAPL", date(2026, 1, 16), "call", 200.0) is None
