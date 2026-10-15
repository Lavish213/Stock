"""Screenshot-informed, broker-free alert fixtures. Not authentic creator replay."""
from datetime import date
from decimal import Decimal
import argparse,json
from unittest.mock import patch
from pathlib import Path
from lavish_core.validation.options_creator_month import offline,parse
from lavish_core.validation.context_replay import replay_break_hold
from lavish_core.validation.options_exchange import Contract,OptionsExchange
from lavish_core.trade.options_broker import build_occ_symbol
from lavish_core.feeds.research_context import classify


def fake_alerts():
    # Every date, price and missing risk level below is invented. Do not
    # transpose historical screenshot dates inside actual-source records.
    for ticker,strike in [('NVDA',220),('OPEN',7),('AMD',285),('MSTR',347.5),('HOOD',170),('INTC',40),('SNDK',1600),('GOOGL',305)]:
        yield dict(id='synthetic-'+ticker,synthetic=True,published_at='2026-10-15T13:31:00Z',
            text=f'BTO {ticker} {strike} CALL exp 2026-11-20 target {strike+5} stop {strike-2}',
            assumptions='Fresh date; missing contracts/levels completed artificially; no historical fill claim')


def hold(closes):
    bars=[dict(start=f'2026-10-15T13:{31+i:02}:00Z',end=f'2026-10-15T13:{32+i:02}:00Z',available_at=f'2026-10-15T13:{32+i:02}:01Z',close=close) for i,close in enumerate(closes)]
    return replay_break_hold(published_at='2026-10-15T13:31:00Z',expires_at='2026-10-15T20:00:00Z',as_of='2026-10-15T13:40:00Z',level=220,bars=bars)


def study(corpus):
    rows=json.loads(Path(corpus).read_text())['actual_source_screenshots']
    source_results=[];generated=[]
    with offline():
        for row in rows:
            # Captions are tested without manufacturing OCR text from UI tabs,
            # adjacent ticker panels or missing contract fields.
            signal=parse(row['caption'])
            source_results.append(dict(file=row['file'],sha256=row['sha256'],expected_role=row['kind'],observed_context=classify(row['caption']),entry_dispatches=int(signal is not None),signal=signal))
        from lavish_core.vision import extract_signal
        # Recognition catalog extension is local to this offline fixture;
        # it does not change the production trading whitelist.
        original_known=set(extract_signal.KNOWN_TICKERS)
        for event in fake_alerts():
            baseline_signal=parse(event['text'])
            with patch.object(extract_signal,'KNOWN_TICKERS',original_known|{'OPEN','INTC','SNDK'}):
                signal=parse(event['text'])
            expected=event['id'].removeprefix('synthetic-')
            correct=bool(signal and signal.get('ticker')==expected and signal.get('side')=='CALL' and signal.get('action')=='BUY' and signal.get('expiry')=='2026-11-20')
            generated.append(dict(**event,baseline_signal=baseline_signal,fixture_recognition_catalog_extended=True,signal=signal,correct=correct))
        conditional=parse('NVDA220 CALL exp 2026-11-20. Entry trigger: reclaim $218-$220 and hold above this area with buyers stepping in. Targets225 and230.')
        # This normalized explicit instruction is emitted only in this isolated
        # fixture after a synthetic closed-bar condition. Production promotion
        # of research conditions is not implemented or enabled.
        hold_results={name:hold(path) for name,path in dict(touch_only=[220,220],false_break=[221,219],confirmed=[221,222]).items()}
        x=OptionsExchange(cash=100,fee=.65,today=date(2026,10,15));expiry=date(2026,11,20)
        symbol=build_occ_symbol('OPEN',expiry,'CALL',7)
        x.add_contract(Contract(symbol,'OPEN',expiry,'CALL',7),.14,.15)
        entry=x.submit(symbol,'BUY',5,.15,'synthetic-entry')
        duplicate=x.submit(symbol,'BUY',5,.15,'synthetic-entry')
        x.quote(symbol,.30,.32)
        trim=x.submit(symbol,'SELL',4,.30,'synthetic-trim')
        remaining_after_trim=x.positions[symbol]
        x.quote(symbol,.20,.22);exit=x.submit(symbol,'SELL',1,.20,'synthetic-runner')
        scaling=dict(synthetic=True,initial=100,entry_contracts=entry['filled_qty'],trim_contracts=trim['filled_qty'],remaining_after_trim=remaining_after_trim,runner_exit_contracts=exit['filled_qty'],duplicate_order_same_identity=duplicate['id']==entry['id'],final_cash=round(x.cash,2),net_pnl=round(x.cash-100,2),positions=x.positions,scope='Isolated exchange fixture; intentional scaling NOT production options monitor',quote_assumption='Invented .15 entry/.30 trim/.20 runner; screenshot OPEN1.43 is not replaced in actual-source evidence')
    return dict(actual_source_captions=dict(cases=len(rows),entry_dispatches=sum(r['entry_dispatches'] for r in source_results),results=source_results),
        synthetic_alerts=dict(cases=len(generated),correct=sum(r['correct'] for r in generated),results=generated),
        synthetic_conditional=dict(initial_dispatches=int(conditional is not None),hold_results=hold_results,confirmation_bars_assumption=2,volume_confirmation_certified=False),
        synthetic_scaling=scaling,source_OPEN_100_dollar_feasibility=dict(premium_visible=1.43,minimum_debit_before_fees=143,can_afford_one=False),
        real_orders=0,historical_fills_verified=False,source_authentication_verified=False,production_conditional_or_scaling_wiring=False)

if __name__=='__main__':
    cli=argparse.ArgumentParser();cli.add_argument('--corpus',default='docs/evidence/screenshot-trigger-corpus-20261015.json');cli.add_argument('--output',required=True);a=cli.parse_args()
    result=study(a.corpus)
    with Path(a.output).open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps({k:{key:value for key,value in v.items() if key!='results'} for k,v in result.items() if k in ('actual_source_captions','synthetic_alerts')}))
