# Covers flatten_all.py's CLI entry point: --confirm is required (this is
# destructive and irreversible), it turns the kill switch on by default
# after flattening (otherwise the bot could immediately re-open a new
# position right after an emergency flatten), --no-kill-switch skips that,
# and a human gets alerted either way.
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

import flatten_all
from lavish_core.db.hybrid_store import HybridStore


def _run(monkeypatch, tmp_path, *args, mode="paper"):
    monkeypatch.setenv("TRADE_MODE", mode)
    monkeypatch.delenv("LIVE_TRADE", raising=False)
    db_path = tmp_path / "test.duckdb"
    monkeypatch.setattr(flatten_all, "DEFAULT_DB", db_path)
    monkeypatch.setattr(sys, "argv", ["flatten_all.py", *args])
    return db_path


def test_confirm_is_required(monkeypatch, tmp_path, capsys):
    _run(monkeypatch, tmp_path, "--reason", "test")
    with pytest.raises(SystemExit) as exc:
        flatten_all.main()
    assert exc.value.code != 0
    assert "--confirm" in capsys.readouterr().err


def test_successful_flatten_turns_on_kill_switch_and_alerts(monkeypatch, tmp_path):
    db_path = _run(monkeypatch, tmp_path, "--confirm", "--reason", "runaway bug")
    fake_result = {"status": "flattened", "n": 1, "details": [{"status": "filled", "symbol": "AAPL"}]}
    with patch.object(flatten_all.TradeAgent, "flatten_all", return_value=fake_result), \
         patch.object(flatten_all, "post_discord") as mock_alert:
        flatten_all.main()

    store = HybridStore(duckdb_path=db_path)
    assert store.get_kill_switch() == (True, "flatten_all: runaway bug")
    mock_alert.assert_called_once()
    assert "1" in mock_alert.call_args[0][0]


def test_no_kill_switch_flag_leaves_it_unchanged(monkeypatch, tmp_path):
    db_path = _run(monkeypatch, tmp_path, "--confirm", "--no-kill-switch")
    fake_result = {"status": "flattened", "n": 0, "details": []}
    with patch.object(flatten_all.TradeAgent, "flatten_all", return_value=fake_result), \
         patch.object(flatten_all, "post_discord"):
        flatten_all.main()

    store = HybridStore(duckdb_path=db_path)
    assert store.get_kill_switch() == (False, "")  # never touched


def test_a_failed_close_exits_non_zero_so_it_cannot_be_missed(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, "--confirm")
    fake_result = {"status": "flattened", "n": 1, "details": [{"status": "error", "symbol": "AAPL", "reason": "no quote"}]}
    with patch.object(flatten_all.TradeAgent, "flatten_all", return_value=fake_result), \
         patch.object(flatten_all, "post_discord"):
        with pytest.raises(SystemExit) as exc:
            flatten_all.main()
    assert exc.value.code != 0


def test_all_closed_successfully_exits_zero(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, "--confirm")
    fake_result = {"status": "flattened", "n": 1, "details": [{"status": "filled", "symbol": "AAPL"}]}
    with patch.object(flatten_all.TradeAgent, "flatten_all", return_value=fake_result), \
         patch.object(flatten_all, "post_discord"):
        flatten_all.main()  # must not raise/exit


# --- Regression: the CLI must never report a dry/no-op flatten as closing positions ---

def test_dry_mode_refuses_without_dry_run_flag(monkeypatch, tmp_path, capsys):
    db_path = _run(monkeypatch, tmp_path, "--confirm", mode="dry")
    with patch.object(flatten_all.TradeAgent, "flatten_all") as mock_flatten, \
         patch.object(flatten_all, "post_discord") as mock_alert:
        with pytest.raises(SystemExit) as exc:
            flatten_all.main()
    assert exc.value.code == 2
    mock_flatten.assert_not_called()
    mock_alert.assert_not_called()
    assert "REFUSING" in capsys.readouterr().err
    assert HybridStore(duckdb_path=db_path).get_kill_switch() == (False, "")


def test_unset_trade_mode_is_treated_as_dry_and_refuses(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, "--confirm")
    monkeypatch.delenv("TRADE_MODE", raising=False)
    with patch.object(flatten_all.TradeAgent, "flatten_all") as mock_flatten:
        with pytest.raises(SystemExit) as exc:
            flatten_all.main()
    assert exc.value.code == 2
    mock_flatten.assert_not_called()


def test_dry_run_preview_changes_nothing_and_sends_no_alert(monkeypatch, tmp_path, capsys):
    db_path = _run(monkeypatch, tmp_path, "--confirm", "--dry-run", mode="dry")
    fake_result = {"status": "flattened", "n": 1, "details": [{"status": "filled", "symbol": "AAPL"}]}
    with patch.object(flatten_all.TradeAgent, "flatten_all", return_value=fake_result), \
         patch.object(flatten_all, "post_discord") as mock_alert:
        flatten_all.main()
    mock_alert.assert_not_called()
    assert "DRY RUN" in capsys.readouterr().out
    assert HybridStore(duckdb_path=db_path).get_kill_switch() == (False, "")


def test_dry_run_flag_is_refused_when_mode_would_hit_the_broker(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, "--confirm", "--dry-run", mode="live")
    with patch.object(flatten_all.TradeAgent, "flatten_all") as mock_flatten:
        with pytest.raises(SystemExit) as exc:
            flatten_all.main()
    assert exc.value.code == 2
    mock_flatten.assert_not_called()


def test_leftover_live_trade_variable_is_rejected(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, "--confirm", mode="live")
    monkeypatch.setenv("LIVE_TRADE", "true")
    with pytest.raises(RuntimeError, match="LIVE_TRADE"):
        flatten_all.main()


def test_broker_read_failure_alerts_failed_halts_entries_and_exits_nonzero(monkeypatch, tmp_path):
    db_path = _run(monkeypatch, tmp_path, "--confirm", "--reason", "outage")
    fake_result = {"status": "error", "reason": "could not read broker positions: down", "n": 0, "details": []}
    with patch.object(flatten_all.TradeAgent, "flatten_all", return_value=fake_result), \
         patch.object(flatten_all, "post_discord") as mock_alert:
        with pytest.raises(SystemExit) as exc:
            flatten_all.main()
    assert exc.value.code == 1
    message = mock_alert.call_args[0][0]
    assert "FAILED" in message and "NOT closed" in message
    assert HybridStore(duckdb_path=db_path).get_kill_switch()[0] is True


@pytest.mark.parametrize("status", ["rejected", "canceled", "expired", "error", ""])
def test_unsuccessful_close_statuses_count_as_failed(monkeypatch, tmp_path, status):
    _run(monkeypatch, tmp_path, "--confirm")
    fake_result = {"status": "flattened", "n": 1, "details": [{"status": status, "symbol": "AAPL"}]}
    with patch.object(flatten_all.TradeAgent, "flatten_all", return_value=fake_result), \
         patch.object(flatten_all, "post_discord") as mock_alert:
        with pytest.raises(SystemExit) as exc:
            flatten_all.main()
    assert exc.value.code == 1
    assert "0 position(s) confirmed closed" in mock_alert.call_args[0][0]


@pytest.mark.parametrize("status", ["new", "accepted", "partially_filled", "pending_new"])
def test_unconfirmed_close_is_not_reported_as_closed(monkeypatch, tmp_path, status):
    _run(monkeypatch, tmp_path, "--confirm")
    fake_result = {"status": "flattened", "n": 1, "details": [{"status": status, "symbol": "AAPL260116C00200000"}]}
    with patch.object(flatten_all.TradeAgent, "flatten_all", return_value=fake_result), \
         patch.object(flatten_all, "post_discord") as mock_alert:
        with pytest.raises(SystemExit) as exc:
            flatten_all.main()
    assert exc.value.code == 1
    assert "1 submitted but not confirmed" in mock_alert.call_args[0][0]
