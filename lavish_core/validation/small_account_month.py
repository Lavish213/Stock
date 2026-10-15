"""Broker-free 32-day fractional SPY experiment using the production text parser.

Invented alerts, sessions and prices: not a historical backtest or forecast.
Fractional sizing is a simulation adapter, NOT deployed broker support.
"""
from __future__ import annotations
import argparse
import json
import math
import random
import socket
import statistics
from contextlib import ExitStack
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

CENT=Decimal('.01')


def money(value, rounding=ROUND_DOWN):
    value=Decimal(str(value))
    if not value.is_finite(): raise ValueError('Finite money required')
    return value.quantize(CENT,rounding=rounding)


class Account:
    def __init__(self,cash='100',fee='.01',spread_bps=2,slippage_bps=1,halt_on_first_loss=True):
        self.cash=money(cash); self.initial=self.cash; self.fee=money(fee,ROUND_UP)
        if self.cash<=0 or self.fee<0: raise ValueError('Positive balance and nonnegative fees required')
        if any(isinstance(x,bool) or not math.isfinite(x) or not 0<=x<=1000 for x in (spread_bps,slippage_bps)):
            raise ValueError('Invalid execution costs')
        self.cost=Decimal(str(spread_bps/20000+slippage_bps/10000))
        self.ten_percent=self.cash>=1000; self.halted=False
        self.position=None; self.seen={}; self.trades=[]; self.relearn=None
        self.halt_on_first_loss=halt_on_first_loss

    def fraction(self):
        return Decimal('.1') if self.ten_percent else Decimal(1)

    def begin_day(self,day):
        pass

    def enter(self,event,signal,mid,*,market_open):
        identity=event['id']; digest=json.dumps(event,sort_keys=True)
        if identity in self.seen:
            if self.seen[identity]!=digest: raise ValueError('Duplicate identity conflict')
            return 'duplicate'
        self.seen[identity]=digest
        if self.halted: return 'halted'
        if not market_open: return 'closed_session'
        if self.position: return 'position_exists'
        if not signal or signal.get('symbol')!='SPY' or signal.get('action')!='BUY' or signal.get('strike'):
            return 'not_spy_entry'
        mid=Decimal(str(mid))
        if not mid.is_finite() or mid<=0: raise ValueError('Positive executable price required')
        fraction=self.fraction()
        allocation=money(self.cash*fraction)
        available=allocation-2*self.fee  # Reserve closing fee, never borrow.
        if available<=0: return 'insufficient_cash'
        entry=mid*(1+self.cost)
        qty=(available/entry).quantize(Decimal('.000000001'),rounding=ROUND_DOWN)
        if qty<=0: return 'insufficient_cash'
        cost=money(qty*entry,ROUND_UP)+self.fee
        if cost+self.fee>allocation or cost>self.cash: raise AssertionError('Sizing exceeded allocation')
        self.cash-=cost
        self.position=dict(event_id=identity,qty=qty,entry=entry,cost=cost,
            allocation=allocation,at=event['created_at'],allocation_mode={Decimal(1):'all_available',Decimal('.5'):'50_percent',Decimal('.1'):'10_percent'}[fraction])
        return 'entered'

    def close(self,mid,at,reason):
        if not self.position: return None
        mid=Decimal(str(mid))
        if not mid.is_finite() or mid<=0: raise ValueError('Positive executable price required')
        p=self.position
        exit_price=mid*(1-self.cost)
        proceeds=money(p['qty']*exit_price)-self.fee
        self.cash+=proceeds
        pnl=proceeds-p['cost']
        row=dict(entry_id=p['event_id'],entry_at=p['at'],exit_at=at,reason=reason,
            qty=str(p['qty']),allocation=float(p['allocation']),allocation_mode=p['allocation_mode'],
            entry_price=float(p['entry']),exit_price=float(exit_price),net_pnl=float(pnl),
            fees=float(2*self.fee),balance=float(self.cash))
        self.trades.append(row); self.position=None
        if self.cash>=1000: self.ten_percent=True
        if pnl<0 and self.halt_on_first_loss:
            self.halted=True
            self.relearn=dict(status='review_required',trigger='first_net_losing_trade',
                entry_id=p['event_id'],net_loss=float(-pnl),requested_at=at,
                automatic_resume=False,automatic_strategy_changes=False)
        return row


class TieredAccount(Account):
    """Revised SIMULATION rule; this class cannot clear a production halt."""
    def __init__(self,*args,big_loss_fraction='.1',consecutive_limit=3,**kwargs):
        super().__init__(*args,halt_on_first_loss=False,**kwargs)
        self.big_loss_fraction=Decimal(str(big_loss_fraction))
        if not self.big_loss_fraction.is_finite() or not 0<self.big_loss_fraction<=1:
            raise ValueError('Invalid assumed big-loss fraction')
        if type(consecutive_limit)is not int or consecutive_limit<2:
            raise ValueError('At least two consecutive losses required')
        self.consecutive_limit=consecutive_limit;self.loss_streak=0
        self.half_reached=self.cash>=500;self.day=None;self.day_baseline=self.cash
        self.day_net=Decimal(0);self.review_requests=[]

    def fraction(self):
        return Decimal('.1') if self.ten_percent else Decimal('.5') if self.half_reached else Decimal(1)

    def begin_day(self,day):
        day=date.fromisoformat(str(day))
        if day.weekday()>=5: raise ValueError('Next simulated weekday session required')
        if self.day is not None and day<self.day: raise ValueError('Cannot backdate simulated session')
        if self.day==day:return
        if self.position: raise ValueError('Cannot resume with unresolved position')
        if self.halted:
            self.halted=False;self.loss_streak=0
        self.day=day;self.day_baseline=self.cash;self.day_net=Decimal(0)

    def close(self,mid,at,reason):
        if self.day is None: raise ValueError('Begin simulated trading session first')
        row=super().close(mid,at,reason)
        if row is None:return None
        pnl=Decimal(str(row['net_pnl']));self.day_net+=pnl
        self.loss_streak=self.loss_streak+1 if pnl<0 else 0
        self.half_reached=self.half_reached or self.cash>=500
        big_loss=pnl<=-self.day_baseline*self.big_loss_fraction or self.day_net<=-self.day_baseline*self.big_loss_fraction
        if big_loss or self.loss_streak>=self.consecutive_limit:
            self.halted=True
            self.relearn=dict(status='review_required',trigger='big_loss' if big_loss else 'consecutive_net_losses',
                entry_id=row['entry_id'],requested_at=at,net_loss=float(max(Decimal(0),-pnl)),day_net_loss=float(max(Decimal(0),-self.day_net)),loss_streak=self.loss_streak,
                big_loss_fraction_assumption=float(self.big_loss_fraction),consecutive_limit_assumption=self.consecutive_limit,
                next_simulated_session_resume=True,production_resume=False,automatic_strategy_changes=False)
            self.review_requests.append(dict(self.relearn))
        return row


def parser(text):
    from lavish_core.trading import alert_handler as ah
    captured=[]
    with patch.object(ah,'execute_trade_from_post',lambda signal:captured.append(signal)):
        ah.handle_alert_text(text,source='synthetic_creator')
    if len(captured)>1: raise AssertionError('Ambiguous parser dispatch')
    return captured[0] if captured else None


def blocked(*args,**kwargs):
    raise AssertionError('Network and real broker execution prohibited in this experiment')


def offline():
    import requests,urllib.request
    stack=ExitStack()
    stack.enter_context(patch.dict('os.environ',{'TRADE_MODE':'dry','DISCORD_WEBHOOK_URL':'','RESEARCH_CONTEXT_DB':''}))
    stack.enter_context(patch.object(requests.sessions.Session,'request',blocked))
    stack.enter_context(patch.object(urllib.request,'urlopen',blocked))
    stack.enter_context(patch.object(socket.socket,'connect',blocked))
    from lavish_core.trading import alert_handler as ah
    stack.enter_context(patch.object(ah,'post_discord',lambda *a,**k:None))
    return stack


def run_month(seed=20261014,*,start='2026-10-01',days=32,parse=parser,account_factory=Account):
    if type(days)is not int or not 1<=days<=366: raise ValueError('Invalid simulated day count')
    rng=random.Random(seed);market_rng=random.Random(seed+1000001)
    start=date.fromisoformat(start); account=account_factory()
    records=[]; mid=770.; observed_alerts=0; duplicate_attempts=0
    for index in range(days):
        day=start+timedelta(days=index); open_session=day.weekday()<5
        at=datetime(day.year,day.month,day.day,10,rng.randrange(0,60),tzinfo=ZoneInfo('America/Los_Angeles'))
        # Fresh hypothetical day path; no knowledge of its future is in the alert.
        mid*=math.exp(market_rng.gauss(0,.003))
        entry_mid=mid
        session_close=at.replace(hour=13,minute=0)
        last_bar=max(1,int((session_close-at).total_seconds()/180))
        path=[]
        # Advance the full market path even when an account refuses an entry.
        # Competing policies therefore get identical alerts and future prices.
        for _ in range(last_bar):
            mid*=math.exp(market_rng.gauss(0,.0008));path.append(mid)
        mid=entry_mid
        target=round(mid*(1+rng.uniform(.003,.008)),2)
        stop=round(mid*(1-rng.uniform(.003,.006)),2)
        variant=rng.choice(['valid','valid','valid','conditional','recap','wrong_ticker'])
        text=(f'BUY SPY now target {target:.2f} stop {stop:.2f}' if variant=='valid' else
              f'SPY setup: break and hold above {mid:.2f}' if variant=='conditional' else
              'SPY is at the second target' if variant=='recap' else
              f'BUY AAPL now target {target:.2f} stop {stop:.2f}')
        event=dict(id=f'{seed}-day-{index+1}',created_at=at.isoformat(),text=text,synthetic=True)
        signal=parse(text); observed_alerts+=1
        if open_session:account.begin_day(day)
        status=account.enter(event,signal,mid,market_open=open_session)
        if account.enter(event,signal,mid,market_open=open_session)!='duplicate': raise AssertionError('Retry not deduplicated')
        duplicate_attempts+=1
        exit_reason=None
        if status=='entered':
            # Exit thresholds come from the real parsed alert, not a second
            # unparsed interpretation of the generator's intended values.
            target=float(signal['target_hint']); stop=float(signal['stop_hint'])
            if not math.isfinite(target) or not math.isfinite(stop) or not 0<stop<mid<target:
                raise AssertionError('Parser did not preserve executable source levels')
            for bar,mid in enumerate(path,1):
                if mid<=stop or mid>=target or bar==last_bar:
                    exit_reason='stop' if mid<=stop else 'target' if mid>=target else 'day_close'
                    exit_at=(session_close if bar==last_bar else at+timedelta(minutes=bar*3)).isoformat()
                    account.close(mid,exit_at,exit_reason)
                    break
        mid=path[-1]
        records.append(dict(day=index+1,date=day.isoformat(),market_open_assumption=open_session,
            alert=event,parser_signal=signal,status=status,exit_reason=exit_reason,
            balance=float(account.cash),halted=account.halted))
    assert account.position is None
    assert money(account.initial+sum(Decimal(str(t['net_pnl'])) for t in account.trades))==account.cash
    return dict(synthetic=True,profitability_evidence=False,seed=seed,start=start.isoformat(),days=days,
        initial_balance=100,final_balance=float(account.cash),net_pnl=float(account.cash-account.initial),
        trades=account.trades,alert_days=observed_alerts,duplicate_attempts=duplicate_attempts,
        reached_500=getattr(account,'half_reached',False),reached_1000=account.ten_percent,halted=account.halted,relearn_request=account.relearn,
        review_requests=getattr(account,'review_requests',[account.relearn] if account.relearn else []),
        final_position=0,real_broker_orders=0,cash_error=0,records=records,
        scope='Production text parser -> isolated fractional simulation adapter; production executor is NOT exercised',
        assumptions=dict(fractional_precision=9,commission_each_side=.01,full_spread_bps=2,slippage_bps_each_side=1,
            bar_log_return_mean=0,bar_log_return_std=.0008,weekday_sessions_only=True,
            market_paths='Uncalibrated random synthetic paths, not authentic SPY quotes',
            calendar='32 calendar days October1-November1 by default; no literal32nd date',
            settlement='One position per day; unsettled-cash constraints not certified',
            sizing='All available until500;50% after500;10% after1000, milestones persist' if isinstance(account,TieredAccount) else 'All available until1000;then10%',
            loss_rule='Big loss or repeated losses pauses until next simulated weekday; thresholds are assumptions' if isinstance(account,TieredAccount) else 'First net loss permanently halts experiment',
            big_loss_fraction=str(account.big_loss_fraction) if isinstance(account,TieredAccount) else None,
            consecutive_limit=getattr(account,'consecutive_limit',None),
            paired_path_version=2))


def experiment(output,trials=200,seed=20261014,policy='tiered_next_day'):
    if type(trials)is not int or not 1<=trials<=5000: raise ValueError('Invalid trial count')
    if policy not in ('first_loss','tiered_next_day'):raise ValueError('Invalid simulation policy')
    # Reuse identical real-parser inputs; price paths are never consulted by
    # the parser or cached instructions. Seed0 is reported before aggregates.
    cache={}
    def cached(text):
        if text not in cache: cache[text]=parser(text)
        return cache[text]
    with offline():
        rows=[run_month(seed+i,parse=cached,account_factory=TieredAccount if policy=='tiered_next_day' else Account) for i in range(trials)]
    balances=sorted(r['final_balance'] for r in rows)
    summary=dict(synthetic=True,profitability_evidence=False,trials=trials,first_seed=seed,policy=policy,paired_path_version=2,
        profitable_trials=sum(r['final_balance']>100 for r in rows),halted_trials=sum(r['halted'] for r in rows),
        reached_500_trials=sum(r['reached_500'] for r in rows),reached_1000_trials=sum(r['reached_1000'] for r in rows),median_final_balance=round(statistics.median(balances),3),
        worst_final_balance=balances[0],best_final_balance=balances[-1],
        real_broker_orders=0,unique_real_parser_inputs=len(cache),automatic_production_resume=False,
        simulation_next_day_resume=policy=='tiered_next_day',total_review_requests=sum(len(r['review_requests']) for r in rows))
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    (output/'month.json').write_text(json.dumps(rows[0],indent=2))
    (output/'summary.json').write_text(json.dumps(summary,indent=2))
    (output/'trials.json').write_text(json.dumps([{k:v for k,v in r.items() if k not in ('records','assumptions')} for r in rows],indent=2))
    return summary


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',required=True)
    ap.add_argument('--trials',type=int,default=200);ap.add_argument('--seed',type=int,default=20261014)
    ap.add_argument('--policy',choices=['first_loss','tiered_next_day'],default='tiered_next_day')
    args=ap.parse_args();print(json.dumps(experiment(args.output,args.trials,args.seed,args.policy),indent=2))

