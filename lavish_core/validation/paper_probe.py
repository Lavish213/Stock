"""Read-only authenticated paper connectivity probe. Never submits orders."""
import argparse,hashlib,json,os
from datetime import datetime,timezone
from pathlib import Path
from lavish_core.utils.safety import check_trading_endpoint,finite_number

def probe():
    if os.getenv('TRADE_MODE')!='paper':raise ValueError('Explicit TRADE_MODE=paper required')
    base=check_trading_endpoint('paper',os.getenv('ALPACA_BASE_URL','https://paper-api.alpaca.markets'))
    if not os.getenv('ALPACA_API_KEY') or not os.getenv('ALPACA_SECRET_KEY'):raise ValueError('Paper credentials unavailable')
    from lavish_core.trade import broker_alpaca as broker
    check_trading_endpoint('paper',broker.BASE_URL)
    account=broker.get_account();clock=broker.get_clock();positions=broker.get_positions()
    finite_number(account.get('equity'),positive=True);finite_number(account.get('cash'),nonnegative=True)
    if not isinstance(clock.get('is_open'),bool) or not isinstance(positions,list):raise ValueError('Malformed account response')
    price=broker.latest_quote('SPY')
    if price is None:raise ValueError('Fresh quote unavailable')
    finite_number(price,positive=True)
    return dict(check='paper_connectivity',source='authenticated-paper',synthetic=False,passed=True,live_ready=False,orders_submitted=0,account_id_hash=hashlib.sha256(str(account['id']).encode()).hexdigest(),market_open=clock['is_open'],position_count=len(positions),observed_at=datetime.now(timezone.utc).isoformat())

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    try:result=probe()
    except Exception as exc:result=dict(check='paper_connectivity',passed=False,live_ready=False,orders_submitted=0,error_type=type(exc).__name__)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n');os.chmod(args.output,0o600)
    print(json.dumps(result));return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
