"""Synthetic contract mutations of observed creator confirmation language.

Not authentic historical alerts or fills. A complete contract must not turn
an explicit candle/retest requirement into an immediate entry.
"""
from datetime import date, timedelta
from unittest.mock import Mock

import pytest

from lavish_core.feeds.research_context import classify
from lavish_core.trading import alert_handler as ah


CONSTRAINTS = [
    "Entry above 342.50 confirmed five-minute hold.",
    "Requires a 5-minute close above 342.50.",
    "Wait for a five minute candle close above 342.50.",
    "Only enter after a retest holds 342.50.",
    "BTO only after the 5m candle closes above 342.50.",
    "Entry requires a retest and hold above 342.50.",
]


@pytest.mark.parametrize("constraint", CONSTRAINTS)
@pytest.mark.parametrize("surface", ["original", "upper", "newlines"])
def test_complete_contract_cannot_override_confirmation(constraint, surface, monkeypatch):
    expiry = (date.today() + timedelta(days=7)).isoformat()
    text = f"BTO AAPL 345 CALL exp {expiry}. {constraint} Target 348 stop 340."
    if surface == "upper":
        text = text.upper()
    elif surface == "newlines":
        text = text.replace(" ", "\n")
    execute = Mock()
    monkeypatch.setattr(ah, "execute_trade_from_post", execute)
    ah.handle_alert_text(text)
    execute.assert_not_called()
    assert classify(text)["kind"] == "conditional_setup"


def test_explicit_unconditional_contract_still_dispatches(monkeypatch):
    expiry = (date.today() + timedelta(days=7)).isoformat()
    execute = Mock()
    monkeypatch.setattr(ah, "execute_trade_from_post", execute)
    ah.handle_alert_text(f"BTO AAPL 345 CALL exp {expiry} target 348 stop 340")
    assert execute.call_count == 1


@pytest.mark.parametrize("update", [
    "Setup retired for the rest of the week.",
    "This trade is cancelled.",
    "The setup has been invalidated.",
    "This is a replacement setup, not a new entry.",
])
def test_retired_or_replacement_card_is_not_an_entry(update, monkeypatch):
    expiry = (date.today() + timedelta(days=7)).isoformat()
    execute = Mock()
    monkeypatch.setattr(ah, "execute_trade_from_post", execute)
    ah.handle_alert_text(f"BTO AAPL 345 CALL exp {expiry}. {update}")
    execute.assert_not_called()


@pytest.mark.parametrize("constraint", CONSTRAINTS)
def test_image_instruction_constraints_are_also_checked(constraint, monkeypatch):
    expiry = (date.today() + timedelta(days=7)).isoformat()
    execute = Mock()
    monkeypatch.setattr(ah, "execute_trade_from_post", execute)
    monkeypatch.setattr(ah, "parse_alert", lambda **kwargs: {
        "ticker": "AAPL", "ticker_score": 100, "side": "CALL",
        "strike": 345, "expiry": expiry, "confidence": .99,
        "instruction_text": constraint,
    })
    ah.handle_alert_text("BTO AAPL", image_path="synthetic-card.png")
    execute.assert_not_called()
