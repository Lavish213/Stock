"""Adversarial recovery snapshots must not rewrite confirmed exposure."""
import json
from unittest.mock import Mock
import pytest
from lavish_core.trade import reconcile as rc, trade_agent as ta, broker_tradier as bt
from lavish_core.validation.options_replay import Response

def pending(store, side='buy'):
    if side == 'sell':
        entry = store.submit_order('AAPL', 'buy', 3)
        store.log_fill(entry, 'AAPL', 'buy', 3, 100)
    else:
        assert store.reserve_entry('recover', 'AAPL', 3, 100, ta.RiskLimits(), 100000) is None
    return store.submit_order('AAPL', side, 3, venue='paper', status='submission_unknown',
                              client_id='recover', meta={'broker_id': 'remote', 'source': 'test'})

@pytest.mark.parametrize('side', ['buy', 'sell'])
@pytest.mark.parametrize('change', [
    {'id': 'foreign'}, {'symbol': 'MSFT'}, {'side': 'wrong'},
    {'client_order_id': 'foreign'}, {'qty': 4}, {'qty': True},
    {'filled_qty': 1}, {'filled_qty': 0}, {'filled_qty': 4},
    {'filled_fee': -1}, {'status': 'unknown_broker_status'},
])
def test_contradictory_snapshot_halts_without_booking(store, monkeypatch, side, change):
    oid = pending(store, side)
    before = store.get_position('AAPL')
    remote = dict(id='remote', symbol='AAPL', side=side, client_order_id='recover',
                  qty=3, status='filled', filled_qty=3, filled_avg_price=110, filled_fee=0)
    remote.update(change)
    monkeypatch.setattr(rc, 'get_order_by_client_id', lambda _: remote)
    assert rc.reconcile_pending_orders(store) == 0
    assert store.get_position('AAPL') == before
    assert store.fetchall('SELECT count(*) FROM fills WHERE order_id=?', (oid,))[0][0] == 0
    assert store.get_open_orders()[0]['status'] == 'fill_unverified'
    assert store.get_kill_switch()[0]
    if side == 'buy':
        assert store.fetchall('SELECT count(*) FROM risk_reservations')[0][0] == 1

def test_partial_recovery_preserves_fees_and_binds_broker_id(store, monkeypatch):
    assert store.reserve_entry('recover', 'AAPL', 3, 100, ta.RiskLimits(), 100000) is None
    oid = store.submit_order('AAPL', 'buy', 3, venue='paper', status='submission_unknown', client_id='recover')
    remote = dict(id='remote', status='partially_filled', filled_qty=1, filled_avg_price=100, filled_fee=.02)
    monkeypatch.setattr(rc, 'get_order_by_client_id', lambda _: remote)
    rc.reconcile_pending_orders(store)
    assert store.fetchall('SELECT sum(fee) FROM fills WHERE order_id=?', (oid,))[0][0] == pytest.approx(.02)
    assert store.get_open_orders()[0]['meta']['broker_id'] == 'remote'
    remote.update(status='filled', filled_qty=3, filled_avg_price=110, filled_fee=.05)
    assert rc.reconcile_pending_orders(store) == 1
    assert store.fetchall('SELECT sum(qty),sum(qty*price),sum(fee) FROM fills WHERE order_id=?', (oid,))[0] == pytest.approx((3,330,.05))

def test_later_broker_id_change_keeps_confirmed_partial(store, monkeypatch):
    oid = pending(store)
    remote = dict(id='remote', status='partially_filled', filled_qty=1, filled_avg_price=100)
    monkeypatch.setattr(rc, 'get_order_by_client_id', lambda _: remote)
    rc.reconcile_pending_orders(store)
    remote.update(id='foreign', status='filled', filled_qty=3)
    rc.reconcile_pending_orders(store)
    assert store.get_position('AAPL')['qty'] == 1
    assert store.get_kill_switch()[0]

def test_tradier_normalization_preserves_supplied_order_identity():
    remote = bt._normalize_order(dict(id=1, symbol='AAPL', side='sell_to_close', quantity=3, tag='exit', status='filled'))
    assert remote['symbol'] == 'AAPL' and remote['side'] == 'sell' and remote['qty'] == 3

def test_tradier_multiple_accounts_require_explicit_selection(monkeypatch):
    monkeypatch.setattr(bt, '_ACCOUNT_ID_OVERRIDE', '')
    monkeypatch.setattr(bt, '_account_id_cache', None)
    monkeypatch.setattr(bt, '_check_keys', lambda: None)
    monkeypatch.setattr(bt.SESSION, 'get', lambda *a, **k: Response({'profile': {'account': [{'account_number': 'one'}, {'account_number': 'two'}]}}))
    with pytest.raises(RuntimeError, match='TRADIER_ACCOUNT_ID'):
        bt._account_id()

def test_duplicate_tradier_tags_are_ambiguous(monkeypatch):
    monkeypatch.setattr(bt, '_check_keys', lambda: None)
    monkeypatch.setattr(bt, '_account_id', lambda: 'test')
    monkeypatch.setattr(bt.SESSION, 'get', lambda *a, **k: Response({'orders': {'order': [{'id':1,'tag':'same'}, {'id':2,'tag':'same'}]}}))
    with pytest.raises(RuntimeError, match='Ambiguous'):
        bt.get_order_by_client_id('same')

def test_repeated_partial_snapshot_fee_increase_is_applied_once(store, monkeypatch):
    oid = pending(store)
    remote = dict(id='remote', status='partially_filled', filled_qty=1, filled_avg_price=100, filled_fee=.02)
    monkeypatch.setattr(rc, 'get_order_by_client_id', lambda _: remote)
    rc.reconcile_pending_orders(store)
    remote['filled_fee'] = .03
    rc.reconcile_pending_orders(store)
    rc.reconcile_pending_orders(store)
    assert store.fetchall('SELECT sum(qty),sum(fee) FROM fills WHERE order_id=?', (oid,))[0] == pytest.approx((1,.03))

def test_stale_response_cannot_reopen_an_order_completed_during_lookup(store, monkeypatch):
    oid = pending(store)
    def lookup(_):
        store.log_fill(oid, 'AAPL', 'buy', 3, 100)
        store.release_entry('recover')
        return dict(id='foreign', status='filled', filled_qty=1, filled_avg_price=110)
    monkeypatch.setattr(rc, 'get_order_by_client_id', lookup)
    rc.reconcile_pending_orders(store)
    assert store.fetchall('SELECT status FROM orders WHERE id=?', (oid,))[0][0] == 'filled'
    assert store.get_position('AAPL')['qty'] == 3

def test_tradier_lookup_requests_tags_and_follows_all_pages(monkeypatch):
    monkeypatch.setattr(bt,'_check_keys',lambda:None)
    monkeypatch.setattr(bt,'_account_id',lambda:'test')
    first=[{'id':i,'tag':'other'} for i in range(1000)]
    calls=[]
    def get(*a,**k):
        calls.append(k['params'])
        rows=first if k['params']['page']==1 else [{'id':'found','tag':'wanted'}]
        return Response({'orders':{'order':rows}})
    monkeypatch.setattr(bt.SESSION,'get',get)
    assert bt.get_order_by_client_id('wanted')['id']=='found'
    assert len(calls)==2 and all(p['includeTags']=='true' and p['limit']==1000 for p in calls)

@pytest.mark.parametrize('status,expected',[('pending','pending_new'),('open','new'),('ok','pending_new')])
def test_tradier_statuses_remain_visible_to_durable_recovery(status,expected):
    assert bt._normalize_order({'id':1,'status':status})['status']==expected

def test_tradier_option_identity_uses_contract_symbol():
    row=bt._normalize_order({'id':1,'symbol':'AAPL','option_symbol':'AAPL261016C00100000','side':'buy_to_open'})
    assert row['symbol']=='AAPL261016C00100000' and row['side']=='buy'
    assert 'client_order_id' not in row  # An omitted tag is not a contradictory tag.
