"""Research-only SPY comparison. No broker imports or live strategy promotion.

Rules and split dates are fixed before results. Adjusted daily returns are a
total-return proxy, not executable historical prices. Intraday bars use split
adjustment and no dividend assumption. Costs are explicit hypothetical bps.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

SPLITS = {'development': ('2017-10-14','2022-12-31'),
          'selection': ('2023-01-01','2024-12-31'),
          'evaluation': ('2025-01-01','2026-10-13')}
COSTS = (1., 5., 10., 25.)  # per side, combines fee/spread/slippage assumption


def load_bars(path):
    payload=json.loads(Path(path).read_text())
    rows=payload['bars']
    frame=pd.DataFrame(rows)
    frame.index=pd.to_datetime(frame.pop('t'),utc=True)
    validate(frame)
    return frame,payload


def validate(frame):
    if frame.empty or not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError('Missing, unordered or duplicate bars')
    if frame.index.tz is None:
        raise ValueError('Timezone-aware data required')
    prices=frame[['o','h','l','c']].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices<=0).any():
        raise ValueError('Nonfinite or nonpositive prices')
    if ((frame.h<frame[['o','c','l']].max(axis=1)) | (frame.l>frame[['o','c','h']].min(axis=1))).any():
        raise ValueError('Inconsistent OHLC')
    if 'v' in frame and ((frame.v<0).any() or not np.isfinite(frame.v).all()):
        raise ValueError('Invalid volume')


def daily_weights(frame):
    close=frame.c
    sma200=close.rolling(200,min_periods=200).mean()
    sma50=close.rolling(50,min_periods=50).mean()
    trend=(close>sma200).astype(float)
    change=close.diff()
    gain=change.clip(lower=0).rolling(2).sum()
    loss=(-change.clip(upper=0)).rolling(2).sum()
    rsi=100*gain/(gain+loss).replace(0,np.nan)
    breakout_hi=frame.h.rolling(20).max().shift(1)
    breakout_lo=frame.l.rolling(10).min().shift(1)
    channel=[];pullback=[];held=False;dip=False;age=0
    for i in range(len(frame)):
        if held and close.iloc[i]<breakout_lo.iloc[i]:held=False
        elif not held and close.iloc[i]>breakout_hi.iloc[i]:held=True
        channel.append(float(held))
        if dip:
            age+=1
            if rsi.iloc[i]>70 or not trend.iloc[i] or age>=5:dip=False
        elif trend.iloc[i] and rsi.iloc[i]<10:dip=True;age=0
        pullback.append(float(dip))
    dip=pd.Series(pullback,index=frame.index)
    vol=close.pct_change().rolling(20).std(ddof=1)*np.sqrt(252)
    return {'cash':pd.Series(0.,index=frame.index),
            'buy_hold':pd.Series(1.,index=frame.index),
            'trend_200':trend,
            'dual_50_200':((sma50>sma200)&(close>sma200)).astype(float),
            'channel_20_10':pd.Series(channel,index=frame.index),
            'trend_pullback_rsi2':dip,
            'trend_vol10':(trend*(.10/vol).clip(upper=1)).fillna(0),
            'blend_trend_pullback':.5*trend+.5*dip}


def daily_returns(frame, signal, cost_bps):
    if not np.isfinite(cost_bps) or cost_bps<0:raise ValueError('Invalid cost')
    if not signal.index.equals(frame.index) or not np.isfinite(signal).all() or (signal<0).any() or (signal>1).any():
        raise ValueError('Invalid unlevered weights')
    # Closing-bar signals cannot fill at that same bar's open or close.
    position=signal.shift(1).fillna(0)
    returns=position*(frame.o.shift(-1)/frame.o-1)
    turnover=position.diff().fillna(position).abs()
    returns-=turnover*cost_bps/10000
    return pd.DataFrame({'return':returns,'exposure':position,'turnover':turnover}).iloc[:-1]


def metrics(returns, exposure=None):
    r=pd.Series(returns).dropna()
    if r.empty:raise ValueError('Empty evaluation window')
    if not np.isfinite(r).all() or (r<=-1).any():raise ValueError('Invalid modeled returns')
    curve=(1+r).cumprod()
    peak=curve.cummax().clip(lower=1)
    vol=float(r.std(ddof=1)) if len(r)>1 else 0.
    return {'intervals':len(r),'return_pct':float((curve.iloc[-1]-1)*100),
            'cagr_pct':float((curve.iloc[-1]**(252/len(r))-1)*100),
            'max_drawdown_pct':float((curve/peak-1).min()*100),
            'sharpe_zero_cash':float(r.mean()/vol*np.sqrt(252)) if vol>0 else 0.,
            'exposure_pct':float(pd.Series(exposure).mean()*100) if exposure is not None else None,
            'yearly_return_pct':{str(y):float(((1+g).prod()-1)*100) for y,g in r.groupby(r.index.year)}}


def intraday_returns(frame, cost_bps=5., calendar=None):
    """15-minute, long-only, one trade/day; closed-bar confirmation then next open.

    No targets inferred from OHLC. Stops fill at open on gaps, otherwise at
    stop plus costs. Exits at the 15:45 bar open, avoiding a fictional fill at
    the final day's last close. Incomplete/early-close sessions are excluded.
    """
    if not np.isfinite(cost_bps) or cost_bps<0:raise ValueError('Invalid cost')
    validate(frame)
    local=frame.copy();local.index=local.index.tz_convert('America/New_York')
    regular=local[(local.index.hour*60+local.index.minute>=570)&(local.index.hour*60+local.index.minute<960)]
    names=('orb15','orb15_vwap','vwap_pullback','orb15_vwap_gap')
    rows={name:[] for name in names};excluded=[];previous_close=None
    for day,bars in regular.groupby(regular.index.date):
        minutes=bars.index.hour*60+bars.index.minute
        if calendar is not None and calendar.get(str(day))!='16:00':
            excluded.append(str(day));previous_close=None;continue
        if len(bars)!=26 or list(minutes)!=list(range(570,960,15)):
            excluded.append(str(day));previous_close=None;continue
        cumulative=(bars.vw*bars.v).cumsum()/bars.v.cumsum().replace(0,np.nan)
        high,low=float(bars.h.iloc[0]),float(bars.l.iloc[0])
        gap=abs(float(bars.o.iloc[0])/previous_close-1) if previous_close else None
        for name in names:
            trade=None
            for j in range(1,24):
                orb=bars.c.iloc[j]>high
                above_vwap=bars.c.iloc[j]>cumulative.iloc[j]
                pullback=(bars.c.iloc[j-1]>cumulative.iloc[j-1] and bars.l.iloc[j]<=cumulative.iloc[j] and above_vwap)
                eligible=pullback if name=='vwap_pullback' else orb
                if name in ('orb15_vwap','orb15_vwap_gap'):eligible=eligible and above_vwap
                if name=='orb15_vwap_gap':eligible=eligible and gap is not None and .002<=gap<=.02
                if not eligible:continue
                entry=float(bars.o.iloc[j+1]);stop=float(bars.l.iloc[j]) if name=='vwap_pullback' else low
                if entry<=stop or (name!='vwap_pullback' and entry<=high):continue
                exit_price=float(bars.o.iloc[25]);reason='15:45_exit';exit_bar=25
                for k in range(j+1,25):
                    if bars.o.iloc[k]<=stop:
                        exit_price=float(bars.o.iloc[k]);reason='gap_stop';exit_bar=k;break
                    if bars.l.iloc[k]<=stop:
                        exit_price=stop;reason='stop';exit_bar=k;break
                # Exact buy/sell cost factors; no short or leveraged exposure.
                ret=(exit_price*(1-cost_bps/10000))/(entry*(1+cost_bps/10000))-1
                trade={'entry':entry,'exit':exit_price,'reason':reason,'entry_bar':j+1,
                       'exit_bar':exit_bar,'signal_bar':j,'stop':stop}
                break
            rows[name].append({'date':str(day),'return':ret if trade else 0.,'trade':trade})
        previous_close=float(bars.c.iloc[-1])
    return rows,excluded


def select_candidate(selection):
    # This rank is frozen without reading evaluation-period results.
    eligible={name:m for name,m in selection.items() if name not in ('cash','buy_hold')
              and m['return_pct']>0 and m['max_drawdown_pct']>=-25}
    return max(eligible,key=lambda name:eligible[name]['sharpe_zero_cash']) if eligible else 'cash'


def paired_block_interval(candidate,benchmark,seed=20261014,trials=2000,block=20):
    """Exploratory paired mean-return difference CI; not proof of future edge."""
    a=candidate.copy();b=benchmark.copy()
    a.index=a.index.strftime('%Y-%m-%d');b.index=b.index.strftime('%Y-%m-%d')
    paired=pd.concat([a.rename('candidate'),b.rename('benchmark')],axis=1).dropna()
    difference=(paired.candidate-paired.benchmark).to_numpy()
    if len(difference)<block*2:raise ValueError('Insufficient paired history')
    rng=np.random.default_rng(seed);means=[];n=len(difference)
    for _ in range(trials):
        starts=rng.integers(0,n,size=int(np.ceil(n/block)))
        indices=((starts[:,None]+np.arange(block))%n).ravel()[:n]
        means.append(float(difference[indices].mean()*252*100))
    low,high=np.quantile(means,[.025,.975])
    return {'paired_intervals':n,'block_days':block,'trials':trials,'seed':seed,
            'annualized_mean_difference_pct':float(difference.mean()*252*100),
            'exploratory_95pct_low':float(low),'exploratory_95pct_high':float(high)}


def compare(daily_path,intraday_path=None,calendar_path=None):
    daily,metadata=load_bars(daily_path)
    if daily.index[0]>pd.Timestamp('2016-10-14',tz='UTC')+pd.Timedelta(days=1) or daily.index[-1]<pd.Timestamp('2026-10-13',tz='UTC'):
        raise ValueError('Requested ten-year endpoints are not covered')
    returns={cost:{} for cost in COSTS};weights=daily_weights(daily)
    for cost in COSTS:
        for name,signal in weights.items():returns[cost][name]=daily_returns(daily,signal,cost)
    intraday_excluded=[];intraday_rows={}
    if intraday_path:
        if not calendar_path:raise ValueError('Authenticated historical session calendar required for intraday study')
        calendar={row['date']:row['close'] for row in json.loads(Path(calendar_path).read_text())}
        intraday,imetadata=load_bars(intraday_path)
        trials,intraday_excluded=intraday_returns(intraday,0.,calendar)
        for cost in COSTS:
            for name,rows in trials.items():
                frame=pd.DataFrame(rows);frame.index=pd.to_datetime(frame.pop('date'),utc=True)
                frame['return']=frame.trade.map(lambda t: t['exit']*(1-cost/10000)/(t['entry']*(1+cost/10000))-1 if t else 0.)
                frame['exposure']=frame.trade.map(lambda t:float(t is not None))
                frame['turnover']=frame.exposure*2
                returns[cost][name]=frame
            if cost==5.:intraday_rows=trials
    summaries={}
    for cost,candidates in returns.items():
        summaries[str(cost)]={}
        for name,frame in candidates.items():
            summaries[str(cost)][name]={}
            for split,(start,end) in SPLITS.items():
                window=frame.loc[start:end]
                m=metrics(window['return'],window.exposure)
                m['turnover_units']=float(window.turnover.sum())
                if 'trade' in window:
                    traded=window[window.trade.notna()]
                    m['trades']=len(traded)
                    m['win_rate_pct']=float((traded['return']>0).mean()*100) if len(traded) else None
                summaries[str(cost)][name][split]=m
    selection={name:parts['selection'] for name,parts in summaries['5.0'].items()}
    chosen=select_candidate(selection)
    test=summaries['5.0'][chosen]['evaluation'];benchmark=summaries['5.0']['buy_hold']['evaluation']
    stress=summaries['25.0'][chosen]['evaluation']
    start,end=SPLITS['evaluation']
    interval=paired_block_interval(returns[5.][chosen].loc[start:end]['return'],returns[5.]['buy_hold'].loc[start:end]['return'])
    gates={'positive_evaluation':test['return_pct']>0,'positive_25bps_stress':stress['return_pct']>0,
           'beats_buy_hold_sharpe':test['sharpe_zero_cash']>benchmark['sharpe_zero_cash'],
           'drawdown_under_15pct':test['max_drawdown_pct']>=-15,
           'paired_return_difference_lower_bound_positive':interval['exploratory_95pct_low']>0,
           'live_forward_paper_verified':False,'realistic_quote_fills_verified':False}
    close_returns=daily.c.pct_change().dropna()
    behavior=metrics(close_returns)
    behavior.update(up_days_pct=float((close_returns>0).mean()*100),
                    worst_day_pct=float(close_returns.min()*100),best_day_pct=float(close_returns.max()*100),
                    annualized_vol_pct=float(close_returns.std()*np.sqrt(252)*100),
                    overnight_mean_bps=float((daily.o/daily.c.shift(1)-1).mean()*10000),
                    open_close_mean_bps=float((daily.c/daily.o-1).mean()*10000))
    return {'source':metadata['source'],'daily_adjustment':metadata.get('adjustment'),
            'daily_bars':len(daily),'daily_start':daily.index[0].isoformat(),'daily_end':daily.index[-1].isoformat(),
            'daily_sha256':hashlib.sha256(Path(daily_path).read_bytes()).hexdigest(),
            'intraday_bars':len(intraday) if intraday_path else 0,'excluded_intraday_sessions':intraday_excluded,
            'intraday_sha256':hashlib.sha256(Path(intraday_path).read_bytes()).hexdigest() if intraday_path else None,
            'calendar_sha256':hashlib.sha256(Path(calendar_path).read_bytes()).hexdigest() if calendar_path else None,
            'trial_count':len(summaries['5.0'])-2,'costs_bps_per_side':list(COSTS),'split_dates':SPLITS,
            'selection_locked_before_evaluation':chosen,'summaries':summaries,'spy_behavior':behavior,
            'selected_vs_benchmark_interval':interval,
            'promotion_gates':gates,'live_promotion_authorized':False,'real_orders':0,
            'limitations':['fractional unlevered research exposure; current $100 whole-share bot cannot reproduce SPY exposure',
                           'daily corporate-action-adjusted total-return proxy; no option prices or profits inferred',
                           'hypothetical costs, no tax or cash interest, no tick-level fills, no early-close trades',
                           'holdout is a one-time historical evaluation, not an unseen future test',
                           'finite candidates; not every strategy or certification of best trader']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--daily',type=Path,required=True);p.add_argument('--intraday',type=Path)
    p.add_argument('--calendar',type=Path)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    report=compare(a.daily,a.intraday,a.calendar)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('summaries','excluded_intraday_sessions')}))


if __name__=='__main__':main()
