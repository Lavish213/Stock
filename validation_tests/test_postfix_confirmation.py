"""Synthetic prerequisite mutations, not authentic creator alerts or fills."""
from datetime import date, timedelta
from unittest.mock import Mock

import pytest

from lavish_core.feeds.research_context import classify
from lavish_core.trading import alert_handler as ah


@pytest.mark.parametrize("constraint", [
    "Entry requires a hold for 5 minutes over 342.50.",
    "Entry requires holding for five minutes over 342.50.",
    "Only enter after price holds over 342.50 for 15 minutes.",
    "Wait for price to hold over 342.50 for one hour.",
])
def test_postfix_duration_cannot_dispatch(constraint, monkeypatch):
    expiry = (date.today() + timedelta(days=7)).isoformat()
    execute = Mock()
    monkeypatch.setattr(ah, "execute_trade_from_post", execute)
    text = f"BTO AAPL 345 CALL exp {expiry}. {constraint} Target 348 stop 340."
    ah.handle_alert_text(text)
    execute.assert_not_called()
    assert classify(text)["kind"] == "conditional_setup"


@pytest.mark.parametrize("note", ["", "Plan to hold for five minutes."])
def test_unconditional_entry_positive_control(note, monkeypatch):
    expiry = (date.today() + timedelta(days=7)).isoformat()
    execute = Mock()
    monkeypatch.setattr(ah, "execute_trade_from_post", execute)
    ah.handle_alert_text(f"BTO AAPL 345 CALL exp {expiry}. Target 348 stop 340. {note}")
    execute.assert_called_once()


def test_image_postfix_constraint_also_checked(monkeypatch):
    expiry = (date.today() + timedelta(days=7)).isoformat()
    execute = Mock()
    monkeypatch.setattr(ah, "execute_trade_from_post", execute)
    monkeypatch.setattr(ah, "parse_alert", lambda **kwargs: {
        "ticker": "AAPL", "ticker_score": 100, "side": "CALL",
        "strike": 345, "expiry": expiry, "confidence": .99,
        "instruction_text": "Entry requires a hold for five minutes over 342.50.",
    })
    ah.handle_alert_text("BTO AAPL", image_path="synthetic-card.png")
    execute.assert_not_called()
