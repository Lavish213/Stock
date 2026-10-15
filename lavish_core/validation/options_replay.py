"""Offline replay through production options HTTP adapter, exit journal and reporting."""
import json,os
from datetime import date,timedelta
from unittest.mock import patch
from lavish_core.validation.options_exchange import OptionsExchange,Contract
from lavish_core.trade import options_broker as ob
from lavish_core.trade.exit_state import ExitState
from lavish_core.db.hybrid_store import HybridStore
from lavish_core.reporting import track_record

class Response:
    def __init__(self,body,status=200):self.body=body;self.status_code=status;self.text='synthetic response'
    def json(self):return self.body

def replay(db_path,ticker='SPY',side='CALL',qty=1,entry=1.,exit_price=1.2):
    expiry=date.today()+timedelta(days=14)
    symbol=ob.build_occ_symbol(ticker,expiry,side,200)
    x=OptionsExchange();x.add_contract(Contract(symbol,ticker,expiry,side,200),entry,entry)
    submitted=[]
    def post(url,*,data,**kwargs):
        payload=json.loads(data);submitted.append(payload)
        assert payload['position_intent']==('buy_to_open' if payload['side']=='buy' else 'sell_to_close')
        return Response(dict(x.submit(payload['symbol'],payload['side'].upper(),int(payload['qty']),float(payload['limit_price']),payload['client_order_id'])),201)
    with patch.dict(os.environ,{'TRADE_MODE':'paper'}),patch.object(ob,'_check_keys',lambda:None),patch.object(ob.SESSION,'post',post):
        s=HybridStore(db_path)
        try:
            order=ob.place_option_order(symbol,'buy',qty,limit_price=entry,client_order_id='entry')
            oid=s.submit_order(symbol,'buy',qty,venue='paper',status='filled',client_id='entry',meta={'source':'synthetic-model'})
            s.log_fill(oid,symbol,'buy',order['filled_qty'],order['filled_avg_price'],fee=order['filled_fee'],venue='paper',event_id='entry')
            x.quote(symbol,exit_price,exit_price)
            state=ExitState(qty,db_path=db_path,symbol=symbol,venue='paper')
            result=state.attempt(lambda remaining,cid:ob.place_option_order(symbol,'sell',remaining,limit_price=exit_price,client_order_id=cid),lambda cid:x.orders.get(x.clients.get(cid)),lambda oid:dict(x.orders[oid]),lambda oid:x.cancel(oid),2,lambda:None)
            if not result or result['status']!='exited':raise AssertionError('Exit failed')
            with patch.object(track_record,'fetch_price_history',lambda *a:None):report=track_record.build_report(s)
            net=sum(row['pnl'] for row in report['round_trips'])
            expected=qty*100*(exit_price-entry)-qty*1.3
            if abs(net-expected)>.011 or abs(x.cash-100000-expected)>.011:raise AssertionError('Cash/ledger mismatch')
            if s.get_position(symbol) is not None or x.positions:raise AssertionError('Residual position')
            return dict(synthetic=True,passed=True,source='synthetic-model',profitability_evidence=False,net_pnl=net,cash=x.cash,orders=len(submitted),fills=s.fetchall('SELECT count(*) FROM fills')[0][0])
        finally:s.con.close()
