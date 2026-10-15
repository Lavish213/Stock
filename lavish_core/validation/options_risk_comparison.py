"""Paired synthetic production-path allocation stress; never alpha evidence."""
import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from .production_alert_replay import replay


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
    return dict(fraction=report['simulation_fixed_fraction'],profile=report['stress_profile'],
        reconciliation_passed=not (cash_errors or inventory_errors),
        final_cash=round(report['final_cash'],2),net=round(report['final_cash']-initial,2),
        monthly=months,months_at_178_60=sum(x['net']>=178.60 for x in months.values()),
        losing_months=sum(x['net']<0 for x in months.values()),
        closed_session_cash_drawdown_pct=round(drawdown*100,4),
        entries=sum(x['entry_orders'] for x in report['sessions']),
        halt_sessions=sum(bool(x['halt'][0]) if isinstance(x['halt'],(list,tuple)) else bool(x['halt']) for x in report['sessions']),
        cash_errors=cash_errors,
        inventory_errors=inventory_errors,
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
    with ProcessPoolExecutor(max_workers=4) as pool: rows=list(pool.map(worker,jobs))
    result=dict(synthetic=True,real_orders=0,days=days,initial=1000,
        profitability_evidence=False,outcomes=rows,
        limits=['Invented 0.10 entry premiums and predetermined event mix; no calibrated market distribution',
                'Daily quote draws paired independently of entries and account outcomes',
                'Existing production halts and risk gates preserved; no automatic reset',
                'Drawdown measured at flat session boundaries, not intraday',
                'No creator close/trim orchestration or conditional promotion',
                'Twelve calendar months on one synthetic year; no probability estimates or strategy selection'],
        production_allocation_changed=False)
    (root/'summary.json').write_text(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    cli=argparse.ArgumentParser();cli.add_argument('--output',required=True)
    cli.add_argument('--days',type=int,default=365);args=cli.parse_args()
    result=experiment(args.output,args.days)
    print(json.dumps([{k:v for k,v in r.items() if k!='monthly'} for r in result['outcomes']],indent=2))
