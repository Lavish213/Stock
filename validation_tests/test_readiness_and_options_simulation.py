import hashlib,json,subprocess,sys
from datetime import date,datetime,timedelta,timezone
from itertools import product
from pathlib import Path
import pytest
from lavish_core.validation.options_exchange import Contract,OptionsExchange
from lavish_core.trade.live_readiness import REQUIRED,runtime_digest,validate_approval
from lavish_core.utils.safety import fresh_timestamp
from lavish_core.utils.control_file import read_control,write_control

@pytest.mark.parametrize('ticker,side,qty,entry,factor',list(product(['SPY','QQQ','AAPL','MSFT'],['CALL','PUT'],[1,2,5],[.5,1,3,10],[.5,1,1.5])))
def test_options_cash_multiplier_fees_and_no_short(ticker,side,qty,entry,factor):
    exchange=OptionsExchange();symbol=ticker+'-OPTION'
    exchange.add_contract(Contract(symbol,ticker,date.today()+timedelta(days=14),side,200),entry,entry)
    buy=exchange.submit(symbol,'BUY',qty,entry,'stable-entry')
    assert buy['filled_qty']==qty
    assert exchange.submit(symbol,'BUY',qty,entry,'stable-entry') is buy
    exchange.quote(symbol,entry*factor,entry*factor)
    sell=exchange.submit(symbol,'SELL',qty,entry*factor,'stable-exit')
    assert sell['filled_qty']==qty and not exchange.positions
    assert exchange.cash==pytest.approx(100000+qty*100*(entry*factor-entry)-qty*1.3)
    with pytest.raises(ValueError):exchange.submit(symbol,'SELL',1,entry,'duplicate-exit')

def test_partial_fill_cancel_race_and_reservations():
    x=OptionsExchange(cash=1000);s='SPY-OPTION'
    x.add_contract(Contract(s,'SPY',date.today()+timedelta(days=10),'CALL',200),1,1,1)
    order=x.submit(s,'BUY',3,1,'entry');assert order['filled_qty']==1
    x.cancel(order['id'],confirmed=False)
    x.quote(s,1,1,1);x.match(order['id']);assert order['filled_qty']==2
    x.cancel(order['id']);x.quote(s,1,1,10);x.match(order['id']);assert order['filled_qty']==2
    sell=x.submit(s,'SELL',2,2,'exit');assert sell['filled_qty']==0
    with pytest.raises(ValueError):x.submit(s,'SELL',1,1,'other-close')
    x.cancel(sell['id']);assert x.submit(s,'SELL',2,1,'confirmed-close')['status']=='filled'

def test_nonmarketable_and_pending_cash():
    x=OptionsExchange(cash=201);s='CALL'
    x.add_contract(Contract(s,'SPY',date.today()+timedelta(days=10),'CALL',200),1,2)
    assert x.submit(s,'BUY',1,1,'first')['filled_qty']==0
    with pytest.raises(ValueError):x.submit(s,'BUY',1,2,'second')

@pytest.mark.parametrize('side,price,status',[('CALL',190,'expired_worthless'),('PUT',210,'expired_worthless'),('CALL',210,'exercise_review_required'),('PUT',190,'exercise_review_required')])
def test_expiry_requires_actual_delivery_review(side,price,status):
    x=OptionsExchange();s='OPTION';expiry=date.today()+timedelta(days=1)
    x.add_contract(Contract(s,'SPY',expiry,side,200),1,1);x.submit(s,'BUY',1,1,'entry')
    cash=x.cash;x.today=expiry+timedelta(days=1)
    assert x.settle(s,price)['status']==status and x.cash==cash
    assert bool(x.positions)==(status=='exercise_review_required')

def test_call_exercise_and_put_delivery():
    x=OptionsExchange();expiry=date.today()+timedelta(days=1)
    for side in ['CALL','PUT']:
        x.add_contract(Contract(side,'SPY',expiry,side,200),1,1);x.submit(side,'BUY',1,1,side)
    x.today=expiry+timedelta(days=1)
    with pytest.raises(ValueError):x.exercise('PUT')
    cash=x.cash;x.exercise('CALL');assert x.shares['SPY']==100 and x.cash==cash-20000
    x.exercise('PUT');assert x.shares['SPY']==0 and x.cash==cash

@pytest.fixture(autouse=True)
def configured_test_creator(monkeypatch):
    monkeypatch.setenv('PATREON_CAMPAIGN_ID','123')
    monkeypatch.setenv('PATREON_ACCESS_TOKEN','synthetic-fixture-token')

def approval(tmp_path):
    from lavish_core.trade.live_readiness import source_config_digest
    now=datetime.now(timezone.utc);checks={}
    for check,sources in REQUIRED.items():
        source=sorted(sources)[0];path=tmp_path/(check+'.json')
        payload=json.dumps(dict(check=check,source=source,synthetic=False,passed=True,observed_at=(now-timedelta(minutes=2)).isoformat(),runtime_sha256=runtime_digest(),account_id_hash=hashlib.sha256(b'test-paper-account').hexdigest(),source_id_hash=source_config_digest())).encode();path.write_bytes(payload)
        checks[check]=dict(status='passed',source=source,evidence_path=path.name,sha256=hashlib.sha256(payload).hexdigest())
    data=dict(schema_version=1,operator_approved=True,issued_at=(now-timedelta(minutes=1)).isoformat(),expires_at=(now+timedelta(days=1)).isoformat(),runtime_sha256=runtime_digest(),source_id_hash=source_config_digest(),live_account_id_hash=hashlib.sha256(b'test-account').hexdigest(),paper_account_id_hash=hashlib.sha256(b'test-paper-account').hexdigest(),limits=dict(max_trade_usd=200,max_gross_exposure=1000,max_daily_loss_usd=100),checks=checks)
    path=tmp_path/'approval.json';path.write_text(json.dumps(data));return path,data

def test_approval_account_limits_and_expiry(tmp_path):
    path,data=approval(tmp_path)
    assert validate_approval(path,account_id='test-account',notional=200)['live_ready']
    assert not validate_approval(path,account_id='wrong')['live_ready']
    assert not validate_approval(path,notional=201)['live_ready']
    assert not validate_approval(path,now=datetime.now(timezone.utc)+timedelta(days=2))['live_ready']
    data['operator_approved']=False;path.write_text(json.dumps(data));assert not validate_approval(path)['live_ready']

@pytest.mark.parametrize('check',list(REQUIRED))
def test_synthetic_evidence_cannot_enable_live(tmp_path,check):
    path,data=approval(tmp_path);evidence=tmp_path/data['checks'][check]['evidence_path']
    body=json.loads(evidence.read_text());body['synthetic']=True;payload=json.dumps(body).encode();evidence.write_bytes(payload)
    data['checks'][check]['sha256']=hashlib.sha256(payload).hexdigest();path.write_text(json.dumps(data))
    assert not validate_approval(path)['live_ready']

@pytest.mark.parametrize('check',list(REQUIRED))
def test_evidence_modified_after_review_blocks(tmp_path,check):
    path,data=approval(tmp_path);(tmp_path/data['checks'][check]['evidence_path']).write_text('{}')
    assert not validate_approval(path)['live_ready']

@pytest.mark.parametrize('age',[-1,61,86400])
def test_stale_or_future_quote(age):
    now=datetime.now(timezone.utc)
    assert not fresh_timestamp((now-timedelta(seconds=age)).isoformat(),now=now)

def test_cross_process_control_with_open_database(tmp_path):
    from lavish_core.db.hybrid_store import HybridStore
    path=tmp_path/'test.duckdb';store=HybridStore(str(path))
    script=Path(__file__).resolve().parents[1]/'kill_switch.py'
    for command,enabled in [('on',True),('off',False)]:
        result=subprocess.run([sys.executable,str(script),*(['--on','test'] if enabled else ['--off']),'--db',str(path)],capture_output=True,text=True)
        assert result.returncode==0,result.stderr
        assert store.get_kill_switch()[0] is enabled
    store.con.close()

def test_corrupt_control_fails_closed(tmp_path):
    path=tmp_path/'db';write_control(path,False,'test');assert read_control(path)[0] is False
    Path(str(path.resolve())+'.control.json').write_text('corrupt');assert read_control(path)[0] is True

@pytest.mark.parametrize('ticker,side,qty,entry,factor',list(product(['SPY','QQQ','AAPL','MSFT'],['CALL','PUT'],[1,2,5],[.5,1,3,10],[.5,1,1.5])))
def test_production_adapter_exit_ledger_replay(tmp_path,ticker,side,qty,entry,factor):
    from lavish_core.validation.options_replay import replay
    result=replay(tmp_path/'replay.duckdb',ticker,side,qty,entry,entry*factor)
    assert result['passed'] and result['synthetic'] and not result['profitability_evidence']
    assert result['orders']==2 and result['fills']==2

@pytest.mark.parametrize('invalid',[None,{},[{'symbol':'SPY','qty':'nan'}],[{'symbol':'SPY','qty':'1'},{'symbol':'SPY','qty':'1'}]])
def test_state_audit_invalid_halts_without_fake_fills(store,invalid):
    from lavish_core.trade.state_audit import audit_state
    result=audit_state(store,invalid)
    assert not result['ok'] and store.get_kill_switch()[0]
    assert store.fetchall('SELECT count(*) FROM fills')[0][0]==0

def test_state_audit_position_drift(store):
    from lavish_core.trade.state_audit import audit_state
    assert audit_state(store,[])['ok']
    assert not audit_state(store,[dict(symbol='SPY',qty=1)])['ok']
    assert store.get_position('SPY') is None

def test_live_buy_blocked_without_approval_before_post(tmp_path,monkeypatch):
    from lavish_core.trade import options_broker as ob
    from unittest.mock import Mock
    monkeypatch.setenv('TRADE_MODE','live');monkeypatch.setenv('LAVISH_READINESS_FILE',str(tmp_path/'missing.json'))
    monkeypatch.setattr(ob,'_check_keys',lambda:None);post=Mock();monkeypatch.setattr(ob.SESSION,'post',post)
    with pytest.raises(RuntimeError):ob.place_option_order('SPY261030C00200000','buy',1,limit_price=1)
    assert not post.called

def test_paper_probe_missing_credentials_is_read_only(monkeypatch):
    from lavish_core.validation.paper_probe import probe
    monkeypatch.setenv('TRADE_MODE','paper');monkeypatch.delenv('ALPACA_API_KEY',raising=False)
    monkeypatch.delenv('ALPACA_SECRET_KEY',raising=False)
    with pytest.raises(ValueError):probe()

@pytest.mark.parametrize('value',['','main','v1','a'*39,'z'*40])
def test_model_revision_requires_immutable_commit(value,monkeypatch):
    from lavish_core.utils.model_revision import required_revision
    monkeypatch.setenv('MODEL_REVISION',value)
    with pytest.raises(ValueError):required_revision('MODEL_REVISION')

@pytest.mark.parametrize('data',[{},[],{'docs':['a'],'meta':[]},{'docs':[1],'meta':[{}]},{'docs':['a'],'meta':['x']}])
def test_vector_cache_rejects_executable_or_invalid_shapes(tmp_path,data):
    from lavish_core.utils.vector_cache import read_memory
    path=tmp_path/'memory.json';path.write_text(json.dumps(data))
    with pytest.raises(ValueError):read_memory(path)

def test_export_analysis_net_costs_drawdown_and_no_approval(tmp_path):
    from lavish_core.validation.trade_export import evaluate
    path=tmp_path/'export.csv';path.write_text('trade_id,entry_at,exit_at,qty,multiplier,entry_price,exit_price,fees\na,2026-01-01T00:00:00Z,2026-01-02T00:00:00Z,1,100,1,2,2\nb,2026-01-03T00:00:00Z,2026-01-04T00:00:00Z,1,100,2,1,2\n')
    result=evaluate(path)
    assert result['net_pnl']==-4 and result['max_realized_drawdown_usd']==102
    assert not result['passed'] and not result['live_ready'] and not result['provenance_verified']

def test_actual_daily_cap_halts_before_live_entry(tmp_path,store,monkeypatch):
    from lavish_core.trade.live_readiness import approved_entry_limits
    path,_=approval(tmp_path);monkeypatch.setenv('LAVISH_READINESS_FILE',str(path))
    store.set_risk_limit('circuit_breaker_day_baseline_equity',1000)
    assert approved_entry_limits(store,100,950,'test-account')['max_daily_loss_usd']==100
    with pytest.raises(RuntimeError):approved_entry_limits(store,100,900,'test-account')
    assert store.get_kill_switch()[0]

@pytest.mark.parametrize('field,value',[('observed_at','2000-01-01T00:00:00Z'),('observed_at','2099-01-01T00:00:00Z'),('observed_at','2026-10-12T00:00:00'),('runtime_sha256','a'*64),('account_id_hash','b'*64)])
def test_approval_requires_recent_same_runtime_paper_evidence(tmp_path,field,value):
    path,data=approval(tmp_path);check='authenticated_paper_lifecycle';record=data['checks'][check];evidence=tmp_path/record['evidence_path']
    body=json.loads(evidence.read_text());body[field]=value;payload=json.dumps(body).encode();evidence.write_bytes(payload)
    record['sha256']=hashlib.sha256(payload).hexdigest();path.write_text(json.dumps(data))
    assert not validate_approval(path)['live_ready']
