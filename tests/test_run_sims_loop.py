# Smoke test for run_sims_loop.py's core round function - not safety-
# critical trading logic (it's a backtesting helper meant to run
# unattended on a machine with real internet access), but a structural
# regression here would silently break the "non-stop simulation" loop
# without anything else catching it.
#
# _build_price_lookups/fetch_price_history hit real Yahoo Finance when
# network is available - mocked out here (same as every other test in
# this suite mocks its own network boundary) so this stays fast and
# deterministic in CI instead of depending on an external service.
from __future__ import annotations

import json
from unittest.mock import patch

import run_sims_loop
from run_sims_loop import _run_one_round


def test_one_round_returns_a_json_serializable_structured_result():
    with patch.object(run_sims_loop, "_build_price_lookups", return_value={}), \
         patch("ml.backtester.fetch_price_history", return_value=None), \
         patch("lavish_core.reporting.option_estimator.fetch_price_history", return_value=None):
        result = _run_one_round(seed=1, days=30, alerts_per_ticker=3, max_hold_days=5)

    assert "classification" in result
    assert "n_alerts" in result
    assert result["n_alerts"] > 0
    assert "equity_backtest" in result
    json.dumps(result, default=str)  # must be loggable as a single JSONL line


def test_same_seed_is_deterministic():
    with patch.object(run_sims_loop, "_build_price_lookups", return_value={}), \
         patch("ml.backtester.fetch_price_history", return_value=None), \
         patch("lavish_core.reporting.option_estimator.fetch_price_history", return_value=None):
        a = _run_one_round(seed=7, days=30, alerts_per_ticker=3, max_hold_days=5)
        b = _run_one_round(seed=7, days=30, alerts_per_ticker=3, max_hold_days=5)
    assert a["classification"] == b["classification"]
    assert a["n_alerts"] == b["n_alerts"]
