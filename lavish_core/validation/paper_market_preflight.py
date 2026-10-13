"""Read-only paper broker/deployment check; optionally halt entries in the bot journal."""
import argparse,hashlib,json,os
from datetime import datetime,timezone
from pathlib import Path
from lavish_core.utils.safety import check_trading_endpoint,finite_number

def check_market(db_path=None,*,halt_entries=False):
    if os.getenv('TRADE_MODE')!='paper':raise ValueError('Explicit paper mode required')
    if os.getenv('BROKER_PROVIDER','alpaca').lower()!='alpaca':raise ValueError('Alpaca paper preflight only')
    from lavish_core.trade import broker_alpaca as broker
    check_trading_endpoint('paper',broker.BASE_URL)
    account=broker.get_account();clock=broker.get_clock();positions=broker.get_positions()
    response=broker.SESSION.get(broker.ORDERS_URL,headers=broker.HEADERS,params={'status':'open'},timeout=20)
    if response.status_code!=200:raise ValueError('Cannot verify open orders')
    orders=response.json()
    if not isinstance(positions,list) or not isinstance(orders,list) or type(clock.get('is_open')) is not bool:raise ValueError('Malformed broker state')
    equity=finite_number(account.get('equity'),positive=True);cash=finite_number(account.get('cash'),nonnegative=True)
    level=finite_number(account.get('options_trading_level'),nonnegative=True)
    broker_ok=account.get('status')=='ACTIVE' and account.get('trading_blocked') is False and account.get('account_blocked') is False and level>=2
    patreon=bool(os.getenv('PATREON_ACCESS_TOKEN') or all(os.getenv(k) for k in ['PATREON_CLIENT_ID','PATREON_CLIENT_SECRET','PATREON_REFRESH_TOKEN']))
    discord=bool(os.getenv('DISCORD_USER_TOKEN') and os.getenv('DISCORD_INGEST_ACCEPT_TOS_RISK')=='1')
    from lavish_core.feeds import alert_feed
    private_feed=alert_feed.enabled()
    if private_feed:alert_feed.settings()
    source_present=patreon or discord or private_feed
    budgets={}
    for key in ['DEFAULT_TRADE_DOLLARS','DEFAULT_OPTION_TRADE_DOLLARS']:
        value=os.getenv(key)
        budgets[key]=finite_number(value,nonnegative=True) if value not in (None,'') else None
    budget_configured=any(value is not None and value>0 for value in budgets.values())
    blockers=[]
    if not broker_ok:blockers.append('broker_account_not_eligible')
    if not source_present:blockers.append('alert_source_credentials_missing')
    else:blockers.append('alert_source_authentication_not_verified')
    if not budget_configured:blockers.append('explicit_entry_budget_missing')
    audit=None;halt=None
    if db_path is not None:
        from lavish_core.db.hybrid_store import HybridStore
        from lavish_core.trade.state_audit import audit_state
        store=HybridStore(db_path)
        try:
            if halt_entries:store.set_kill_switch(True,'market preparation: configure feed and explicit paper budgets before enabling entries')
            audit=audit_state(store,positions)
            if not audit['ok']:blockers.append('broker_ledger_drift_or_unknown_orders')
            halt=store.get_kill_switch()[0]
            if halt:blockers.append('entry_kill_switch_enabled')
        finally:store.con.close()
    from lavish_core.trade.live_readiness import runtime_digest
    return dict(check='paper_market_preflight',source='authenticated-paper',synthetic=False,passed=broker_ok and (audit is None or audit['ok']),automatic_entries_ready=not blockers and clock['is_open'],live_ready=False,orders_submitted=0,account_id_hash=hashlib.sha256(str(account['id']).encode()).hexdigest(),observed_at=datetime.now(timezone.utc).isoformat(),runtime_sha256=runtime_digest(),market_open=clock['is_open'],next_open=clock.get('next_open'),next_close=clock.get('next_close'),positions_count=len(positions),open_orders_count=len(orders),options_trading_level=level,alert_source_credentials_present=source_present,alert_source_authentication_verified=False,explicit_entry_budget_configured=budget_configured,entry_halted=halt,state_audit=audit,blockers=blockers,scope='connectivity and state only; actual fills, feed authentication, emergency notification delivery and deployed watchdog recovery not certified')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--env-file',type=Path,required=True);parser.add_argument('--db',type=Path);parser.add_argument('--halt-entries',action='store_true');parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    if args.output.exists() or (args.db is not None and args.output.resolve() in {args.db.resolve(),Path(str(args.db.resolve())+'.wal'),Path(str(args.db.resolve())+'.control.json')}):parser.error('Use a new receipt path separate from the journal/control files')
    if args.halt_entries and args.db is None:parser.error('--halt-entries requires --db')
    from dotenv import load_dotenv
    load_dotenv(args.env_file,override=False)
    os.umask(0o077)
    try:result=check_market(args.db,halt_entries=args.halt_entries)
    except Exception as exc:result=dict(check='paper_market_preflight',passed=False,live_ready=False,orders_submitted=0,error_type=type(exc).__name__)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n');os.chmod(args.output,0o600);print(json.dumps(result));return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
