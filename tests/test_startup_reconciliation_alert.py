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
