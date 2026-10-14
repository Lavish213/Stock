from datetime import date, timedelta
from unittest.mock import Mock
import pytest
from lavish_core.trading import trade_handler as th
from lavish_core.trade.options_broker import build_occ_symbol
from lavish_core.db.hybrid_store import HybridStore

@pytest.mark.parametrize('poll', [False, True])
@pytest.mark.parametrize('change', ['id','symbol','side','client','qty','incomplete','fee'])
def test_option_execution_identity_mismatch_never_books(paper_boundary, monkeypatch, poll, change):
    expiry = date.today()+timedelta(days=7)
    symbol = build_occ_symbol('AAPL',expiry,'CALL',100)
    monkeypatch.setattr(th,'POSITION_SIZE_SCALE',1)
    monkeypatch.setattr(th,'resolve_and_price_contract',lambda *a,**k: dict(symbol=symbol,mid_price=1,bid=.99,ask=1.01))
    monkeypatch.setattr(th.time,'sleep',lambda _:None)
    remote = {}
    def submit(**kwargs):
        remote.update(id='remote',symbol=symbol,side='buy',client_order_id=kwargs['client_order_id'],qty=kwargs['qty'],
                      status='filled',filled_qty=kwargs['qty'],filled_avg_price=1,filled_fee=0)
        if change=='id': remote['id']='foreign'
        elif change=='symbol': remote['symbol']=build_occ_symbol('NVDA',expiry,'CALL',100)
        elif change=='side': remote['side']='sell'
        elif change=='client': remote['client_order_id']='foreign'
        elif change=='qty': remote['qty']=kwargs['qty']+1
        elif change=='incomplete': remote['filled_qty']=1
        else: remote['filled_fee']=-1
        # A first response alone establishes broker ID; its other identity
        # fields still must match. ID-change only applies to polling.
        if change=='id' and not poll: remote['client_order_id']='foreign'
        return dict(id='remote',status='new') if poll else remote
    monkeypatch.setattr(th,'place_option_order',submit)
    monkeypatch.setattr(th,'broker_get_order',lambda _:remote)
    monkeypatch.setattr(th,'broker_cancel_order',Mock())
    th.execute_trade_from_post(dict(ticker='AAPL',side='CALL',strike=100,expiry=expiry.isoformat(),confidence=1,amount_usd=300,source='test'))
    store=HybridStore(th.DEFAULT_DB)
    try:
        assert not store.fetchall('SELECT * FROM fills')
        assert store.get_open_orders()[0]['status']=='fill_unverified'
        assert store.get_kill_switch()[0]
    finally:store.con.close()
