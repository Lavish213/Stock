"""An unsupported instruction must never become a different trade."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from lavish_core.trading import alert_handler as ah, trade_handler as th
from lavish_core.vision import extract_signal as es


@pytest.mark.parametrize("text", [
    "trim AAPL", "AAPL trimming the position here", "SELL half my AAPL",
    "SELL 50% AAPL", "SELL AAPL 2 shares", "sell two shares of AAPL",
    "take some profits on AAPL", "short AAPL", "shorting AAPL here",
    "cover AAPL", "BUY AAPL to cover", "BTC AAPL",
])
def test_unsupported_equity_exit_or_short_never_dispatches(text, monkeypatch):
    execute = Mock()
    monkeypatch.setattr(ah, "execute_trade_from_post", execute)
    ah.handle_alert_text(text)
    execute.assert_not_called()


@pytest.mark.parametrize("text", [
    "BUY AAPL and MSFT now", "BUY aapl and nvda now", "SELL AAPL and SPY",
    "BTO AAPL 200 CALL exp 10/16/2026 and NVDA 100 CALL exp 10/16/2026",
])
def test_multiple_tickers_require_review(text, monkeypatch):
    execute = Mock()
    monkeypatch.setattr(ah, "execute_trade_from_post", execute)
    ah.handle_alert_text(text)
    execute.assert_not_called()
    assert "ticker" in es.parse_text(text).get("field_conflicts", [])


@pytest.mark.parametrize("field,caption,image", [
    ("target_hint", "BUY AAPL target 210 stop 190", "AAPL target 220 stop 190"),
    ("stop_hint", "BUY AAPL target 210 stop 190", "AAPL target 210 stop 180"),
    ("entry_price_est", "BUY AAPL entry $1.20", "AAPL entry $1.30"),
])
def test_caption_image_price_conflict_never_dispatches(field, caption, image, monkeypatch):
    monkeypatch.setattr(es, "parse_image", lambda *args: es.parse_text(image))
    execute = Mock()
    monkeypatch.setattr(ah, "execute_trade_from_post", execute)
    parsed = es.parse_alert(caption, Path("card.png"))
    assert field in parsed.get("field_conflicts", [])
    ah.handle_alert_text(caption, image_path="card.png")
    execute.assert_not_called()


@pytest.mark.parametrize("text", ["SELL AAPL", "selling AAPL now", "close all AAPL", "exit AAPL"])
def test_unambiguous_full_exit_still_dispatches(text, monkeypatch):
    execute = Mock()
    monkeypatch.setattr(ah, "execute_trade_from_post", execute)
    ah.handle_alert_text(text)
    assert execute.call_count == 1
    assert execute.call_args.args[0]["action"] == "SELL"


def test_structured_short_cannot_close_a_long(paper_boundary):
    th.execute_trade_from_post({"symbol": "AAPL", "action": "SHORT", "confidence": .9})
    paper_boundary.assert_not_called()


def test_image_internal_conflict_survives_merge(monkeypatch):
    monkeypatch.setattr(es, "parse_image", lambda *args: es.parse_text("BUY AAPL and MSFT"))
    parsed = es.parse_alert("BUY AAPL", Path("card.png"))
    assert "ticker" in parsed.get("field_conflicts", [])


def test_single_ticker_repeated_is_not_a_conflict():
    assert not es.parse_text("BUY AAPL; AAPL target 210 stop 190").get("field_conflicts")


@pytest.mark.parametrize("text", [
    "I am not buying AAPL", "not selling AAPL", "no buy AAPL", "BUY AAPL tomorrow",
    "BUY AAPL next week", "BUY AAPL when it breaks 200", "BUY AAPL once above 200",
    "SELL AAPL later", "BUY AAPL at the open", "BUY AAPL after earnings",
])
def test_negated_or_deferred_instruction_never_executes_now(text, monkeypatch):
    execute = Mock()
    monkeypatch.setattr(ah, "execute_trade_from_post", execute)
    ah.handle_alert_text(text)
    execute.assert_not_called()


@pytest.mark.parametrize("text,field", [
    ("BUY AAPL target 105 target 115", "target_hint"),
    ("BUY AAPL stop 95 stop 90", "stop_hint"),
    ("BTO AAPL 200 calls and 200 puts exp 10/16/2026", "side"),
])
def test_conflicting_fields_within_one_message_require_review(text, field, monkeypatch):
    execute = Mock()
    monkeypatch.setattr(ah, "execute_trade_from_post", execute)
    ah.handle_alert_text(text)
    execute.assert_not_called()
    assert field in es.parse_text(text).get("field_conflicts", [])


@pytest.mark.parametrize("level", ["-105", "0", "NaN", "Infinity"])
@pytest.mark.parametrize("label", ["target", "stop"])
def test_invalid_explicit_level_is_not_silently_discarded(level, label, monkeypatch):
    execute = Mock()
    monkeypatch.setattr(ah, "execute_trade_from_post", execute)
    ah.handle_alert_text(f"BUY AAPL {label} {level}")
    execute.assert_not_called()


@pytest.mark.parametrize("field,value", [("target_hint",90),("target_hint",100),("stop_hint",110),("stop_hint",100)])
def test_equity_rejects_invalid_source_levels_instead_of_replacing_them(field,value,paper_boundary):
    th.execute_trade_from_post({"symbol":"AAPL","action":"BUY","confidence":.9,field:value})
    paper_boundary.assert_not_called()


@pytest.mark.parametrize("side,field,value", [
    ("CALL","target_hint",90),("CALL","target_hint",100),("CALL","stop_hint",110),("CALL","stop_hint",100),
    ("PUT","target_hint",110),("PUT","target_hint",100),("PUT","stop_hint",90),("PUT","stop_hint",100),
])
def test_options_reject_wrong_direction_or_premium_like_underlying_levels(side,field,value,paper_boundary,monkeypatch):
    from datetime import date,timedelta
    expiry=date.today()+timedelta(days=3)
    contract={"symbol":"synthetic","mid_price":1,"bid":.99,"ask":1.01}
    monkeypatch.setattr(th,"resolve_and_price_contract",lambda *args,**kwargs:contract)
    submit=Mock()
    monkeypatch.setattr(th,"place_option_order",submit)
    th.execute_trade_from_post({"ticker":"AAPL","side":side,"strike":100,"expiry":expiry.isoformat(),"confidence":.9,field:value})
    submit.assert_not_called()
