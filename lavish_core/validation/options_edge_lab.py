"""Offline, quote-based options hypothesis comparisons. No broker imports or I/O.

Quotes are observations, not guaranteed fills. Results never authorize execution.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


def number(value, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError('Invalid numeric value')
    return value


def validate(data):
    if data['source_kind'] not in ('synthetic', 'declared_opra') or not data['source_id']:
        raise ValueError('Explicit source kind and ID required')
    contracts = {c['symbol']: c for c in data['contracts']}
    if len(contracts) != len(data['contracts']):
        raise ValueError('Duplicate contract identity')
    for c in contracts.values():
        if c['side'] not in ('CALL', 'PUT') or c['multiplier'] != 100:
            raise ValueError('Standard long calls/puts only')
        number(c['strike'], .001)
        stamp(c['expiry_at'])
    ids = set()
    posts = set()
    for a in data['alerts']:
        identity = (a['post_id'], a['version_id'])
        if identity in ids or not all(identity):
            raise ValueError('Duplicate or missing source version')
        if a['post_id'] in posts:
            raise ValueError('Multiple versions of one post require edit-lifecycle replay, not independent trades')
        ids.add(identity)
        posts.add(a['post_id'])
        published, observed = stamp(a['published_at']), stamp(a['observed_at'])
        start, end = stamp(a['session_open']), stamp(a['session_close'])
        if observed < published or start >= end or not start <= observed < end:
            raise ValueError('Invalid alert availability or session')
        if a['side'] not in ('CALL', 'PUT'):
            raise ValueError('Invalid direction')
        for key in ('level', 'target', 'stop'):
            number(a[key], .001)
        direction = 1 if a['side'] == 'CALL' else -1
        if not direction*(a['target']-a['level']) > 0 or not direction*(a['level']-a['stop']) > 0:
            raise ValueError('Invalid structural boundaries')
        if a.get('creator_close_at'):
            stamp(a['creator_close_at'])  # receipt time, never inferred from a recap
    for key in ('ticks', 'quotes'):
        seen = set()
        for row in data[key]:
            event, available = stamp(row['timestamp']), stamp(row['available_at'])
            identity = (row.get('symbol', row.get('underlying')), event)
            if available < event or identity in seen:
                raise ValueError('Duplicate observation or availability before event')
            seen.add(identity)
            if key == 'ticks':
                number(row['price'], .001)
            else:
                if row['symbol'] not in contracts:
                    raise ValueError('Unknown contract quote')
                number(row['bid']); number(row['ask'], .001)
                if row['bid'] > row['ask']:
                    raise ValueError('Crossed quote')
                for size in ('bid_size', 'ask_size'):
                    if type(row[size]) is not int or row[size] < 0:
                        raise ValueError('Whole-contract displayed sizes required')
    return contracts


def latest(rows, when, max_age=5):
    eligible = [r for r in rows if stamp(r['available_at']) <= when and
                0 <= (when-stamp(r['timestamp'])).total_seconds() <= max_age]
    return max(eligible, key=lambda r: stamp(r['timestamp'])) if eligible else None


def trigger(alert, ticks, mode, delay):
    """Two-minute sampled hold, <=60s gaps; retest must precede the hold."""
    available = max(stamp(alert['published_at']), stamp(alert['observed_at'])) + timedelta(seconds=delay)
    end = stamp(alert['session_close'])
    direction = 1 if alert['side'] == 'CALL' else -1
    began = previous = None
    crossed = retested = False
    for row in sorted(ticks, key=lambda r: stamp(r['available_at'])):
        now = stamp(row['available_at'])
        if not available <= now < end or (now-stamp(row['timestamp'])).total_seconds() > 5:
            continue
        above = direction*(row['price']-alert['level']) > 0
        if previous is not None and (now-previous).total_seconds() > 60:
            began = None
        if above:
            crossed = True
            if mode == 'hold' or retested:
                began = began or now
                if (now-began).total_seconds() >= 120:
                    return now, row['price']
        else:
            if crossed:
                retested = True
            began = None
        previous = now
    return None


def trial(data, contracts, alert, mode='hold', delay=0, bucket=(1, 7), exit_rule='target',
          budget=100, fee=.65, slippage=.01, order_latency=1):
    if mode not in ('hold', 'retest_hold') or exit_rule not in ('target', 'premium_45', 'scale_80_runner'):
        raise ValueError('Unknown research policy')
    for value in (budget, fee, slippage, order_latency, delay): number(value)
    if len(bucket) != 2 or not 0 <= bucket[0] <= bucket[1]:
        raise ValueError('Invalid DTE bucket')
    ticks = [r for r in data['ticks'] if r['underlying'] == alert['underlying']]
    gate = trigger(alert, ticks, mode, delay)
    base = dict(post_id=alert['post_id'], version_id=alert['version_id'], regime=alert['regime'],
                published_at=alert['published_at'], observed_at=alert['observed_at'],
                entry_mode=mode, delay_seconds=delay, dte_bucket=list(bucket), exit_rule=exit_rule)
    def done(status, **values):
        return dict(base, status=status, **values)
    if not gate:
        return done('no_confirmed_trigger')
    when, spot = gate
    entry_time = when + timedelta(seconds=order_latency)
    if alert.get('creator_close_at') and stamp(alert['creator_close_at']) <= entry_time:
        return done('creator_already_closed')
    if entry_time >= stamp(alert['session_close']):
        return done('entry_after_session')
    tick = latest(ticks, entry_time)
    direction = 1 if alert['side'] == 'CALL' else -1
    if not tick or direction*(tick['price']-alert['level']) <= 0 or direction*(tick['price']-alert['target']) >= 0:
        return done('entry_invalidated_or_already_at_target')
    choices = []
    for symbol, c in contracts.items():
        if c['underlying'] != alert['underlying'] or c['side'] != alert['side']:
            continue
        if alert.get('contract_symbol') and symbol != alert['contract_symbol']:
            continue  # never silently replace the creator's specified contract
        dte = (stamp(c['expiry_at'])-entry_time).total_seconds()/86400
        if not bucket[0] <= dte <= bucket[1]:
            continue
        quotes = [q for q in data['quotes'] if q['symbol'] == symbol]
        q = latest(quotes, entry_time)
        if q and q['ask_size'] and (q['ask']-q['bid'])/q['ask'] <= .10:
            choices.append((abs(c['strike']-tick['price']), symbol, q, quotes))
    if not choices:
        return done('no_eligible_contract_quote')
    _, symbol, q, quotes = min(choices, key=lambda x: (x[0], x[1]))
    buy = q['ask'] + slippage
    qty = min(math.floor(budget/(buy*100+fee)), q['ask_size'])
    if qty < 1:
        return done('unaffordable')
    remaining = qty
    cash_flow = -qty*(buy*100+fee)
    fills = [dict(side='buy', at=entry_time.isoformat(), qty=qty, price=buy)]
    scaled = False
    pending = None
    end = stamp(alert['session_close'])
    close = stamp(alert['creator_close_at']) if alert.get('creator_close_at') else None
    # Underlying stops, creator receipts and session deadlines run on their
    # own event clocks. An option-quote-only loop loses transient breaches
    # and adds an entire quote interval to creator-close latency.
    events = [(stamp(r['available_at']), 0, 'tick', r) for r in ticks]
    events += [(stamp(r['available_at']), 3, 'quote', r) for r in quotes]
    events.append((end-timedelta(minutes=5), 2, 'session', None))
    if close:
        events.append((close, 1, 'creator', None))
    for now, _, event, row in sorted(events, key=lambda e: (e[0], e[1])):
        if not entry_time < now <= end:
            continue
        if row is not None and (now-stamp(row['timestamp'])).total_seconds() > 5:
            continue
        underlying = latest(ticks, now)
        if pending is None:
            reason = None
            if now >= end-timedelta(minutes=5): reason = 'session_exit'
            elif underlying and direction*(underlying['price']-alert['stop']) <= 0: reason = 'structural_stop'
            elif event == 'quote' and row['bid'] <= buy*.70: reason = 'premium_stop'
            elif close and now >= close: reason = 'creator_close'
            elif exit_rule == 'target' and underlying and direction*(underlying['price']-alert['target']) >= 0: reason = 'target'
            elif event == 'quote' and exit_rule == 'premium_45' and row['bid'] >= buy*1.45: reason = 'premium_target'
            elif event == 'quote' and exit_rule == 'scale_80_runner' and not scaled and row['bid'] >= buy*1.45: reason = 'scale'
            elif event == 'quote' and scaled and row['bid'] <= buy: reason = 'runner_breakeven_trigger'
            if reason:
                sell_qty = max(1, math.floor(qty*.8)) if reason == 'scale' else remaining
                pending = (now+timedelta(seconds=order_latency), sell_qty, reason)
        if event == 'quote' and pending and now >= pending[0] and row['bid'] > 0 and row['bid_size'] >= pending[1]:
            _, sell_qty, reason = pending
            sell = max(0, row['bid']-slippage)
            cash_flow += sell_qty*(sell*100-fee)
            remaining -= sell_qty
            fills.append(dict(side='sell', at=now.isoformat(), qty=sell_qty, price=sell, reason=reason))
            if reason == 'scale': scaled = True
            pending = None
            if remaining == 0:
                return done('closed', symbol=symbol, qty=qty, net=round(cash_flow, 2), fills=fills,
                            entry_at=entry_time.isoformat(), fractional_runner=False)
    return done('unresolved_exit', symbol=symbol, qty=qty, remaining=remaining, net=None,
                realized_cash_flow=round(cash_flow, 2), fills=fills)


def evaluate(data, budget=100):
    number(budget, .01)
    contracts = validate(data)
    outcomes = []
    for mode, delay, bucket, exit_rule in itertools.product(
            ('hold', 'retest_hold'), (0, 5, 15, 30, 60, 120),
            ((1, 7), (8, 21), (22, 45)), ('target', 'premium_45', 'scale_80_runner')):
        trades = [trial(data, contracts, a, mode, delay, bucket, exit_rule, budget) for a in data['alerts']]
        closed = [t for t in trades if t['status'] == 'closed']
        unresolved = sum(t['status'] == 'unresolved_exit' for t in trades)
        counts = {status: sum(t['status'] == status for t in trades) for status in sorted({t['status'] for t in trades})}
        regimes = {regime: dict(closed=sum(t['regime']==regime for t in closed),
                    closed_trade_net=round(sum(t['net'] for t in closed if t['regime']==regime),2))
                   for regime in sorted({t['regime'] for t in trades})}
        outcomes.append(dict(entry_mode=mode, delay_seconds=delay, dte_bucket=list(bucket), exit_rule=exit_rule,
            closed=len(closed), unresolved=unresolved, eligible_for_comparison=not unresolved and bool(closed),
            status_counts=counts, regimes=regimes,
            winning_closed_trades=sum(t['net']>0 for t in closed),
            closed_trade_net=round(sum(t['net'] for t in closed), 2),
            expectancy_per_closed_trade=round(sum(t['net'] for t in closed)/len(closed), 4) if closed else None,
            trades=trades))
    return dict(source_kind=data['source_kind'], source_id=data['source_id'], real_orders=0,
        profitability_established=False, authenticity_independently_verified=False,
        candidate_count=len(outcomes), per_alert_budget=budget, outcomes=outcomes,
        limitations=['Independent alert trials, not a shared-capital or compounded portfolio',
                    'Sampled two-minute hold is not proof of continuous price holding',
                    'Ask-entry/bid-exit plus 0.01 adverse slippage and 0.65 fee per contract per side',
                    'Displayed depth does not guarantee a fill; no queue, routing or hidden liquidity model',
                    'One-second order latency; missing or insufficient exit quotes stay unresolved',
                    'Caller supplies source session calendar; source authenticity requires independent audit',
                    'No automatic winner selection, parameter learning, or production configuration changes',
                    '108 comparisons require fresh prospective validation; consumed SPY holdout is excluded'])


if __name__ == '__main__':
    cli = argparse.ArgumentParser()
    cli.add_argument('--input', required=True); cli.add_argument('--output', required=True)
    cli.add_argument('--budget', type=float, default=100)
    args = cli.parse_args()
    raw = Path(args.input).read_bytes()
    report = evaluate(json.loads(raw), args.budget)
    report['input_sha256'] = hashlib.sha256(raw).hexdigest()
    with Path(args.output).open('x') as target:
        json.dump(report, target, indent=2)
