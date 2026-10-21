from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lavish_core.db.hybrid_store import HybridStore
from lavish_core.trade import options_exit_monitor as monitor
from lavish_core.trade import reconcile


def _fast_clock():
    return SimpleNamespace(time=Mock(side_effect=range(100)), sleep=Mock())


def test_exit_intent_round_trip_and_validation(store):
    store.set_exit_intent("SPY261016C00770000", "her_stop", 95)
    assert store.get_exit_intent("SPY261016C00770000")["reason"] == "her_stop"
    with pytest.raises(ValueError):
        store.set_exit_intent("SPY261016C00770000", "buy_more", 95)
    store.clear_exit_intent("SPY261016C00770000")
    assert store.get_exit_intent("SPY261016C00770000") is None


def test_trigger_is_durable_before_broker_attempt(store, monkeypatch):
    symbol = "SPY261016C00770000"
    monkeypatch.setattr(monitor, "DEFAULT_DB", store.db_path)
    monkeypatch.setattr(monitor, "time", _fast_clock())
    monkeypatch.setattr(monitor, "MAX_MONITOR_HOURS", 3 / 3600)
    monkeypatch.setattr(monitor, "latest_quote", Mock(return_value=90))
    monkeypatch.setattr(monitor, "_option_limit_price", Mock(return_value=1))
    monkeypatch.setattr(monitor, "post_discord", Mock())
    attempt = Mock(side_effect=KeyboardInterrupt("synthetic process death"))
    monkeypatch.setattr(
        monitor, "ExitState", lambda *a, **k: SimpleNamespace(pending=None, unknown=False, attempt=attempt)
    )
    with pytest.raises(KeyboardInterrupt):
        monitor.watch_and_exit("SPY", symbol, 1, "call", 1, stop_underlying=95)
    reopened = HybridStore(store.db_path)
    try:
        intent = reopened.get_exit_intent(symbol)
    finally:
        reopened.con.close()
    assert intent["reason"] == "her_stop" and intent["underlying_price"] == 90


def test_reconcile_passes_durable_intent_but_positive_control_has_none(store, monkeypatch):
    monkeypatch.setattr(reconcile, "DEFAULT_DB", store.db_path)
    symbol = "SPY261016C00770000"
    control = "QQQ261016C00600000"
    store.set_exit_intent(symbol, "our_stop_loss", 700)
    positions = [
        {"asset_class": "us_option", "side": "long", "symbol": symbol, "qty": "1", "avg_entry_price": "1"},
        {"asset_class": "us_option", "side": "long", "symbol": control, "qty": "1", "avg_entry_price": "1"},
    ]
    monkeypatch.setattr(reconcile, "get_positions", lambda: positions)
    monkeypatch.setattr(reconcile, "_find_original_target_stop", lambda _: (None, None))
    launch = Mock()
    monkeypatch.setattr(reconcile, "watch_and_exit_async", launch)
    monkeypatch.setattr(reconcile, "post_discord", Mock())
    reconcile._watched.clear()
    assert reconcile.reconcile_open_positions() == 2
    by_symbol = {call.kwargs["contract_symbol"]: call.kwargs for call in launch.call_args_list}
    assert by_symbol[symbol]["resume_exit_reason"] == "our_stop_loss"
    assert by_symbol[symbol]["resume_underlying_price"] == 700
    assert by_symbol[control]["resume_exit_reason"] is None


def test_reconcile_does_not_attach_old_intent_to_a_later_entry(store, monkeypatch):
    symbol = "SPY261016C00770000"
    monkeypatch.setattr(reconcile, "DEFAULT_DB", store.db_path)
    store.set_exit_intent(symbol, "her_stop", 90)
    oid = store.submit_order(symbol, "buy", 1, status="filled")
    store.log_fill(oid, symbol, "buy", 1, 1)
    monkeypatch.setattr(
        reconcile,
        "get_positions",
        lambda: [{
            "asset_class": "us_option", "side": "long", "symbol": symbol,
            "qty": "1", "avg_entry_price": "1",
        }],
    )
    monkeypatch.setattr(reconcile, "_find_original_target_stop", lambda _: (None, None))
    launch = Mock()
    monkeypatch.setattr(reconcile, "watch_and_exit_async", launch)
    monkeypatch.setattr(reconcile, "post_discord", Mock())
    reconcile._watched.clear()

    assert reconcile.reconcile_open_positions() == 1
    assert launch.call_args.kwargs["resume_exit_reason"] is None
    assert store.get_exit_intent(symbol) is None
    assert store.get_kill_switch()[0]


def test_reconcile_uses_delayed_fill_time_not_earlier_submission(store, monkeypatch):
    symbol = "SPY261016C00770000"
    monkeypatch.setattr(reconcile, "DEFAULT_DB", store.db_path)
    oid = store.submit_order(symbol, "buy", 1, status="submitted")
    store.set_exit_intent(symbol, "her_stop", 90)
    store.log_fill(oid, symbol, "buy", 1, 1)
    monkeypatch.setattr(
        reconcile,
        "get_positions",
        lambda: [{
            "asset_class": "us_option", "side": "long", "symbol": symbol,
            "qty": "1", "avg_entry_price": "1",
        }],
    )
    monkeypatch.setattr(reconcile, "_find_original_target_stop", lambda _: (None, None))
    launch = Mock()
    monkeypatch.setattr(reconcile, "watch_and_exit_async", launch)
    monkeypatch.setattr(reconcile, "post_discord", Mock())
    reconcile._watched.clear()

    assert reconcile.reconcile_open_positions() == 1
    assert launch.call_args.kwargs["resume_exit_reason"] is None
    assert store.get_exit_intent(symbol) is None
    assert store.get_kill_switch()[0]


def test_reconcile_keeps_intent_during_later_partial_fill_of_same_order(store, monkeypatch):
    symbol = "SPY261016C00770000"
    monkeypatch.setattr(reconcile, "DEFAULT_DB", store.db_path)
    oid = store.submit_order(symbol, "buy", 2, status="partially_filled")
    store.log_fill(oid, symbol, "buy", 1, 1)
    store.set_exit_intent(symbol, "her_stop", 90)
    store.log_fill(oid, symbol, "buy", 1, 1)
    monkeypatch.setattr(
        reconcile,
        "get_positions",
        lambda: [{
            "asset_class": "us_option", "side": "long", "symbol": symbol,
            "qty": "2", "avg_entry_price": "1",
        }],
    )
    monkeypatch.setattr(reconcile, "_find_original_target_stop", lambda _: (None, None))
    launch = Mock()
    monkeypatch.setattr(reconcile, "watch_and_exit_async", launch)
    monkeypatch.setattr(reconcile, "post_discord", Mock())
    reconcile._watched.clear()

    assert reconcile.reconcile_open_positions() == 1
    assert launch.call_args.kwargs["resume_exit_reason"] == "her_stop"
    assert store.get_exit_intent(symbol)["reason"] == "her_stop"
    assert not store.get_kill_switch()[0]


def test_reconcile_keeps_intent_when_add_on_buy_extends_same_open_position(store, monkeypatch):
    symbol = "SPY261016C00770000"
    monkeypatch.setattr(reconcile, "DEFAULT_DB", store.db_path)
    original = store.submit_order(symbol, "buy", 1, status="filled")
    store.log_fill(original, symbol, "buy", 1, 1)
    store.set_exit_intent(symbol, "her_stop", 90)
    add_on = store.submit_order(symbol, "buy", 1, status="filled")
    store.log_fill(add_on, symbol, "buy", 1, 1)
    # Make the lifecycle ordering explicit instead of relying on the database's
    # timestamp precision during this fast synthetic mutation.
    opened_at = datetime(2026, 10, 20, 14, tzinfo=timezone.utc)
    store.execute("UPDATE orders SET ts=? WHERE id=?", (opened_at, original))
    store.execute("UPDATE fills SET ts=? WHERE order_id=?", (opened_at, original))
    store.execute(
        "UPDATE exit_intents SET triggered_at=? WHERE symbol=?",
        (opened_at + timedelta(minutes=1), symbol),
    )
    store.execute("UPDATE orders SET ts=? WHERE id=?", (opened_at + timedelta(minutes=2), add_on))
    store.execute("UPDATE fills SET ts=? WHERE order_id=?", (opened_at + timedelta(minutes=2), add_on))
    assert reconcile._find_latest_entry_time(symbol) == opened_at.replace(tzinfo=None)
    monkeypatch.setattr(
        reconcile,
        "get_positions",
        lambda: [{
            "asset_class": "us_option", "side": "long", "symbol": symbol,
            "qty": "2", "avg_entry_price": "1",
        }],
    )
    monkeypatch.setattr(reconcile, "_find_original_target_stop", lambda _: (None, None))
    launch = Mock()
    monkeypatch.setattr(reconcile, "watch_and_exit_async", launch)
    monkeypatch.setattr(reconcile, "post_discord", Mock())
    reconcile._watched.clear()

    assert reconcile.reconcile_open_positions() == 1
    assert launch.call_args.kwargs["resume_exit_reason"] == "her_stop"
    assert store.get_exit_intent(symbol)["reason"] == "her_stop"
    assert not store.get_kill_switch()[0]


def test_unfilled_newer_order_cannot_hide_confirmed_entry_from_stale_check(store, monkeypatch):
    symbol = "SPY261016C00770000"
    monkeypatch.setattr(reconcile, "DEFAULT_DB", store.db_path)
    store.set_exit_intent(symbol, "her_stop", 90)
    confirmed = store.submit_order(symbol, "buy", 1, status="filled")
    store.log_fill(confirmed, symbol, "buy", 1, 1)
    store.submit_order(symbol, "buy", 1, status="partially_filled")
    monkeypatch.setattr(
        reconcile,
        "get_positions",
        lambda: [{
            "asset_class": "us_option", "side": "long", "symbol": symbol,
            "qty": "1", "avg_entry_price": "1",
        }],
    )
    monkeypatch.setattr(reconcile, "_find_original_target_stop", lambda _: (None, None))
    launch = Mock()
    monkeypatch.setattr(reconcile, "watch_and_exit_async", launch)
    monkeypatch.setattr(reconcile, "post_discord", Mock())
    reconcile._watched.clear()

    assert reconcile.reconcile_open_positions() == 1
    assert launch.call_args.kwargs["resume_exit_reason"] is None
    assert store.get_exit_intent(symbol) is None
    assert store.get_kill_switch()[0]


def test_reconcile_keeps_intent_triggered_after_current_entry(store, monkeypatch):
    symbol = "SPY261016C00770000"
    monkeypatch.setattr(reconcile, "DEFAULT_DB", store.db_path)
    oid = store.submit_order(symbol, "buy", 1, status="filled")
    store.log_fill(oid, symbol, "buy", 1, 1)
    store.set_exit_intent(symbol, "her_stop", 90)
    monkeypatch.setattr(
        reconcile,
        "get_positions",
        lambda: [{
            "asset_class": "us_option", "side": "long", "symbol": symbol,
            "qty": "1", "avg_entry_price": "1",
        }],
    )
    monkeypatch.setattr(reconcile, "_find_original_target_stop", lambda _: (None, None))
    launch = Mock()
    monkeypatch.setattr(reconcile, "watch_and_exit_async", launch)
    monkeypatch.setattr(reconcile, "post_discord", Mock())
    reconcile._watched.clear()

    assert reconcile.reconcile_open_positions() == 1
    assert launch.call_args.kwargs["resume_exit_reason"] == "her_stop"
    assert store.get_exit_intent(symbol)["reason"] == "her_stop"
    assert not store.get_kill_switch()[0]


def test_resumed_intent_closes_after_price_recovers_and_clears_journal(store, monkeypatch):
    symbol = "SPY261016C00770000"
    store.set_exit_intent(symbol, "her_stop", 90)
    monkeypatch.setattr(monitor, "DEFAULT_DB", store.db_path)
    monkeypatch.setattr(monitor, "time", _fast_clock())
    monkeypatch.setattr(monitor, "MAX_MONITOR_HOURS", 3 / 3600)
    quote = Mock(side_effect=AssertionError("resumed intent must not be canceled by a recovered price"))
    monkeypatch.setattr(monitor, "latest_quote", quote)
    monkeypatch.setattr(monitor, "_option_limit_price", Mock(return_value=1))
    monkeypatch.setattr(monitor, "post_discord", Mock())
    attempt = Mock(return_value={"status": "exited", "price": 1, "filled_qty": 1, "order": {}})
    monkeypatch.setattr(
        monitor, "ExitState", lambda *a, **k: SimpleNamespace(pending=None, unknown=False, attempt=attempt)
    )
    result = monitor.watch_and_exit(
        "SPY", symbol, 1, "call", 1,
        resume_exit_reason="her_stop", resume_underlying_price=90,
    )
    assert result["status"] == "exited" and result["reason"] == "her_stop"
    assert store.get_exit_intent(symbol) is None
    quote.assert_not_called()
