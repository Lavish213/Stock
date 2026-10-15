# Default to "market is open" for every test. Most tests exercise trade
# logic that has nothing to do with market hours, but REQUIRE_MARKET_OPEN
# (lavish_core.trading.trade_handler) gates every paper/live entry on a
# real get_clock() call now. Without this, several pre-existing tests
# were found to still report "pass" after that gate was added, but for
# the wrong reason: the gate's own terminal skip (handled=True, broker
# call never made) happened to match what those tests already asserted,
# silently replacing the behavior they were actually meant to cover. A
# test that wants to exercise the market-hours gate itself overrides this
# fixture's patch with its own.
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _default_market_open(monkeypatch):
    import lavish_core.trading.trade_handler as th
    monkeypatch.setattr(th, "get_clock", lambda: {"is_open": True})
    monkeypatch.setattr(th, "get_account", lambda: {"equity": 100000})
    monkeypatch.setattr(th, "get_order_by_client_id", lambda *args: None)


# Default the candle-confirmation gatekeeper OFF for every test. Same
# reasoning/precedent as _default_market_open above: most existing tests
# exercise entry logic (sizing, risk gates, fill confirmation, broker
# errors) that has nothing to do with candle confirmation, and that logic
# now runs on a background thread only once the gate confirms - without
# this, those tests would just see "handled=True, nothing called yet"
# (the gate thread hasn't run) and silently stop testing what they were
# written to cover. test_candle_gatekeeper*.py explicitly re-enables this
# to exercise the gate itself.
@pytest.fixture(autouse=True)
def _default_gatekeeper_disabled(monkeypatch):
    import lavish_core.trading.trade_handler as th
    monkeypatch.setattr(th, "GATEKEEPER_ENABLED", False)


@pytest.fixture(autouse=True)
def _default_exit_lookup_confirms_absent(monkeypatch,tmp_path):
    from lavish_core.trade import options_exit_monitor, equity_exit_monitor
    for module in (options_exit_monitor, equity_exit_monitor):
        monkeypatch.setattr(module,"get_order_by_client_id",lambda *args:None)
        monkeypatch.setattr(module,"DEFAULT_DB",tmp_path / "exit-journal.duckdb")
