"""Synthetic same-symbol close/reopen races, never authentic trading evidence."""
from unittest.mock import Mock

from lavish_core.trade.exit_state import ExitState


def fill(store, side, qty):
    order = store.submit_order("AAPL", side, qty)
    store.log_fill(order, "AAPL", side, qty, 100)


def reply(qty, client_id):
    return dict(id="close", status="filled", side="sell", symbol="AAPL",
                qty=qty, client_order_id=client_id, filled_qty=qty,
                filled_avg_price=110, filled_fee=0)


def attempt(state, submit):
    return state.attempt(submit, Mock(), Mock(), Mock(), 1, lambda: None)


def test_old_worker_cannot_sell_same_quantity_reopened_position(store):
    fill(store, "buy", 2)
    state = ExitState(2, db_path=store.db_path, symbol="AAPL")
    fill(store, "sell", 2)
    fill(store, "buy", 2)
    submit = Mock(side_effect=reply)
    result = attempt(state, submit)
    submit.assert_not_called()
    assert result["status"] == "fill_unverified"
    assert store.get_position("AAPL")["qty"] == 2
    assert store.get_kill_switch()[0]


def test_reopen_during_broker_call_cannot_book_old_fill_against_new_position(store):
    fill(store, "buy", 2)
    state = ExitState(2, db_path=store.db_path, symbol="AAPL")
    def submit(qty, client_id):
        fill(store, "sell", 2)
        fill(store, "buy", 2)
        return reply(qty, client_id)
    result = attempt(state, submit)
    assert result["status"] == "fill_unverified"
    assert store.get_position("AAPL")["qty"] == 2
    assert store.fetchall("SELECT sum(qty) FROM fills WHERE side='sell'")[0][0] == 2
    assert store.get_kill_switch()[0]


def test_epoch_survives_scale_in_trim_and_store_restart(store):
    from lavish_core.db.hybrid_store import HybridStore
    fill(store, "buy", 2)
    epoch = store.get_position_epoch("AAPL")
    assert epoch
    fill(store, "buy", 1)
    fill(store, "sell", 1)
    reopened = HybridStore(store.db_path)
    try:
        assert reopened.get_position_epoch("aapl") == epoch
    finally:
        reopened.con.close()
    result = attempt(ExitState(2, db_path=store.db_path, symbol="AAPL"), reply)
    assert result["status"] == "exited"
    assert store.get_position_epoch("AAPL") is None
    fill(store, "buy", 2)
    assert store.get_position_epoch("AAPL") != epoch
    assert not store.get_kill_switch()[0]


def test_old_journal_cannot_be_adopted_by_new_position(store):
    fill(store, "buy", 2)
    state = ExitState(2, db_path=store.db_path, symbol="AAPL")
    pending = dict(id="pending", status="new", filled_qty=0)
    assert state.attempt(lambda *args: pending, Mock(), lambda *args: pending,
                         Mock(), 1, lambda: None) is None
    fill(store, "sell", 2)
    fill(store, "buy", 2)
    submit = Mock(side_effect=reply)
    result = attempt(ExitState(2, db_path=store.db_path, symbol="AAPL"), submit)
    assert result["status"] == "fill_unverified"
    submit.assert_not_called()
    assert store.get_open_orders("AAPL")[0]["status"] == "fill_unverified"


def test_matching_epoch_partial_close_can_resume_after_restart(store):
    fill(store, "buy", 2)
    state = ExitState(2, db_path=store.db_path, symbol="AAPL")
    pending = dict(id="pending", status="partially_filled", filled_qty=1,
                   filled_avg_price=110, filled_fee=.1)
    assert state.attempt(lambda *args: pending, Mock(), lambda *args: pending,
                         Mock(), 1, lambda: None) is None
    restarted = ExitState(1, db_path=store.db_path, symbol="AAPL")
    submit = Mock()
    assert attempt(restarted, submit) is None  # adopt durable journal first
    final = dict(pending, status="filled", filled_qty=2, filled_fee=.2)
    result = restarted.attempt(submit, lambda *args: final, Mock(), Mock(), 1, lambda: None)
    assert result["status"] == "exited"
    submit.assert_not_called()
    assert store.get_position("AAPL") is None
    assert store.fetchall("SELECT sum(fee) FROM fills WHERE side='sell'")[0][0] == .2
    assert not store.get_kill_switch()[0]


def test_missing_legacy_epoch_never_mints_ownership(store):
    fill(store, "buy", 2)
    store.execute("DELETE FROM position_epochs WHERE symbol='AAPL'")
    submit = Mock(side_effect=reply)
    result = attempt(ExitState(2, db_path=store.db_path, symbol="AAPL"), submit)
    submit.assert_not_called()
    assert result["status"] == "fill_unverified"
    assert store.get_position_epoch("AAPL") is None
    assert store.get_position("AAPL")["qty"] == 2


def test_duplicate_entry_fill_does_not_rotate_epoch(store):
    oid = store.submit_order("AAPL", "buy", 2)
    store.log_fill(oid, "AAPL", "buy", 2, 100, event_id="entry")
    epoch = store.get_position_epoch("AAPL")
    assert store.log_fill(oid, "AAPL", "buy", 2, 100, event_id="entry") is None
    assert store.get_position_epoch("AAPL") == epoch
    assert store.get_position("AAPL")["qty"] == 2


def test_supervisor_captures_epoch_before_delayed_thread_start(store, monkeypatch):
    from lavish_core.utils import exit_supervisor as supervisor
    scheduled = []
    class DelayedThread:
        def __init__(self, target, daemon):
            scheduled.append(target)
        def start(self):
            pass
    monkeypatch.setattr(supervisor.threading, "Thread", DelayedThread)
    fill(store, "buy", 2)
    submit = Mock(side_effect=reply)
    def target(*, on_exit, position_epoch):
        return attempt(ExitState(2, db_path=store.db_path, symbol="AAPL",
                                 position_epoch=position_epoch), submit)
    callback = Mock()
    supervisor.start_exit_worker(target, (), {"on_exit": callback}, symbol="AAPL",
                                 db_path=store.db_path, callback_index=0, bind_position_epoch=True)
    fill(store, "sell", 2)
    fill(store, "buy", 2)
    scheduled[0]()
    submit.assert_not_called()
    assert callback.call_args.args[0]["status"] == "fill_unverified"
    assert store.get_position("AAPL")["qty"] == 2


def test_generic_reconciliation_cannot_bypass_exit_epoch(store, monkeypatch):
    from lavish_core.trade import reconcile
    fill(store, "buy", 2)
    state = ExitState(2, db_path=store.db_path, symbol="AAPL")
    pending = dict(id="pending", status="new", filled_qty=0)
    assert state.attempt(lambda *args: pending, Mock(), lambda *args: pending,
                         Mock(), 1, lambda: None) is None
    fill(store, "sell", 2)
    fill(store, "buy", 2)
    remote = dict(pending, status="filled", filled_qty=2, filled_avg_price=110)
    monkeypatch.setattr(reconcile, "get_order_by_client_id", lambda *args: remote)
    assert reconcile.reconcile_pending_orders(store) == 0
    assert store.get_position("AAPL")["qty"] == 2
    assert store.get_kill_switch()[0]


def test_stale_worker_cannot_latch_or_clear_new_position_intent(store):
    fill(store, "buy", 2)
    old = store.get_position_epoch("AAPL")
    fill(store, "sell", 2)
    fill(store, "buy", 2)
    store.set_exit_intent("AAPL", "her_stop", 90, position_epoch=old)
    assert store.get_exit_intent("AAPL") is None
    new = store.get_position_epoch("AAPL")
    store.set_exit_intent("AAPL", "her_target", 110, position_epoch=new)
    store.clear_exit_intent("AAPL", position_epoch=old)
    assert store.get_exit_intent("AAPL")["reason"] == "her_target"
    fill(store, "sell", 2)
    store.clear_exit_intent("AAPL", position_epoch=new)
    assert store.get_exit_intent("AAPL") is None


def test_matching_epoch_does_not_revive_quarantined_journal(store):
    fill(store, "buy", 2)
    state = ExitState(2, db_path=store.db_path, symbol="AAPL")
    pending = dict(id="pending", status="new", filled_qty=0)
    assert state.attempt(lambda *args: pending, Mock(), lambda *args: pending,
                         Mock(), 1, lambda: None) is None
    store.set_order_status(state.journal_id, "fill_unverified")
    restarted = ExitState(2, db_path=store.db_path, symbol="AAPL")
    submit = Mock()
    assert attempt(restarted, submit)["status"] == "fill_unverified"
    submit.assert_not_called()
    final = dict(pending, status="filled", filled_qty=2, filled_avg_price=110)
    result = state.attempt(Mock(), Mock(), lambda *args: final, Mock(), 1, lambda: None)
    assert result["status"] == "fill_unverified"
    assert store.get_position("AAPL")["qty"] == 2
