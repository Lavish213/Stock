"""Durable synthetic creator execution lab. No broker imports or network execution.

Prices and timestamps are supplied observations, never historical fill evidence.
All state transitions, receipts and simulated fills commit in one SQLite transaction.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def number(value, *, positive=False):
    if isinstance(value, bool) or len(str(value)) > 64:
        raise ValueError('Boolean is not a number')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('Invalid number') from exc
    if not result.is_finite() or result < 0 or result > Decimal('1e12') or (positive and result == 0):
        raise ValueError('Invalid numeric range')
    return result


def cents(value):
    return int((number(value) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result.timestamp()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


class PaperLab:
    """One simulated account. Transactions also serialize independent processes.

    Faults are modeled as unavailable execution ticks, partial liquidity, and
    committed-but-unacknowledged receipts; an identical retry returns its receipt.
    """
    def __init__(self, path, *, cash='10000', trade_budget='250', gross_limit='1000',
                 daily_loss='100', fee='0.65', delay=1.0, quote_age=5.0):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        config = dict(initial_cash=cents(cash), trade_budget=cents(trade_budget),
                      gross_limit=cents(gross_limit), daily_loss=cents(daily_loss),
                      fee=cents(fee), delay=float(number(delay)), quote_age=float(number(quote_age, positive=True)))
        if not all(config[k] > 0 for k in ('initial_cash', 'trade_budget', 'gross_limit', 'daily_loss')):
            raise ValueError('Explicit positive simulation limits required')
        if config['trade_budget'] > config['gross_limit']:
            raise ValueError('Trade budget exceeds gross limit')
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, digest TEXT NOT NULL, body TEXT NOT NULL)')
            columns = {row[1] for row in db.execute('PRAGMA table_info(receipts)')}
            for name, definition in (('event', 'TEXT'), ('observed_at', 'REAL')):
                if name not in columns:
                    db.execute('ALTER TABLE receipts ADD COLUMN '+name+' '+definition)
            db.execute('INSERT OR IGNORE INTO state VALUES (1,?)', (canonical(dict(
                config=config, cash=config['initial_cash'], realized=0, loss_day=None,
                daily_realized=0, halted=False, plans={}, quotes={}, fills=[], last_tick=None)),))
            existing = json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            if existing['config'] != config:
                raise ValueError('Restart must preserve persisted simulation limits')
        os.chmod(path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA synchronous=FULL')
        return db

    def snapshot(self):
        with closing(self.connect()) as db:
            return json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])

    def apply(self, event, *, now=None):
        now = time.time() if now is None else float(number(now, positive=True))
        if not isinstance(event, dict) or not isinstance(event.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', event['id']):
            raise ValueError('Event identity required')
        digest = hashlib.sha256(canonical(event).encode()).hexdigest()
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT digest,body FROM receipts WHERE id=?', (event['id'],)).fetchone()
            if old:
                if old[0] != digest:
                    raise ValueError('Event identity conflict')
                db.rollback()
                return dict(json.loads(old[1]), duplicate=True)
            observed = timestamp(event['published_at'])
            if not 0 <= now - observed <= 120:
                raise ValueError('Stale or future event')
            state = json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            if db.execute('SELECT count(*) FROM receipts').fetchone()[0] >= 50000:
                raise ValueError('Journal capacity requires review')
            outcome = self.transition(state, event, observed, now)
            result = dict(id=event['id'], outcome=outcome, synthetic=True, broker_orders=0)
            db.execute('UPDATE state SET body=? WHERE id=1', (canonical(state),))
            db.execute('INSERT INTO receipts (id,digest,body,event,observed_at) VALUES (?,?,?,?,?)',
                       (event['id'], digest, canonical(result), canonical(event), now))
            db.commit()
            return result
        finally:
            db.close()

    def transition(self, s, e, observed, now):
        kind = e['kind']
        common = {'id', 'kind', 'published_at'}
        fields = {
            'setup': {'plan', 'version', 'symbol', 'asset', 'expires_at', 'trigger', 'hold_bars', 'bar_seconds', 'stop', 'targets'},
            'quote': {'symbol', 'bid', 'ask', 'underlying', 'market_open', 'liquidity', 'available'},
            'candle': {'plan', 'closed_at', 'close', 'market_open'},
            'close': {'plan', 'version', 'quantity'},
            'update': {'plan', 'version', 'stop', 'targets'},
            'tick': set(), 'halt': set(),
        }
        if kind not in fields or set(e) != common | fields[kind]:
            raise ValueError('Strict event schema required')
        if kind == 'halt':
            s['halted'] = True
            return 'halted_entries_exits_preserved'
        if kind == 'setup':
            if len(s['plans']) >= 1000:
                raise ValueError('Setup capacity requires review')
            if e['plan'] in s['plans']:
                raise ValueError('Plan already exists; use versioned update')
            symbol = e['symbol']
            if e['asset'] == 'equity':
                valid = isinstance(symbol, str) and re.fullmatch(r'[A-Z]{1,6}', symbol)
            elif e['asset'] == 'call':
                valid = isinstance(symbol, str) and re.fullmatch(r'[A-Z]{1,6}\d{6}C\d{8}', symbol)
                if valid:
                    expiry = datetime.strptime(re.search(r'(\d{6})C', symbol)[1], '%y%m%d').replace(tzinfo=timezone.utc)
                    valid = (datetime.fromtimestamp(observed, timezone.utc).date() <= expiry.date()
                             and datetime.fromtimestamp(timestamp(e['expires_at']), timezone.utc).date() <= expiry.date())
            else:
                valid = False
            if not valid or not isinstance(e['plan'], str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', e['plan']):
                raise ValueError('Exact equity or unexpired call identity required')
            if type(e['hold_bars']) is not int or not 1 <= e['hold_bars'] <= 20 or type(e['version']) is not int or e['version'] != 1:
                raise ValueError('Explicit confirmation and initial version required')
            if type(e['bar_seconds']) is not int or not 1 <= e['bar_seconds'] <= 3600:
                raise ValueError('Explicit candle interval required')
            trigger = number(e['trigger'], positive=True)
            self.levels(e['stop'], e['targets'], trigger)
            if timestamp(e['expires_at']) <= now:
                raise ValueError('Setup expired')
            s['plans'][e['plan']] = dict(e, state='armed', confirmations=0, last_candle=None,
                last_source=observed, quantity=0, cost=0, pending=None, next_target=0)
            return 'armed'
        if kind == 'quote':
            bid, ask = number(e['bid'], positive=True), number(e['ask'], positive=True)
            number(e['underlying'], positive=True)
            if bid > ask or not cents(bid) or type(e['liquidity']) is not int or not 0 <= e['liquidity'] <= 1000000 or type(e['market_open']) is not bool or type(e['available']) is not bool:
                raise ValueError('Invalid executable quote')
            old = s['quotes'].get(e['symbol'])
            if old and observed <= old['at']:
                raise ValueError('Quote observations must increase')
            s['quotes'][e['symbol']] = dict(e, at=observed)
            self.tick(s, now)
            return 'quote_observed'
        if kind == 'tick':
            self.tick(s, now)
            return 'advanced'
        p = s['plans'].get(e['plan'])
        if not p:
            raise ValueError('Unknown owned setup')
        if kind == 'candle':
            closed = timestamp(e['closed_at'])
            if closed > observed or now - closed > s['config']['quote_age'] or (p['last_candle'] is not None and closed <= p['last_candle']):
                raise ValueError('Candle must be fresh, closed and increasing')
            if p['last_candle'] is not None:
                interval = closed - p['last_candle']
                if interval < p['bar_seconds']:
                    raise ValueError('Candle interval too short')
                if interval > p['bar_seconds'] * 1.5:
                    p['confirmations'] = 0
            if type(e['market_open']) is not bool:
                raise ValueError('Market session required')
            price = number(e['close'], positive=True)
            p['last_candle'] = closed
            if p['state'] != 'armed':
                return 'candle_ignored'
            p['confirmations'] = p['confirmations'] + 1 if e['market_open'] and price > number(p['trigger']) else 0
            if p['confirmations'] >= p['hold_bars']:
                self.enter(s, p, now)
            return p['state']
        if type(e['version']) is not int or e['version'] != p['version'] + 1 or observed < p['last_source']:
            raise ValueError('Source version out of order')
        if p['state'] in ('closed', 'expired', 'canceled'):
            raise ValueError('Setup lifecycle already terminal')
        if kind == 'update':
            self.levels(e['stop'], e['targets'], number(p['trigger']), amendment=True)
            if p['pending'] and p['pending']['side'] == 'sell':
                raise ValueError('Exit pending; update requires review')
            p.update(stop=e['stop'], targets=e['targets'], next_target=0)
            self.tick(s, now)
        elif kind == 'close':
            if type(e['quantity']) is not int or not 1 <= e['quantity'] <= p['quantity']:
                raise ValueError('Close must specify owned whole units')
            if p['pending']:
                raise ValueError('One order may be pending per setup')
            self.intent(p, 'sell', e['quantity'], now, 'creator_close')
        p.update(version=e['version'], last_source=observed)
        return 'updated' if kind == 'update' else 'exit_pending'

    @staticmethod
    def levels(stop, targets, trigger, *, amendment=False):
        stop = number(stop, positive=True)
        if not isinstance(targets, list) or not 1 <= len(targets) <= 10:
            raise ValueError('Explicit targets required')
        prices = []
        total = Decimal(0)
        for target in targets:
            if not isinstance(target, dict) or set(target) != {'price', 'fraction'}:
                raise ValueError('Target price and allocation required')
            prices.append(number(target['price'], positive=True))
            total += number(target['fraction'], positive=True)
        if stop >= (prices[0] if amendment else trigger) or any(x <= trigger for x in prices) or prices != sorted(set(prices)) or total != 1:
            raise ValueError('Long setup levels or allocations inconsistent')

    @staticmethod
    def intent(p, side, quantity, now, reason):
        p['pending'] = dict(side=side, remaining=quantity, requested=quantity, at=now, reason=reason)
        p['state'] = 'entry_pending' if side == 'buy' else 'exit_pending'

    def quote(self, s, p, now):
        q = s['quotes'].get(p['symbol'])
        return q if q and 0 <= now - q['at'] <= s['config']['quote_age'] and q['market_open'] and q['available'] else None

    def enter(self, s, p, now):
        q = self.quote(s, p, now)
        if s['halted'] or now >= timestamp(p['expires_at']) or not q:
            p['confirmations'] = 0
            return
        if number(q['underlying']) < number(p['trigger']) or number(q['underlying']) >= number(p['targets'][0]['price']):
            p['confirmations'] = 0
            return
        multiplier = 100 if p['asset'] == 'call' else 1
        per_unit = cents(number(q['ask']) * multiplier) + s['config']['fee']
        reserved = sum(x['pending']['reserve'] for x in s['plans'].values() if x['pending'] and x['pending']['side'] == 'buy')
        gross = sum(x['cost'] for x in s['plans'].values()) + reserved
        budget = min(s['config']['trade_budget'], s['cash'] - reserved, s['config']['gross_limit'] - gross)
        qty = budget // per_unit
        if qty <= 0:
            p['confirmations'] = 0
            return
        self.intent(p, 'buy', qty, now, 'confirmed_trigger')
        p['pending'].update(reserve=qty * per_unit, max_unit=per_unit)

    def tick(self, s, now):
        if s['last_tick'] is not None and now < s['last_tick']:
            raise ValueError('Simulation clock cannot regress')
        s['last_tick'] = now
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        if s['loss_day'] != day:
            s.update(loss_day=day, daily_realized=0)
        for p in s['plans'].values():
            q = self.quote(s, p, now)
            if p['state'] == 'armed' and now >= timestamp(p['expires_at']):
                p['state'] = 'expired'
            pending = p['pending']
            if pending and pending['side'] == 'buy' and (s['halted'] or now >= timestamp(p['expires_at']) or (q and (number(q['underlying']) <= number(p['stop']) or number(q['underlying']) >= number(p['targets'][0]['price'])))):
                p['pending'] = None
                p['state'] = 'open' if p['quantity'] else 'canceled'
                pending = None
            if pending and q and now >= pending['at'] + s['config']['delay']:
                side = pending['side']
                multiplier = 100 if p['asset'] == 'call' else 1
                unit = cents(number(q['ask' if side == 'buy' else 'bid']) * multiplier)
                fee = s['config']['fee']
                quantity = min(pending['remaining'], q['liquidity'])
                if side == 'buy' and unit + fee > pending['max_unit']:
                    p['pending'] = None
                    p['state'] = 'open' if p['quantity'] else 'canceled'
                    quantity = 0
                if quantity:
                    q['liquidity'] -= quantity
                    if side == 'buy':
                        cost = quantity * (unit + fee)
                        s['cash'] -= cost
                        p['cost'] += cost
                        p['quantity'] += quantity
                        pending['reserve'] -= quantity * pending['max_unit']
                    else:
                        basis = p['cost'] if quantity == p['quantity'] else p['cost'] * quantity // p['quantity']
                        proceeds = quantity * (unit - fee)
                        s['cash'] += proceeds
                        p['cost'] -= basis
                        p['quantity'] -= quantity
                        pnl = proceeds - basis
                        s['realized'] += pnl
                        s['daily_realized'] += pnl
                        if s['daily_realized'] <= -s['config']['daily_loss']:
                            s['halted'] = True
                    pending['remaining'] -= quantity
                    s['fills'].append(dict(plan=p['plan'], symbol=p['symbol'], side=side, quantity=quantity,
                        unit_cents=unit, fee_cents=quantity * fee, at=now, intent_at=pending['at'], reason=pending['reason']))
                    if not pending['remaining']:
                        p['pending'] = None
                        p['state'] = 'open' if p['quantity'] else 'closed'
            if p['quantity'] and not p['pending'] and q:
                underlying = number(q['underlying'])
                if now >= timestamp(p['expires_at']) or underlying <= number(p['stop']):
                    self.intent(p, 'sell', p['quantity'], now, 'expiry' if now >= timestamp(p['expires_at']) else 'stop')
                elif p['next_target'] < len(p['targets']):
                    target = p['targets'][p['next_target']]
                    if underlying >= number(target['price']):
                        initial = sum(f['quantity'] for f in s['fills'] if f['plan'] == p['plan'] and f['side'] == 'buy')
                        quantity = p['quantity'] if p['next_target'] == len(p['targets']) - 1 else min(p['quantity'], int(initial * number(target['fraction'])))
                        p['next_target'] += 1
                        if quantity:
                            self.intent(p, 'sell', quantity, now, 'target')


def handler(lab, secret):
    if not re.fullmatch(r'[a-fA-F0-9]{64,128}', secret):
        raise ValueError('Random 32-byte or stronger hex secret required')

    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, result):
            body = canonical(result).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != '/health':
                return self.respond(404, {'error': 'not_found'})
            self.respond(200, dict(synthetic=True, broker_orders=0, live=False,
                readiness='simulation_only', halted=lab.snapshot()['halted']))

        def do_POST(self):
            try:
                if self.path != '/v1/events' or self.headers.get('Transfer-Encoding'):
                    raise ValueError('Invalid request')
                for key in ('Content-Length', 'X-Timestamp', 'X-Signature'):
                    if len(self.headers.get_all(key, [])) != 1:
                        raise ValueError('Missing or duplicate header')
                length = int(self.headers['Content-Length'])
                if not 1 <= length <= 32768:
                    raise ValueError('Invalid body length')
                self.connection.settimeout(3)
                body = self.rfile.read(length)
                stamp = self.headers['X-Timestamp']
                if not re.fullmatch(r'\d{10,11}', stamp) or not 0 <= time.time() - int(stamp) <= 120:
                    raise ValueError('Authentication expired')
                expected = hmac.new(secret.encode(), stamp.encode() + b'\n' + body, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected, self.headers['X-Signature']):
                    return self.respond(401, {'error': 'authentication_failed'})
                event = json.loads(body, object_pairs_hook=unique)
                self.respond(200, lab.apply(event))
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                self.respond(400, {'error': 'invalid_event_or_conflict'})
            except (sqlite3.Error, OSError):
                self.respond(503, {'error': 'journal_unavailable'})
    return Receiver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--port', type=int, default=8878)
    parser.add_argument('--cash', required=True)
    parser.add_argument('--trade-budget', required=True)
    parser.add_argument('--gross-limit', required=True)
    parser.add_argument('--daily-loss', required=True)
    parser.add_argument('--check', action='store_true', help='Validate persisted limits and secret without starting a receiver')
    args = parser.parse_args()
    lab = PaperLab(args.db, cash=args.cash, trade_budget=args.trade_budget,
                   gross_limit=args.gross_limit, daily_loss=args.daily_loss)
    receiver = handler(lab, os.environ.get('PAPER_LAB_SECRET', ''))
    if args.check:
        lab.snapshot()
        print('Synthetic lab preflight passed; zero broker orders')
        return
    server = HTTPServer(('127.0.0.1', args.port), receiver)
    server.timeout = 3
    print('Synthetic creator receiver: 127.0.0.1:%s; zero broker orders' % args.port, flush=True)
    from lavish_core.utils import sd_notify
    sd_notify.notify_ready()
    while True:
        server.handle_request()
        lab.snapshot()  # Journal must remain readable before sending heartbeat.
        sd_notify.notify_watchdog()


if __name__ == '__main__':
    main()
