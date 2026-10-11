# Covers a real bug found and fixed: poll_loop() used to call
# _ensure_campaign_id() BEFORE the inner loop's own try/except even
# existed, so a failure during setup (a transient Patreon blip at the
# exact moment the bot started, a token refresh failing once) killed the
# whole Patreon background thread permanently and silently - the rest of
# the process (including the watchdog ping thread) kept looking perfectly
# healthy with nobody watching Patreon ever again. poll_loop() is now a
# thin restart wrapper around _poll_loop_inner() that must never let that
# thread die.
from __future__ import annotations

from unittest.mock import patch

import lavish_core.patreon.patreon_trigger as pt


def test_a_setup_failure_is_caught_and_retried_not_fatal(monkeypatch):
    monkeypatch.setattr(pt, "POLL_LOOP_RESTART_DELAY_SECONDS", 0)
    calls = []

    def fake_inner():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("Patreon identity endpoint unreachable")
        raise SystemExit("stop the test - inner loop reached a real run")

    with patch.object(pt, "_poll_loop_inner", side_effect=fake_inner), \
         patch.object(pt, "post_discord") as mock_alert, \
         patch.object(pt.time, "sleep"):
        try:
            pt.poll_loop()
        except SystemExit:
            pass

    assert len(calls) == 3  # survived 2 failures before the 3rd call
    assert mock_alert.call_count == 2  # alerted on each of the 2 failures


def test_a_single_clean_run_returns_without_restarting():
    with patch.object(pt, "_poll_loop_inner", return_value=None), \
         patch.object(pt, "post_discord") as mock_alert:
        pt.poll_loop()  # must not raise or loop forever

    mock_alert.assert_not_called()


def test_restart_delay_is_actually_respected(monkeypatch):
    monkeypatch.setattr(pt, "POLL_LOOP_RESTART_DELAY_SECONDS", 42)
    slept = []

    def fake_inner():
        if not slept:
            raise RuntimeError("boom")
        raise SystemExit()

    with patch.object(pt, "_poll_loop_inner", side_effect=fake_inner), \
         patch.object(pt, "post_discord"), \
         patch.object(pt.time, "sleep", side_effect=lambda s: slept.append(s)):
        try:
            pt.poll_loop()
        except SystemExit:
            pass

    assert slept == [42]
