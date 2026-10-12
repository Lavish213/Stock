import json,os,subprocess,sys
from datetime import date,timedelta
from pathlib import Path
import pytest
from lavish_core.validation import paper_cycle as pc
from lavish_core.validation.options_exchange import OptionsExchange,Contract
from lavish_core.validation.options_replay import Response
from lavish_core.trade import broker_alpaca as broker,options_broker as options
from lavish_core.db.hybrid_store import HybridStore

@pytest.fixture
def boundary(monkeypatch):
    monkeypatch.setenv('TRADE_MODE','paper');monkeypatch.setenv('ALPACA_API_KEY','synthetic');monkeypatch.setenv('ALPACA_SECRET_KEY','synthetic')
    expiry=date.today()+timedelta(days=14);symbol=options.build_occ_symbol('SPY',expiry,'CALL',200)
    exchange=OptionsExchange(fee=0);exchange.add_contract(Contract(symbol,'SPY',expiry,'CALL',200),1,1)
    state={'posts':0,'cash_drift':0,'broker_drift':False,'cancel_unknown':False,'lost_response':False,'lookup_unknown':False,'zero_bid':False}
    def account():return dict(id='synthetic-account',cash=str(exchange.cash+(state['cash_drift'] if state['posts']>1 else 0)),equity='100000',options_buying_power='100000',options_trading_level=2,trading_blocked=False,account_blocked=False)
    def positions():
        rows=[dict(symbol=s,qty=str(qty)) for s,qty in exchange.positions.items()]
        if state['broker_drift'] and state['posts']>1:rows.append(dict(symbol='SPY',qty='1'))
        return rows
    def get(url,**kwargs):
        if '/options/contracts/' in url:return Response(dict(symbol=symbol,tradable=True,size='100',type='call',expiration_date=expiry.isoformat()))
        return Response([])
    def post(url,*,data,**kwargs):
        payload=json.loads(data);state['posts']+=1
        order=exchange.submit(symbol,payload['side'].upper(),int(payload['qty']),float(payload['limit_price']),payload['client_order_id'])
        if payload['side']=='buy':
            exchange.quote(symbol,0 if state['zero_bid'] else 1.1,1.1)
            if state['lost_response']:raise TimeoutError('synthetic lost response')
        return Response(dict(order),201)
    def lookup(cid):
        if state['lookup_unknown']:raise TimeoutError('synthetic lookup outage')
        return dict(exchange.orders[exchange.clients[cid]]) if cid in exchange.clients else None
    monkeypatch.setattr(broker,'get_account',account);monkeypatch.setattr(broker,'get_clock',lambda:{'is_open':True});monkeypatch.setattr(broker,'get_positions',positions)
    monkeypatch.setattr(broker,'get_order',lambda oid:dict(exchange.orders[oid]));monkeypatch.setattr(broker,'get_order_by_client_id',lookup)
    monkeypatch.setattr(broker,'cancel_order',lambda oid:exchange.cancel(oid,confirmed=not state['cancel_unknown']))
    monkeypatch.setattr(options,'latest_option_quote',lambda s:dict(bid=exchange.quotes[s][0],ask=exchange.quotes[s][1]))
    monkeypatch.setattr(options,'_check_keys',lambda:None);monkeypatch.setattr(broker.SESSION,'get',get);monkeypatch.setattr(options.SESSION,'post',post)
    return exchange,symbol,state

@pytest.mark.parametrize('qty',[1,2,3])
@pytest.mark.parametrize('lost',[False,True])
def test_paper_cycle_through_production_adapter_and_exit(tmp_path,boundary,qty,lost):
    x,symbol,state=boundary;state['lost_response']=lost
    result=pc.run_cycle(symbol,tmp_path/'cycle.duckdb',execute_paper=True,max_premium_usd=300,quantity=qty,sleep=lambda _:None)
    assert result['passed'] and not result['live_ready']
    assert result['check']=='paper_order_cycle'  # A smoke cycle never certifies full readiness.
    assert state['posts']==2 and x.positions=={} and abs(result['cash_reconciliation_error'])<1e-8

@pytest.mark.parametrize('failure',['zero_bid','cancel_unknown','unknown_submission'])
def test_unknown_paper_outcomes_never_blindly_resubmit(tmp_path,boundary,failure):
    x,symbol,state=boundary;qty=1
    if failure=='cancel_unknown':qty=2;x.quote(symbol,1,1,liquidity=1);state['cancel_unknown']=True
    elif failure=='unknown_submission':state.update(lost_response=True,lookup_unknown=True)
    else:state['zero_bid']=True
    path=tmp_path/'cycle.duckdb'
    with pytest.raises(RuntimeError):pc.run_cycle(symbol,path,execute_paper=True,max_premium_usd=300,quantity=qty,poll_attempts=2,sleep=lambda _:None)
    assert state['posts']==1
    store=HybridStore(path)
    assert store.get_kill_switch()[0]
    if failure!='zero_bid':assert store.get_open_orders()
    store.con.close()

@pytest.mark.parametrize('fault',['cash_drift','broker_drift'])
def test_paper_cycle_reconciliation_rejects_drift(tmp_path,boundary,fault):
    _,symbol,state=boundary;state[fault]=5 if fault=='cash_drift' else True
    path=tmp_path/'cycle.duckdb';result=pc.run_cycle(symbol,path,execute_paper=True,max_premium_usd=100,sleep=lambda _:None)
    assert not result['passed'];store=HybridStore(path);assert store.get_kill_switch()[0];store.con.close()

def test_partial_entry_closed_without_claiming_requested_full_cycle(tmp_path,boundary):
    x,symbol,state=boundary;x.quote(symbol,1,1,liquidity=1)
    result=pc.run_cycle(symbol,tmp_path/'cycle.duckdb',execute_paper=True,max_premium_usd=200,quantity=2,poll_attempts=1,sleep=lambda _:None)
    assert result['entry_filled_qty']==1 and not result['passed'] and not x.positions

@pytest.mark.parametrize('fault',['no_execute','live_mode','missing_keys','existing_journal','cap','fractional','too_many'])
def test_paper_cycle_preflight_no_submission(tmp_path,boundary,monkeypatch,fault):
    _,symbol,state=boundary;path=tmp_path/'cycle.duckdb';execute=True;qty=1;cap=100
    if fault=='no_execute':execute=False
    elif fault=='live_mode':monkeypatch.setenv('TRADE_MODE','live')
    elif fault=='missing_keys':monkeypatch.delenv('ALPACA_API_KEY')
    elif fault=='existing_journal':path.write_text('do not overwrite')
    elif fault=='cap':cap=99
    elif fault=='fractional':qty=1.5
    else:qty=4
    with pytest.raises(ValueError):pc.run_cycle(symbol,path,execute_paper=execute,max_premium_usd=cap,quantity=qty,sleep=lambda _:None)
    assert state['posts']==0

@pytest.mark.parametrize('phase',['uncommitted_fill','committed_fill','unknown_order'])
def test_real_process_death_and_journal_recovery(tmp_path,phase):
    path=tmp_path/'recovery.duckdb'
    code='''import os,sys
from lavish_core.db.hybrid_store import HybridStore
s=HybridStore(sys.argv[1]);oid=s.submit_order('SPY261030C00200000','buy',1,venue='paper',status='pending_submit',client_id='recovery-client')
phase=sys.argv[2]
if phase=='uncommitted_fill':
 s.execute('BEGIN TRANSACTION')
 s.execute("UPDATE orders SET status='filled' WHERE id=?",(oid,))
elif phase=='committed_fill':
 s.log_fill(oid,'SPY261030C00200000','buy',1,2,event_id='confirmed-event')
else:s.set_order_status(oid,'submission_unknown')
os._exit(23)
'''
    env=dict(os.environ,TRADE_MODE='dry',DISCORD_WEBHOOK_URL='')
    process=subprocess.run([sys.executable,'-c',code,str(path),phase],env=env,capture_output=True,text=True,timeout=20)
    assert process.returncode==23,process.stderr
    store=HybridStore(path);orders=store.fetchall('SELECT id,status FROM orders');assert len(orders)==1
    if phase=='committed_fill':
        assert store.get_position('SPY261030C00200000')['qty']==1
        store.log_fill(orders[0][0],'SPY261030C00200000','buy',1,2,event_id='confirmed-event')
        assert store.fetchall('SELECT count(*) FROM fills')[0][0]==1
    else:
        assert store.get_position('SPY261030C00200000') is None
        assert orders[0][1]==('submission_unknown' if phase=='unknown_order' else 'pending_submit')
    store.con.close()

@pytest.mark.parametrize('mode',['live','dry','unknown'])
def test_legacy_simulated_alerts_cannot_reach_money_execution(mode,monkeypatch):
    from simulate_alerts import live_run
    from lavish_core.trading import alert_handler
    from unittest.mock import Mock
    monkeypatch.setenv('TRADE_MODE',mode);dispatch=Mock();monkeypatch.setattr(alert_handler,'handle_alert_text',dispatch)
    monkeypatch.setattr('builtins.input',lambda *a:pytest.fail('No confirmation may unlock a simulation for live money'))
    with pytest.raises(RuntimeError):live_run([dict(label='synthetic',text='BUY SPY')],0)
    assert not dispatch.called

@pytest.mark.parametrize('fault',['blocked','closed','approval_missing','existing_positions','open_orders','unsupported_contract'])
def test_paper_cycle_broker_preflight_blocks(tmp_path,boundary,monkeypatch,fault):
    _,symbol,state=boundary
    if fault=='blocked':
        original=broker.get_account;monkeypatch.setattr(broker,'get_account',lambda:dict(original(),trading_blocked=True))
    elif fault=='closed':monkeypatch.setattr(broker,'get_clock',lambda:dict(is_open=False))
    elif fault=='approval_missing':
        original=broker.get_account;monkeypatch.setattr(broker,'get_account',lambda:dict(original(),options_trading_level=1))
    elif fault=='existing_positions':monkeypatch.setattr(broker,'get_positions',lambda:[dict(symbol='SPY',qty='1')])
    elif fault=='open_orders':monkeypatch.setattr(broker.SESSION,'get',lambda *a,**k:Response([dict(id='unmanaged')]))
    else:
        original=broker.SESSION.get
        monkeypatch.setattr(broker.SESSION,'get',lambda url,**k:Response(dict(original(url,**k).json(),size='10')) if '/options/contracts/' in url else original(url,**k))
    with pytest.raises(ValueError):pc.run_cycle(symbol,tmp_path/'cycle.duckdb',execute_paper=True,max_premium_usd=100,sleep=lambda _:None)
    assert state['posts']==0

def test_empty_kill_reason_still_halts_entries(tmp_path):
    path=tmp_path/'halt.duckdb';store=HybridStore(path)
    env=dict(os.environ,TRADE_MODE='dry',DISCORD_WEBHOOK_URL='')
    process=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'kill_switch.py'),'--on','','--db',str(path)],env=env,capture_output=True,text=True,timeout=20)
    assert process.returncode==0,process.stderr
    assert store.get_kill_switch()[0] is True
    store.con.close()

def test_paper_cycle_receipt_cannot_overwrite_journal(tmp_path):
    path=tmp_path/'protected.duckdb'
    process=subprocess.run([sys.executable,'-m','lavish_core.validation.paper_cycle','--execute-paper','--contract','SPY261030C00200000','--db',str(path),'--output',str(path),'--max-premium-usd','100'],capture_output=True,text=True,timeout=20)
    assert process.returncode==2 and not path.exists()
