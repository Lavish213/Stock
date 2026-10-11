# Covers broker_tradier.py - the Tradier equivalent of broker_alpaca.py.
# Nothing here was ever run against a real Tradier sandbox call (this
# environment can't reach Tradier's API, same block as Alpaca's) - these
# tests only confirm the response-shape translation matches what was
# researched (Tradier's real field names -> the Alpaca-shaped dict the
# rest of this codebase reads), using mocked HTTP responses. The first
# real sandbox call is still the thing that actually proves this.
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

import lavish_core.trade.broker_tradier as bt


def _resp(status_code=200, body=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = body or {}
    r.text = str(body)
    return r


@pytest.fixture(autouse=True)
def _reset_account_cache():
    # _account_id() caches module-globally - without resetting between
    # tests, whichever test runs first would leak its resolved account id
    # (or override) into every test after it.
    bt._account_id_cache = None
    with patch.object(bt, "_ACCOUNT_ID_OVERRIDE", ""):
        yield
    bt._account_id_cache = None


def test_check_keys_raises_without_token():
    with patch.object(bt, "API_TOKEN", ""):
        with pytest.raises(RuntimeError, match="TRADIER_API_KEY"):
            bt._check_keys()


def test_account_id_resolves_from_single_account_object():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "profile": {"account": {"account_number": "ACC123"}}
         })):
        assert bt._account_id() == "ACC123"


def test_account_id_resolves_from_list_and_picks_first():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "profile": {"account": [{"account_number": "FIRST"}, {"account_number": "SECOND"}]}
         })):
        assert bt._account_id() == "FIRST"


def test_account_id_is_cached_after_first_resolution():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "profile": {"account": {"account_number": "ACC123"}}
         })) as mock_get:
        bt._account_id()
        bt._account_id()
    assert mock_get.call_count == 1


def test_account_id_override_env_var_skips_profile_lookup():
    with patch.object(bt, "_ACCOUNT_ID_OVERRIDE", "OVERRIDDEN"), \
         patch.object(bt.SESSION, "get") as mock_get:
        assert bt._account_id() == "OVERRIDDEN"
    mock_get.assert_not_called()


def test_account_id_raises_on_empty_account_list():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={"profile": {"account": []}})):
        with pytest.raises(RuntimeError, match="empty account list"):
            bt._account_id()


def test_get_account_maps_tradier_balances_to_alpaca_field_names():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "balances": {"total_equity": 10000.0, "total_cash": 5000.0, "stock_buying_power": 20000.0}
         })):
        acct = bt.get_account()

    assert acct == {"equity": 10000.0, "cash": 5000.0, "buying_power": 20000.0}


def test_get_account_falls_back_to_option_buying_power_if_stock_missing():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "balances": {"total_equity": 10000.0, "total_cash": 5000.0, "option_buying_power": 7500.0}
         })):
        acct = bt.get_account()

    assert acct["buying_power"] == 7500.0


def test_get_clock_open_state_maps_to_is_open_true():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "clock": {"state": "open", "next_change": "2026-01-02T21:00:00+00:00"}
         })):
        clk = bt.get_clock()

    assert clk["is_open"] is True
    assert clk["next_close"] == "2026-01-02T21:00:00+00:00"


def test_get_clock_closed_state_maps_to_is_open_false():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "clock": {"state": "closed", "next_change": "2026-01-02T14:30:00+00:00"}
         })):
        clk = bt.get_clock()

    assert clk["is_open"] is False


def test_get_positions_normalizes_equity_position():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "positions": {"position": {"symbol": "AAPL", "quantity": 10, "cost_basis": 1500.0}}
         })):
        positions = bt.get_positions()

    assert positions == [{
        "symbol": "AAPL", "qty": 10.0, "asset_class": "us_equity",
        "side": "long", "avg_entry_price": 150.0,
    }]


def test_get_positions_normalizes_option_position_using_occ_regex_and_multiplier():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "positions": {"position": {"symbol": "AAPL260116C00200000", "quantity": 2, "cost_basis": 800.0}}
         })):
        positions = bt.get_positions()

    pos = positions[0]
    assert pos["asset_class"] == "us_option"
    # cost_basis is TOTAL dollars, not per-contract - 800 / (2 * 100) = 4.0
    assert pos["avg_entry_price"] == 4.0


def test_get_positions_handles_multiple_positions_as_a_list():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "positions": {"position": [
                 {"symbol": "AAPL", "quantity": 10, "cost_basis": 1500.0},
                 {"symbol": "MSFT", "quantity": -5, "cost_basis": 2000.0},
             ]}
         })):
        positions = bt.get_positions()

    assert len(positions) == 2
    assert positions[1]["side"] == "short"


def test_get_positions_returns_empty_list_when_null():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={"positions": "null"})):
        assert bt.get_positions() == []


def test_get_position_returns_none_when_symbol_not_held():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "positions": {"position": {"symbol": "AAPL", "quantity": 10, "cost_basis": 1500.0}}
         })):
        assert bt.get_position("MSFT") is None
        assert bt.get_position("aapl")["symbol"] == "AAPL"


def test_get_order_normalizes_tradier_fields_to_alpaca_shape():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "order": {"id": 42, "status": "filled", "exec_quantity": 10, "avg_fill_price": 150.25, "tag": "lavish_abc"}
         })):
        order = bt.get_order("42")

    assert order == {
        "id": "42", "status": "filled", "filled_qty": 10, "filled_avg_price": 150.25,
        "client_order_id": "lavish_abc", "legs": [],
    }


def test_get_order_by_client_id_matches_on_tag_field():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "orders": {"order": [
                 {"id": 1, "status": "filled", "tag": "other"},
                 {"id": 2, "status": "filled", "tag": "lavish_target"},
             ]}
         })):
        order = bt.get_order_by_client_id("lavish_target")

    assert order["id"] == "2"


def test_get_order_by_client_id_returns_none_when_no_match():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "orders": {"order": {"id": 1, "status": "filled", "tag": "other"}}
         })):
        assert bt.get_order_by_client_id("lavish_target") is None


def test_cancel_order_returns_true_on_200():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "delete", return_value=_resp(status_code=200)):
        assert bt.cancel_order("42") is True


def test_cancel_order_returns_false_when_already_terminal():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "delete", return_value=_resp(status_code=404)):
        assert bt.cancel_order("42") is False


def test_cancel_order_raises_on_unexpected_error_status():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "delete", return_value=_resp(status_code=500)):
        with pytest.raises(RuntimeError):
            bt.cancel_order("42")


def test_latest_quote_prefers_last_trade_price():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "quotes": {"quote": {"last": 150.5, "bid": 150.0, "ask": 151.0}}
         })):
        assert bt.latest_quote("AAPL") == 150.5


def test_latest_quote_falls_back_to_bid_ask_midpoint_when_no_last():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt.SESSION, "get", return_value=_resp(body={
             "quotes": {"quote": {"last": None, "bid": 100.0, "ask": 102.0}}
         })):
        assert bt.latest_quote("AAPL") == 101.0


def test_latest_quote_returns_none_on_request_exception():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt.SESSION, "get", side_effect=RuntimeError("network down")):
        assert bt.latest_quote("AAPL") is None


def test_place_order_submits_plain_equity_order():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "post", return_value=_resp(status_code=200, body={
             "order": {"id": 99, "status": "pending", "exec_quantity": None, "avg_fill_price": None}
         })) as mock_post:
        result = bt.place_order(symbol="aapl", side="buy", qty="10", type_="market")

    assert result["id"] == "99"
    payload = mock_post.call_args.kwargs["data"]
    assert payload["class"] == "equity"
    assert payload["symbol"] == "AAPL"
    assert "take_profit" not in payload and "stop_loss" not in payload


def test_place_order_with_bracket_request_warns_and_still_submits_plain_order():
    # Tradier has no single-call bracket equivalent - trade_handler.py is
    # responsible for routing to the active exit monitor instead, but
    # place_order() itself must never silently drop the entry order just
    # because take_profit/stop_loss were passed.
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "post", return_value=_resp(status_code=200, body={
             "order": {"id": 99, "status": "pending"}
         })), \
         patch.object(bt.log, "warning") as mock_warn:
        result = bt.place_order(symbol="AAPL", side="buy", qty="10", take_profit=160.0, stop_loss=140.0)

    assert result["id"] == "99"
    assert mock_warn.called


def test_place_order_raises_on_broker_error():
    with patch.object(bt, "API_TOKEN", "test"), \
         patch.object(bt, "_account_id", return_value="ACC123"), \
         patch.object(bt.SESSION, "post", return_value=_resp(status_code=400, body={"errors": "bad request"})):
        with pytest.raises(RuntimeError):
            bt.place_order(symbol="AAPL", side="buy", qty="10")
