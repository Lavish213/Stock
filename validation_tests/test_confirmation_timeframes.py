"""Synthetic explicit prerequisites must not become immediate BUY orders."""
from datetime import date, timedelta
from unittest.mock import Mock

import pytest

from lavish_core.trading import alert_handler as ah
from lavish_core.feeds.research_context import classify


@pytest.mark.parametrize("period", ["1-minute", "15-minute", "30m", "one minute", "one-hour", "2h"])
def test_non_five_minute_close_constraint_also_requires_review(period, monkeypatch):
    expiry = (date.today() + timedelta(days=7)).isoformat()
    execute = Mock()
    monkeypatch.setattr(ah, "execute_trade_from_post", execute)
    text = f"BTO AAPL 345 CALL exp {expiry}. Requires a {period} hold above 342.50. Target 348 stop 340."
    ah.handle_alert_text(text)
    execute.assert_not_called()
    assert classify(text)["kind"] == "conditional_setup"
