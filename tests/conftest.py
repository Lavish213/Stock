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
