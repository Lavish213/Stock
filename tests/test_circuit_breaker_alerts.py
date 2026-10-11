# Covers circuit_breaker.py's Discord alerting on an actual trip (daily
# loss, weekly loss, consecutive losses) - this existed in the code
# already but had no test coverage of its own. Also covers _alert_once's
# dedup: repeated check_ok() calls while still tripped must not re-alert
# every time (that would spam one Discord message per incoming signal
# during a halt), and recovering back to healthy must not fire a second
# "tripped" message.
from __future__ import annotations

import time
from unittest.mock import patch

from lavish_core.db.hybrid_store import HybridStore
from lavish_core.trade import circuit_breaker


def _seed_day_baseline(store, baseline_equity):
    store.set_risk_limits({
        "circuit_breaker_day_baseline_ts": time.time(),
        "circuit_breaker_day_baseline_equity": baseline_equity,
    })


def _seed_week_baseline(store, baseline_equity):
    store.set_risk_limits({
        "circuit_breaker_week_baseline_ts": time.time(),
        "circuit_breaker_week_baseline_equity": baseline_equity,
    })


def test_daily_loss_trip_sends_one_alert(tmp_path):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    _seed_day_baseline(store, 100_000.0)
    _seed_week_baseline(store, 100_000.0)  # keep week baseline from also tripping
    with patch.object(circuit_breaker, "get_account", return_value={"equity": "90000"}), \
         patch.object(circuit_breaker, "post_discord") as mock_alert:
        ok, reason = circuit_breaker.check_ok(store)

    assert ok is False
    assert "daily loss limit" in reason
    mock_alert.assert_called_once()
    assert "Circuit breaker tripped" in mock_alert.call_args[0][0]


def test_weekly_loss_trip_sends_one_alert(tmp_path):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    _seed_day_baseline(store, 93_000.0)  # day drawdown alone stays under the daily limit
    _seed_week_baseline(store, 100_000.0)
    with patch.object(circuit_breaker, "get_account", return_value={"equity": "92000"}), \
         patch.object(circuit_breaker, "post_discord") as mock_alert:
        ok, reason = circuit_breaker.check_ok(store)

    assert ok is False
    assert "weekly loss limit" in reason
    mock_alert.assert_called_once()


def test_consecutive_losses_trip_sends_one_alert(tmp_path, monkeypatch):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    _seed_day_baseline(store, 100_000.0)
    _seed_week_baseline(store, 100_000.0)
    store.set_risk_limits({"circuit_breaker_consecutive_losses": circuit_breaker.MAX_CONSECUTIVE_LOSSES})
    with patch.object(circuit_breaker, "get_account", return_value={"equity": "100000"}), \
         patch.object(circuit_breaker, "post_discord") as mock_alert:
        ok, reason = circuit_breaker.check_ok(store)

    assert ok is False
    assert "consecutive losses" in reason
    mock_alert.assert_called_once()


def test_repeated_checks_while_still_tripped_do_not_respam(tmp_path):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    _seed_day_baseline(store, 100_000.0)
    _seed_week_baseline(store, 100_000.0)
    with patch.object(circuit_breaker, "get_account", return_value={"equity": "90000"}), \
         patch.object(circuit_breaker, "post_discord") as mock_alert:
        circuit_breaker.check_ok(store)
        circuit_breaker.check_ok(store)
        circuit_breaker.check_ok(store)

    assert mock_alert.call_count == 1  # same tripped state every time - one alert, not three


def test_recovering_to_healthy_does_not_send_a_tripped_alert(tmp_path):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    _seed_day_baseline(store, 100_000.0)
    _seed_week_baseline(store, 100_000.0)
    with patch.object(circuit_breaker, "get_account", return_value={"equity": "90000"}), \
         patch.object(circuit_breaker, "post_discord"):
        circuit_breaker.check_ok(store)  # trips once

    with patch.object(circuit_breaker, "get_account", return_value={"equity": "100000"}), \
         patch.object(circuit_breaker, "post_discord") as mock_alert:
        ok, reason = circuit_breaker.check_ok(store)

    assert ok is True
    mock_alert.assert_not_called()


def test_a_new_trip_kind_alerts_again_after_a_different_one_cleared(tmp_path, monkeypatch):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    _seed_day_baseline(store, 100_000.0)
    _seed_week_baseline(store, 100_000.0)
    with patch.object(circuit_breaker, "get_account", return_value={"equity": "90000"}), \
         patch.object(circuit_breaker, "post_discord") as mock_alert:
        circuit_breaker.check_ok(store)  # daily trip (kind=1)

    store.set_risk_limits({"circuit_breaker_consecutive_losses": circuit_breaker.MAX_CONSECUTIVE_LOSSES})
    with patch.object(circuit_breaker, "get_account", return_value={"equity": "100000"}), \
         patch.object(circuit_breaker, "post_discord") as mock_alert2:
        ok, reason = circuit_breaker.check_ok(store)  # different trip (kind=3) - equity recovered but losses didn't

    assert ok is False
    assert "consecutive losses" in reason
    mock_alert2.assert_called_once()  # a different trip kind must still alert
