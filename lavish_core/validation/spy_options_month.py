"""Aggressive SPY option research replay using recorded option trade bars.

Real parser and candle gate; independent proxy execution and exit accounting.
Never submits a broker order. Historical trade bars are not executable NBBO.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from unittest.mock import patch

from lavish_core.validation.monthly_alert_replay import ET, ReplayClock, prepare


def build_plan(sessions):
    from lavish_core.trade import candle_gatekeeper as gate
    from lavish_core.vision import extract_signal as parser
    from lavish_core.trade.options_broker import build_occ_symbol
    clock = ReplayClock()

    class HistoricalDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = clock.now()
            return value.astimezone(tz) if tz else value.astimezone(ET).replace(tzinfo=None)

    class HistoricalDate(date):
        @classmethod
        def today(cls):
            return clock.now().astimezone(ET).date()

    output = []
    for session in sessions:
        posted = datetime.fromisoformat(session['date']+'T09:45:00').replace(tzinfo=ET)
        clock.set(posted)
        first = session['bars'][0]['o']
        last = session['bars'][14]['c']
        side = 'CALL' if last > first else 'PUT'
        strike = int(math.floor(last+.5))
        sign = 1 if side == 'CALL' else -1
        target, stop = round(last*(1+sign*.005),2), round(last*(1-sign*.003),2)
        text = f'BTO SPY {strike} {side} exp {session["date"]} target {target:.2f} stop {stop:.2f}'

        def closed_bars(*args, **kwargs):
            end = bisect_right(session['stamps'],clock.now()-timedelta(minutes=1))
            return session['bars'][max(0,end-15):end]

        with ExitStack() as stack:
            for module in (gate,parser):
                stack.enter_context(patch.object(module,'datetime',HistoricalDateTime))
            stack.enter_context(patch.object(parser,'date',HistoricalDate))
            for name,value in dict(time=clock,get_bars=closed_bars,ENABLED=True,
                    MAX_WAIT_SECONDS=900,POLL_INTERVAL_SEC=15,MIN_REFERENCE_RANGE_BPS=1).items():
                stack.enter_context(patch.object(gate,name,value))
            parsed = parser.parse_alert(text=text,source='synthetic-month')
            if (parsed.get('ticker') != 'SPY' or parsed.get('side') != side
                    or parsed.get('strike') != strike or parsed.get('expiry') != session['date']
                    or parsed.get('field_conflicts')):
                raise AssertionError('Fake option alert did not parse exactly')
            result = gate.confirm_entry('SPY','bullish' if side == 'CALL' else 'bearish')
        output.append(dict(day=session['date'],posted=posted.isoformat(),text=text,parsed=parsed,
            side=side,strike=strike,expiry=session['date'],target=target,stop=stop,
            symbol=build_occ_symbol('SPY',date.fromisoformat(session['date']),side,strike),
            gate=result,decision_at=clock.now().isoformat(),close=session['closing'].isoformat()))
    return output


def validated_options(rows):
    output=[]
    previous=None
    for bar in rows:
        stamp=datetime.fromisoformat(bar['t'].replace('Z','+00:00'))
        if stamp.tzinfo is None or previous is not None and stamp <= previous:
            raise ValueError('Ambiguous or nonchronological option tape')
        values=[float(bar[k]) for k in ('o','h','l','c','v')]
        if not all(math.isfinite(v) for v in values) or min(values[:4]) <= 0 or values[4] < 0:
            raise ValueError('Invalid option prices')
        if bar['h'] < max(bar[k] for k in ('o','l','c')) or bar['l'] > min(bar[k] for k in ('o','h','c')):
            raise ValueError('Inconsistent option OHLC')
        previous=stamp
        output.append((stamp,bar))
    return output


def replay(plan, sessions, option_tapes, adverse_fraction=.03):
    if not math.isfinite(adverse_fraction) or not 0 <= adverse_fraction < 1:
        raise ValueError('Invalid adverse cost')
    cash=1000.
    peak=1000.
    drawdown=0.
    ledger=[]
    outcomes=[]
    session_map={s['date']:s for s in sessions}
    for alert in plan:
        record=dict(day=alert['day'],symbol=alert['symbol'],side=alert['side'])
        if not alert['gate']['confirmed']:
            outcomes.append(dict(record,result='gate_'+alert['gate']['reason']))
            continue
        tape=validated_options(option_tapes.get(alert['symbol'],[]))
        decision=datetime.fromisoformat(alert['decision_at'])
        eligible=[(t,b) for t,b in tape if t>=decision]
        if not eligible or (eligible[0][0]-decision).total_seconds()>60:
            outcomes.append(dict(record,result='missing_entry_option_bar'))
            continue
        entry_time,entry_bar=eligible[0]
        session=session_map[alert['day']]
        entry_index=bisect_right(session['stamps'],entry_time-timedelta(minutes=1))-1
        known_underlying=session['bars'][entry_index]['c']
        levels_valid=(alert['stop']<known_underlying<alert['target'] if alert['side']=='CALL'
                      else alert['target']<known_underlying<alert['stop'])
        if not levels_valid:
            outcomes.append(dict(record,result='source_levels_already_crossed'))
            continue
        # Adverse cent rounding and reserve BOTH sides' $0.65 assumed fees.
        entry=math.ceil(entry_bar['o']*(1+adverse_fraction)*100-1e-9)/100
        qty=math.floor(cash/(entry*100+1.30))
        if qty<1:
            outcomes.append(dict(record,result='cannot_afford_one_contract',cash=cash,entry_proxy=entry))
            continue
        forced=datetime.fromisoformat(alert['close'])-timedelta(minutes=30)
        high_water=0.
        trailing=False
        prior=None
        exit_info=None
        for stamp,bar in eligible:
            if prior is not None and (stamp-prior[0]).total_seconds()>60:
                raise ValueError('Missing option minute while position open; do not invent exit')
            index=bisect_right(session['stamps'],stamp-timedelta(minutes=1))-1
            underlying=session['bars'][index]['c']
            reason=None
            if alert['side']=='CALL':
                if underlying>=alert['target']:reason='creator_target'
                elif underlying<=alert['stop']:reason='creator_stop'
            else:
                if underlying<=alert['target']:reason='creator_target'
                elif underlying>=alert['stop']:reason='creator_stop'
            if reason is None and prior is not None:
                pnl=(prior[1]['c']-entry)/entry
                high_water=max(high_water,pnl)
                trailing=trailing or high_water>=.30
                if pnl<=-.50:reason='premium_stop_50pct'
                elif trailing and high_water-pnl>=.20:reason='premium_trailing_stop'
            if reason is None and stamp>=forced:reason='expiry_force_close'
            if reason:
                exit_price=max(0.,math.floor(bar['o']*(1-adverse_fraction)*100+1e-9)/100)
                exit_info=(stamp,exit_price,reason,bar['o'])
                break
            prior=(stamp,bar)
        if exit_info is None:
            raise ValueError('No observed expiry exit: unresolved holding, not a completed month')
        exit_time,exit_price,reason,exit_raw=exit_info
        fee=qty*1.30
        pnl=qty*100*(exit_price-entry)-fee
        spent=qty*(entry*100+.65)
        if spent+qty*.65>cash+1e-8:
            raise AssertionError('Premium and roundtrip fees exceed simulated cash')
        cash+=pnl
        if cash < -.000001:
            raise AssertionError('Negative cash')
        peak=max(peak,cash)
        drawdown=max(drawdown,(peak-cash)/peak*100)
        ledger.append(dict(record,entry_at=entry_time.isoformat(),exit_at=exit_time.isoformat(),
            qty=qty,starting_equity=cash-pnl,premium_spent=spent,entry_price=entry,exit_price=exit_price,
            reason=reason,fees=fee,modeled_price_cost=qty*100*((entry-entry_bar['o'])+(exit_raw-exit_price)),
            net_pnl=pnl,ending_equity=cash))
        outcomes.append(dict(record,result='closed',reason=reason))
    return dict(starting_equity=1000.,allocation_cap=1.,adverse_fraction_per_side=adverse_fraction,
        final_equity=cash,net_pnl=cash-1000,return_pct=(cash/1000-1)*100,
        trades=len(ledger),wins=sum(t['net_pnl']>0 for t in ledger),losses=sum(t['net_pnl']<0 for t in ledger),
        closed_trade_equity_drawdown_pct=drawdown,fees=sum(t['fees'] for t in ledger),
        modeled_price_cost=sum(t['modeled_price_cost'] for t in ledger),final_positions=0,
        outcomes=outcomes,ledger=ledger)


def run(stock_path, calendar_path, option_path):
    stock_path,option_path=Path(stock_path),Path(option_path)
    stock=json.loads(stock_path.read_text())
    if stock.get('timeframe')!='1Min':raise ValueError('Need actual one-minute underlying bars')
    sessions=prepare(stock['bars'],json.loads(Path(calendar_path).read_text()),'2026-09')
    plan=build_plan(sessions)
    options=json.loads(option_path.read_text())
    if options['symbols']!=[a['symbol'] for a in plan]:
        raise ValueError('Option data does not match the predetermined alert contracts')
    return dict(month='2026-09',sessions=len(sessions),synthetic_creator=True,real_orders=0,
        starting_equity=1000,allocation_cap=1,stock_sha256=hashlib.sha256(stock_path.read_bytes()).hexdigest(),
        option_sha256=hashlib.sha256(option_path.read_bytes()).hexdigest(),
        plan='One 09:45 ET fake alert per session. CALL when first 15-minute close exceeds session open, otherwise PUT. Nearest whole-dollar strike, same-day expiry. Actual production candle gate; no parameter retuning after returns.',
        source='Alpaca historical SPY and option trade bars; authenticated read-only GETs',
        alerts=plan,models=[replay(plan,sessions,options['bars'],c) for c in (.01,.03,.05)],
        limitations=['Independent option execution/exit research model, not production entry/exit monitor or broker fills.',
            'Real trade OHLC, not historical bid/ask. Entry/exit open prints are proxy fills with assumed adverse costs, no queue/slippage validation.',
            'Closed-minute signals and premium marks, not live five-second monitor resolution.',
            'Assumed $0.65 per contract per side, not authenticated fee schedule.',
            'No broker account/PDT/settled-cash eligibility enforcement. No live configuration changed.',
            'All-in refers to cash reserved for whole-contract premium and roundtrip fees; unspendable residual remains cash.',
            'Synthetic creator trend rule, not Stock Alley skill, a prospective holdout, or a profit forecast.'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock',type=Path,required=True)
    parser.add_argument('--calendar',type=Path,required=True)
    parser.add_argument('--options',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.stock,args.calendar,args.options)
    args.output.write_text(json.dumps(result,indent=2))
    print(json.dumps([{k:v for k,v in m.items() if k not in ('ledger','outcomes')}
                      for m in result['models']],indent=2))
