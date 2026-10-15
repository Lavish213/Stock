"""Offline acceptance probes for the supplied options blueprint, not a strategy.

Known blueprint gaps are reported as gaps, never disguised as passing tests.
"""
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import argparse
import json
from pathlib import Path
from unittest.mock import patch
from lavish_core.validation.options_creator_month import offline,parse


def run():
    from lavish_core.trade import candle_gatekeeper as gate,options_exit_monitor as exits
    def confirmation(volume):
        base=datetime.now(timezone.utc).replace(second=0,microsecond=0)-timedelta(minutes=5)
        bars=[dict(t=(base+timedelta(minutes=i)).isoformat(),o=100,h=101,l=99,c=100,v=1000) for i in range(3)]
        breakout=dict(t=(base+timedelta(minutes=3)).isoformat(),o=100,h=103,l=99,c=102,v=volume)
        class Clock:
            def __init__(self):self.value=0
            def monotonic(self):self.value+=1;return self.value
            def sleep(self,*a):pass
        responses=iter([bars,bars+[breakout]])
        with patch.object(gate,'time',Clock()),patch.object(gate,'get_bars',lambda *a,**k:next(responses,bars+[breakout])):
            return gate.confirm_entry('SPY','bullish',max_wait_seconds=10)
    with offline():
        baseline=parse('BTO SPY 780 CALL exp 2026-10-16 target 785 stop 765')
        multi=parse('BTO SPY 780 CALL exp 2026-10-16 target 785 stop 765. Current premium 0.27. Target 1: 785. Target 2: 790. Stretch target: 795.')
        recap=parse('SPY is at the second target')
        strong=confirmation(2000);weak=confirmation(1)
    probes=[dict(name='single_target_option_entry',passed=bool(baseline and baseline.get('ticker')=='SPY' and baseline.get('side')=='CALL')),
            dict(name='recap_is_not_entry',passed=recap is None),
            dict(name='strong_volume_positive_entry',passed=strong['confirmed']),
            dict(name='weak_volume_must_not_confirm_under_blueprint',passed=not weak['confirmed'],actual=weak),
            dict(name='multiple_targets_preserved_under_blueprint',passed=bool(multi and len(multi.get('targets',[]))==3),actual=multi)]
    fields=['ticker','side','strike','expiry','asset_class','underlying_spot','dte','premium','breakeven','breakeven_move_pct','entry_trigger','targets','stop_hint']
    return dict(synthetic=True,real_orders=0,profitability_evidence=False,
        scope='Five deliberately selected acceptance probes; not a statistical field error rate',
        probes=probes,passed=sum(p['passed'] for p in probes),gaps=sum(not p['passed'] for p in probes),
        baseline_parser_signal=baseline,baseline_schema_presence={key:key in (baseline or {}) for key in fields},
        default_premium_stop_pct=exits.OUR_STOP_LOSS_PCT,blueprint_premium_stop_pct=.30,
        integer_scaling=[dict(contracts=q,exact_80_20_possible=q%5==0,take_profit_quantity=str(Decimal(q)*Decimal('.8')),runner_quantity=str(Decimal(q)*Decimal('.2'))) for q in [1,2,3,4,5,10]],
        automatic_learning='Not verified or activated; losses do not authorize model promotion',
        production_changes=False)


if __name__=='__main__':
    cli=argparse.ArgumentParser();cli.add_argument('--output',required=True);args=cli.parse_args()
    result=run();destination=Path(args.output)
    with destination.open('x') as handle:json.dump(result,handle,indent=2)
    print(json.dumps(dict(passed=result['passed'],gaps=result['gaps'],real_orders=0)))
