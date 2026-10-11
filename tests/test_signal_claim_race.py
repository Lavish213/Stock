# Covers the second half of the dedup race fix: _find_duplicate_signal is a
# SELECT, and two threads (one per alert source - Patreon and Discord - or
# two near-simultaneous retries) can both pass that SELECT before either has
# written anything, so both proceed to trade on the same real-world alert.
# HybridStore.try_claim_signal() closes this with a single
# INSERT ... ON CONFLICT DO NOTHING, read back to see which attempt's token
# actually landed. Verified below with real concurrent threads, not assumed -
# same rigor as test_hybrid_store_startup_race.py.
from __future__ import annotations

import threading
import time
from datetime import date, timedelta
from unittest.mock import patch

import lavish_core.trading.trade_handler as th
from lavish_core.db.hybrid_store import HybridStore


# ---------------------------------------------------------------------------
# Storage-layer unit tests
# ---------------------------------------------------------------------------

def test_first_claim_succeeds(tmp_path):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    assert store.try_claim_signal("AAPL|buy||", 300) is True


def test_second_claim_within_window_is_refused(tmp_path):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    assert store.try_claim_signal("AAPL|buy||", 300) is True
    assert store.try_claim_signal("AAPL|buy||", 300) is False


def test_different_keys_do_not_collide(tmp_path):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    assert store.try_claim_signal("AAPL|buy||", 300) is True
    assert store.try_claim_signal("MSFT|buy||", 300) is True
    assert store.try_claim_signal("AAPL|sell||", 300) is True


def test_stale_claim_can_be_taken_over_after_window_expires(tmp_path):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    assert store.try_claim_signal("AAPL|buy||", 0.05) is True
    time.sleep(0.1)
    assert store.try_claim_signal("AAPL|buy||", 0.05) is True  # took over the stale claim


def test_release_claim_lets_an_immediate_retry_through(tmp_path):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    assert store.try_claim_signal("AAPL|buy||", 300) is True
    store.release_claim("AAPL|buy||")
    assert store.try_claim_signal("AAPL|buy||", 300) is True  # not blocked - claim was freed


def test_release_of_an_unknown_key_is_a_harmless_no_op(tmp_path):
    store = HybridStore(duckdb_path=tmp_path / "test.duckdb")
    store.release_claim("never-claimed")  # must not raise


def test_concurrent_claims_on_the_same_key_only_one_wins(tmp_path):
    # The actual race this whole fix targets: two threads hitting the exact
    # same claim key at effectively the same instant.
    store_path = tmp_path / "race.duckdb"
    HybridStore(duckdb_path=store_path)  # pre-create schema (see startup-race test/fix)

    results = []
    barrier = threading.Barrier(2)

    def worker():
        store = HybridStore(duckdb_path=store_path)
        barrier.wait()
        results.append(store.try_claim_signal("AAPL|buy|200.0|2026-01-16", 300))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not any(t.is_alive() for t in threads)
    assert sorted(results) == [False, True]  # exactly one winner, never both, never neither


# ---------------------------------------------------------------------------
# trade_handler integration
# ---------------------------------------------------------------------------

def _equity_signal(action="BUY", symbol="AAPL"):
    return {"source": "patreon", "action": action, "symbol": symbol, "confidence": 0.9}


def _option_signal():
    expiry = (date.today() + timedelta(days=5)).isoformat()
    return {"ticker": "AAPL", "side": "CALL", "strike": 200.0, "expiry": expiry, "confidence": 0.9, "source": "patreon"}


def test_concurrent_execute_trade_from_post_only_dispatches_once(tmp_path, monkeypatch):
    # Simulates the real scenario: the same alert arrives from Patreon and
    # Discord within milliseconds, both calling execute_trade_from_post
    # concurrently. Only one may reach _execute_equity_trade.
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    HybridStore(duckdb_path=tmp_path / "test.duckdb")  # pre-create schema

    # No barrier here on purpose: with the race fixed, only ONE of the two
    # threads should ever reach this mock, so a barrier requiring both
    # parties to arrive would itself hang forever.
    def slow_exec(signal):
        time.sleep(0.05)  # widen the window so an unfixed race would be caught
        return True

    with patch.object(th, "_execute_equity_trade", side_effect=slow_exec) as mock_exec:
        threads = [threading.Thread(target=th.execute_trade_from_post, args=(_equity_signal(),)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

    assert not any(t.is_alive() for t in threads)
    assert mock_exec.call_count == 1  # the atomic claim stopped the second thread before dispatch


def test_claim_is_released_so_a_different_later_alert_is_not_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    with patch.object(th, "_execute_equity_trade", return_value=True) as mock_exec:
        th.execute_trade_from_post(_equity_signal(symbol="AAPL"))
        th.execute_trade_from_post(_equity_signal(symbol="MSFT"))
    assert mock_exec.call_count == 2  # different claim keys - no false collision


def test_option_signals_claim_on_exact_strike_and_expiry(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "DEFAULT_DB", tmp_path / "test.duckdb")
    monkeypatch.setattr(th, "CONF_FLOOR", 0.0)
    with patch.object(th, "_execute_option_trade", return_value=True) as mock_exec:
        th.execute_trade_from_post(_option_signal())
        th.execute_trade_from_post(_option_signal())  # identical strike/expiry - same claim key
    assert mock_exec.call_count == 1
