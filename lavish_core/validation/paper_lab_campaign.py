"""Fake creator publishes signed HTTP setups, amendments and closes to PaperLab.

This is a wall-clock transport test with invented quotes and contracts. It
cannot send real broker orders and never writes an operational approval.
"""
import argparse
import hashlib
import hmac
import http.client
import json
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer
from pathlib import Path

from lavish_core.validation.durable_paper_lab import PaperLab, canonical, handler


def iso(value=None):
    return datetime.fromtimestamp(time.time() if value is None else value, timezone.utc).isoformat()


def publish(port, secret, event):
    """Destination is deliberately restricted to loopback."""
    body=canonical(event).encode(); stamp=str(int(time.time()))
    signature=hmac.new(secret.encode(),stamp.encode()+b'\n'+body,hashlib.sha256).hexdigest()
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
    start=time.monotonic()
    try:
        conn.request('POST','/v1/events',body,headers={'X-Timestamp':stamp,'X-Signature':signature})
        response=conn.getresponse(); result=json.loads(response.read())
        return response.status,result,(time.monotonic()-start)*1000
    finally:
        conn.close()


def campaign(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    lab=PaperLab(directory/'campaign.sqlite', delay='.05')
    if lab.snapshot()['plans']:
        raise ValueError('Use a new campaign directory; never reset an existing account')
    secret=secrets.token_hex(32)
    server=HTTPServer(('127.0.0.1',0),handler(lab,secret))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    transcript=[]; cases=[]; serial=0
    def send(kind,**fields):
        nonlocal serial
        serial+=1
        event=dict(id=f'fake-creator-{serial}',kind=kind,published_at=iso(),**fields)
        code,receipt,latency=publish(server.server_port,secret,event)
        duplicate_code,duplicate,_=publish(server.server_port,secret,event)
        transcript.append(dict(event=event,http_status=code,receipt=receipt,dispatch_ms=latency,
            duplicate_http_status=duplicate_code,duplicate=duplicate.get('duplicate',False)))
        if code!=200 or duplicate_code!=200 or not duplicate.get('duplicate'):
            raise RuntimeError('Synthetic campaign receipt failed')
        return receipt
    try:
        for asset in ('equity','call'):
            for scenario in ('targets','creator_stop'):
                plan=f'{asset}-{scenario}'
                expiry=datetime.now(timezone.utc)+timedelta(days=7)
                symbol='AAPL' if asset=='equity' else 'NVDA'+expiry.strftime('%y%m%d')+'C00105000'
                send('setup',plan=plan,version=1,symbol=symbol,asset=asset,expires_at=iso(time.time()+3600),
                    trigger='100',hold_bars=2,bar_seconds=1,stop='95',targets=[dict(price='105',fraction='.5'),dict(price='110',fraction='.5')])
                def quote(underlying='101',bid=None,ask=None,liquidity=100,available=True):
                    return send('quote',symbol=symbol,bid=bid or ('1' if asset=='call' else '100'),
                        ask=ask or ('1.02' if asset=='call' else '100.02'),underlying=underlying,
                        liquidity=liquidity,available=available,market_open=True)
                quote(liquidity=1)
                for _ in range(2):
                    time.sleep(1.01)
                    send('candle',plan=plan,closed_at=iso(),close='101',market_open=True)
                time.sleep(.06);send('tick')
                # Entry partially fills, then the actual controller object is
                # replaced; SQLite receipts and pending intent survive restart.
                lab=PaperLab(lab.path,delay='.05')
                server.RequestHandlerClass=handler(lab,secret)
                quote(liquidity=1)
                if scenario=='targets':
                    for underlying,bid,ask in [('105','1.20' if asset=='call' else '104.90','1.22' if asset=='call' else '105.02'),
                                               ('110','1.40' if asset=='call' else '109.90','1.42' if asset=='call' else '110.02')]:
                        quote(underlying,bid,ask)
                        time.sleep(.06);send('tick')
                else:
                    send('update',plan=plan,version=2,stop='96',targets=[dict(price='108',fraction='1')])
                    send('close',plan=plan,version=3,quantity=1)
                    quote(available=False)
                    time.sleep(.06);quote(liquidity=1)
                    quote('94','0.80' if asset=='call' else '93.90','0.82' if asset=='call' else '94.02')
                    time.sleep(.06);send('tick')
                state=lab.snapshot();p=state['plans'][plan]
                fills=[f for f in state['fills'] if f['plan']==plan]
                passed=(p['state']=='closed' and p['quantity']==0 and p['pending'] is None
                    and sum(f['quantity'] for f in fills if f['side']=='buy')==2
                    and sum(f['quantity'] for f in fills if f['side']=='sell')==2
                    and all(f['symbol']==symbol and f['at']>=f['intent_at']+.05 for f in fills))
                cases.append(dict(plan=plan,passed=passed,symbol=symbol,fills=fills))
        rejection_cases=[]
        for fault in ('missing_contract','future','stale','missing_stop','oversell','out_of_order_version'):
            serial+=1
            event=dict(id=f'negative-{serial}',kind='setup',published_at=iso(),plan=f'negative-{fault}',
                version=1,symbol='NVDA',asset='call',expires_at=iso(time.time()+3600),trigger='100',
                hold_bars=2,bar_seconds=1,stop='95',targets=[dict(price='105',fraction='1')])
            if fault!='missing_contract':
                event.update(symbol='AAPL',asset='equity')
            if fault=='future':event['published_at']=iso(time.time()+10)
            elif fault=='stale':event['published_at']=iso(time.time()-121)
            elif fault=='missing_stop':del event['stop']
            elif fault in ('oversell','out_of_order_version'):
                event=dict(id=event['id'],kind='close',published_at=iso(),plan='equity-creator_stop',
                           version=4 if fault=='oversell' else 2,quantity=999 if fault=='oversell' else 1)
            code,receipt,latency=publish(server.server_port,secret,event)
            rejection_cases.append(dict(fault=fault,passed=code==400,http_status=code))
            transcript.append(dict(event=event,http_status=code,receipt=receipt,dispatch_ms=latency))
        state=lab.snapshot()
        latencies=[t['dispatch_ms'] for t in transcript]
        result=dict(synthetic=True,profitability_evidence=False,broker_orders=0,actual_creator_samples=0,
            cases=cases,rejection_cases=rejection_cases,
            passed=all(c['passed'] for c in cases+rejection_cases),events=len(transcript),
            duplicate_deliveries=sum(t.get('duplicate',False) for t in transcript),dispatch_max_ms=max(latencies),
            dispatch_median_ms=sorted(latencies)[len(latencies)//2],
            initial_cash_cents=state['config']['initial_cash'],final_cash_cents=state['cash'],
            realized_pnl_cents=state['realized'],fees_cents=sum(f['fee_cents'] for f in state['fills']),
            cash_reconciliation_error_cents=state['cash']-state['config']['initial_cash']-state['realized'],
            final_positions=sum(p['quantity'] for p in state['plans'].values()),
            scope='Signed HTTP -> typed synthetic controller -> simulated fills; production text parser remains a separate benchmark')
        result['passed']=result['passed'] and result['cash_reconciliation_error_cents']==0 and result['final_positions']==0
        (directory/'transcript.json').write_text(json.dumps(transcript,indent=2))
        (directory/'results.json').write_text(json.dumps(result,indent=2))
        return result
    finally:
        server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    result=campaign(parser.parse_args().output)
    print(json.dumps({k:v for k,v in result.items() if k!='cases'},indent=2))
    raise SystemExit(0 if result['passed'] else 1)
