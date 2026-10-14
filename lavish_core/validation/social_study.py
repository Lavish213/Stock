"""Bounded actual-source excerpts plus clearly separate synthetic stress cases."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from unittest.mock import patch
from lavish_core.feeds.research_context import classify


def source_cases():
    creator = [
        ('IMG_2852.jpeg:original-card', 'GOOGL $350C, October 9. Break and hold above $346.', 'conditional_setup'),
        ('IMG_2852.jpeg:caption', 'Google trade is at the second target', 'progress_update'),
        ('IMG_2850.png:caption', 'JPM is at the second target', 'progress_update'),
        ('IMG_2851.png:caption', 'While everything is crashing overnight our set up is looking good right now', 'conditional_setup'),
        ('IMG_2849.png:caption', 'JPM was up $6 at opening, if you didn’t take profits, that’s on you. The contracts were high as $8.10 a contract.', 'progress_update'),
        ('IMG_2848.png:caption', 'Tomorrow is the inflation report that sets the tone for the week. Headline CPI annual 3.6% 3.4%. Core CPI annual 2.5% 2.4%. What This Could Mean for SPY and QQQ', 'macro_context'),
        ('IMG_2847.png:caption', 'Cooler than expected inflation could ease rate concerns and help spark a rally. Hotter than expected inflation could push Treasury yields higher and put pressure on stocks.', 'macro_context'),
    ]
    rows=[dict(id=identity,text=text,expected_kind=kind,source='stockalley-screenshot',published_at=None,
               evidence='manual transcription from supplied screenshot',expected_dispatches=0) for identity,text,kind in creator]
    base='https://www.reddit.com/r/wallstreetbets/'
    excerpts=[
        ('comments/1x4xd46/put_the_remaining_of_my_life_savings_150k_into/', 'Put the remaining of my life savings ($150k) into OTM $SPOT calls going into earnings'),
        ('comments/1x543wr/ttwo_545k_yolo_am_i_fucked/', 'TTWO 545K YOLO - Am i fucked?'),
        ('comments/1x46klu/6m_rddt_yolo_32000_shares_400_itm_calls_and_a/', 'Current positions: 32,000 RDDT shares, 220 Nov $130 calls, 180 Mar 2027 $100 calls, 110 Jan 2028 $135 puts sold'),
        ('comments/1wzimpi/1280_gain_nvda_too_scared_to_sell/', '1280% gain NVDA - too scared to sell'),
        ('comments/1wzimpi/1280_gain_nvda_too_scared_to_sell/', 'Sell covered calls'),
        ('comments/1wzimpi/1280_gain_nvda_too_scared_to_sell/', 'Sell half, keep half'),
        ('comments/1x4skqc/daily_discussion_thread_for_october_13_2026/', 'MU opening at $1500'),
        ('comments/1x4skqc/daily_discussion_thread_for_october_13_2026/', 'SNDK will break 1900 before earnings'),
        ('comments/1x4skqc/daily_discussion_thread_for_october_13_2026/', 'I bought puts on the only things that only go up every day. The Dow Jones and MSFT.'),
        ('comments/1x1qqq0/weekly_earnings_thread_oct_12_oct_16_2026/', 'JPM 3.58% BAC 3.99% ASML 6.07% TSM 4.26% implied moves'),
        ('new/', 'FLUT - Flutter entertainment - People’s opinions'),
        ('new/', 'AAOI loss'), ('new/', 'Nvo yolo'),
        ('new/', 'Is it a YOLO when it’s half of you years pay? $LAC'),
    ]
    for i,(url,text) in enumerate(excerpts):
        rows.append(dict(id=f'wsb-{i}',text=text,source='reddit:wallstreetbets',source_url=base+url,
                         published_at=None,evidence='public source excerpt; exact original timestamp unavailable',expected_dispatches=0))
    return rows


def stress_cases():
    rows=[]
    for ticker in ('JPM','GOOGL','AMZN','SPY','QQQ','NVDA','RDDT','SPOT','TTWO','ASML','TSM'):
        for text in (f'{ticker} 100 CALL exp 2026-10-16 target hit',
                     f'{ticker} 100 CALL exp 2026-10-16 is at the second target',
                     f'Current positions: {ticker} 100 CALL exp 2026-10-16',
                     f'{ticker} 100 CALL exp 2026-10-16. Break and hold above $100.',
                     f'BUY {ticker} now ahead of CPI'):
            for source in ('reddit','reddit:wallstreetbets','WSB'):
                rows.append(dict(id=f'stress-{len(rows)}',text=text,source=source,expected_dispatches=0,
                                 evidence='synthetic source-boundary mutation'))
        for text in (f'{ticker} 100 CALL exp 2026-10-16 target hit',
                     f'{ticker} 100 CALL exp 2026-10-16 is at the second target',
                     f'Current positions: {ticker} 100 CALL exp 2026-10-16',
                     f'{ticker} 100 CALL exp 2026-10-16. Break and hold above $100.'):
            rows.append(dict(id=f'stress-{len(rows)}',text=text,source='patreon',expected_dispatches=0,
                             evidence='synthetic progress/conditional mutation'))
    return rows


def evaluate(rows):
    from lavish_core.trading import alert_handler as ah
    results=[]
    with patch.object(ah,'execute_trade_from_post') as dispatch, patch.object(ah,'post_discord'),patch.object(ah,'log'):
        for row in rows:
            dispatch.reset_mock()
            ah.handle_alert_text(row['text'],source=row['source'])
            kind=classify(row['text'])['kind']
            correct=dispatch.call_count==row['expected_dispatches'] and ('expected_kind' not in row or kind==row['expected_kind'])
            results.append({**row,'observed_kind':kind,'dispatches':dispatch.call_count,'correct':correct})
    incorrect=sum(not r['correct'] for r in results)
    return dict(cases=len(rows),incorrect=incorrect,incorrect_pct=100*incorrect/len(rows),
                unsafe_dispatches=sum(r['dispatches'] for r in results),results=results)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    actual=evaluate(source_cases());stress=evaluate(stress_cases())
    report=dict(actual_source_excerpts=actual,synthetic_mutations=stress,
                real_broker_orders=0,historical_fills_established=False,
                passed=actual['incorrect']==0 and stress['incorrect']==0)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:{name:value for name,value in r.items() if name!='results'} for k,r in [('actual_source_excerpts',actual),('synthetic_mutations',stress)]}))
    if not report['passed']: raise SystemExit(1)


if __name__=='__main__': main()
