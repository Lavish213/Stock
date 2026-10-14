"""Signed synthetic alerts -> production entry/exit paths -> offline broker.

No real credentials, network access, or broker orders are used. The candle
confirmation gate is disabled for this baseline and tested separately.
"""
from __future__ import annotations
import argparse
import io
import json
import math
import os
import time
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from lavish_core.validation.options_replay import Response


class Clock:
    def __init__(self):
        self.epoch=float(int(time.time()));self.elapsed=0.;self.monitor_started=None
    def time(self):return self.epoch+self.elapsed
    def sleep(self,seconds):
        self.elapsed+=seconds
        if self.elapsed>600:raise AssertionError('Synthetic scenario exceeded bounded 600-second clock')


class Broker:
    """Zero-fee deterministic model; guards real HTTP adapter payloads."""
    def __init__(self,clock,ticker,expiry,trigger,poll_interval,market_open=True):
        self.clock,self.ticker,self.expiry,self.trigger=clock,ticker,expiry,trigger
        self.poll_interval=poll_interval;self.cash=100000.;self.positions={}
        self.market_open=market_open
        self.orders={};self.clients={};self.events=[];self.posts=[]
        from lavish_core.trade.options_broker import build_occ_symbol
        self.contract=build_occ_symbol(ticker,expiry,'CALL',100)
    def tick(self):
        if self.clock.monitor_started is None:return 0
        return min(2,int((self.clock.elapsed-self.clock.monitor_started)/self.poll_interval))
    def stock_price(self):return ([100,102,106] if self.trigger=='target' else [100,98,94])[self.tick()]
    def option_price(self):return ([1,1.02,1.2] if self.trigger=='target' else [1,.98,.8])[self.tick()]
    def book(self,order,price):
        qty=float(order['qty']);symbol=order['symbol'];option=symbol==self.contract
        direction=1 if order['side']=='buy' else -1
        if direction<0 and qty>self.positions.get(symbol,0):raise AssertionError('Closing order would short')
        self.cash-=direction*qty*price*(100 if option else 1)
        self.positions[symbol]=self.positions.get(symbol,0)+direction*qty
        if not self.positions[symbol]:del self.positions[symbol]
        order.update(status='filled',filled_qty=str(qty),filled_avg_price=str(price))
        self.events.append(dict(symbol=symbol,side=order['side'],qty=qty,price=price,elapsed=self.clock.elapsed))
    def post(self,url,*,data,**kwargs):
        payload=json.loads(data);self.posts.append(payload)
        if payload['symbol'] not in (self.ticker,self.contract):raise AssertionError('Wrong instrument')
        option=payload['symbol']==self.contract
        if option and payload['position_intent']!=('buy_to_open' if payload['side']=='buy' else 'sell_to_close'):
            raise AssertionError('Wrong options intent')
        cid=payload['client_order_id']
        if cid in self.clients:return Response(dict(self.orders[self.clients[cid]]),201)
        oid='synthetic-'+str(len(self.orders)+1)
        order=dict(payload,id=oid,status='new',filled_qty='0',filled_avg_price=None)
        self.orders[oid]=order;self.clients[cid]=oid
        price=self.option_price() if option else self.stock_price()
        if option and (payload['type']!='limit' or float(payload['limit_price'])<price):
            raise AssertionError('Synthetic entry/exit is not marketable at bounded limit')
        self.book(order,price)
        if payload.get('order_class')=='bracket':
            legs=[]
            for kind in ('take_profit','stop_loss'):
                lid=oid+'-'+kind
                leg=dict(id=lid,symbol=self.ticker,side='sell',qty=payload['qty'],status='new',
                         filled_qty='0',filled_avg_price=None,type='limit' if kind=='take_profit' else 'stop',
                         trigger=kind,level=float(payload[kind]['limit_price' if kind=='take_profit' else 'stop_price']))
                self.orders[lid]=leg;legs.append(dict(leg))
            order['legs']=legs
        return Response(dict(order),201)
    def get(self,url,params=None,**kwargs):
        path=urlsplit(url).path;params=params or {}
        if path.endswith('/account'):
            return Response(dict(id='synthetic-account',equity='100000',cash=str(self.cash),
                                 options_buying_power=str(self.cash),options_trading_level=3,
                                 status='ACTIVE',trading_blocked=False,account_blocked=False))
        if path.endswith('/clock'):return Response(dict(is_open=self.market_open,next_close=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()))
        if path.endswith('/positions'):return Response([dict(symbol=s,qty=str(q)) for s,q in self.positions.items()])
        if '/positions/' in path:
            symbol=path.rsplit('/',1)[1]
            return Response(dict(symbol=symbol,qty=str(self.positions[symbol])),200) if symbol in self.positions else Response({},404)
        if path.endswith('orders:by_client_order_id'):
            oid=self.clients.get(params['client_order_id'])
            return Response(dict(self.orders[oid]),200) if oid else Response({},404)
        if '/orders/' in path:
            order=self.orders[path.rsplit('/',1)[1]]
            if order.get('trigger') and order['status']=='new':
                price=self.stock_price();level=order['level']
                if (order['trigger']=='take_profit' and price>=level) or (order['trigger']=='stop_loss' and price<=level):
                    self.book(order,level if order['trigger']=='take_profit' else price)
                    for other in self.orders.values():
                        if other is not order and other.get('trigger'):other['status']='canceled'
            return Response(dict(order))
        if path.endswith('/options/contracts'):
            return Response({'option_contracts':[dict(symbol=self.contract,underlying_symbol=self.ticker,
                expiration_date=self.expiry.isoformat(),type='call',strike_price='100',tradable=True,size='100')]})
        stamp=datetime.now(timezone.utc).isoformat()
        if '/stocks/' in path and path.endswith('/trades/latest'):
            assert path.split('/stocks/')[1].split('/')[0]==self.ticker
            return Response({'trade':{'p':self.stock_price(),'t':stamp}})
        if path.endswith('/options/quotes/latest'):
            assert params['symbols']==self.contract
            return Response({'quotes':{self.contract:{'bp':self.option_price(),'ap':self.option_price(),'t':stamp}}})
        raise AssertionError('Unexpected synthetic broker GET '+path)
    def delete(self,url,**kwargs):
        order=self.orders[urlsplit(url).path.rsplit('/',1)[1]]
        if order['status']=='filled':return Response({},422)
        order['status']='canceled';return Response({},204)


class Connection:
    def __init__(self,data):self.data=data;self.output=bytearray()
    def makefile(self,*args):return io.BytesIO(self.data)
    def settimeout(self,*args):pass
    def sendall(self,data):self.output.extend(data)


def send(inbox,text,identity,clock,*,age=0,created_epoch=None):
    from lavish_core.feeds import alert_feed as feed
    secret='f'*64  # Synthetic fixture only; never used outside this model.
    created_epoch = clock.time()-age if created_epoch is None else created_epoch
    body=json.dumps(dict(id=identity,text=text,created_at=datetime.fromtimestamp(created_epoch,timezone.utc).isoformat())).encode()
    stamp=str(int(clock.time()));signature=feed.sign(secret,stamp,body)
    headers=['POST /v1/alerts HTTP/1.1','Host: localhost','Content-Type: application/json',
             'Content-Length: '+str(len(body)),'X-Alert-Timestamp: '+stamp,'X-Alert-Signature: '+signature]
    connection=Connection(('\r\n'.join(headers)+'\r\n\r\n').encode()+body)
    feed.handler_for(inbox,secret,clock=clock.time)(connection,('127.0.0.1',1),SimpleNamespace(server_name='localhost',server_port=1))
    head,payload=bytes(connection.output).split(b'\r\n\r\n',1)
    return int(head.split()[1]),json.loads(payload)


def run_case(db_path,*,instrument='equity',ticker='AAPL',budget=200,trigger='target',dte=7,age=0,market_open=True,gate=None):
    if instrument not in ('equity','call') or trigger not in ('target','stop'):
        raise ValueError('Unsupported synthetic scenario')
    if gate not in (None,'confirmed','invalidated','timeout'):
        raise ValueError('Unsupported synthetic candle outcome')
    db_path=Path(db_path)
    if db_path.exists():raise ValueError('Use a new isolated simulation journal')
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ,{'TRADE_MODE':'paper','BROKER_PROVIDER':'alpaca','DISCORD_WEBHOOK_URL':'',
              'DEFAULT_TRADE_DOLLARS':str(budget),'DEFAULT_OPTION_TRADE_DOLLARS':str(budget),'ALERT_FEED_EXECUTE':'true'}))
        from lavish_core.trade import broker_alpaca as ba,options_broker as ob,trade_agent as ta
        from lavish_core.trade import equity_exit_monitor as eq,options_exit_monitor as op
        from lavish_core.trading import trade_handler as th,alert_handler
        from lavish_core.feeds.alert_feed import AlertInbox
        from lavish_core.db.hybrid_store import HybridStore
        clock=Clock();expiry=date.today()+timedelta(days=dte)
        interval=eq.POLL_INTERVAL_SEC if instrument=='equity' else op.POLL_INTERVAL_SEC
        model=Broker(clock,ticker,expiry,trigger,interval,market_open);monitors=[]
        for module in (th,eq,op):stack.enter_context(patch.object(module,'DEFAULT_DB',db_path))
        for module in (th,ta,eq,op):stack.enter_context(patch.object(module,'time',clock))
        stack.enter_context(patch.object(ba,'API_KEY','synthetic'))
        stack.enter_context(patch.object(ba,'API_SECRET','synthetic'))
        for name,value in [('TRADE_MODE','paper'),('GATEKEEPER_ENABLED',gate is not None),('POSITION_SIZE_SCALE',1),
                           ('DEFAULT_OPTION_TRADE_DOLLARS',budget),('get_clock',ba.get_clock)]:
            stack.enter_context(patch.object(th,name,value))
        candle_reads = []
        if gate is not None:
            from lavish_core.trade import candle_gatekeeper as gk
            # Execute the real gate and gated entry callback deterministically.
            # Worker scheduling is tested separately; OHLC and broker data here
            # are synthetic, with real closed-candle timestamps.
            class InlineThread:
                def __init__(self,target,args=(),kwargs=None,**unused):
                    self.target,self.args,self.kwargs=target,args,kwargs or {}
                def start(self):self.target(*self.args,**self.kwargs)
            stack.enter_context(patch.object(th,'threading',SimpleNamespace(Thread=InlineThread)))
            stack.enter_context(patch.object(th,'confirm_entry',gk.confirm_entry))
            for name,value in [('time',clock),('ENABLED',True),('MAX_WAIT_SECONDS',30),('POLL_INTERVAL_SEC',1)]:
                stack.enter_context(patch.object(gk,name,value))
            base=datetime.now(timezone.utc).replace(second=0,microsecond=0)-timedelta(minutes=3)
            ref=dict(t=base.isoformat(),o=100,h=101,l=99,c=100,v=1000)
            close=102 if gate=='confirmed' else 98 if gate=='invalidated' else 100
            follow=dict(t=(base+timedelta(minutes=1)).isoformat(),o=100,h=max(101,close),l=min(99,close),c=close,v=2000)
            def bars(*args,**kwargs):
                candle_reads.append(clock.elapsed)
                if model.posts:raise AssertionError('Entry submitted before candle gate finished')
                return [ref] if len(candle_reads)==1 else [ref,follow]
            stack.enter_context(patch.object(gk,'get_bars',bars))
        for method in ('get','post','delete'):stack.enter_context(patch.object(ba.SESSION,method,getattr(model,method)))
        # Every other HTTP path is a test failure, including notification I/O.
        def blocked(*args,**kwargs):raise AssertionError('External network prohibited in alert simulator')
        import requests
        stack.enter_context(patch.object(requests.sessions.Session,'request',blocked))
        stack.enter_context(patch.object(th,'watch_bracket_async',lambda **kwargs:monitors.append(('equity',kwargs))))
        stack.enter_context(patch.object(th,'watch_and_exit_async',lambda **kwargs:monitors.append(('call',kwargs))))
        inbox=AlertInbox(db_path)
        text=f'BUY {ticker} now target 105 stop 95' if instrument=='equity' else f'BTO {ticker} 100 CALL exp {expiry:%m/%d/%Y} target 105 stop 95'
        created_epoch=clock.time()-age
        began=time.perf_counter();status,receipt=send(inbox,text,'entry-1',clock,age=age,created_epoch=created_epoch)
        expected=age<=120 and age>=0 and math.isfinite(budget) and budget>=100 and market_open and (instrument=='equity' or dte>=0) and gate in (None,'confirmed')
        if status==202:inbox.dispatch_once(alert_handler.handle_alert_text,execute=True,now=clock.time())
        dispatch_ms=(time.perf_counter()-began)*1000
        # Repeated delivery must never create another model entry.
        duplicate_status,duplicate=send(inbox,text,'entry-1',clock,age=age,created_epoch=created_epoch)
        if status==202 and (duplicate_status!=200 or duplicate.get('duplicate') is not True):
            raise AssertionError('Same alert receipt did not deduplicate')
        inbox.dispatch_once(alert_handler.handle_alert_text,execute=True,now=clock.time())
        if bool(model.posts)!=expected:raise AssertionError('Entry acceptance differs from independent scenario rules')
        outcome=None
        if expected:
            entry=model.events[0];symbol=ticker if instrument=='equity' else model.contract
            if gate is not None and (len(candle_reads)<2 or entry['elapsed']<candle_reads[1]):
                raise AssertionError('Entry did not wait for a confirming candle')
            qty=math.floor(budget/100)
            if entry['symbol']!=symbol or entry['qty']!=qty or entry['side']!='buy':raise AssertionError('Wrong entry identity or quantity')
            if entry['price']*entry['qty']*(100 if instrument=='call' else 1)>budget:raise AssertionError('Entry exceeded premium/notional cap')
            if len(monitors)!=1:raise AssertionError('Exactly one exit supervisor required')
            clock.monitor_started=clock.elapsed
            kind,kwargs=monitors[0]
            outcome=eq.watch_bracket(**kwargs) if kind=='equity' else op.watch_and_exit(**kwargs)
            if outcome.get('status')!='exited' or model.positions:raise AssertionError('Exit left unresolved exposure')
            exit_event=model.events[-1]
            trigger_at=clock.monitor_started+2*interval
            if exit_event['elapsed']<trigger_at or exit_event['elapsed']>trigger_at+interval:
                raise AssertionError('Exit did not follow first eligible observed trigger tick')
            if exit_event['symbol']!=symbol or exit_event['qty']!=qty or exit_event['side']!='sell':raise AssertionError('Wrong closing identity or quantity')
        store=HybridStore(db_path)
        try:
            fills=store.fetchall('SELECT symbol,side,qty,price FROM fills ORDER BY ts,id')
            if len(fills)!=(2 if expected else 0) or store.fetchall('SELECT count(*) FROM positions')[0][0]:
                raise AssertionError('Ledger fills or final holdings inconsistent')
            ledger_cash=100000+sum((1 if side=='sell' else -1)*qty*price*(100 if instrument=='call' else 1) for _,side,qty,price in fills)
            if abs(ledger_cash-model.cash)>.001:raise AssertionError('Model cash and independent ledger differ')
        finally:store.con.close()
        return dict(synthetic=True,passed=True,real_broker_orders=0,profitability_evidence=False,
                    instrument=instrument,ticker=ticker,budget=budget,expiry=expiry.isoformat() if instrument=='call' else None,
                    alert_text=text,trigger=trigger,age=age,market_open=market_open,entry_expected=expected,
                    candle_gate=gate,candle_reads=len(candle_reads),
                    expected_qty=math.floor(budget/100) if expected else 0,
                    receipt_http_status=status,dispatch_wall_ms=round(dispatch_ms,3),
                    events=model.events,fill_count=len(fills),final_cash=model.cash,final_positions=0,
                    scope='production signed HTTP handler, inbox, parser, sizing/risk logic, broker HTTP adapters and exit monitors; virtual clock and zero-fee broker; '+('real candle gate with synthetic closed bars and inline worker scheduling' if gate else 'baseline candle gate disabled'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True);args=parser.parse_args()
    args.directory.mkdir(parents=True,exist_ok=False)
    results=[]
    for instrument in ('equity','call'):
        for ticker in ('AAPL','NVDA','SPY'):
            for budget in (99,100,250,500):
                for trigger in ('target','stop'):
                    for dte in ((3,7,21) if instrument=='call' else (7,)):
                        results.append(run_case(args.directory/f'case-{len(results)}.db',instrument=instrument,ticker=ticker,budget=budget,trigger=trigger,dte=dte))
    for instrument in ('equity','call'):
        for age in (-5,121,3600):results.append(run_case(args.directory/f'case-{len(results)}.db',instrument=instrument,age=age))
        results.append(run_case(args.directory/f'case-{len(results)}.db',instrument=instrument,market_open=False))
    results.append(run_case(args.directory/f'case-{len(results)}.db',instrument='call',dte=-1))
    report=dict(synthetic=True,passed=all(r['passed'] for r in results),cases=len(results),real_broker_orders=0,results=results)
    (args.directory/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({key:report[key] for key in ('synthetic','passed','cases','real_broker_orders')}))

if __name__=='__main__':main()
