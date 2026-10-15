"""Synthetic month simulation for high-volatility small-cap call options
(LUNR, ASTS by default) - requested as a direct extension of
options_creator_month.py's SPY simulation, reusing its CallAccount
mechanics (whole-contract sizing, real costs, allocation tiers, duplicate/
zero-bid handling, loss-streak pause) unchanged.

What's DIFFERENT here, and why it matters: these aren't SPY. Starting
price and volatility are rough, clearly-approximate levels pieced
together from web search (no live market data reachable from this
environment) - LUNR ~$16, ASTS ~$80, both at 90% annualized volatility
vs. SPY's ~20-25%. That volatility difference is the whole point of
running this separately rather than just relabeling the SPY output:
higher IV means expensive premium (bigger chunk of a small account per
contract) AND bigger/faster price swings (more extreme win/loss
outcomes), which is exactly the real character of these names versus a
broad index.

This is NOT tuned to produce a profit. Whatever this prints is whatever
it actually produces - a loss is reported as a loss, same as
options_creator_month.py's own results.

Real finding surfaced just by trying this: as configured today, the
production parser rejects BOTH tickers outright (extract_signal.py
requires a text-only alert's ticker to score a perfect 100 against
KNOWN_TICKERS, which is built from WHITELIST_TICKERS - neither LUNR nor
ASTS is on the default list). experiment() patches KNOWN_TICKERS to add
them for the duration of this simulation only, so the options mechanics
can actually be exercised - this does NOT change the real bot's default
whitelist or any live config.
"""
from __future__ import annotations
import json
import math
import random
import statistics
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

PT = ZoneInfo('America/Los_Angeles')

from lavish_core.validation.options_creator_month import CallAccount, offline, parse, quote, cash


def generate(ticker, start_price, volatility, seed, days=32):
    rng = random.Random(seed)
    first = date(2026, 10, 1)
    scenarios = []
    s = start_price
    for i in range(days):
        day = first + timedelta(days=i)
        s = start_price * math.exp(rng.gauss(0, volatility / math.sqrt(252)) * math.sqrt(i + 1) / math.sqrt(days))
        # Strike chosen slightly OTM, same spacing logic as the SPY sim
        # just scaled to this ticker's own price level instead of a fixed
        # $10-22 offset (which would be a meaningless strike gap on a $16 stock).
        strike_offset = max(0.5, round(start_price * rng.uniform(0.03, 0.08), 1))
        strike = round(s + strike_offset, 1)
        at = datetime.combine(day, datetime.min.time(), tzinfo=PT).replace(hour=7, minute=30)
        target = round(s + strike_offset * 1.6, 2)
        stop = round(s - strike_offset * 1.1, 2)
        creator_minute = rng.choice([60, 120, None])
        vol = volatility
        path = []
        for minute in range(331):
            if minute:
                minute_vol = vol / math.sqrt(252 * 390)
                s *= math.exp(rng.gauss(-minute_vol ** 2 / 2, minute_vol))
                vol = max(0.3, min(2.0, vol * math.exp(rng.gauss(0, 0.02))))
            bid, ask = quote(s, strike, max(0, 330 - minute), vol, spread=max(0.02, strike * 0.01))
            path.append(dict(at=(at + timedelta(minutes=minute)).isoformat(), underlying=s, bid=str(bid), ask=str(ask)))
        variant = rng.choice(['valid'] * 8 + ['recap', 'missing_expiry'])
        text = (f'BTO {ticker} {strike} CALL exp {day.isoformat()} target {target} stop {stop}' if variant == 'valid'
                else f'{ticker} is at the second target' if variant == 'recap'
                else f'BTO {ticker} {strike} CALL')
        scenarios.append(dict(
            event=dict(id=f'synthetic-{ticker}-{seed}-{i}', created_at=at.isoformat(), text=text, synthetic=True),
            path=path, target=target, stop=stop, creator_minute=creator_minute,
        ))
    return scenarios


def run(ticker, start_price, volatility, seed=20261015, delay_seconds=0, parse_fn=parse, initial=100, scenarios=None):
    account = CallAccount(initial)
    records = []
    for case in (generate(ticker, start_price, volatility, seed) if scenarios is None else scenarios):
        event = case['event']; path = case['path']; day = datetime.fromisoformat(event['created_at']).date()
        if day.weekday() < 5:
            account.begin(day)
        signal = parse_fn(event['text'])
        index = math.ceil(delay_seconds / 60)
        q = path[index]
        status = account.enter(event, signal, q['bid'], q['ask'], q['at'], q['underlying'], ticker=ticker)
        exit_trigger = None
        if status == 'entered':
            target = float(signal['target_hint']); stop = float(signal['stop_hint'])
            for j in range(index + 1, 316):
                now = path[j]; price = now['underlying']
                if j == 315 or price >= target or price <= stop or j == case['creator_minute']:
                    reason = 'day_cutoff' if j == 315 else 'target' if price >= target else 'stop' if price <= stop else 'creator_exit'
                    fill_index = min(330, j + index); fill = path[fill_index]
                    result = account.close(fill['bid'], fill['at'], reason, strike=signal['strike'], expiry=signal['expiry'])
                    if isinstance(result, dict):
                        exit_trigger = reason
                    break
            if account.position:
                end = path[-1]
                if end['underlying'] >= float(account.position['strike']):
                    raise AssertionError('Unresolved ITM exercise risk')
                account.close(0, end['at'], 'expired_worthless')
                exit_trigger = 'expired_worthless'
        records.append(dict(event=event, parser_signal=signal, status=status, exit_trigger=exit_trigger, balance=float(account.balance)))
    assert account.position is None
    assert account.initial + sum(D(str(t['net_pnl'])) for t in account.trades) == account.balance
    return dict(synthetic=True, profitability_evidence=False, ticker=ticker, seed=seed, delay_seconds=delay_seconds,
                initial=float(account.initial), final=float(account.balance), trades=account.trades,
                records=records, reviews=account.reviews, reached_500=account.half, reached_1000=account.tenth)


def experiment(ticker, start_price, volatility, trials=100):
    from lavish_core.vision import extract_signal
    cache = {}
    def cached(text):
        if text not in cache:
            cache[text] = parse(text)
        return cache[text]
    rows = []
    # Scoped to this experiment only - does not touch the real default
    # whitelist. See module docstring: neither ticker passes the real
    # parser's exact-match requirement without this.
    with offline(), patch.object(extract_signal, 'KNOWN_TICKERS', extract_signal.KNOWN_TICKERS | {ticker}):
        for i in range(trials):
            cases = generate(ticker, start_price, volatility, 20261015 + i)
            rows.append(run(ticker, start_price, volatility, 20261015 + i, 0, cached, scenarios=cases))
    balances = [r['final'] for r in rows]
    return dict(
        ticker=ticker, trials=trials, start_price=start_price, volatility=volatility,
        profitable=sum(x > 100 for x in balances), median=statistics.median(balances),
        worst=min(balances), best=max(balances),
        reached_500=sum(r['reached_500'] for r in rows), reached_1000=sum(r['reached_1000'] for r in rows),
        trades=sum(len(r['trades']) for r in rows),
        unaffordable=sum(x['status'] == 'unaffordable_contract' for r in rows for x in r['records']),
        worthless=sum(t['reason'] == 'expired_worthless' for r in rows for t in r['trades']),
    ), rows


if __name__ == '__main__':
    for ticker, price, vol in [('LUNR', 16.0, 0.90), ('ASTS', 80.0, 0.90)]:
        summary, rows = experiment(ticker, price, vol, trials=100)
        print(json.dumps(summary, indent=2, default=str))
        print(f"  first trial trades: {len(rows[0]['trades'])}, final balance: ${rows[0]['final']:.2f}")
        print()
