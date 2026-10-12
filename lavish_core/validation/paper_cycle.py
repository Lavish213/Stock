"""Explicit, bounded paper-only options round trip with a durable isolated journal.

A successful cycle is a smoke observation, not full lifecycle or live approval.
Never retry an ambiguous submission or replace an unconfirmed closing order.
"""
import argparse,hashlib,json,os,time,uuid
from datetime import date,datetime,timezone
from pathlib import Path
from lavish_core.utils.safety import check_trading_endpoint,finite_number
from lavish_core.trade.exit_state import ExitState
from lavish_core.trade.state_audit import audit_state
from lavish_core.db.hybrid_store import HybridStore

TERMINAL={'filled','canceled','expired','rejected'}

def _require_paper():
    if os.getenv('TRADE_MODE')!='paper':raise ValueError('Explicit paper mode required')
    if os.getenv('BROKER_PROVIDER','alpaca').lower()!='alpaca':raise ValueError('This paper cycle supports Alpaca only')
    check_trading_endpoint('paper',os.getenv('ALPACA_BASE_URL','https://paper-api.alpaca.markets'))
    if not os.getenv('ALPACA_API_KEY') or not os.getenv('ALPACA_SECRET_KEY'):raise ValueError('Paper credentials unavailable')


def _confirmed_entry(store,oid,order,qty):
    filled=finite_number(order.get('filled_qty') or 0,nonnegative=True)
    if not filled.is_integer() or filled>qty or (order.get('status')=='filled' and filled!=qty):raise ValueError('Invalid confirmed option quantity')
    if filled:
        price=finite_number(order.get('filled_avg_price'),positive=True)
        store.log_fill(oid,order['symbol'],'buy',filled,price,fee=finite_number(order.get('filled_fee',0),nonnegative=True),venue='paper',event_id='paper-cycle:'+order['id']+':'+str(filled))
    store.set_order_status(oid,order.get('status','submission_unknown'))
    return int(filled)


def run_cycle(contract_symbol,db_path,*,execute_paper=False,max_premium_usd,quantity=1,poll_attempts=10,sleep=time.sleep):
    if execute_paper is not True:raise ValueError('Explicit --execute-paper required')
    _require_paper()
    if isinstance(quantity,bool) or not isinstance(quantity,int) or not 1<=quantity<=3:raise ValueError('Quantity must be 1 to 3 whole contracts')
    if isinstance(poll_attempts,bool) or not isinstance(poll_attempts,int) or not 1<=poll_attempts<=30:raise ValueError('Invalid polling bound')
    cap=finite_number(max_premium_usd,positive=True)
    db_path=Path(db_path).resolve()
    if db_path.exists():raise ValueError('Use a new isolated journal; never a running bot database')
    from lavish_core.trade import broker_alpaca as broker,options_broker as options
    check_trading_endpoint('paper',broker.BASE_URL);check_trading_endpoint('paper',options.BASE_URL)
    account=broker.get_account();clock=broker.get_clock()
    if account.get('trading_blocked') is not False or account.get('account_blocked') is not False or clock.get('is_open') is not True:raise ValueError('Account blocked, unverifiable or market closed')
    if finite_number(account.get('options_trading_level'),nonnegative=True)<2:raise ValueError('Long-options approval required')
    cash_before=finite_number(account.get('cash'),nonnegative=True)
    if broker.get_positions()!=[]:raise ValueError('Use a dedicated empty paper account')
    response=broker.SESSION.get(broker.ORDERS_URL,headers=broker.HEADERS,params={'status':'open'},timeout=20)
    if response.status_code!=200 or response.json()!=[]:raise ValueError('Open orders present or unverified')
    response=options.SESSION.get(options.CONTRACTS_URL+'/'+contract_symbol,headers=options.HEADERS,timeout=20)
    if response.status_code!=200:raise ValueError('Contract metadata unavailable')
    contract=response.json()
    if contract.get('symbol')!=contract_symbol or contract.get('tradable') is not True or str(contract.get('size'))!='100' or contract.get('type') not in ('call','put') or date.fromisoformat(contract['expiration_date'])<=date.today():raise ValueError('Nonstandard, untradable, expiry-day or mismatched contract')
    quote=options.latest_option_quote(contract_symbol)
    if not quote or not 0<finite_number(quote['bid'],nonnegative=True)<=finite_number(quote['ask'],positive=True):raise ValueError('Fresh two-sided market required')
    entry_limit=quote['ask'];premium=quantity*entry_limit*100
    if premium>cap or premium>cash_before or premium>finite_number(account.get('options_buying_power'),nonnegative=True):raise ValueError('Paper premium cap or buying power exceeded')
    store=HybridStore(db_path);os.chmod(db_path,0o600);submitted=False
    from lavish_core.trade.live_readiness import runtime_digest
    result=dict(runtime_sha256=runtime_digest(),check='paper_order_cycle',source='authenticated-paper',synthetic=False,passed=False,live_ready=False,observed_at=datetime.now(timezone.utc).isoformat(),account_id_hash=hashlib.sha256(str(account['id']).encode()).hexdigest(),contract=contract_symbol,quantity=quantity,scope='one bounded round trip; does not approve partial-fill recovery, host recovery, strategy performance or settlement')
    try:
        client='paper_validation_'+uuid.uuid4().hex[:20]
        oid=store.submit_order(contract_symbol,'buy',quantity,order_type='limit',limit_price=entry_limit,venue='paper',status='pending_submit',client_id=client,meta={'validation':True})
        submitted=True
        try:order=options.place_option_order(contract_symbol,'buy',quantity,limit_price=entry_limit,client_order_id=client)
        except Exception:
            try:order=broker.get_order_by_client_id(client)
            except Exception:order=None
            if not order:
                store.set_order_status(oid,'submission_unknown');raise RuntimeError('Unknown entry outcome; broker review required')
        if not isinstance(order,dict) or not order.get('id'):raise ValueError('Invalid submission response')
        for _ in range(poll_attempts):
            if order.get('status') in TERMINAL:break
            sleep(1);order=broker.get_order(order['id'])
        if order.get('status') not in TERMINAL:
            broker.cancel_order(order['id'])
            for _ in range(poll_attempts):
                sleep(1);order=broker.get_order(order['id'])
                if order.get('status') in TERMINAL:break
        # Do not close while an entry can still execute after accepted cancellation.
        if order.get('status') not in TERMINAL:
            store.set_order_status(oid,'submission_unknown');raise RuntimeError('Cancellation unconfirmed; broker review required')
        if order.get('symbol')!=contract_symbol:raise ValueError('Mismatched entry response')
        filled=_confirmed_entry(store,oid,order,quantity)
        result['entry_status']=order['status'];result['entry_filled_qty']=filled
        if not filled:
            result['reason']='entry_not_filled';return result
        state=ExitState(filled,db_path=db_path,symbol=contract_symbol,venue='paper');outcome=None
        for _ in range(poll_attempts):
            quote=options.latest_option_quote(contract_symbol)
            if not quote or not finite_number(quote.get('bid'),nonnegative=True):sleep(1);continue
            bid=quote['bid']
            outcome=state.attempt(lambda remaining,cid:options.place_option_order(contract_symbol,'sell',int(remaining),limit_price=bid,client_order_id=cid),broker.get_order_by_client_id,broker.get_order,broker.cancel_order,2,lambda:sleep(1))
            if outcome:break
            sleep(1)
        if not outcome or outcome.get('status')!='exited':raise RuntimeError('Unresolved exit; journal retained for broker review')
        positions=broker.get_positions();audit=audit_state(store,positions)
        cash_after=finite_number(broker.get_account().get('cash'),nonnegative=True)
        expected=(outcome['price']-finite_number(order['filled_avg_price'],positive=True))*filled*100
        fees=store.fetchall('SELECT coalesce(sum(fee),0) FROM fills')[0][0]
        cash_error=cash_after-cash_before-(expected-fees)
        result.update(entry_price=float(order['filled_avg_price']),exit_price=outcome['price'],cash_before=cash_before,cash_after=cash_after,recorded_fees=fees,cash_reconciliation_error=cash_error,state_audit=audit,filled_requested_quantity=filled==quantity)
        result['passed']=audit['ok'] and filled==quantity and abs(cash_error)<=.02
        if not result['passed']:store.set_kill_switch(True,'paper cycle reconciliation or requested fill incomplete')
        return result
    except Exception:
        if submitted:store.set_kill_switch(True,'paper cycle interrupted; inspect broker and isolated journal before rerun')
        raise
    finally:store.con.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute-paper',action='store_true',required=True);p.add_argument('--contract',required=True);p.add_argument('--db',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--max-premium-usd',type=float,required=True);p.add_argument('--quantity',type=int,default=1)
    args=p.parse_args()
    if args.output.exists() or args.output.resolve() in {args.db.resolve(),Path(str(args.db.resolve())+'.wal'),Path(str(args.db.resolve())+'.control.json')}:p.error('Use a new receipt path separate from the journal and its control files')
    os.umask(0o077)
    try:result=run_cycle(args.contract,args.db,execute_paper=args.execute_paper,max_premium_usd=args.max_premium_usd,quantity=args.quantity)
    except Exception as exc:result=dict(check='paper_order_cycle',passed=False,live_ready=False,error_type=type(exc).__name__,review_journal=str(args.db))
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n');os.chmod(args.output,0o600);print(json.dumps(result));return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
