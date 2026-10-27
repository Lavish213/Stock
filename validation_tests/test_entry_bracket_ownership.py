"""Invented ledger/broker races; not authentic source replay."""
from unittest.mock import Mock

from lavish_core.trade import equity_exit_monitor as em
from lavish_core.trade.exit_state import ExitState
from lavish_core.trade.state_audit import audit_state


def entry(store):
    oid = store.submit_order('AAPL', 'buy', 2)
    store.log_fill(oid, 'AAPL', 'buy', 2, 100)
    return oid


def replace(store):
    oid = store.submit_order('AAPL', 'sell', 2)
    store.log_fill(oid, 'AAPL', 'sell', 2, 100)
    return entry(store)


def leg():
    return dict(id='leg', symbol='AAPL', side='sell', qty=2, status='filled',
                filled_qty=2, filled_avg_price=110, filled_fee=.1)


def test_entry_receipt_does_not_rebind_during_delayed_launch(store):
    original = entry(store)
    old_epoch = store.get_entry_epoch(original)
    replacement = replace(store)
    assert old_epoch != store.get_entry_epoch(replacement)
    submit = Mock()
    state = ExitState(2, db_path=store.db_path, symbol='AAPL', position_epoch=store.get_entry_epoch(original))
    result = state.attempt(submit, Mock(), Mock(), Mock(), 1, lambda: None)
    assert result['status'] == 'fill_unverified'
    submit.assert_not_called()


def test_old_bracket_cannot_consume_reopened_position(store, monkeypatch):
    original = entry(store)
    replace(store)
    monkeypatch.setattr(em, 'DEFAULT_DB', store.db_path)
    monkeypatch.setattr(em, 'get_order', lambda _: leg())
    result = em.watch_bracket(original, 'AAPL', 2, 100, ['leg'])
    assert result['status'] == 'fill_unverified'
    assert store.get_position('AAPL')['qty'] == 2
    assert store.fetchall("SELECT sum(qty) FROM fills WHERE side='sell'")[0][0] == 2
    assert store.get_kill_switch()[0]


def test_bracket_reopen_inside_broker_lookup_is_quarantined(store, monkeypatch):
    original = entry(store)
    monkeypatch.setattr(em, 'DEFAULT_DB', store.db_path)
    def lookup(_):
        replace(store)
        return leg()
    monkeypatch.setattr(em, 'get_order', lookup)
    result = em.watch_bracket(original, 'AAPL', 2, 100, ['leg'])
    assert result['status'] == 'fill_unverified'
    assert store.get_position('AAPL')['qty'] == 2


def test_valid_bracket_and_duplicate_receipt_record_one_exit(store, monkeypatch):
    original = entry(store)
    monkeypatch.setattr(em, 'DEFAULT_DB', store.db_path)
    monkeypatch.setattr(em, 'get_order', lambda _: leg())
    for _ in range(2):
        assert em.watch_bracket(original, 'AAPL', 2, 100, ['leg'])['status'] == 'exited'
    assert store.get_position('AAPL') is None
    assert store.fetchall("SELECT count(*),sum(fee) FROM fills WHERE side='sell'")[0] == (1, .1)


def test_external_inventory_mismatch_revokes_existing_workers(store):
    entry(store)
    old = ExitState(2, db_path=store.db_path, symbol='AAPL')
    result = audit_state(store, [dict(symbol='AAPL', qty=1, side='long')])
    assert not result['ok']
    assert store.get_position_epoch('AAPL') is None
    submit = Mock()
    outcome = old.attempt(submit, Mock(), Mock(), Mock(), 1, lambda: None)
    assert outcome['status'] == 'fill_unverified'
    submit.assert_not_called()
    assert store.get_position('AAPL')['qty'] == 2  # Never invent an external fill.


def test_diagnostic_audit_does_not_revoke_and_matching_audit_keeps_epoch(store):
    original = entry(store)
    epoch = store.get_entry_epoch(original)
    assert not audit_state(store, [], halt_on_mismatch=False)['ok']
    assert store.get_position_epoch('AAPL') == epoch
    assert audit_state(store, [dict(symbol='AAPL', qty=2)])['ok']
    assert store.get_position_epoch('AAPL') == epoch
