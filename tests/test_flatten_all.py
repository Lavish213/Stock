# Covers TradeAgent.flatten_all() - the emergency "close everything"
# function. Real bug found and fixed: in paper/live mode it only ever read
# the local `positions` DB table, which is equity-only (options fills are
# never written there - reconcile.py already treats the broker's own
# position list as the only real source of truth for options). An
# emergency flatten that silently skipped every open option contract would
# be worse than useless. Fixed to read the real broker position list in
# paper/live mode (covering both asset classes), while keeping dry mode's
# local-ledger-only behavior (no network call) unchanged.
from __future__ import annotations

from unittest.mock import patch

import lavish_core.trade.trade_agent as ta
from lavish_core.db.hybrid_store import HybridStore


def _agent(tmp_path, monkeypatch, mode):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    agent = ta.TradeAgent(store=store)
    agent.default_mode = mode
    return agent


def test_dry_mode_flattens_from_the_local_ledger_only(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, "dry")
    agent.store.execute(
        "INSERT INTO positions(symbol, qty, avg_price, updated_at) VALUES (?, ?, ?, ?)",
        ("AAPL", 10.0, 150.0, __import__("datetime").datetime.now(__import__("datetime").timezone.utc)),
    )
    with patch("lavish_core.trade.broker_alpaca.get_positions") as mock_broker_positions:
        result = agent.flatten_all()

    mock_broker_positions.assert_not_called()  # dry mode must never hit the real broker
    assert result["status"] == "flattened"
    assert result["n"] == 1


def test_live_mode_closes_both_equity_and_options_positions(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, "live")
    broker_positions = [
        {"symbol": "AAPL", "qty": "10", "asset_class": "us_equity"},
        {"symbol": "AAPL260116C00200000", "qty": "2", "asset_class": "us_option"},
    ]
    with patch("lavish_core.trade.broker_alpaca.get_positions", return_value=broker_positions), \
         patch("lavish_core.trade.options_broker.latest_option_quote", return_value={"bid": 1.0, "ask": 1.2}), \
         patch("lavish_core.trade.options_broker.place_option_order", return_value={"status": "filled"}) as mock_opt, \
         patch.object(ta, "place_trade", return_value={"status": "filled"}) as mock_equity:
        result = agent.flatten_all()

    assert result["status"] == "flattened"
    assert result["n"] == 2
    mock_equity.assert_called_once()
    mock_opt.assert_called_once()
    opt_args = mock_opt.call_args[0]
    assert opt_args[0] == "AAPL260116C00200000"
    assert opt_args[1] == "sell"
    assert opt_args[2] == 2


def test_live_mode_short_equity_position_closes_with_a_buy(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, "live")
    broker_positions = [{"symbol": "TSLA", "qty": "-5", "asset_class": "us_equity"}]
    with patch("lavish_core.trade.broker_alpaca.get_positions", return_value=broker_positions), \
         patch.object(ta, "place_trade", return_value={"status": "filled"}) as mock_equity:
        agent.flatten_all()

    assert mock_equity.call_args[0][2] == "buy"  # closing a short means buying back
    assert mock_equity.call_args[0][3] == 5.0


def test_option_with_no_usable_quote_is_reported_not_silently_skipped(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, "live")
    broker_positions = [{"symbol": "AAPL260116C00200000", "qty": "2", "asset_class": "us_option"}]
    with patch("lavish_core.trade.broker_alpaca.get_positions", return_value=broker_positions), \
         patch("lavish_core.trade.options_broker.latest_option_quote", return_value=None), \
         patch("lavish_core.trade.options_broker.place_option_order") as mock_opt:
        result = agent.flatten_all()

    mock_opt.assert_not_called()
    assert result["n"] == 1
    assert result["details"][0]["status"] == "error"


def test_one_failed_close_does_not_abort_the_rest(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, "live")
    broker_positions = [
        {"symbol": "AAPL", "qty": "10", "asset_class": "us_equity"},
        {"symbol": "MSFT", "qty": "5", "asset_class": "us_equity"},
    ]
    with patch("lavish_core.trade.broker_alpaca.get_positions", return_value=broker_positions), \
         patch.object(ta, "place_trade", side_effect=[RuntimeError("broker rejected"), {"status": "filled"}]):
        result = agent.flatten_all()

    assert result["n"] == 2
    assert result["details"][0]["status"] == "error"
    assert result["details"][1]["status"] == "filled"


def test_broker_read_failure_in_live_mode_is_reported_not_raised(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, "paper")
    with patch("lavish_core.trade.broker_alpaca.get_positions", side_effect=RuntimeError("broker down")):
        result = agent.flatten_all()

    assert result["status"] == "error"
    assert result["n"] == 0


def test_zero_qty_positions_are_skipped(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch, "live")
    broker_positions = [{"symbol": "AAPL", "qty": "0", "asset_class": "us_equity"}]
    with patch("lavish_core.trade.broker_alpaca.get_positions", return_value=broker_positions), \
         patch.object(ta, "place_trade") as mock_equity:
        result = agent.flatten_all()

    mock_equity.assert_not_called()
    assert result["n"] == 0
