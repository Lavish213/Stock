# Covers a real gap found and fixed: "buying AAPL here" / "selling my
# position" / "shorting this" / "exiting now" / "closing out" are
# completely normal, common phrasing for the exact same intent as
# BUY/SELL/SHORT/EXIT/CLOSE - but _equity_action_from_text only matched an
# optional trailing "S" (plural), never the "-ing" gerund form, so any
# alert phrased this way was silently treated as "no clear direction" and
# skipped entirely, never even reaching the confidence question.
from __future__ import annotations

from lavish_core.trading.alert_handler import _equity_action_from_text


def test_buying_is_recognized_as_buy():
    assert _equity_action_from_text("buying AAPL now") == "BUY"
    assert _equity_action_from_text("I am buying AAPL here") == "BUY"


def test_selling_is_recognized_as_sell():
    assert _equity_action_from_text("selling AAPL now") == "SELL"


def test_shorting_requires_review_in_a_long_only_pipeline():
    assert _equity_action_from_text("shorting AAPL here") is None


def test_exiting_is_recognized_as_sell():
    assert _equity_action_from_text("exiting my AAPL position") == "SELL"


def test_closing_is_recognized_as_sell():
    # Spelling matters here - naive "CLOSE" + "ING" would be "CLOSEING",
    # not the real English spelling "CLOSING".
    assert _equity_action_from_text("closing out AAPL") == "SELL"


def test_calling_and_putting_are_deliberately_not_recognized():
    # CALL/PUT's gerunds (CALLING/PUTTING) are common, everyday English
    # words unrelated to trading - adding them would be a real false-
    # positive risk ("calling my broker", "putting my stop at X").
    assert _equity_action_from_text("calling my broker about AAPL") is None
    assert _equity_action_from_text("putting my stop at 150 on AAPL") is None


def test_longing_is_deliberately_not_recognized():
    # Not real trading phrasing - "going long" is a different pattern,
    # and LONG has no natural gerund that means anything here.
    assert _equity_action_from_text("longing AAPL here") is None


def test_imperative_forms_still_work_unchanged():
    assert _equity_action_from_text("BUY AAPL now") == "BUY"
    assert _equity_action_from_text("SELL AAPL") == "SELL"
    assert _equity_action_from_text("grabbing calls on AAPL") == "BUY"
    assert _equity_action_from_text("loading puts on AAPL") == "SELL"
