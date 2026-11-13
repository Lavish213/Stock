# Covers run_ingest._reconcile_startup(): recovering an unmanaged open
# position after a crash/restart, or the reconciliation sweep itself
# failing, must alert a human - not just log it, since a silently-watched
# log file is exactly how an unmanaged option position could sit with no
# exit guardrail for hours after a real crash.
from __future__ import annotations

from unittest.mock import patch

import run_ingest


def test_recovered_positions_trigger_an_alert():
    with patch.object(run_ingest, "reconcile_open_positions", return_value=2), \
         patch.object(run_ingest, "post_discord") as mock_alert:
        run_ingest._reconcile_startup()

    mock_alert.assert_called_once()
    assert "2" in mock_alert.call_args[0][0]


def test_no_unmanaged_positions_does_not_alert():
    with patch.object(run_ingest, "reconcile_open_positions", return_value=0), \
         patch.object(run_ingest, "post_discord") as mock_alert:
        run_ingest._reconcile_startup()

    mock_alert.assert_not_called()


def test_reconciliation_failure_alerts_and_does_not_crash_startup():
    with patch.object(run_ingest, "reconcile_open_positions", side_effect=RuntimeError("broker down")), \
         patch.object(run_ingest, "post_discord") as mock_alert:
        run_ingest._reconcile_startup()  # must not raise - startup continues either way

    mock_alert.assert_called_once()
    assert "FAILED" in mock_alert.call_args[0][0]


# --- Regression: a failed broker read must never look like a clean sweep ---

def test_reconcile_open_positions_raises_when_broker_positions_unavailable():
    from lavish_core.trade import reconcile
    import pytest
    with patch.object(reconcile, "get_positions", side_effect=RuntimeError("broker down")):
        with pytest.raises(RuntimeError, match="broker positions unavailable"):
            reconcile.reconcile_open_positions()


def test_startup_broker_read_failure_alerts_instead_of_reporting_nothing_found():
    from lavish_core.trade import reconcile
    with patch.object(reconcile, "get_positions", side_effect=RuntimeError("broker down")), \
         patch.object(run_ingest, "post_discord") as mock_alert:
        run_ingest._reconcile_startup()
    mock_alert.assert_called_once()
    assert "FAILED" in mock_alert.call_args[0][0]


def test_unclean_startup_audit_is_alerted():
    import lavish_core.trade.reconcile as reconcile
    with patch.object(reconcile, "synchronize_broker_state",
                      return_value={"ok": False, "reasons": ["broker_positions_unavailable"]}), \
         patch.object(run_ingest, "post_discord") as mock_alert:
        result = run_ingest._synchronize_startup()
    assert result["ok"] is False
    mock_alert.assert_called_once()
    assert "NOT clean" in mock_alert.call_args[0][0]


def test_clean_startup_audit_does_not_alert():
    import lavish_core.trade.reconcile as reconcile
    with patch.object(reconcile, "synchronize_broker_state", return_value={"ok": True, "reasons": []}), \
         patch.object(run_ingest, "post_discord") as mock_alert:
        run_ingest._synchronize_startup()
    mock_alert.assert_not_called()


def test_startup_audit_exception_halts_entries_alerts_and_does_not_crash(tmp_path, monkeypatch):
    import lavish_core.trade.reconcile as reconcile
    from lavish_core.db.hybrid_store import HybridStore
    db_path = tmp_path / "test.duckdb"
    monkeypatch.setattr(run_ingest, "DEFAULT_DB", db_path)
    with patch.object(reconcile, "synchronize_broker_state", side_effect=RuntimeError("db locked")), \
         patch.object(run_ingest, "post_discord") as mock_alert:
        result = run_ingest._synchronize_startup()  # must not raise
    assert result["ok"] is False
    mock_alert.assert_called_once()
    assert HybridStore(duckdb_path=db_path).get_kill_switch()[0] is True
