"""Fake signed creator publisher -> real loopback HTTP -> offline trading bot.

Run in a separate CLI process. Only the local HTTP server uses sockets; broker
and notification HTTP are blocked by alert_following's isolated test context.
Never uses actual creator credentials, broker keys or a production journal.
"""
import argparse
import http.client
import json
import math
import statistics
import threading
import time
from datetime import datetime,timezone
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch
from lavish_core.validation import alert_following as following

def publish(inbox,text,identity,clock,*,age=0,created_epoch=None):
    from lavish_core.feeds import alert_feed as feed
    secret='f'*64  # Fixture secret, local simulator only.
    created=clock.time()-age if created_epoch is None else created_epoch
    body=json.dumps(dict(id=identity,text=text,created_at=datetime.fromtimestamp(created,timezone.utc).isoformat())).encode()
    stamp=str(int(clock.time()))
    server=HTTPServer(('127.0.0.1',0),feed.handler_for(inbox,secret,clock=clock.time))
    server.timeout=3
    worker=threading.Thread(target=server.handle_request,daemon=True)
    worker.start()
    connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
    try:
        connection.request('POST','/v1/alerts',body=body,headers={
            'Content-Type':'application/json','X-Alert-Timestamp':stamp,
            'X-Alert-Signature':feed.sign(secret,stamp,body)})
        response=connection.getresponse()
        result=(response.status,json.loads(response.read()))
    finally:
        connection.close();worker.join(4);server.server_close()
    if worker.is_alive():raise AssertionError('Fake publisher HTTP worker failed to stop')
    return result

def grade(rows,latency_budget_ms):
    if not math.isfinite(latency_budget_ms) or latency_budget_ms<=0:
        raise ValueError('Latency budget must be finite and positive')
    successes=[r for r in rows if r.get('passed')]
    errors=len(rows)-len(successes)
    accepted=[r for r in successes if r['entry_expected']]
    latencies=[r['dispatch_wall_ms'] for r in accepted]
    late=sum(r['dispatch_wall_ms']>latency_budget_ms for r in accepted)
    exit_delays=[]
    for row in accepted:
        # All model triggers occur two polls after entry monitoring begins.
        events=row['events']
        exit_delays.append(max(0,events[-1]['elapsed']-row['first_eligible_exit_at']))
    late_exits=sum(delay>.5 for delay in exit_delays)
    complete=errors==0 and bool(accepted) and any(not r['entry_expected'] for r in successes)
    score='A' if complete and not late and not late_exits else 'B' if complete else 'F'
    return dict(benchmark_grade=score,operational_grade='not established',cases=len(rows),
        failed_cases=errors,error_pct=round(100*errors/len(rows),4) if rows else None,
        accepted_round_trips=len(accepted),blocked_cases=len(successes)-len(accepted),
        latency_budget_ms=latency_budget_ms,late_dispatches=late,late_exits=late_exits,
        median_dispatch_ms=statistics.median(latencies) if latencies else None,
        max_dispatch_ms=max(latencies) if latencies else None,
        max_exit_delay_seconds=max(exit_delays) if exit_delays else None,
        synthetic_gross_pnl=round(sum(r['final_cash']-100000 for r in accepted),2),
        fee_model='zero fees; zero spread; immediate fills; accelerated prices',
        profits_are_grade_criterion=False,profitability_evidence=False,real_broker_orders=0)

def run(directory,latency_budget_ms=3000):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    cases=[]
    for instrument,ticker in [('equity','AAPL'),('call','NVDA')]:
        for budget in (99,100,250,500):
            for trigger in ('target','stop'):
                cases.append(dict(instrument=instrument,ticker=ticker,budget=budget,trigger=trigger,gate='confirmed'))
        for gate in ('invalidated','timeout'):
            cases.append(dict(instrument=instrument,ticker=ticker,budget=250,gate=gate))
        for age in (-5,121):cases.append(dict(instrument=instrument,ticker=ticker,budget=250,age=age))
        cases.append(dict(instrument=instrument,ticker=ticker,budget=250,market_open=False))
    rows=[]
    with patch.object(following,'send',publish):
        for index,case in enumerate(cases):
            try:row=following.run_case(directory/f'case-{index}.duckdb',real_time=True,**case)
            except Exception as error:
                row=dict(passed=False,scenario=case,error_type=type(error).__name__,error=str(error))
            rows.append(row)
            print(json.dumps(dict(case=index,passed=row['passed'],instrument=case['instrument'])),flush=True)
            # Checkpoint every case; interruptions never erase completed work.
            (directory/'results.json').write_text(json.dumps(dict(summary=grade(rows,latency_budget_ms),results=rows),indent=2))
    return grade(rows,latency_budget_ms)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',required=True)
    parser.add_argument('--latency-budget-ms',type=float,default=3000)
    args=parser.parse_args()
    if not math.isfinite(args.latency_budget_ms) or args.latency_budget_ms<=0:
        parser.error('Latency budget must be finite and positive')
    print(json.dumps(run(args.directory,args.latency_budget_ms),indent=2))
