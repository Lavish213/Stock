# Only the broker layer may reach an order endpoint. Every other module that
# used to place orders directly (sharing ALPACA_API_KEY/SECRET but none of the
# production gates) must raise QuarantinedBrokerPath before any network call.
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lavish_core.utils.quarantine import QuarantinedBrokerPath

ROOT = Path(__file__).resolve().parents[1]

# The gated broker layer (mode/endpoint binding, live approval, idempotency).
BROKER_LAYER = {
    "lavish_core/trade/broker_alpaca.py",
    "lavish_core/trade/options_broker.py",
    "lavish_core/trade/broker_tradier.py",
    "lavish_core/trade/options_broker_tradier.py",
}
# Legacy modules that still contain order code but are hard-stopped.
QUARANTINED = {
    "lavish_core/trading/executor.py",
    "lavish_core/trading/auto_signal_runner.py",
    "lavish_core/vision/king_vision.py",
    "lavish_core/superbot.py",
    "lavish_core/legacy_dashboard_api/services/alpaca_client.py",
    "lavish_core/legacy_dashboard_api/routers/orders.py",  # route only; client is guarded
}
EXCLUDED_DIRS = {"archive", "tests", "validation_tests", "ml extras", "news extras",
                 "trader extras", "notebooks", ".git", "venv", ".venv", "__pycache__"}
ORDER_PATTERN = re.compile(
    r"TradingClient\s*\(|/v2/orders|accounts/\{[^}]*\}/orders|/orders['\"]|alpaca_trade_api|tradeapi\.REST"
)


def _source_files():
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if set(rel.parts) & EXCLUDED_DIRS:
            continue
        yield rel.as_posix(), path


def test_no_new_module_can_reach_an_order_endpoint():
    offenders = sorted(rel for rel, path in _source_files()
                       if ORDER_PATTERN.search(path.read_text(errors="ignore"))
                       and rel not in BROKER_LAYER | QUARANTINED)
    assert offenders == [], (
        "These modules reference a broker order endpoint outside lavish_core/trade/broker*.py. "
        "Route orders through lavish_core.trade.broker, or guard with refuse_broker_path(): "
        f"{offenders}")


def test_every_quarantined_module_still_carries_the_guard():
    for rel in QUARANTINED - {"lavish_core/legacy_dashboard_api/routers/orders.py"}:
        assert "refuse_broker_path(" in (ROOT / rel).read_text(), rel


def _import_with_stubs(name, monkeypatch):
    """Import a legacy module whose optional third-party deps (or, for the
    already-broken legacy dashboard package, missing sibling modules) are
    not installed, by stubbing each missing module. The guard under test
    sits in our own code, so stubbed dependencies cannot mask it."""
    for _ in range(25):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as e:
            if not e.name or e.name in sys.modules:
                raise
            monkeypatch.setitem(sys.modules, e.name, MagicMock(name=e.name))
            sys.modules.pop(name, None)
    raise AssertionError(f"could not import {name}")


def _no_network():
    def boom(*a, **k):
        raise AssertionError("quarantined path attempted a network call")
    return patch("requests.post", side_effect=boom), patch("requests.request", side_effect=boom), \
        patch("requests.Session.request", side_effect=boom)


def test_executor_refuses_paper_and_live(monkeypatch):
    p1, p2, p3 = _no_network()
    with p1, p2, p3:
        for mode in ("paper", "live"):
            monkeypatch.setenv("TRADE_MODE", mode)
            sys.modules.pop("lavish_core.trading.executor", None)
            mod = importlib.import_module("lavish_core.trading.executor")
            with pytest.raises(QuarantinedBrokerPath):
                mod.TradeExecutor()
        monkeypatch.setenv("TRADE_MODE", "mock")
        sys.modules.pop("lavish_core.trading.executor", None)
        mod = importlib.import_module("lavish_core.trading.executor")
        assert mod.TradeExecutor().place(mod.TradeSignal("BUY", "AAPL", 1, "t"))["status"] == "SIMULATED"
    sys.modules.pop("lavish_core.trading.executor", None)


def test_king_vision_refuses_on_import():
    sys.modules.pop("lavish_core.vision.king_vision", None)
    with pytest.raises(QuarantinedBrokerPath):
        importlib.import_module("lavish_core.vision.king_vision")


def test_auto_signal_runner_buy_refuses_without_network(monkeypatch):
    mod = _import_with_stubs("lavish_core.trading.auto_signal_runner", monkeypatch)
    p1, p2, p3 = _no_network()
    with p1, p2, p3, pytest.raises(QuarantinedBrokerPath):
        mod.alpaca_place_market_buy("AAPL", 100.0)


def test_superbot_executor_refuses_without_network(monkeypatch):
    mod = _import_with_stubs("lavish_core.superbot", monkeypatch)
    p1, p2, p3 = _no_network()
    with p1, p2, p3, pytest.raises(QuarantinedBrokerPath):
        mod.AlpacaExecutor(base_url="https://paper-api.alpaca.markets/v2", key="k", secret="s").place_order("AAPL", "buy", 1)


def test_legacy_dashboard_client_refuses_without_network(monkeypatch):
    alpaca_client = _import_with_stubs("lavish_core.legacy_dashboard_api.services.alpaca_client", monkeypatch)
    client = alpaca_client.AlpacaClient(api_key="k", secret="s", base_url="https://paper-api.alpaca.markets")
    p1, p2, p3 = _no_network()
    with p1, p2, p3, pytest.raises(QuarantinedBrokerPath):
        client.submit_order("AAPL", "buy", qty=1)
