# Covers kill_switch.py's CLI entry point itself (not just HybridStore's
# get_kill_switch/set_kill_switch, already covered in test_kill_switch.py) -
# specifically that toggling it actually alerts a human via Discord, not
# just a log line nobody's watching in real time.
from __future__ import annotations

import sys
from unittest.mock import patch

import kill_switch
from lavish_core.db.hybrid_store import HybridStore


def _run(monkeypatch, tmp_path, *args):
    db_path = tmp_path / "test.duckdb"
    monkeypatch.setattr(kill_switch, "DEFAULT_DB", db_path)
    monkeypatch.setattr(sys, "argv", ["kill_switch.py", *args])
    return db_path


def test_on_sets_the_flag_and_alerts(tmp_path, monkeypatch):
    db_path = _run(monkeypatch, tmp_path, "--on", "manual review")
    with patch.object(kill_switch, "post_discord") as mock_alert:
        kill_switch.main()

    store = HybridStore(duckdb_path=db_path)
    assert store.get_kill_switch() == (True, "manual review")
    mock_alert.assert_called_once()
    assert "ON" in mock_alert.call_args[0][0]
    assert "manual review" in mock_alert.call_args[0][0]


def test_off_clears_the_flag_and_alerts(tmp_path, monkeypatch):
    db_path = _run(monkeypatch, tmp_path, "--off")
    HybridStore(duckdb_path=db_path).set_kill_switch(True, "pre-existing halt")

    with patch.object(kill_switch, "post_discord") as mock_alert:
        kill_switch.main()

    store = HybridStore(duckdb_path=db_path)
    assert store.get_kill_switch() == (False, "")
    mock_alert.assert_called_once()
    assert "OFF" in mock_alert.call_args[0][0]


def test_status_does_not_alert_or_change_anything(tmp_path, monkeypatch, capsys):
    db_path = _run(monkeypatch, tmp_path, "--status")
    HybridStore(duckdb_path=db_path).set_kill_switch(True, "existing")

    with patch.object(kill_switch, "post_discord") as mock_alert:
        kill_switch.main()

    mock_alert.assert_not_called()
    store = HybridStore(duckdb_path=db_path)
    assert store.get_kill_switch() == (True, "existing")  # unchanged
    assert "KILL SWITCH IS ON" in capsys.readouterr().out
