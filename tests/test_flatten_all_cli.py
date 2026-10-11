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


def _run(monkeypatch, tmp_path, *args):
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
