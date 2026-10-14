"""Independent labeled text-routing corpus; no broker calls or real alerts.

Rejections count as errors for supported alerts. Wrong dispatches count as
errors for unsafe/unsupported alerts. This is not a trading-success metric.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch


TICKERS = ('AAPL','NVDA','SPY','QQQ','MSFT','AMD','TSLA','META','GOOGL','AVGO','NFLX','AMZN')
POSITIVE_EQUITY = (
    ('BUY {t} now', 'BUY'), ('buying {t} here', 'BUY'),
    ('adding {t} on this pullback', 'BUY'), ('grabbing shares on {t}', 'BUY'),
    ('BUY {t} target 210 stop 190', 'BUY'),
    ('SELL {t} now', 'SELL'), ('selling {t} now', 'SELL'),
    ('close all {t}', 'SELL'), ('exit {t} now', 'SELL'), ('closing out {t}', 'SELL'),
)
NEGATIVE = (
    'not buying {t}', 'not selling {t}', 'no longer buying {t}',
    'avoid buying {t}', 'avoid selling {t}', 'refrain from buying {t}',
    'without buying {t}', 'waiting to buy {t}', 'waiting to sell {t}',
    'BUY {t} tomorrow', 'BUY {t} later', 'BUY {t} next week',
    'BUY {t} in an hour', 'BUY {t} in 10 minutes', 'BUY {t} at 10:30',
    'BUY {t} when it breaks 200', 'BUY {t} once above 200', 'BUY {t} after earnings',
    'SELL half {t}', 'SELL 25 percent {t}', 'SELL a third of {t}',
    'SELL {t} 1/2', 'SELL {t} 2 shares', 'trim {t}', 'trimming {t}',
    'short {t}', 'shorting {t}', 'BUY {t} to cover', 'cover {t}',
    'BUY {t} and {other}', 'SELL {t} and {other}',
    'BUY {t} target 210 target 220', 'BUY {t} stop 190 stop 180',
    'BUY {t} target -210', 'BUY {t} target 0', 'BUY {t} stop NaN',
    'BUY {t} target Infinity', 'BUY {t} calls',
    'BTO {t} 200 CALL and 210 CALL exp {expiry}',
    'BTO {t} 200 CALL exp {expiry} and exp {other_expiry}',
    'BTO {t} 200 CALL and 200 PUT exp {expiry}',
    'STC {t} 200 CALL exp {expiry}', 'STO {t} 200 CALL exp {expiry}',
    'BTC {t} 200 CALL exp {expiry}',
    'BUY {t} 10x leverage', 'BUY {t}/USDC-P', 'Should I BUY {t}?',
)


def variants(text):
    # Surface mutations never change the independent instruction label.
    return (text, text.lower(), text.upper(), text.replace(' ', '\n'),
            text.replace(' ', '   '), text.replace(' ', '\t'))


CHALLENGE_NEGATIVE = (
    'not going long {t}', 'not opening {t} 200 CALL exp {expiry}',
    'not yet buying {t}', 'hold off buying {t}', 'skip BUY {t}',
    'planning to BTO {t} 200 CALL exp {expiry}',
    'considering BTO {t} 200 CALL exp {expiry}',
    'thinking about BTO {t} 200 CALL exp {expiry}',
    'could buy {t}', 'may buy {t}', 'BUY {t} only above 210',
    'BUY {t} at $210', 'BUY {t} at 210',
    'SELL one quarter of {t}', 'SELL {t} 75 percent', 'SELL 1/3 of {t}',
    'BUY {t} stop 190 SL 180',
    'BTO {t} 100c and 200c exp {expiry}',
    'BTO {t} 100p and 100c exp {expiry}',
    'BTO {t} 200 CALL expiration {expiry} expiration {other_expiry}',
)


def corpus(seed=20261014,challenge=False):
    expiry=(date.today()+timedelta(days=7)).isoformat()
    other_expiry=(date.today()+timedelta(days=14)).isoformat()
    rows=[]
    for index,ticker in enumerate(TICKERS):
        fields=dict(t=ticker,other=TICKERS[(index+1)%len(TICKERS)],expiry=expiry,other_expiry=other_expiry)
        positives = POSITIVE_EQUITY if not challenge else (
            ('BUY ${t} now!', 'BUY'), ('buying shares of {t} now', 'BUY'),
            ('SELL all my {t} now!', 'SELL'), ('exiting {t} now', 'SELL'),
            ('{t}: BUY now target 210 stop 190', 'BUY'),
        )
        for template,action in positives:
            for text in variants(template.format(**fields)):
                expected=dict(symbol=ticker,action=action)
                if 'target 210 stop 190' in template:
                    expected.update(target_hint=210.,stop_hint=190.)
                rows.append(dict(text=text,stratum='supported_equity',expected=expected))
        for side in ('CALL','PUT'):
            letter='c' if side=='CALL' else 'p'
            for strike in (100,200,500):
                templates = (
                    f'BTO {{t}} {strike} {side} exp {{expiry}}',
                    f'BUY {{t}} {strike}{letter} exp {{expiry}}',
                    f'{{t}} {side} . ${strike} exp {{expiry}}',
                    f'Call Put {{t}} ${strike} {side} exp {{expiry}}',
                ) if not challenge else (
                    f'BTO ${{t}} ${strike} strike {side} exp {{expiry}}',
                    f'{{t}} ${strike} {side} strike expiration {{expiry}}',
                    f'Call Put ${{t}} {strike}.00{letter} expires {{expiry}}',
                )
                for template in templates:
                    for text in variants(template.format(**fields)):
                        rows.append(dict(text=text,stratum='supported_options',expected=dict(ticker=ticker,action='BUY',side=side,strike=float(strike),expiry=expiry)))
        for template in (CHALLENGE_NEGATIVE if challenge else NEGATIVE):
            for text in variants(template.format(**fields)):
                rows.append(dict(text=text,stratum='unsafe_or_unsupported',expected=None))
    random.Random(seed).shuffle(rows)
    return rows


def evaluate(rows):
    from lavish_core.trading import alert_handler
    counts=Counter();errors=Counter();failures=[]
    # Even a mistaken dispatch cannot reach a broker in this benchmark.
    with patch.object(alert_handler,'execute_trade_from_post') as dispatch, \
         patch.object(alert_handler,'post_discord'), patch.object(alert_handler,'log'):
        for row in rows:
            counts[row['stratum']]+=1
            dispatch.reset_mock()
            try:
                alert_handler.handle_alert_text(row['text'],source='labeled-intent-benchmark')
                expected=row['expected']
                if expected is None:
                    correct=dispatch.call_count==0
                else:
                    correct=dispatch.call_count==1 and all(dispatch.call_args.args[0].get(k)==v for k,v in expected.items())
                if not correct:
                    errors[row['stratum']]+=1
                    failures.append({**row,'observed':[call.args[0] for call in dispatch.call_args_list]})
            except Exception as exc:
                errors[row['stratum']]+=1
                failures.append({**row,'exception_type':type(exc).__name__})
    total=sum(counts.values());incorrect=sum(errors.values())
    strata={s:dict(cases=n,incorrect=errors[s],incorrect_pct=round(100*errors[s]/n,4)) for s,n in sorted(counts.items())}
    return dict(synthetic=True,scope='text interpretation and routing only',cases=total,incorrect=incorrect,
                incorrect_pct=round(100*incorrect/total,4) if total else None,strata=strata,
                passed=bool(total) and errors['unsafe_or_unsupported']==0 and all(errors[s]/n<.03 for s,n in counts.items()),
                unsafe_dispatches=errors['unsafe_or_unsupported'],failures=failures,
                real_broker_orders=0,real_creator_accuracy_established=False)


def randomized_corpus(seed=982451653,count=6000):
    """Fresh field values and compositions; labels precede parser execution."""
    rng=random.Random(seed)
    rows=[]
    for index in range(count):
        ticker=rng.choice(TICKERS)
        expiry=(date.today()+timedelta(days=rng.randint(3,21))).isoformat()
        casing=rng.choice((str.upper,str.lower,lambda text:text))
        if index%3==0:
            action=rng.choice(('BUY','SELL'))
            verb=rng.choice(('BUY','buying','ENTRY')) if action=='BUY' else rng.choice(('SELL','selling','closing out','exit'))
            text=f'{verb} ${ticker} now'
            expected=dict(symbol=ticker,action=action)
            if action=='BUY' and rng.choice((True,False)):
                target=rng.randint(110,900)+rng.choice((0,.25,.50,.75))
                stop=rng.randint(10,90)+rng.choice((0,.25,.50,.75))
                text+=f' target {target:.2f} stop {stop:.2f}'
                expected.update(target_hint=target,stop_hint=stop)
            stratum='supported_equity'
        elif index%3==1:
            side=rng.choice(('CALL','PUT'))
            strike=rng.randint(10,1500)+rng.choice((0,.25,.50,.75))
            text=f'BTO ${ticker} {strike:.2f} {side} exp {expiry}'
            expected=dict(ticker=ticker,action='BUY',side=side,strike=strike,expiry=expiry)
            stratum='supported_options'
        else:
            kind=rng.randrange(6)
            if kind==0:
                # Literal identity differs: correcting an unconfirmed typo
                # to a real ticker is not verified creator instruction.
                while True:
                    pos=rng.randrange(len(ticker))
                    typo=ticker[:pos]+rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')+ticker[pos+1:]
                    if typo not in TICKERS:break
                text=f'BUY {typo} now'
            elif kind==1:text=f'not\u200bbuying {ticker}'
            elif kind==2:text=f'SELL {rng.randint(1,99)} percent {ticker}'
            elif kind==3:text=f'BTO {ticker} 100 CALL and {rng.randint(101,900)} CALL exp {expiry}'
            elif kind==4:text=f'BUY {ticker} in {rng.randint(1,99)} minutes'
            else:text=f'BUY {ticker} target 210 target {rng.randint(211,900)}'
            expected=None;stratum='unsafe_or_unsupported'
        text=casing(text)
        text=text.replace(' ',rng.choice((' ','  ','\n','\t')))
        rows.append(dict(text=text,stratum=stratum,expected=expected))
    rng.shuffle(rows)
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed',type=int,default=20261014)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--challenge',action='store_true')
    parser.add_argument('--randomized',action='store_true')
    args=parser.parse_args()
    if args.output.exists():parser.error('Use a new evidence path')
    result=evaluate(randomized_corpus(args.seed) if args.randomized else corpus(args.seed,challenge=args.challenge))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='failures'},indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
