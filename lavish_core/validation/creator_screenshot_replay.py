"""Screenshot-derived offline study. Never a historical execution backtest.

Visible claims are transcription inputs, not independently verified prices.
Conditional paths are a reference model: the production router must quarantine
these setups until it has a durable, specified conditional instruction system.
"""
import argparse
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from lavish_core.trading import alert_handler as ah
from lavish_core.validation.options_replay import replay

CARDS = [
    ('membership','Weekly Newsletter $20 / month. Stocks to watch. Catalysts. Trade setups.', 'advertisement'),
    ('nvda','NVDA CALL SETUP Contract: NVDA $235 Call October 2. Entry trigger: Break $230 and hold above it. Targets T1 $233 T2 $236 T3 $240 Stretch target $242 to $245. Invalidation: Loss of $226.50 after entry. No break above $230 means no trade.', 'conditional'),
    ('spy_recap_1','Nice #SPY', 'recap'),
    ('spy_recap_2','Winner winner #SPY', 'recap'),
    ('mrvl','SETUP 2 - MRVL CALL Expiration: October 16, 2026. Entry trigger: Reclaim and hold $280. Targets: Target 1 $290 Target 2 $300 Target 3 $315 Stretch target $330.', 'incomplete_conditional'),
    ('mrvl_exit','Wow! We hit our stretch target on #MRVL. Take profits and call it a week.', 'ambiguous_exit'),
    ('spy_premium','0.27 a contract to $1.07 a contract on my spy trade for the regular groups on Patreon. I told you we were going to move up this week.', 'recap'),
    ('sndk_recap','Your #SNDK trade is up 122%. Posted in the highest tiers.', 'recap'),
    ('sndk_premium','Wow! Our #SNDK trade is up over 353%. Contracts went from $2.36 to $10.74 so far.', 'recap'),
    ('googl','GOOGL $350C, October 9. Break and hold above $346.', 'conditional_incomplete_exit'),
]

def conditional_path(prices, trigger, target, stop, hold_bars=2):
    """Illustrative close-based confirmation; no option-price inference.

    Two closes is an explicit experiment assumption, not the creator's rule.
    A gap exits at the observed price, never at an unavailable stop price.
    """
    held=0; entry=None
    for index,price in enumerate(prices):
        if entry is None:
            held=held+1 if price>trigger else 0
            if held>=hold_bars: entry=(index,price)
            continue
        if price<=stop or price>=target:
            return dict(status='closed',entry_index=entry[0],entry=entry[1],
                        exit_index=index,exit=price,reason='stop' if price<=stop else 'target')
    return dict(status='waiting' if entry is None else 'open',entry=entry[1] if entry else None)

def run(directory):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=False)
    routing=[]
    with patch('requests.sessions.Session.request',side_effect=AssertionError('External I/O forbidden')), patch.object(ah,'post_discord'):
        for name,text,category in CARDS:
            with patch.object(ah,'execute_trade_from_post') as execute:
                ah.handle_alert_text(text)
                assert not execute.called, (name,execute.call_args)
                routing.append(dict(card=name,category=category,dispatches=0))
        adapter_replays=[]
        for ticker,entry,end in [('SPY',.27,1.07),('SNDK',2.36,10.74)]:
            # Neither image provides full contract identity or executable quotes.
            # The existing adapter replay deliberately uses its own synthetic
            # contract/expiry and quotes; no claim of reproducing her order.
            qty=int(Decimal('250')/(Decimal(str(entry))*100+Decimal('.65')))
            result=replay(directory/(ticker+'.duckdb'),ticker=ticker,qty=qty,entry=entry,exit_price=end)
            expected=Decimal(qty)*((Decimal(str(end))-Decimal(str(entry)))*100-Decimal('1.30'))
            assert abs(Decimal(str(result['net_pnl']))-expected)<Decimal('.011')
            adapter_replays.append(dict(ticker=ticker,qty=qty,simulated_budget=250,
                screenshot_claim_entry=entry,screenshot_claim_end=end,
                assumed_fee_per_side=.65,synthetic_contract=True,**result))
    paths=[]
    for ticker,trigger,targets,stop in [('NVDA',230,[233,236,240,242,245],226.5),('MRVL',280,[290,300,315,330],270)]:
        for target in targets:
            cases=[('no_break',[trigger-1]*5,'waiting'),
                   ('false_break',[trigger-1,trigger+.1,trigger-1,trigger-.5],'waiting'),
                   ('target',[trigger-1,trigger+.1,trigger+.2,target],'closed'),
                   ('gap_stop',[trigger-1,trigger+.1,trigger+.2,stop-2],'closed')]
            for label,prices,status in cases:
                result=conditional_path(prices,trigger,target,stop)
                assert result['status']==status
                if label=='target': assert result['reason']=='target' and result['exit']==target
                if label=='gap_stop': assert result['reason']=='stop' and result['exit']==stop-2
                paths.append(dict(ticker=ticker,target=target,scenario=label,
                    stop=stop,stop_is_assumed=ticker=='MRVL',**result))
    report=dict(passed=True,real_broker_orders=0,historical_backtest=False,
        screenshot_cards=routing,conditional_reference_paths=paths,
        premium_adapter_replays=adapter_replays,
        limitations=['Original publication timestamps unavailable',
            'No verified historical bars or option bid/ask sequence',
            'MRVL strike and stop not visible; stop 270 used only as stress assumption',
            'GOOGL targets and invalidation absent',
            'SPY/SNDK original strike, expiry and source entry alerts unavailable',
            'Two confirming closes and alternative full exits at each target are experiment assumptions',
            'Production conditional and creator-directed options exits remain unsupported'])
    (directory/'results.json').write_text(json.dumps(report,indent=2))
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',required=True)
    args=parser.parse_args()
    result=run(args.directory)
    print(json.dumps(dict(passed=result['passed'],cards=len(result['screenshot_cards']),
        conditional_paths=len(result['conditional_reference_paths']),
        adapter_replays=len(result['premium_adapter_replays']),real_broker_orders=0)))
