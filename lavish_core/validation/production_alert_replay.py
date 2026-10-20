"""Fake creator -> production parser/gates/orders/monitor/ledger, offline only.

Only external broker/data, clock and worker scheduling are substituted.
No account/exit policy reimplementation and no outcome is forced to pass.
"""
from __future__ import annotations
import argparse
import json
import logging
import io
import random
import math
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from .options_creator_month import offline
from .options_exchange import OptionsExchange, Contract
from .options_replay import Response

# NYSE published 2026/2027 calendar, observed 2026-10-15.
# https://www.nyse.com/trade/hours-calendars
HOLIDAYS = set('2026-11-26 2026-12-25 2027-01-01 2027-01-18 2027-02-15 2027-03-26 2027-05-31 2027-06-18 2027-07-05 2027-09-06'.split())
EARLY_CLOSE = {'2026-11-27','2026-12-24'}
ET = ZoneInfo('America/New_York')
REPLAY_START_DATE = date(2026, 10, 1)

def session_clock(now):
    local=now.astimezone(ET);day=local.date()
    close=datetime.combine(day,datetime.min.time(),ET).replace(hour=13 if str(day) in EARLY_CLOSE else 16)
    opened=day.weekday()<5 and str(day) not in HOLIDAYS and (9,30)<=(local.hour,local.minute)<(close.hour,0)
    return dict(is_open=opened,next_close=close.isoformat())


def replay(output, days=365, initial=1000, seed=20261015, scenario_offset=0,
           fixed_fraction=None, stress_profile='baseline'):
    if type(days) is not int or not 1 <= days <= 366 or initial <= 0:
        raise ValueError('Invalid replay settings')
    if fixed_fraction is not None and (isinstance(fixed_fraction, bool) or not math.isfinite(fixed_fraction) or not 0 < fixed_fraction <= 1):
        raise ValueError('Simulation fraction must be finite and in (0, 1]')
    if stress_profile not in ('baseline', 'weak_followthrough', 'gap_loss'):
        raise ValueError('Unknown simulation stress profile')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    clock = SimpleNamespace(now=datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc))
    class SimDate(date):
        @classmethod
        def today(cls): return clock.now.date()
    class SimDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now.astimezone(tz) if tz else clock.now.replace(tzinfo=None)
    def sleep(seconds):
        clock.now += timedelta(seconds=max(1, seconds))
        if (clock.now - state['session_start']).total_seconds() > 3600:
            raise RuntimeError('Replay worker exceeded one virtual hour')
    simtime = SimpleNamespace(time=lambda:clock.now.timestamp(), monotonic=lambda:clock.now.timestamp(), sleep=sleep)
    class InlineThread:
        def __init__(self, target, args=(), kwargs=None, **unused):
            self.target, self.args, self.kwargs = target, args, kwargs or {}
        def start(self): self.target(*self.args, **self.kwargs)
    x = OptionsExchange(cash=initial, today=clock.now.date())
    state = dict(session_start=clock.now, underlying=770., premium=.10, kind='target',
                 bars=0, phase='entry', attempts=0, lost=False)
    monitors, records, notifications = [], [], []
    class ReplayRestart(BaseException):pass
    rng = random.Random(seed)
    journal = output/'production.duckdb'
    def normalized(order): return dict(order, side=order['side'].lower())
    def account():
        equity=x.cash+sum(q*x.quotes[s][0]*100 for s,q in x.positions.items())
        return dict(id='synthetic-only', equity=equity, cash=x.cash, buying_power=x.cash,
                    options_buying_power=x.cash, options_trading_level=2)
    def get_order(oid):
        order=x.orders[oid]
        if state['phase']=='exit' and state['kind']=='partial_exit_restart' and not state['lost']:
            state['lost']=True
            raise ReplayRestart('Lose worker after broker partial fill, before acknowledgement')
        if order['status'] in ('new','partially_filled'):
            x.quote(order['symbol'],state['premium'],state['premium'],liquidity=100)
            x.match(oid)
        return normalized(order)
    def lookup(cid):
        return normalized(x.orders[x.clients[cid]]) if cid in x.clients else None
    def get(url, **kwargs):
        if '/options/contracts' in url:
            p=kwargs.get('params',{}); expiry=date.fromisoformat(p['expiration_date_gte'])
            symbol=ob.build_occ_symbol('SPY',expiry,'CALL',770)
            x.add_contract(Contract(symbol,'SPY',expiry,'CALL',770),.1,.1,
                           liquidity=1 if state['kind']=='partial_entry' else 100)
            return Response(dict(option_contracts=[dict(symbol=symbol,underlying_symbol='SPY',
                expiration_date=expiry.isoformat(),strike_price='770',type='call',tradable=True,size='100')]))
        raise AssertionError('Unmapped offline GET: '+url)
    def post(url, *, data, **kwargs):
        p=json.loads(data); state['attempts']+=1
        if state['phase']=='exit' and state['kind']=='exit_outage' and not state['lost']:
            state['lost']=True
            raise TimeoutError('Synthetic outage before write')
        order=x.submit(p['symbol'],p['side'].upper(),int(p['qty']),float(p['limit_price']),p['client_order_id'])
        if state['kind']=='lost_ack' and p['side']=='buy' and not state['lost']:
            state['lost']=True
            raise TimeoutError('Synthetic acknowledgement lost after commit')
        return Response(normalized(order),201)
    def bars(*args, **kwargs):
        state['bars']+=1
        base=state['session_start']-timedelta(minutes=3)
        def b(t,c,h,l):return dict(t=t.isoformat(),o=770,h=h,l=l,c=c,v=2000)
        ref=b(base,770,771,769)
        if state['bars']==1:return [ref]
        bad=state['kind']=='false_break'
        return [ref,b(base+timedelta(minutes=1),768 if bad else 772,773,767 if bad else 769)]
    prior_logging=logging.root.manager.disable
    with offline(), ExitStack() as stack:
        from lavish_core.trading import alert_handler as ah, trade_handler as th
        from lavish_core.trade import options_broker as ob, options_exit_monitor as em
        from lavish_core.trade import broker, circuit_breaker as cb, pdt_guard as pdt, candle_gatekeeper as gk, reconcile as rec
        from lavish_core.db import hybrid_store as hs
        from lavish_core.feeds import alert_feed as feed
        logging.disable(logging.CRITICAL)
        stack.callback(logging.disable, prior_logging)
        stack.enter_context(patch.dict('os.environ', {'TRADE_MODE':'paper','RESEARCH_CONTEXT_DB':'','REDIS_URL':'','ALERT_FEED_EXECUTE':'true'}))
        def setv(module,name,value):stack.enter_context(patch.object(module,name,value))
        for module in (th,em,rec):setv(module,'DEFAULT_DB',journal)
        setv(rec,'_watched',set())
        from lavish_core.vision import extract_signal as parser
        for module in (th,em,pdt,parser):
            if hasattr(module,'date'):setv(module,'date',SimDate)
        for module in (th,em,gk,rec,parser):setv(module,'datetime',SimDatetime)
        for module in (th,em,gk,cb):setv(module,'time',simtime)
        setv(hs,'_utcnow',lambda:clock.now)
        setv(th,'TRADE_MODE','paper')
        setv(th,'OPTION_FEE_RESERVE_PER_CONTRACT',x.fee)
        setv(th,'threading',SimpleNamespace(Thread=InlineThread))
        setv(th,'watch_and_exit_async',lambda **kw:monitors.append(kw))
        setv(rec,'watch_and_exit_async',lambda **kw:monitors.append(kw))
        for module in (th,cb,pdt):setv(module,'get_account',account)
        for module in (th,em):
            setv(module,'latest_quote',lambda *a:state['underlying'])
            setv(module,'get_clock',lambda:session_clock(clock.now))
            setv(module,'get_order_by_client_id',lookup)
        setv(th,'broker_get_order',get_order);setv(em,'get_order',get_order)
        setv(th,'broker_cancel_order',x.cancel);setv(em,'cancel_order',x.cancel)
        setv(broker,'get_positions',lambda:[dict(symbol=s,qty=q) for s,q in x.positions.items()])
        setv(rec,'get_positions',lambda:[dict(symbol=s,qty=q,asset_class='us_option',side='long',avg_entry_price=.1) for s,q in x.positions.items()])
        setv(rec,'get_order_by_client_id',lookup);setv(rec,'get_order',get_order)
        for module in (ah,th,em,cb,rec):setv(module,'post_discord',lambda message:notifications.append(message))
        setv(gk,'get_bars',bars)
        setv(ob,'_check_keys',lambda:None)
        setv(ob.SESSION,'get',get);setv(ob.SESSION,'post',post)
        for module in (ob,em):setv(module,'latest_option_quote',lambda s:dict(bid=x.quotes[s][0],ask=x.quotes[s][1]))
        inbox=feed.AlertInbox(journal)
        class Connection:
            def __init__(self,data):self.data=data;self.output=bytearray()
            def makefile(self,*args):return io.BytesIO(self.data)
            def settimeout(self,*args):pass
            def sendall(self,data):self.output.extend(data)
        def send(identity,text,budget,*,bad_signature=False,age=0):
            body=json.dumps(dict(id=identity,text=text,created_at=(clock.now-timedelta(seconds=age)).isoformat())).encode()
            stamp=str(int(clock.now.timestamp()))
            # Public deterministic fixture key; never an account credential.
            secret='synthetic-test-key-only-'*3
            signature='0'*64 if bad_signature else feed.sign(secret,stamp,body)
            headers=['POST /v1/alerts HTTP/1.1','Host: localhost','Content-Type: application/json',
                     f'Content-Length: {len(body)}',f'X-Alert-Timestamp: {stamp}',f'X-Alert-Signature: {signature}']
            connection=Connection(('\r\n'.join(headers)+'\r\n\r\n').encode()+body)
            feed.handler_for(inbox,secret,clock=lambda:clock.now.timestamp())(
                connection,('127.0.0.1',1234),SimpleNamespace(server_name='localhost',server_port=8877))
            head,response=bytes(connection.output).split(b'\r\n\r\n',1)
            code=int(head.split()[1]);receipt=json.loads(response)
            dispatched=inbox.dispatch_once(lambda **kw:ah.handle_alert_text(**kw,amount_usd=budget),execute=True,now=clock.now.timestamp()) if code in (200,202) else None
            return dict(http_status=code,receipt=receipt,dispatch=dispatched)
        # Entry parser, execute_trade_from_post, confirm_entry, contract resolver,
        # place_option_order, all risk checks, ExitState and ledger remain real.
        reached_half, reached_tenth = initial>=500, initial>=1000
        for i in range(days):
            day=REPLAY_START_DATE+timedelta(days=i)
            clock.now=datetime.combine(day,datetime.min.time(),timezone.utc).replace(hour=15)
            x.today=day
            kind=['target','premium_stop','partial_entry','lost_ack','exit_outage','false_break',
                  'conditional','trim','creator_close','restart','creator_trim_lifecycle','creator_close_lifecycle',
                  'partial_exit_restart','structural_stop','stale','wrong_contract'][(i+scenario_offset)%16]
            state.update(session_start=clock.now,underlying=770.,premium=.10,kind=kind,bars=0,phase='entry',attempts=0,lost=False)
            expiry=day+timedelta(days=7)
            while expiry.weekday()>=5 or str(expiry) in HOLIDAYS:expiry+=timedelta(days=1)
            reached_half |= account()['equity']>=500
            reached_tenth |= account()['equity']>=1000
            fraction=.1 if reached_tenth else .5 if reached_half else 1.
            if fixed_fraction is not None: fraction=fixed_fraction
            # Explicit simulation-only budget argument, not a change to live allocation.
            budget=max(.01,account()['equity']*fraction)
            text=f'BTO SPY 770 CALL exp {expiry.isoformat()} target 775 stop 765'
            if kind=='wrong_contract':text=text.replace('SPY','NVDA')
            if kind=='conditional':text+=' Wait for confirmation.'
            if kind=='trim':text=f'SPY 770 CALL exp {expiry.isoformat()}. Take 80% profits and ride the rest.'
            if kind=='creator_close':text=f'STC SPY 770 CALL exp {expiry.isoformat()}'
            before=len(x.orders)
            event_start=len(x.events)
            identity=f'synthetic-{seed}-{i}'
            invalid_receipt=send(identity+'-forged',text,budget,bad_signature=True)
            age=180 if kind=='stale' else 0
            feed_receipt=send(identity,text,budget,age=age)
            # Re-send exact original publication time so this is a true duplicate,
            # not an edit caused by virtual time advancing through the gate.
            saved_now=clock.now;clock.now=state['session_start']
            duplicate_receipt=send(identity,text,budget,age=age)
            clock.now=saved_now
            if feed_receipt['http_status'] in (200,202):
                ah.handle_alert_text(text,source='synthetic_creator_duplicate',amount_usd=budget)
            entry_orders=[o for o in list(x.orders.values())[before:] if o['side']=='BUY']
            creator_exit_gap=False
            if monitors and kind in ('creator_trim_lifecycle','creator_close_lifecycle'):
                prior_sells=sum(o['side']=='SELL' for o in x.orders.values())
                action='Take 80% profits and ride the rest.' if kind=='creator_trim_lifecycle' else 'STC'
                send(identity+'-exit',f'{action} SPY 770 CALL exp {expiry.isoformat()}',budget)
                creator_exit_gap=sum(o['side']=='SELL' for o in x.orders.values())==prior_sells
            recovered=0
            if kind=='restart' and monitors:
                monitors.clear()  # Lost in-memory workers; durable ledger is untouched.
                for symbol in x.positions:rec.unmark_watched(symbol)
                rec.reconcile_pending_orders()
                recovered=rec.reconcile_open_positions()
            results=[]
            while monitors:
                kw=monitors.pop(0)
                state['phase']='exit'
                state['underlying']=764 if kind=='structural_stop' else 770 if kind=='premium_stop' else 776
                if fixed_fraction is None and stress_profile=='baseline':
                    state['premium']=.04 if kind=='premium_stop' else rng.choice([.12,.14,.16])
                else:
                    # A calendar-day quote is independent of entries, sizing,
                    # worker retries and account outcome. Paired risk policies
                    # must not consume different RNG draws after a rejection.
                    choices=[.10,.11,.12] if stress_profile=='weak_followthrough' else [.12,.14,.16]
                    daily_quote=random.Random(seed+i).choice(choices)
                    loss_quote=.01 if stress_profile=='gap_loss' else .04
                    state['premium']=loss_quote if kind=='premium_stop' else daily_quote
                x.quote(kw['contract_symbol'],state['premium'],state['premium'],
                        liquidity=1 if kind=='partial_exit_restart' and not state['lost'] else 100)
                try:
                    result=em.watch_and_exit(**kw)
                except ReplayRestart:
                    rec.unmark_watched(kw['contract_symbol'])
                    rec.reconcile_pending_orders()
                    recovered+=rec.reconcile_open_positions()
                    continue
                results.append(result)
            store=hs.HybridStore(journal)
            ledger=store.fetchall('SELECT side,qty,price,fee FROM fills')
            expected=initial+sum((1 if side=='sell' else -1)*qty*price*100-fee for side,qty,price,fee in ledger)
            local={s:qty for s,qty in store.fetchall('SELECT symbol,qty FROM positions')}
            records.append(dict(date=day.isoformat(),post_id=f'synthetic-{seed}-{i}',text=text,
                published_at=state['session_start'].isoformat(),observed_at=state['session_start'].isoformat(),
                kind=kind,budget=budget,entry_orders=len(entry_orders),exit_results=results,
                feed_receipt=feed_receipt,duplicate_receipt=duplicate_receipt,invalid_receipt=invalid_receipt,
                market_open=session_clock(state['session_start'])['is_open'],
                recovered_monitors=recovered,
                creator_exit_gap=creator_exit_gap,
                order_submit_attempts=state['attempts'],fills=x.events[event_start:],
                broker_cash=x.cash,cash_error=round(x.cash-expected,8),
                position_match=local==x.positions,halt=store.get_kill_switch(),
                risk_state=store.get_risk_limits()))
            store.con.close()
            if x.positions:raise AssertionError('Open position after replay session')
    report=dict(synthetic=True,real_orders=0,seed=seed,days=days,initial=initial,final_cash=x.cash,
        simulation_fixed_fraction=fixed_fraction,stress_profile=stress_profile,
        production_paths=['signed HTTP handler','AlertInbox.dispatch_once','handle_alert_text','execute_trade_from_post','confirm_entry',
            'resolve_and_price_contract','place_option_order','watch_and_exit','ExitState','HybridStore'],
        substitutions=['broker/data I/O','virtual time','serial worker scheduler','simulation budget and explicit fee estimate'],
        limitations=['NYSE published holiday/early-close calendar; unexpected closures not modeled','Synthetic quotes, not profitability evidence',
            'Creator conditional promotion and trim/close orchestration not implemented',
            'Restart recovery re-arms lost workers in-process; not an OS process-kill or concurrency test'],
        sessions=records,exchange_events=x.events,notifications=notifications)
    (output/'report.json').write_text(json.dumps(report,indent=2,default=str))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--days',type=int,default=365);p.add_argument('--initial',type=float,default=1000)
    a=p.parse_args();r=replay(a.output,a.days,a.initial)
    print(json.dumps({k:v for k,v in r.items() if k not in ('sessions','exchange_events','notifications')},indent=2))
