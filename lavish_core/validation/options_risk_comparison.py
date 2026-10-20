"""Paired synthetic production-path allocation stress; never alpha evidence."""
import argparse
import json
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path
from .production_alert_replay import REPLAY_START_DATE, replay


def _write_json_atomic(path, payload):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, indent=2))
    temporary.replace(path)


def audit_fill_conservation(report, tolerance=1e-7):
    """Independently derive cash and inventory from raw synthetic fills."""
    cash = float(report['initial'])
    inventory = {}
    errors = []
    fills = [fill for session in report.get('sessions', ()) for fill in session.get('fills', ())]
    for index, fill in enumerate(fills):
        try:
            side = str(fill['side']).lower()
            symbol = str(fill['symbol'])
            qty = float(fill['qty'])
            price = float(fill['price'])
            fee = float(fill.get('fee', 0))
            if side not in ('buy', 'sell') or not symbol or not all(map(math.isfinite, (qty, price, fee))):
                raise ValueError
            if qty <= 0 or price < 0 or fee < 0:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            errors.append(f'invalid_fill_{index}')
            continue
        direction = 1 if side == 'sell' else -1
        cash += direction * qty * price * 100 - fee
        inventory[symbol] = inventory.get(symbol, 0.0) - direction * qty
    cash_error = cash - float(report['final_cash'])
    nonzero_inventory = {symbol: qty for symbol, qty in inventory.items() if abs(qty) > tolerance}
    passed = not errors and abs(cash_error) <= tolerance and not nonzero_inventory
    return dict(passed=passed, fills=len(fills), cash_error=round(cash_error, 8),
                nonzero_inventory=nonzero_inventory, errors=errors)


def audit_session_coverage(report):
    days = report.get('days')
    sessions = report.get('sessions', ())
    if type(days) is not int or days < 1:
        return dict(passed=False, expected=days, observed=len(sessions), errors=['invalid_days'])
    expected = [(REPLAY_START_DATE + timedelta(days=i)).isoformat() for i in range(days)]
    observed = [session.get('date') for session in sessions]
    errors = []
    if len(observed) != days: errors.append('wrong_session_count')
    if len(set(observed)) != len(observed): errors.append('duplicate_dates')
    if observed != sorted(observed): errors.append('out_of_order_dates')
    if observed != expected: errors.append('date_coverage_mismatch')
    return dict(passed=not errors, expected=days, observed=len(observed), errors=errors)


def summarize(report):
    initial=report['initial']; previous=initial; peak=initial; drawdown=0
    months={}
    for session in report['sessions']:
        value=session['broker_cash']; month=session['date'][:7]
        if month not in months: months[month]={'start':previous}
        months[month]['end']=value
        peak=max(peak,value); drawdown=max(drawdown,1-value/peak)
        previous=value
    for row in months.values():
        row['net']=round(row['end']-row['start'],2)
        row['return_pct']=round(100*(row['end']/row['start']-1),4) if row['start'] else None
    cash_errors=sum(x['cash_error']!=0 for x in report['sessions'])
    inventory_errors=sum(not x['position_match'] for x in report['sessions'])
    coverage=audit_session_coverage(report)
    conservation=audit_fill_conservation(report)
    return dict(fraction=report['simulation_fixed_fraction'],profile=report['stress_profile'],
        reconciliation_passed=not (cash_errors or inventory_errors) and coverage['passed'] and conservation['passed'],
        final_cash=round(report['final_cash'],2),net=round(report['final_cash']-initial,2),
        monthly=months,months_at_178_60=sum(x['net']>=178.60 for x in months.values()),
        losing_months=sum(x['net']<0 for x in months.values()),
        closed_session_cash_drawdown_pct=round(drawdown*100,4),
        entries=sum(x['entry_orders'] for x in report['sessions']),
        halt_sessions=sum(bool(x['halt'][0]) if isinstance(x['halt'],(list,tuple)) else bool(x['halt']) for x in report['sessions']),
        cash_errors=cash_errors,
        inventory_errors=inventory_errors,
        coverage=coverage,fill_conservation=conservation,
        below_half_start=report['final_cash']<initial/2)


def worker(args):
    output,fraction,profile,days=args
    return summarize(replay(output,days=days,initial=1000,seed=20261015,
                            fixed_fraction=fraction,stress_profile=profile))


def experiment(output,days=365):
    root=Path(output);root.mkdir(parents=True,exist_ok=False)
    jobs=[(root/f'{int(f*100)}-{profile}',f,profile,days)
          for f in (.1,.25,.5,1.)
          for profile in ('baseline','weak_followthrough','gap_loss')]
    manifest=dict(synthetic=True,real_orders=0,days=days,initial=1000,
                  status='pending',outcomes=[dict(fraction=f,profile=p,status='pending') for _,f,p,_ in jobs])
    _write_json_atomic(root/'summary.json', manifest)
    rows=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(worker,job):job for job in jobs}
        for future in as_completed(futures):
            _,fraction,profile,_=futures[future]
            try:
                row=future.result();row['status']='complete'
            except Exception as error:
                row=dict(fraction=fraction,profile=profile,status='error',
                         reconciliation_passed=False,error_type=type(error).__name__)
            rows.append(row)
    rows.sort(key=lambda row:(row['fraction'],row['profile']))
    all_passed=all(row.get('status')=='complete' and row.get('reconciliation_passed') is True for row in rows)
    result=dict(synthetic=True,real_orders=0,days=days,initial=1000,
        status='complete',all_passed=all_passed,
        profitability_evidence=False,outcomes=rows,
        limits=['Invented 0.10 entry premiums and predetermined event mix; no calibrated market distribution',
                'Daily quote draws paired independently of entries and account outcomes',
                'Existing production halts and risk gates preserved; no automatic reset',
                'Drawdown measured at flat session boundaries, not intraday',
                'No creator close/trim orchestration or conditional promotion',
                'Twelve calendar months on one synthetic year; no probability estimates or strategy selection'],
        production_allocation_changed=False)
    _write_json_atomic(root/'summary.json', result)
    return result


if __name__=='__main__':
    cli=argparse.ArgumentParser();cli.add_argument('--output',required=True)
    cli.add_argument('--days',type=int,default=365);args=cli.parse_args()
    result=experiment(args.output,args.days)
    print(json.dumps([{k:v for k,v in r.items() if k!='monthly'} for r in result['outcomes']],indent=2))
    raise SystemExit(0 if result['all_passed'] else 1)
