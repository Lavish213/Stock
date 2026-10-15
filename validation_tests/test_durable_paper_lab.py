import concurrent.futures
import hashlib
import hmac
import http.client
import json
import threading
import time
import socket
from datetime import datetime, timezone
from http.server import HTTPServer

import pytest

from lavish_core.validation.durable_paper_lab import PaperLab, canonical, handler

BASE = datetime(2026, 10, 14, 16, tzinfo=timezone.utc).timestamp()
REAL_CONNECT = socket.socket.connect


def stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


class Creator:
    def __init__(self, lab, symbol='AAPL', asset='equity', plan='creator-1'):
        self.lab, self.symbol, self.asset, self.plan = lab, symbol, asset, plan
        self.t, self.sequence = BASE, 0

    def event(self, kind, **fields):
        self.sequence += 1
        return dict(id=f'alert-{self.plan}-{self.sequence}', kind=kind, published_at=stamp(self.t), **fields)

    def send(self, kind, **fields):
        return self.lab.apply(self.event(kind, **fields), now=self.t)

    def setup(self, **changes):
        fields = dict(plan=self.plan, version=1, symbol=self.symbol, asset=self.asset,
            expires_at=stamp(BASE+3600), trigger='100', hold_bars=2, bar_seconds=1, stop='95',
            targets=[dict(price='105', fraction='.5'), dict(price='110', fraction='.5')])
        fields.update(changes)
        return self.send('setup', **fields)

    def quote(self, *, underlying='101', bid=None, ask=None, liquidity=100, available=True, market_open=True):
        self.t += 1
        return self.send('quote', symbol=self.symbol, bid=bid or ('1' if self.asset == 'call' else '100'),
            ask=ask or ('1.02' if self.asset == 'call' else '100.02'), underlying=underlying,
            liquidity=liquidity, available=available, market_open=market_open)

    def candle(self, price='101', market_open=True):
        self.t += 1
        return self.send('candle', plan=self.plan, closed_at=stamp(self.t), close=price, market_open=market_open)

    def enter(self):
        self.setup()
        self.quote()
        self.candle()
        self.candle()
        self.t += 1
        self.send('tick')

    def close(self, quantity, version=2):
        return self.send('close', plan=self.plan, version=version, quantity=quantity)

    def p(self):
        return self.lab.snapshot()['plans'][self.plan]


@pytest.fixture
def lab(tmp_path):
    return PaperLab(tmp_path/'paper.sqlite')


@pytest.mark.parametrize('asset,symbol', [('equity', 'AAPL'), ('call', 'NVDA261016C00235000')])
def test_entry_partial_targets_spread_fees_and_cash(lab, asset, symbol):
    c = Creator(lab, symbol, asset)
    c.enter()
    assert c.p()['quantity'] == 2
    assert c.p()['state'] == 'open'
    c.quote(underlying='105', bid='1.20' if asset=='call' else '104.90', ask='1.22' if asset=='call' else '105.02')
    c.t += 1
    c.send('tick')
    assert c.p()['quantity'] == 1
    c.quote(underlying='110', bid='1.40' if asset=='call' else '109.90', ask='1.42' if asset=='call' else '110.02')
    c.t += 1
    c.send('tick')
    s = lab.snapshot()
    assert c.p()['state'] == 'closed'
    assert s['cash'] == s['config']['initial_cash'] + s['realized']
    assert len(s['fills']) == 3
    assert sum(f['fee_cents'] for f in s['fills']) == 260
    assert s['realized'] == (5340 if asset=='call' else 1216)


def test_partial_entry_restart_and_partial_creator_exit(lab):
    c = Creator(lab)
    c.setup()
    c.quote(liquidity=1)
    c.candle(); c.candle()
    c.t += 1; c.send('tick')
    assert c.p()['quantity'] == 1 and c.p()['pending']['remaining'] == 1
    c.lab = PaperLab(lab.path)
    c.quote(liquidity=1)
    assert c.p()['quantity'] == 2 and c.p()['pending'] is None
    receipt = c.event('close', plan=c.plan, version=2, quantity=1)
    c.lab.apply(receipt, now=c.t)
    assert c.lab.apply(receipt, now=c.t+500)['duplicate']
    c.quote(liquidity=1)
    assert c.p()['quantity'] == 1
    c.close(1, version=3)
    c.quote(liquidity=1)
    assert c.p()['quantity'] == 0


def test_exit_waits_through_outage_then_fills_once(lab):
    c = Creator(lab); c.enter(); c.close(2)
    c.quote(available=False)
    assert c.p()['quantity'] == 2
    c.lab = PaperLab(lab.path)
    c.quote(liquidity=1)
    assert c.p()['quantity'] == 1
    c.quote(liquidity=1)
    assert c.p()['quantity'] == 0
    assert len(c.lab.snapshot()['fills']) == 3


def test_stop_cancels_unfilled_entry_then_closes_owned_partial(lab):
    c = Creator(lab); c.setup(); c.quote(liquidity=1); c.candle(); c.candle()
    c.t += 1; c.send('tick')
    c.quote(underlying='94', bid='94', ask='94.10')
    assert c.p()['pending']['side'] == 'sell'
    c.quote(underlying='94', bid='94', ask='94.10')
    assert c.p()['quantity'] == 0
    assert sum(f['quantity'] for f in lab.snapshot()['fills'] if f['side']=='buy') == 1


@pytest.mark.parametrize('mode', ['closed', 'below', 'stale', 'outage', 'gap', 'halt'])
def test_rejected_entries_have_no_fills(lab, mode):
    c = Creator(lab); c.setup()
    if mode == 'halt': c.send('halt')
    c.quote(underlying='106' if mode=='gap' else '101', available=mode!='outage', market_open=mode!='closed')
    if mode == 'stale': c.t += 10
    c.candle('99' if mode=='below' else '101'); c.candle('99' if mode=='below' else '101')
    c.t += 1; c.send('tick')
    assert not lab.snapshot()['fills']


def test_nonconsecutive_candles_reset_confirmation(lab):
    c=Creator(lab); c.setup(); c.quote(); c.candle(); c.candle('99'); c.candle()
    assert c.p()['state']=='armed'
    c.candle(); assert c.p()['state']=='entry_pending'


def test_no_exit_race_and_versioned_update(lab):
    c=Creator(lab); c.enter()
    c.send('update', plan=c.plan, version=2, stop='96', targets=[dict(price='108', fraction='1')])
    c.close(1, version=3)
    with pytest.raises(ValueError): c.close(1, version=4)
    with pytest.raises(ValueError): c.send('update', plan=c.plan, version=4, stop='97', targets=[dict(price='109', fraction='1')])
    c.quote(); assert c.p()['quantity']==1
    with pytest.raises(ValueError): c.close(1, version=3)


def test_expiry_closes_positions_without_forcing_market(lab):
    c=Creator(lab); c.enter()
    c.t=BASE+3601
    c.quote(market_open=False)
    assert c.p()['quantity']==2
    c.quote()
    assert c.p()['pending']['reason']=='expiry'
    c.quote()
    assert c.p()['state']=='closed'


def test_halt_preserves_exit_and_daily_loss(lab):
    c=Creator(lab); c.enter(); c.send('halt'); c.close(2); c.quote()
    assert c.p()['state']=='closed' and lab.snapshot()['halted']
    other=Creator(lab, plan='another'); other.t=c.t; other.enter()
    assert other.p()['quantity']==0


def test_daily_loss_latches_entry_halt(tmp_path):
    lab=PaperLab(tmp_path/'loss.sqlite', daily_loss='1')
    c=Creator(lab); c.enter(); c.quote(underlying='94',bid='94',ask='94.10'); c.quote(underlying='94',bid='94',ask='94.10')
    assert lab.snapshot()['halted']
    assert lab.snapshot()['daily_realized'] < -100


def test_budget_reserved_across_setups(tmp_path):
    lab=PaperLab(tmp_path/'limits.sqlite',gross_limit='250')
    a=Creator(lab); a.setup(); a.quote(); a.candle(); a.candle()
    b=Creator(lab,plan='second'); b.t=a.t; b.setup(); b.candle(); b.candle()
    assert b.p()['quantity']==0 and b.p()['pending'] is None
    a.t=b.t+1; a.send('tick')
    assert sum(p['cost'] for p in lab.snapshot()['plans'].values())<=25000


def test_no_gap_chase_and_no_worse_fill(lab):
    c=Creator(lab); c.setup(); c.quote(); c.candle(); c.candle()
    c.quote(underlying='102',ask='102',bid='101.9')
    assert c.p()['state']=='canceled' and not lab.snapshot()['fills']


def test_concurrent_duplicate_delivery_one_setup(lab):
    c=Creator(lab)
    event=c.event('setup',plan=c.plan,version=1,symbol='AAPL',asset='equity',expires_at=stamp(BASE+3600),trigger='100',hold_bars=2,bar_seconds=1,stop='95',targets=[dict(price='105',fraction='1')])
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _: PaperLab(lab.path).apply(event,now=BASE),range(16)))
    assert sum(not r.get('duplicate',False) for r in results)==1
    changed=dict(event,trigger='101')
    with pytest.raises(ValueError): lab.apply(changed,now=BASE)


@pytest.mark.parametrize('changes', [dict(asset='put'),dict(symbol='NVDA'),dict(symbol='NVDA261013C00235000'),dict(hold_bars=True),dict(version=True),dict(stop='101'),dict(targets=[dict(price='110',fraction='.5')]),dict(expires_at=stamp(BASE-1))])
def test_incomplete_or_invalid_calls_quarantined(lab,changes):
    c=Creator(lab,'NVDA261016C00235000','call')
    with pytest.raises(ValueError): c.setup(**changes)
    assert not lab.snapshot()['plans']


@pytest.mark.parametrize('changes', [dict(ask='NaN'),dict(bid='Infinity'),dict(liquidity=-1),dict(liquidity=True),dict(bid='102',ask='100')])
def test_bad_quote_cannot_mutate_account(lab,changes):
    c=Creator(lab); c.setup()
    with pytest.raises(ValueError): c.quote(**changes)
    assert not lab.snapshot()['quotes']


def test_restart_cannot_reset_budget_or_cash(lab):
    with pytest.raises(ValueError): PaperLab(lab.path, cash='999999')
    assert lab.snapshot()['cash']==1000000


def test_trailing_stop_update_can_lock_profit_and_exit_immediately(lab):
    c=Creator(lab);c.enter()
    c.send('update',plan=c.plan,version=2,stop='102',targets=[dict(price='108',fraction='1')])
    assert c.p()['pending']['reason']=='stop'
    c.quote()
    assert c.p()['state']=='closed'


def test_original_event_versions_and_observations_retained(lab):
    c=Creator(lab);c.enter()
    c.send('update',plan=c.plan,version=2,stop='96',targets=[dict(price='108',fraction='1')])
    with lab.connect() as db:
        events=[json.loads(row[0]) for row in db.execute('SELECT event FROM receipts ORDER BY observed_at')]
    setup=[e for e in events if e['kind']=='setup'][0]
    update=[e for e in events if e['kind']=='update'][0]
    assert setup['stop']=='95' and setup['version']==1
    assert update['stop']=='96' and update['version']==2


def test_same_candle_different_receipt_cannot_confirm_twice(lab):
    c=Creator(lab);c.setup();c.quote();c.candle()
    event=c.event('candle',plan=c.plan,closed_at=stamp(c.t),close='101',market_open=True)
    with pytest.raises(ValueError): lab.apply(event,now=c.t)
    assert c.p()['confirmations']==1


def test_missing_candle_resets_hold_and_short_interval_is_rejected(lab):
    c=Creator(lab);c.setup();c.quote();c.candle()
    c.t+=.5
    event=c.event('candle',plan=c.plan,closed_at=stamp(c.t),close='101',market_open=True)
    with pytest.raises(ValueError):lab.apply(event,now=c.t)
    c.t+=1.5
    c.send('candle',plan=c.plan,closed_at=stamp(c.t),close='101',market_open=True)
    assert c.p()['state']=='armed' and c.p()['confirmations']==1


def test_journal_failure_rolls_back_fill_and_receipt(lab):
    import sqlite3
    c=Creator(lab);c.setup();c.quote();c.candle();c.candle()
    before=lab.snapshot()
    with lab.connect() as db:
        db.execute("CREATE TRIGGER reject_state BEFORE UPDATE ON state BEGIN SELECT RAISE(FAIL, 'fixture disk fault'); END")
    c.t+=1
    event=c.event('tick')
    with pytest.raises(sqlite3.Error):lab.apply(event,now=c.t)
    assert lab.snapshot()==before
    with lab.connect() as db:db.execute('DROP TRIGGER reject_state')
    assert not lab.apply(event,now=c.t).get('duplicate',False)
    assert len(lab.snapshot()['fills'])==1


@pytest.mark.parametrize('offset',[121,-1])
def test_stale_and_future_creator_event_cannot_arm(lab,offset):
    c=Creator(lab)
    event=c.event('halt');event['published_at']=stamp(BASE-offset)
    with pytest.raises(ValueError): lab.apply(event,now=BASE)
    assert not lab.snapshot()['halted']


def test_signed_http_positive_and_negative(lab, monkeypatch):
    secret='f'*64
    server=HTTPServer(('127.0.0.1',0),handler(lab,secret))
    def connect(sock, address):
        assert address == ('127.0.0.1', server.server_port)
        return REAL_CONNECT(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', connect)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    now=time.time()
    c=Creator(lab); c.t=now
    e=c.event('setup',plan='http',version=1,symbol='AAPL',asset='equity',expires_at=stamp(now+3600),trigger='100',hold_bars=2,bar_seconds=1,stop='95',targets=[dict(price='105',fraction='1')])
    body=canonical(e).encode(); ts=str(int(now))
    signature=hmac.new(secret.encode(),ts.encode()+b'\n'+body,hashlib.sha256).hexdigest()
    try:
        for sig,expected in [('0'*64,401),(signature,200),(signature,200)]:
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            conn.request('POST','/v1/events',body,headers={'X-Timestamp':ts,'X-Signature':sig})
            response=conn.getresponse(); result=json.loads(response.read()); conn.close()
            assert response.status==expected
            if expected==200: assert result['broker_orders']==0
        assert result['duplicate']
    finally:
        server.shutdown(); thread.join(3); server.server_close()
