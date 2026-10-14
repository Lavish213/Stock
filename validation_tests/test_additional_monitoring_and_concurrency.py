import os,subprocess,sys,threading
from pathlib import Path
from unittest.mock import Mock
import pytest
from lavish_core.db.hybrid_store import HybridStore
from lavish_core.trade import account_monitor as am,equity_exit_monitor as em,trade_agent as ta,broker_alpaca as ba,options_broker as ob
@pytest.mark.parametrize('field,value',[(f,v) for f in ['equity','cash','buying_power'] for v in ['nan','inf','corrupt',None]])
def test_corrupt_account_snapshot_not_persisted(field,value,store,monkeypatch):
 a={'equity':100000,'cash':100000,'buying_power':200000,'last_equity':99000};a[field]=value;monkeypatch.setattr(am,'get_account',lambda:a)
 try:am.snapshot_once(store)
 except (ValueError,TypeError):pass
 assert store.fetchall('SELECT count(*) FROM account_snapshots')[0][0]==0
def test_account_snapshot_preserves_day_and_cumulative_pnl(store,monkeypatch):
 a=iter([{'equity':100000,'cash':90000,'buying_power':180000,'last_equity':99000},{'equity':101000,'cash':91000,'buying_power':182000,'last_equity':100500}]);monkeypatch.setattr(am,'get_account',lambda:next(a));am.snapshot_once(store);am.snapshot_once(store);assert store.fetchall('SELECT equity,day_pl,cumulative_pl FROM account_snapshots ORDER BY ts')==[(100000.,1000.,0.),(101000.,500.,1000.)]
@pytest.mark.parametrize('field,value',[('filled_qty',None),('filled_qty','0'),('filled_qty','-1'),('filled_qty','nan'),('filled_avg_price',None),('filled_avg_price','0'),('filled_avg_price','-1'),('filled_avg_price','nan'),('filled_avg_price','inf')])
def test_bad_bracket_fill_not_recorded_as_real_exit(field,value,store,monkeypatch):
 monkeypatch.setattr(em,'DEFAULT_DB',store.db_path);o=store.submit_order('AAPL','buy',1);store.log_fill(o,'AAPL','buy',1,100);r={'status':'filled','filled_qty':'1','filled_avg_price':'110'};r[field]=value;monkeypatch.setattr(em,'get_order',lambda *a:r)
 try:em.watch_bracket(o,'AAPL',1,100,['leg'])
 except (ValueError,TypeError):pass
 assert store.fetchall("SELECT count(*) FROM fills WHERE side='sell'")[0][0]==0
def test_duplicate_bracket_watch_does_not_duplicate_exit_fill(store,monkeypatch):
 monkeypatch.setattr(em,'DEFAULT_DB',store.db_path);o=store.submit_order('AAPL','buy',1);store.log_fill(o,'AAPL','buy',1,100);monkeypatch.setattr(em,'get_order',lambda *a:{'status':'filled','filled_qty':'1','filled_avg_price':'110'});em.watch_bracket(o,'AAPL',1,100,['same-leg']);em.watch_bracket(o,'AAPL',1,100,['same-leg']);assert store.get_position('AAPL') is None;assert store.fetchall("SELECT count(*) FROM fills WHERE side='sell'")[0][0]==1
def test_simultaneous_entries_respect_combined_portfolio_budget(tmp_path,monkeypatch):
 path=tmp_path/'race.duckdb';root=HybridStore(duckdb_path=path);root.set_risk_limits({'max_gross_exposure':150,'max_pos_per_symbol':5000,'max_leverage':2});monkeypatch.setattr(ta,'_circuit_breaker_check_ok',lambda *a:(True,'ok'));monkeypatch.setattr(ta,'get_account',lambda:{'equity':100000});monkeypatch.setattr(ta,'_soft_quote',lambda *a:100);original=ta.would_violate_limits;barrier=threading.Barrier(2)
 def check(*a,**k):r=original(*a,**k);barrier.wait(timeout=4);return r
 monkeypatch.setattr(ta,'would_violate_limits',check);submissions=[]
 def submit(**k):submissions.append(k);return {'id':'synthetic-'+k['symbol'],'status':'filled','filled_qty':'1','filled_avg_price':'100'}
 monkeypatch.setattr(ta,'broker_place_order',submit);errors=[]
 def worker(symbol):
  local=None
  try:local=HybridStore(duckdb_path=path);ta.place_trade(local,symbol,'buy',1,mode='paper',client_id='validation-'+symbol)
  except Exception as e:errors.append(repr(e))
  finally:
   if local is not None:local.con.close()
 threads=[threading.Thread(target=worker,args=(s,)) for s in ['AAPL','MSFT']]
 for t in threads:t.start()
 for t in threads:t.join(timeout=6)
 assert not any(t.is_alive() for t in threads);assert not errors;exposure=sum(q*p for q,p in root.fetchall('SELECT qty,avg_price FROM positions'));root.con.close();assert len(submissions)<=1 and exposure<=150
@pytest.mark.parametrize('limit',[100,200,1000])
def test_equity_budget_smaller_than_one_share_cannot_force_purchase(limit,paper_boundary,monkeypatch):
 from lavish_core.trading import trade_handler as th
 monkeypatch.setattr(th,'latest_quote',lambda *a:limit*2);th.execute_trade_from_post({'symbol':'AAPL','action':'BUY','confidence':.9,'amount_usd':limit});assert not paper_boundary.called
@pytest.mark.parametrize('base_url',['https://paper-api.alpaca.markets','https://paper-api.alpaca.markets/v2','https://paper-api.alpaca.markets/v2/'])
def test_documented_broker_base_url_normalization(base_url):
 e=dict(os.environ,ALPACA_BASE_URL=base_url,ALPACA_API_KEY='dummy',ALPACA_SECRET_KEY='dummy',TRADE_MODE='dry',DISCORD_WEBHOOK_URL='');r=subprocess.run([sys.executable,'-c','from lavish_core.trade.broker_alpaca import ORDERS_URL; print(ORDERS_URL)'],capture_output=True,text=True,env=e,timeout=10,check=True);assert r.stdout.strip()=='https://paper-api.alpaca.markets/v2/orders'
@pytest.mark.parametrize('instrument',['equity','option'])
def test_paper_mode_refuses_production_broker_endpoint(instrument,monkeypatch):
 monkeypatch.setenv('TRADE_MODE','paper');monkeypatch.setattr(ba,'API_KEY','dummy');monkeypatch.setattr(ba,'API_SECRET','dummy');monkeypatch.setattr(ba,'ORDERS_URL','https://api.alpaca.markets/v2/orders');monkeypatch.setattr(ob,'ORDERS_URL','https://api.alpaca.markets/v2/orders');r=Mock(status_code=201);r.json.return_value={'id':'fake','status':'accepted'};p=Mock(return_value=r);monkeypatch.setattr(ba.SESSION,'post',p)
 try:
  if instrument=='equity':ba.place_order('AAPL','buy',1)
  else:ob.place_option_order('AAPL261016C00200000','buy',1,limit_price=1)
 except (RuntimeError,ValueError):pass
 assert not p.called
def test_preflight_fails_when_paper_mode_points_to_live_endpoint():
 root=Path(__file__).resolve().parents[1];e=dict(os.environ,ALPACA_BASE_URL='https://api.alpaca.markets',ALPACA_API_KEY='dummy',ALPACA_SECRET_KEY='dummy',PATREON_ACCESS_TOKEN='dummy',TRADE_MODE='paper',DISCORD_WEBHOOK_URL='');r=subprocess.run([sys.executable,str(root/'system_boot.py')],cwd=root,env=e,capture_output=True,text=True,timeout=10);assert r.returncode!=0
