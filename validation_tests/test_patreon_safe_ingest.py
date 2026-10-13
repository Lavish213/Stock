from datetime import datetime, timezone, timedelta
from unittest.mock import Mock
import json

import pytest

from lavish_core.db.hybrid_store import HybridStore
from lavish_core.patreon import patreon_trigger as pt
from lavish_core.patreon.ingest_safety import PostJournal, normalize_html


def post(identity="1", age=5, **attrs):
    return {"type": "post", "id": identity, "attributes": {
        "title": "Alert", "content": "BUY AAPL now",
        "published_at": (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(), **attrs}}


def response(data, next_url=None, status=200):
    body = json.dumps({"data": data, "links": {"next": next_url}}).encode()
    return Mock(status_code=status, iter_content=lambda size: [body])


@pytest.fixture
def journal(tmp_path):
    store = HybridStore(tmp_path / "journal.db")
    result = PostJournal(store)
    yield result
    store.con.close()


@pytest.mark.parametrize("hidden", ["script", "style", "template", "noscript", "blockquote", "del"])
def test_hidden_and_quoted_text_is_not_an_alert(hidden):
    text, images = normalize_html(f"<{hidden}>SELL TSLA now</{hidden}><p>BUY &amp; hold AAPL</p><!--SELL MSFT-->")
    assert text == "BUY & hold AAPL"
    assert not images


@pytest.mark.parametrize("attr", ["hidden", 'aria-hidden="true"', 'style="display: none"', 'style="visibility:hidden"'])
def test_hidden_attributes(attr):
    assert normalize_html(f"<div {attr}><b>BUY TSLA</b></div><p>visible</p>")[0] == "visible"


@pytest.mark.parametrize("html", ["<script>BUY</div>SELL", "<p>BUY", "</div>BUY", "x" * 120001])
def test_malformed_or_large_html_requires_review(html):
    with pytest.raises(ValueError):
        normalize_html(html)


def test_image_single_quotes_entities_and_duplicates():
    assert normalize_html("<img src='https://example.invalid/a?x=1&amp;y=2'><img src='https://example.invalid/a?x=1&amp;y=2'>")[1] == ["https://example.invalid/a?x=1&y=2"]


def test_pagination_complete_before_processing(monkeypatch):
    url = f"{pt.API}/campaigns/123/posts?page[cursor]=second"
    get = Mock(side_effect=[response([post("2")], url), response([post("1")])])
    monkeypatch.setattr(pt.requests, "get", get)
    assert {p["id"] for p in pt._fetch_posts("123")} == {"1", "2"}
    assert all(call.kwargs["allow_redirects"] is False for call in get.call_args_list)


def test_oversized_response_stream_is_stopped_and_closed(monkeypatch):
    r = Mock(status_code=200, iter_content=lambda size: iter([b"x" * (2 * 1024 * 1024 + 1)]))
    monkeypatch.setattr(pt.requests, "get", Mock(return_value=r))
    with pytest.raises(ValueError, match="Oversized"):
        pt._fetch_posts("123")
    r.close.assert_called_once()


@pytest.mark.parametrize("url", ["https://evil.invalid/posts", "http://www.patreon.com/api/oauth2/v2/campaigns/123/posts", "https://www.patreon.com@evil.invalid/posts", "https://www.patreon.com/api/oauth2/v2/campaigns/999/posts", "https://www.patreon.com/api/oauth2/v2/campaigns/123/posts#token"])
def test_pagination_cannot_exfiltrate_token(monkeypatch, url):
    get = Mock(return_value=response([], url))
    monkeypatch.setattr(pt.requests, "get", get)
    with pytest.raises(ValueError):
        pt._fetch_posts("123")
    assert get.call_count == 1


def test_cycle_and_page_limit(monkeypatch):
    monkeypatch.setattr(pt.requests, "get", Mock(return_value=response([], f"{pt.API}/campaigns/123/posts?cursor=repeat")))
    with pytest.raises(ValueError, match="cycle"):
        pt._fetch_posts("123")
    monkeypatch.setattr(pt.requests, "get", Mock(side_effect=[response([], f"{pt.API}/campaigns/123/posts?cursor={n}") for n in range(20)]))
    with pytest.raises(ValueError, match="1000"):
        pt._fetch_posts("123")


@pytest.mark.parametrize("status", [301, 302, 403, 429, 500, 503])
def test_http_failures_do_not_log_body(monkeypatch, status):
    monkeypatch.setattr(pt.requests, "get", Mock(return_value=response([], status=status)))
    with pytest.raises(RuntimeError, match=f"status {status}"):
        pt._fetch_posts("123")


def test_refresh_updates_token_and_retry(monkeypatch):
    monkeypatch.setattr(pt, "ACCESS", "old")
    monkeypatch.setattr(pt, "refresh_patreon_token", lambda: "new")
    get = Mock(side_effect=[response([], status=401), response([])])
    monkeypatch.setattr(pt.requests, "get", get)
    assert pt._fetch_posts("123") == []
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer new"


def test_empty_first_snapshot_is_durable_seed(journal, monkeypatch):
    callback = Mock()
    monkeypatch.setattr(pt, "_handle_post", callback)
    pt._process_snapshot([], journal)
    assert PostJournal(journal.store).seeded()
    pt._process_snapshot([post()], journal)
    callback.assert_called_once()


def test_seed_duplicate_and_restart_never_replay(journal, monkeypatch):
    callback = Mock()
    monkeypatch.setattr(pt, "_handle_post", callback)
    initial = post()
    pt._process_snapshot([initial], journal)
    new = post("2")
    pt._process_snapshot([initial, new], journal)
    pt._process_snapshot([initial, new], PostJournal(journal.store))
    callback.assert_called_once_with(new)


@pytest.mark.parametrize("stamp", [None, "bad", "2026-10-13T12:00:00", "", 123])
def test_unpublished_invalid_timestamps_do_not_dispatch(journal, monkeypatch, stamp):
    journal.finish_seed()
    callback = Mock()
    monkeypatch.setattr(pt, "_handle_post", callback)
    pt._process_snapshot([post(published_at=stamp)], journal)
    callback.assert_not_called()


@pytest.mark.parametrize("age", [-60, 901, 3600])
def test_future_stale_publication_ignored(journal, monkeypatch, age):
    journal.finish_seed()
    callback = Mock()
    monkeypatch.setattr(pt, "_handle_post", callback)
    pt._process_snapshot([post(age=age)], journal)
    callback.assert_not_called()


def test_dispatch_failure_quarantines_stops_batch_and_halts(journal, monkeypatch):
    journal.finish_seed()
    callback = Mock(side_effect=RuntimeError("unknown broker outcome"))
    monkeypatch.setattr(pt, "_handle_post", callback)
    alert = post()
    pt._process_snapshot([alert, post("2", age=1)], journal)
    pt._process_snapshot([alert], PostJournal(journal.store))
    assert callback.call_count == 1
    assert journal.store.get_kill_switch()[0]
    assert journal.store.fetchall("SELECT status FROM patreon_receipts WHERE post_id='1'") == [("review_required",)]


def test_crash_after_claim_cannot_retry(journal, monkeypatch):
    journal.finish_seed()
    alert = post()
    assert journal.claim(alert, "processing")
    restarted = PostJournal(journal.store)
    callback = Mock()
    monkeypatch.setattr(pt, "_handle_post", callback)
    pt._process_snapshot([alert], restarted)
    callback.assert_not_called()
    assert journal.store.get_kill_switch()[0]


def test_edit_after_dispatch_requires_review(journal, monkeypatch):
    journal.finish_seed()
    callback = Mock()
    monkeypatch.setattr(pt, "_handle_post", callback)
    alert = post()
    pt._process_snapshot([alert], journal)
    edited = {**alert, "attributes": {**alert["attributes"], "content": "SELL AAPL now"}}
    pt._process_snapshot([edited], journal)
    assert callback.call_count == 1
    assert journal.store.get_kill_switch()[0]


@pytest.mark.parametrize("count", [1, 2])
def test_unavailable_or_multiple_cards_cannot_trigger(monkeypatch, count):
    monkeypatch.setattr(pt, "VISION_AUTO", True)
    monkeypatch.setattr(pt, "_download_images_from_post", lambda p: [])
    callback = Mock()
    monkeypatch.setattr(pt, "handle_alert_text", callback)
    with pytest.raises(ValueError):
        pt._handle_post(post(content="".join(f"<img src='https://example.invalid/{i}'>" for i in range(count))))
    callback.assert_not_called()


def test_visible_contract_reaches_shared_parser(monkeypatch):
    callback = Mock()
    monkeypatch.setattr(pt, "handle_alert_text", callback)
    monkeypatch.setattr(pt, "VISION_AUTO", False)
    monkeypatch.delenv("PATRON_DOLLARS", raising=False)
    pt._handle_post(post(title="Trade", content="<p>BTO <b>AAPL</b> 250 CALL 2026-10-16</p>"))
    assert callback.call_args.kwargs["text"] == "Trade\nBTO AAPL 250 CALL 2026-10-16"


def test_multiple_symbols_do_not_mix_trades(monkeypatch):
    callback = Mock()
    monkeypatch.setattr(pt, "handle_alert_text", callback)
    with pytest.raises(ValueError, match="Multiple symbols"):
        pt._handle_post(post(content="BUY AAPL and SELL TSLA"))
    callback.assert_not_called()


@pytest.mark.parametrize("instruction", ["STC", "SELL", "CLOSE", "EXIT", "selling", "trimming", "STO", "BTC"])
def test_option_exit_never_becomes_buy(monkeypatch, instruction):
    from lavish_core.trading import alert_handler as handler
    callback = Mock()
    monkeypatch.setattr(handler, "execute_trade_from_post", callback)
    pt._handle_post(post(content=f"<p>{instruction} AAPL 250 CALL 2026-10-16</p>"))
    callback.assert_not_called()


def test_real_option_parser_keeps_contract_fields(monkeypatch):
    from lavish_core.trading import alert_handler as handler
    callback = Mock()
    monkeypatch.setattr(handler, "execute_trade_from_post", callback)
    monkeypatch.delenv("PATRON_DOLLARS", raising=False)
    pt._handle_post(post(content="<p>BTO AAPL 250 CALL 2026-10-16</p>"))
    signal = callback.call_args.args[0]
    assert (signal["ticker"], signal["side"], signal["strike"], signal["expiry"], signal["action"]) == ("AAPL", "CALL", 250, "2026-10-16", "BUY")


@pytest.mark.parametrize("image_instruction", ["STC AAPL 250 CALL 2026-10-16", "Maybe BUY AAPL 250 CALL 2026-10-16", "Yesterday BUY AAPL 250 CALL 2026-10-16"])
def test_ocr_instructions_remain_actionable_gate(monkeypatch, image_instruction):
    from lavish_core.trading import alert_handler as handler
    from lavish_core.vision import extract_signal as vision
    callback = Mock()
    monkeypatch.setattr(handler, "execute_trade_from_post", callback)
    monkeypatch.setattr(vision, "ocr_image", lambda p: image_instruction)
    handler.handle_alert_text("Trade card", image_path="synthetic.png", source="patreon")
    callback.assert_not_called()


def test_caption_cannot_hide_ocr_close_instruction(monkeypatch):
    from lavish_core.trading import alert_handler as handler
    from lavish_core.vision import extract_signal as vision
    callback = Mock()
    monkeypatch.setattr(handler, "execute_trade_from_post", callback)
    monkeypatch.setattr(vision, "ocr_image", lambda p: "STC AAPL 250 CALL 2026-10-16")
    handler.handle_alert_text("BUY AAPL 250 CALL 2026-10-16", image_path="synthetic.png", source="patreon")
    callback.assert_not_called()


@pytest.mark.parametrize("action", ["SELL", "STC", "STO", "BTC", "SELL_TO_CLOSE"])
def test_low_level_option_entry_rejects_close_actions(monkeypatch, action):
    from lavish_core.trading import trade_handler as handler
    quote = Mock()
    monkeypatch.setattr(handler, "latest_quote", quote)
    handler.execute_trade_from_post({"ticker": "AAPL", "side": "CALL", "strike": 250,
                                    "expiry": "2026-10-16", "confidence": .9, "action": action})
    quote.assert_not_called()


def test_ambiguous_campaign_is_not_guessed(monkeypatch):
    monkeypatch.setattr(pt, "CAMPAIGN", "")
    monkeypatch.setattr(pt.requests, "get", Mock(return_value=Mock(status_code=200, json=lambda: {"included": [{"type": "campaign", "id": "1"}, {"type": "campaign", "id": "2"}]})))
    with pytest.raises(RuntimeError, match="ambiguous"):
        pt._ensure_campaign_id()


@pytest.mark.parametrize("stamp,expected", [("2026-10-16", "2026-10-16"), ("2026-10-15", "2026-10-15"), ("2026-02-30", None)])
def test_explicit_iso_expiry_not_nudged(stamp, expected):
    from lavish_core.vision.extract_signal import parse_text
    assert parse_text(f"BTO AAPL 250 CALL {stamp}")["expiry"] == expected
