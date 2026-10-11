# Covers lavish_core/trade/broker.py - the BROKER_PROVIDER-driven switch
# between the Alpaca and Tradier backends. Confirms it picks the right
# implementation's functions (by identity, not just by name) and that
# SUPPORTS_NATIVE_BRACKETS matches which broker actually supports a
# server-side bracket/OCO order - trade_handler.py relies on that flag to
# decide between watching bracket legs vs starting the active equity exit
# monitor.
from __future__ import annotations

import importlib
import pytest


def _reload_broker(monkeypatch, provider):
    if provider is None:
        monkeypatch.delenv("BROKER_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("BROKER_PROVIDER", provider)
    import lavish_core.trade.broker as broker
    return importlib.reload(broker)


def test_defaults_to_alpaca_when_unset(monkeypatch):
    broker = _reload_broker(monkeypatch, None)
    import lavish_core.trade.broker_alpaca as alpaca

    assert broker.BROKER_PROVIDER == "alpaca"
    assert broker.SUPPORTS_NATIVE_BRACKETS is True
    assert broker.get_account is alpaca.get_account
    assert broker.place_order is alpaca.place_order


def test_explicit_alpaca_matches_default(monkeypatch):
    broker = _reload_broker(monkeypatch, "alpaca")
    import lavish_core.trade.broker_alpaca as alpaca
    import lavish_core.trade.options_broker as options_alpaca

    assert broker.get_positions is alpaca.get_positions
    assert broker.find_contract is options_alpaca.find_contract


def test_tradier_selects_tradier_backend(monkeypatch):
    broker = _reload_broker(monkeypatch, "tradier")
    import lavish_core.trade.broker_tradier as tradier
    import lavish_core.trade.options_broker_tradier as options_tradier

    assert broker.SUPPORTS_NATIVE_BRACKETS is False
    assert broker.get_account is tradier.get_account
    assert broker.place_order is tradier.place_order
    assert broker.get_positions is tradier.get_positions
    assert broker.find_contract is options_tradier.find_contract
    assert broker.place_option_order is options_tradier.place_option_order
    assert broker.resolve_and_price_contract is options_tradier.resolve_and_price_contract


def test_tradier_is_case_insensitive(monkeypatch):
    broker = _reload_broker(monkeypatch, "TRADIER")
    import lavish_core.trade.broker_tradier as tradier

    assert broker.get_account is tradier.get_account


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("BROKER_PROVIDER", "robinhood")
    import lavish_core.trade.broker as broker
    with pytest.raises(RuntimeError, match="robinhood"):
        importlib.reload(broker)

    # Leave the module in a working state for every test that runs after
    # this one in the same process (reload failed mid-exec, but the
    # previously-imported module object is still bound in sys.modules).
    monkeypatch.setenv("BROKER_PROVIDER", "alpaca")
    importlib.reload(broker)
