"""Compute results from normalized closed-trade exports; does not approve live trading."""
import argparse,csv,json
from datetime import datetime
from pathlib import Path
from lavish_core.utils.safety import finite_number

def evaluate(path):
    pnls=[];dates=[];equity=0.;peak=0.;drawdown=0.;identities=set()
    with Path(path).open(newline='') as handle:
        for row in csv.DictReader(handle):
            identity=row['trade_id']
            if not identity or identity in identities:raise ValueError('Duplicate trade identity')
            identities.add(identity)
            entry=datetime.fromisoformat(row['entry_at'].replace('Z','+00:00'));exit=datetime.fromisoformat(row['exit_at'].replace('Z','+00:00'))
            if entry.tzinfo is None or exit.tzinfo is None or exit<entry:raise ValueError('Invalid execution dates')
            qty=finite_number(row['qty'],positive=True);multiplier=finite_number(row['multiplier'],positive=True)
            if multiplier not in (1,100) or (multiplier==100 and not qty.is_integer()):raise ValueError('Unsupported contract multiplier or quantity')
            pnl=(finite_number(row['exit_price'],nonnegative=True)-finite_number(row['entry_price'],positive=True))*qty*multiplier-finite_number(row['fees'],nonnegative=True)
            pnls.append(pnl);dates.append(exit)
    if not pnls:raise ValueError('No executions')
    for _,pnl in sorted(zip(dates,pnls)):
        equity+=pnl;peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
    wins=sum(p for p in pnls if p>0);loss=-sum(p for p in pnls if p<0)
    return dict(source='normalized-export',provenance_verified=False,passed=False,live_ready=False,trades=len(pnls),net_pnl=sum(pnls),win_rate=sum(p>0 for p in pnls)/len(pnls),profit_factor=wins/loss if loss else None,max_realized_drawdown_usd=drawdown,scope='closed trades only; excludes unrealized exposure and export completeness')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('csv',type=Path);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=evaluate(args.csv);args.output.write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()
