import hashlib
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from lavish_core.validation.cash_audit import reconcile,fetch_activities,money
from lavish_core.validation.options_replay import Response


@pytest.fixture
def evidence():
    receipt={'account_id_hash':hashlib.sha256(b'synthetic').hexdigest(),'observed_at':'2026-10-13T14:13:28Z',
             'entry_price':1.49,'exit_price':1.48,'entry_filled_qty':1,'cash_before':100000,'recorded_fees':0}
    account={'id':'synthetic','cash':'99998.95'}
    fee={'id':'fee-1','activity_type':'FEE','date':'2026-10-13','status':'executed','net_amount':'-.05'}
    return receipt,account,fee


def test_exact_posted_fee_resolves_cash_without_faking_fill(store,evidence):
    receipt,account,fee=evidence
    result=reconcile(store,receipt,account,[fee],['buy-id','sell-id'])
    assert result['passed'] and result['cash_reconciliation_error']==0
    assert result['posted_cash_fees']==.05
    assert store.fetchall('SELECT count(*) FROM fills')[0][0]==0


def test_repeated_fee_is_recorded_once(store,evidence):
    r,a,f=evidence
    for rows in [[f,f],[f]]:assert reconcile(store,r,a,rows,['b','s'])['passed']
    assert store.fetchall('SELECT count(*) FROM broker_cash_activities')[0][0]==1


def test_missing_fee_remains_pending_with_exact_decimal_delta(store,evidence):
    r,a,f=evidence
    result=reconcile(store,r,a,[],['b','s'])
    assert not result['passed'] and result['cash_reconciliation_error']==-.05
    assert result['status']=='pending_cash_activity_review'
    assert store.get_kill_switch()[0]


@pytest.mark.parametrize('amount',[None,True,'nan','inf','-inf','bad',0,.05])
def test_invalid_or_credit_fee_cannot_resolve(store,evidence,amount):
    r,a,f=evidence;f['net_amount']=amount
    with pytest.raises(ValueError):reconcile(store,r,a,[f],['b','s'])


@pytest.mark.parametrize('change',[{'date':'2026-10-12'},{'status':'pending'},{'order_id':'other-order'},{'id':None}])
def test_unverified_fee_attribution_stays_pending(store,evidence,change):
    r,a,f=evidence;f.update(change)
    result=reconcile(store,r,a,[f],['b','s'])
    assert not result['passed'] and result['posted_cash_fees']==0


def test_conflicting_activity_id_cannot_replace_evidence(store,evidence):
    r,a,f=evidence
    reconcile(store,r,a,[f],['b','s'])
    f['net_amount']='-.06'
    with pytest.raises(ValueError):reconcile(store,r,a,[f],['b','s'])
    assert store.fetchall('SELECT amount FROM broker_cash_activities')==[('-0.05',)]


def test_unknown_cash_movement_cannot_be_hidden_by_fee(store,evidence):
    r,a,f=evidence
    other={'id':'transfer','activity_type':'JNLC','net_amount':'100'}
    assert not reconcile(store,r,a,[f,other],['b','s'])['passed']


def test_unrelated_fill_prevents_single_cycle_attribution(store,evidence):
    r,a,f=evidence
    assert not reconcile(store,r,a,[f,{'activity_type':'FILL','order_id':'other'}],['b','s'])['passed']


def test_fill_fee_and_cash_fee_not_double_counted(store,evidence):
    r,a,f=evidence;r['recorded_fees']=.05
    assert not reconcile(store,r,a,[f],['b','s'])['passed']
    assert store.fetchall('SELECT count(*) FROM broker_cash_activities')[0][0]==0


def test_foreign_account_rejected(store,evidence):
    r,a,f=evidence;a['id']='foreign'
    with pytest.raises(ValueError):reconcile(store,r,a,[f],['b','s'])


def test_paginated_activity_snapshot_uses_cursor_not_urls():
    rows=[{'id':str(n)} for n in range(100)]
    get=Mock(side_effect=[Response(rows),Response([])])
    broker=SimpleNamespace(BASE_URL='https://paper-api.alpaca.markets',HEADERS={},SESSION=SimpleNamespace(get=get))
    assert len(fetch_activities(broker,'2026-10-13T14:13:28Z'))==100
    assert get.call_args.kwargs['params']['page_token']=='99'
    assert get.call_args.kwargs['allow_redirects'] is False


def test_cycle_or_truncation_not_silent():
    rows=[{'id':str(n)} for n in range(100)]
    broker=SimpleNamespace(BASE_URL='https://paper-api.alpaca.markets',HEADERS={},SESSION=SimpleNamespace(get=Mock(return_value=Response(rows))))
    with pytest.raises(ValueError,match='cursor'):fetch_activities(broker,'2026-10-13T14:13:28Z')


@pytest.mark.parametrize('value',[None,True,'nan','inf','-inf','1e999',{},[]])
def test_money_requires_finite_decimal(value):
    with pytest.raises(ValueError):money(value)


def test_report_includes_verified_account_fee_without_fake_allocation(store,evidence,monkeypatch):
    from lavish_core.reporting import track_record
    r,a,f=evidence
    for side,price in [('buy',1.49),('sell',1.48)]:
        oid=store.submit_order('SPY261016C00782000',side,1,status='filled')
        store.log_fill(oid,'SPY261016C00782000',side,1,price)
    from datetime import datetime,timezone
    today=datetime.now(timezone.utc).date().isoformat()
    r['observed_at']=today+'T14:13:28Z';f['date']=today
    reconcile(store,r,a,[f],['b','s'])
    monkeypatch.setattr(track_record,'fetch_price_history',lambda *a:None)
    result=track_record.build_report(store)
    assert result['fill_based_pnl']==-1
    assert result['total_pnl']==-1.05
    assert result['posted_broker_cash_fees']==.05
