# TRADE_MODE is the only paper/live switch. TradeAgent (and therefore
# flatten_all.py and run_live_trader.py) used to read LIVE_TRADE instead,
# so with TRADE_MODE=paper|live and LIVE_TRADE unset an emergency flatten
# silently ran in dry mode.
from __future__ import annotations

import pytest

import lavish_core.trade.trade_agent as ta
from lavish_core.db.hybrid_store import HybridStore


@pytest.mark.parametrize("mode", ["dry", "paper", "live", "PAPER", " live "])
def test_trade_agent_follows_trade_mode(tmp_path, monkeypatch, mode):
    monkeypatch.delenv("LIVE_TRADE", raising=False)
    monkeypatch.setenv("TRADE_MODE", mode)
    agent = ta.TradeAgent(store=HybridStore(duckdb_path=tmp_path / "t.duckdb"))
    assert agent.default_mode == mode.strip().lower()


def test_unset_trade_mode_defaults_to_dry(monkeypatch):
    monkeypatch.delenv("LIVE_TRADE", raising=False)
    monkeypatch.delenv("TRADE_MODE", raising=False)
    assert ta.resolve_trade_mode() == "dry"


@pytest.mark.parametrize("value", ["true", "false", ""])
def test_any_leftover_live_trade_is_rejected(monkeypatch, value):
    monkeypatch.setenv("TRADE_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADE", value)
    with pytest.raises(RuntimeError, match="LIVE_TRADE"):
        ta.resolve_trade_mode()


def test_unknown_trade_mode_is_rejected(monkeypatch):
    monkeypatch.delenv("LIVE_TRADE", raising=False)
    monkeypatch.setenv("TRADE_MODE", "mock")
    with pytest.raises(RuntimeError, match="Unknown TRADE_MODE"):
        ta.resolve_trade_mode()


def test_super_core_bridge_follows_trade_mode_and_fails_safe(monkeypatch):
    from lavish_core import super_core
    monkeypatch.setattr(ta, "TradeAgent", lambda: object())
    monkeypatch.delenv("LIVE_TRADE", raising=False)
    monkeypatch.setenv("TRADE_MODE", "paper")
    assert super_core.TradeBridge().live is True
    monkeypatch.setenv("TRADE_MODE", "dry")
    assert super_core.TradeBridge().live is False
    monkeypatch.setenv("LIVE_TRADE", "true")  # leftover variable -> stays dry
    assert super_core.TradeBridge().live is False
