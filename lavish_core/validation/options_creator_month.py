"""Synthetic SPY call alert experiment. No broker, historical fills or forecast.

European call formula is a toy quote generator, not an executable SPY chain.
All quotes/events are generated independently of account outcomes.
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

D=Decimal
PT=ZoneInfo('America/Los_Angeles')


def cash(x, rounding=ROUND_DOWN):
    x=D(str(x))
    if not x.is_finite(): raise ValueError('Finite cash required')
    return x.quantize(D('.01'),rounding=rounding)


def offline():
    import requests, urllib.request
    stack=ExitStack()
    stack.enter_context(patch.dict('os.environ',{'TRADE_MODE':'dry','DISCORD_WEBHOOK_URL':'','RESEARCH_CONTEXT_DB':''}))
    def blocked(*a,**k): raise AssertionError('External execution prohibited')
    stack.enter_context(patch.object(requests.sessions.Session,'request',blocked))
    stack.enter_context(patch.object(urllib.request,'urlopen',blocked))
    stack.enter_context(patch.object(socket.socket,'connect',blocked))
    from lavish_core.trading import alert_handler as ah
    stack.enter_context(patch.object(ah,'post_discord',lambda *a,**k:None))
    return stack


def parse(text):
    from lavish_core.trading import alert_handler as ah
    found=[]
    with patch.object(ah,'execute_trade_from_post',lambda s:found.append(s)):
        ah.handle_alert_text(text,source='synthetic_creator')
    if len(found)>1: raise AssertionError('Ambiguous dispatch')
    return found[0] if found else None


def quote(underlying,strike,remaining_minutes,volatility=.25,spread=.04):
    """Invented bid/ask. Theta and volatility changes, no historical calibration."""
    if not all(math.isfinite(x) for x in (underlying,strike,remaining_minutes,volatility,spread)) or min(underlying,strike,volatility)<=0 or remaining_minutes<0 or spread<0:
        raise ValueError('Invalid toy quote input')
    intrinsic=max(0,underlying-strike)
    if remaining_minutes==0: mid=intrinsic
    else:
        t=remaining_minutes/(252*390); root=volatility*math.sqrt(t)
        d1=(math.log(underlying/strike)+volatility**2*t/2)/root
        norm=lambda x:(1+math.erf(x/math.sqrt(2)))/2
        mid=max(intrinsic,underlying*norm(d1)-strike*norm(d1-root))
    return cash(max(0,mid-spread/2)),cash(max(.01,mid+spread/2),ROUND_UP)


class CallAccount:
    def __init__(self,initial=100,fee='.65',slippage='.01',big_loss='.1',streak=3):
        self.initial=self.balance=cash(initial);self.fee=cash(fee,ROUND_UP);self.slippage=cash(slippage,ROUND_UP)
        self.big_loss=D(str(big_loss));self.limit=streak
        if self.balance<=0 or min(self.fee,self.slippage)<0 or not self.big_loss.is_finite() or not 0<self.big_loss<=1 or type(streak)is not int or streak<2:raise ValueError('Invalid experiment settings')
        self.half=self.balance>=500;self.tenth=self.balance>=1000
        self.position=None;self.seen={};self.trades=[];self.reviews=[];self.day=None
        self.paused=False;self.loss_streak=0;self.day_net=D(0);self.baseline=self.balance

    def fraction(self):return D('.1') if self.tenth else D('.5') if self.half else D(1)

    def begin(self,day):
        day=date.fromisoformat(str(day))
        if day.weekday()>=5 or (self.day and day<self.day):raise ValueError('Forward weekday required')
        if day==self.day:return
        if self.position:raise ValueError('Unresolved holding')
        if self.paused:self.paused=False;self.loss_streak=0
        self.day=day;self.baseline=self.balance;self.day_net=D(0)

    def enter(self,event,signal,bid,ask,at,underlying=None,ticker='SPY'):
        digest=json.dumps(event,sort_keys=True)
        if event['id'] in self.seen:
            if self.seen[event['id']]!=digest:raise ValueError('Conflicting duplicate')
            return 'duplicate'
        self.seen[event['id']]=digest
        published=datetime.fromisoformat(event['created_at']);at=datetime.fromisoformat(at)
        if at.tzinfo is None or published.tzinfo is None:raise ValueError('Timezone required')
        at=at.astimezone(PT)
        if at<published or (at-published).total_seconds()>300:return 'stale_or_future'
        if at.date()!=self.day or at.weekday()>=5 or not (6,30)<=(at.hour,at.minute)<(12,45):return 'closed_session'
        if self.paused:return 'paused'
        if self.position:return 'holding'
        if not signal or (signal.get('ticker'),signal.get('side'),signal.get('action'))!=(ticker,'CALL','BUY'):return 'not_spy_call'
        try:expiry=date.fromisoformat(signal['expiry']);strike=D(str(signal['strike']))
        except (ValueError,KeyError):return 'missing_contract'
        if not strike.is_finite() or strike<=0 or expiry<at.date():return 'invalid_contract'
        if underlying is not None:
            level=D(str(underlying));target=D(str(signal.get('target_hint')));stop=D(str(signal.get('stop_hint')))
            if not all(x.is_finite() for x in (level,target,stop)) or not 0<stop<level<target:return 'invalidated_before_entry'
        bid=cash(bid);ask=cash(ask,ROUND_UP)
        if bid<0 or ask<=0 or bid>ask:raise ValueError('Invalid quote')
        entry=ask+self.slippage;allocation=cash(self.balance*self.fraction())
        qty=int(allocation/(entry*100+2*self.fee))
        if qty<1:return 'unaffordable_contract'
        cost=qty*(entry*100+self.fee)
        assert cost+qty*self.fee<=allocation<=self.balance
        self.balance-=cost
        self.position=dict(id=event['id'],strike=str(strike),expiry=expiry.isoformat(),qty=qty,entry=entry,cost=cost,allocation=allocation,at=at.isoformat())
        return 'entered'

    def close(self,bid,at,reason,*,strike=None,expiry=None):
        if not self.position:return None
        p=self.position;when=datetime.fromisoformat(at)
        if when.tzinfo is None or when<datetime.fromisoformat(p['at']):raise ValueError('Invalid exit time')
        if strike is not None and D(str(strike))!=D(p['strike']):return 'wrong_contract'
        if expiry is not None and expiry!=p['expiry']:return 'wrong_contract'
        bid=cash(bid)
        if bid<0:raise ValueError('Negative bid')
        if bid==0 and reason!='expired_worthless':return 'unfilled_zero_bid'
        if reason=='expired_worthless' and (when.astimezone(PT).date()<date.fromisoformat(p['expiry']) or (when.astimezone(PT).date()==date.fromisoformat(p['expiry']) and (when.astimezone(PT).hour,when.astimezone(PT).minute)<(13,0))):raise ValueError('Cannot expire early')
        if reason=='expired_worthless' and bid!=0:raise ValueError('Worthless expiry requires zero bid')
        exit_price=max(D('.01'),bid-self.slippage) if bid>0 else D(0)
        fee=D(0) if reason=='expired_worthless' else self.fee*p['qty']
        proceeds=exit_price*100*p['qty']-fee; pnl=proceeds-p['cost'];self.balance+=proceeds
        row=dict(entry_id=p['id'],entry_at=p['at'],exit_at=when.isoformat(),strike=p['strike'],expiry=p['expiry'],contracts=p['qty'],multiplier=100,allocation=float(p['allocation']),entry_ask_fill=float(p['entry']),exit_bid_fill=float(exit_price),net_pnl=float(pnl),fees=float(self.fee*p['qty']+fee),reason=reason,balance=float(self.balance))
        self.trades.append(row);self.position=None;self.half|=self.balance>=500;self.tenth|=self.balance>=1000
        self.day_net+=pnl;self.loss_streak=self.loss_streak+1 if pnl<0 else 0
        if pnl<=-self.baseline*self.big_loss or self.day_net<=-self.baseline*self.big_loss or self.loss_streak>=self.limit:
            self.paused=True;self.reviews.append(dict(at=at,trigger='big_loss' if self.day_net<=-self.baseline*self.big_loss or pnl<=-self.baseline*self.big_loss else 'loss_streak',automatic_training=False,production_resume=False))
        assert self.balance>=0
        return row


def generate(seed,days=32):
    rng=random.Random(seed);first=date(2026,10,1);scenarios=[]
    for i in range(days):
        day=first+timedelta(days=i);s=770*math.exp(rng.gauss(0,.01));strike=math.ceil(s)+rng.randrange(10,23)
        at=datetime.combine(day,datetime.min.time(),PT).replace(hour=7,minute=30)
        target=round(s+3,2);stop=round(s-2,2);creator_minute=rng.choice([60,120,None]);vol=.25;path=[]
        for minute in range(331):
            if minute:
                minute_vol=vol/math.sqrt(252*390)
                s*=math.exp(rng.gauss(-minute_vol**2/2,minute_vol))
                vol=max(.08,min(.8,vol*math.exp(rng.gauss(0,.012))))
            bid,ask=quote(s,strike,max(0,330-minute),vol)
            path.append(dict(at=(at+timedelta(minutes=minute)).isoformat(),underlying=s,bid=str(bid),ask=str(ask)))
        variant=rng.choice(['valid']*8+['recap','missing_expiry'])
        text=f'BTO SPY {strike} CALL exp {day.isoformat()} target {target} stop {stop}' if variant=='valid' else 'SPY is at the second target' if variant=='recap' else f'BTO SPY {strike} CALL'
        scenarios.append(dict(event=dict(id=f'synthetic-{seed}-{i}',created_at=at.isoformat(),text=text,synthetic=True),path=path,target=target,stop=stop,creator_minute=creator_minute))
    return scenarios


def run(seed=20261015,delay_seconds=0,parse_fn=parse,initial=100,scenarios=None):
    if type(delay_seconds)is not int or not 0<=delay_seconds<=600:raise ValueError('Invalid latency')
    account=CallAccount(initial);records=[]
    for case in generate(seed) if scenarios is None else scenarios:
        event=case['event'];path=case['path'];day=datetime.fromisoformat(event['created_at']).date()
        if day.weekday()<5:account.begin(day)
        signal=parse_fn(event['text']);index=math.ceil(delay_seconds/60);q=path[index]
        status=account.enter(event,signal,q['bid'],q['ask'],q['at'],q['underlying'])
        assert account.enter(event,signal,q['bid'],q['ask'],q['at'],q['underlying'])=='duplicate'
        exit_trigger=None;exit_signal_at=None
        if status=='entered':
            target=float(signal['target_hint']);stop=float(signal['stop_hint'])
            for j in range(index+1,316):
                now=path[j];price=now['underlying']
                # Exit signals use the exact holding identity; they do not pass
                # production's entry-only parser and are a synthetic adapter.
                if j==315 or price>=target or price<=stop or j==case['creator_minute']:
                    reason='day_cutoff' if j==315 else 'target' if price>=target else 'stop' if price<=stop else 'creator_exit'
                    fill_index=min(330,j+index);fill=path[fill_index]
                    result=account.close(fill['bid'],fill['at'],reason,strike=signal['strike'],expiry=signal['expiry'])
                    if isinstance(result,dict):exit_trigger=reason;exit_signal_at=now['at'];result['exit_signal_at']=exit_signal_at;result['exit_delay_seconds']=int((datetime.fromisoformat(fill['at'])-datetime.fromisoformat(now['at'])).total_seconds());break
            if account.position:
                # Zero bids are not pretend fills. This toy OTM path expires
                # only if worthless; ITM unresolved holding fails verification.
                end=path[-1]
                if end['underlying']>=float(account.position['strike']):raise AssertionError('Unresolved ITM exercise risk')
                account.close(0,end['at'],'expired_worthless');exit_trigger='expired_worthless'
        records.append(dict(event=event,parser_signal=signal,status=status,entry_delay_seconds=index*60,exit_trigger=exit_trigger,balance=float(account.balance)))
    assert account.position is None
    assert account.initial+sum(D(str(t['net_pnl'])) for t in account.trades)==account.balance
    return dict(synthetic=True,profitability_evidence=False,seed=seed,delay_seconds=delay_seconds,initial=float(account.initial),final=float(account.balance),trades=account.trades,records=records,reviews=account.reviews,reached_500=account.half,reached_1000=account.tenth,real_orders=0,cash_error=0)


def experiment(output,trials=200):
    if type(trials)is not int or not 1<=trials<=1000:raise ValueError('Invalid trial count')
    cache={}
    def cached(text):
        if text not in cache:cache[text]=parse(text)
        return cache[text]
    cohorts={str(delay):[] for delay in (0,60,180)}
    with offline():
        for i in range(trials):
            cases=generate(20261015+i)
            for delay in (0,60,180):cohorts[str(delay)].append(run(20261015+i,delay,cached,scenarios=cases))
    summary={}
    for delay,rows in cohorts.items():
        balances=[r['final'] for r in rows]
        summary[delay]=dict(trials=trials,profitable=sum(x>100 for x in balances),median=statistics.median(balances),worst=min(balances),best=max(balances),reached_500=sum(r['reached_500'] for r in rows),reached_1000=sum(r['reached_1000'] for r in rows),trades=sum(len(r['trades']) for r in rows),unaffordable=sum(x['status']=='unaffordable_contract' for r in rows for x in r['records']),worthless=sum(t['reason']=='expired_worthless' for r in rows for t in r['trades']))
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    (output/'summary.json').write_text(json.dumps(dict(synthetic=True,profitability_evidence=False,summary=summary),indent=2))
    (output/'first-month.json').write_text(json.dumps(cohorts['0'][0],indent=2))
    (output/'trial-outcomes.json').write_text(json.dumps({delay:[{k:v for k,v in r.items() if k not in ('records','trades','reviews')} for r in rows] for delay,rows in cohorts.items()},indent=2))
    return summary

if __name__=='__main__':
    cli=argparse.ArgumentParser();cli.add_argument('--output',required=True);cli.add_argument('--trials',type=int,default=200)
    args=cli.parse_args();print(json.dumps(experiment(args.output,args.trials),indent=2))
