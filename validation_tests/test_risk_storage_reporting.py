import math,threading,time
from unittest.mock import Mock
import pytest
from lavish_core.db.hybrid_store import HybridStore
from lavish_core.trade import circuit_breaker as cb,pdt_guard as pdt,portfolio_risk as pr,trade_agent as ta
from lavish_core.reporting import track_record as tr
@pytest.mark.parametrize('equity',[None,0,-1,'nan','inf','-inf','corrupt'])
@pytest.mark.parametrize('gate',['circuit','pdt'])
def test_invalid_account_equity_fails_closed(equity,gate,store,monkeypatch):
 m=cb if gate=='circuit' else pdt;monkeypatch.setattr(m,'get_account',lambda:{'equity':equity});monkeypatch.setattr(m,'post_discord',Mock());a,_=m.check_ok(store) if gate=='circuit' else m.check_pdt_ok(store);assert a is False
@pytest.mark.parametrize('loss',[0,.01,.029,.03,.031,.1])
@pytest.mark.parametrize('window',['day','week'])
def test_drawdown_thresholds(loss,window,store,monkeypatch):
 e=100000*(1-loss);store.set_risk_limits({'circuit_breaker_day_baseline_ts':time.time(),'circuit_breaker_week_baseline_ts':time.time(),'circuit_breaker_day_baseline_equity':100000 if window=='day' else e,'circuit_breaker_week_baseline_equity':100000});monkeypatch.setattr(cb,'get_account',lambda:{'equity':e});monkeypatch.setattr(cb,'post_discord',Mock());a,_=cb.check_ok(store);assert a is (loss<(cb.DAILY_LOSS_LIMIT_PCT if window=='day' else cb.WEEKLY_LOSS_LIMIT_PCT))
@pytest.mark.parametrize('losses',range(8))
def test_loss_streak_and_winning_reset(losses,store,monkeypatch):
 monkeypatch.setattr(cb,'get_account',lambda:{'equity':100000});monkeypatch.setattr(cb,'post_discord',Mock())
 for _ in range(losses):cb.record_trade_outcome(store,-100)
 assert cb.check_ok(store)[0] is (losses<cb.MAX_CONSECUTIVE_LOSSES);cb.record_trade_outcome(store,100);assert cb.check_ok(store)[0] is True
@pytest.mark.parametrize('count',range(6))
@pytest.mark.parametrize('equity',[24000,25000,26000])
def test_pdt_config_threshold_matrix(count,equity,store,monkeypatch):
 monkeypatch.setattr(pdt,'get_account',lambda:{'equity':equity});monkeypatch.setattr(pdt,'count_recent_day_trades',lambda *a:count);assert pdt.check_pdt_ok(store)[0] is (equity>=pdt.PDT_EQUITY_THRESHOLD or count<pdt.PDT_MAX_DAY_TRADES)
@pytest.mark.parametrize('count',range(5))
@pytest.mark.parametrize('option_positions',[False,True])
def test_correlation_counts_distinct_underlyings(count,option_positions,monkeypatch):
 syms=['MSFT','GOOGL','META','AAPL'][:count]
 if option_positions:syms=[s+'261016C00200000' for s in syms]
 assert pr.check_correlation_ok('AAPL',get_positions_fn=lambda:[{'symbol':s,'qty':1} for s in syms])[0] is (len([s for s in syms if not s.startswith('AAPL')])<pr.MAX_CORRELATED_POSITIONS)
@pytest.mark.parametrize('qty,price',[(1,100),(5000,1),(5001,1),(1,250001),(1,float('nan')),(float('nan'),100),(1,float('inf')),(-1,100),(0,100),(1,-1),(1,0)])
def test_exposure_boundaries_and_invalid_numbers(qty,price,store):
 limits=ta.RiskLimits(max_gross_exposure=250000,max_pos_per_symbol=5000,max_leverage=2);v=ta.would_violate_limits(store,'AAPL','buy',qty,price,limits,account_equity=100000);invalid=not math.isfinite(qty) or not math.isfinite(price) or qty<=0 or price<=0;assert bool(v) is (invalid or qty>5000 or qty*price>200000)
@pytest.mark.parametrize('held,reduce_qty',[(100,1),(100,50),(100,99)])
def test_over_limit_portfolio_can_still_reduce_exposure(held,reduce_qty,store):
 store._apply_fill_to_position('AAPL','buy',held,100);assert ta.would_violate_limits(store,'AAPL','sell',reduce_qty,100,ta.RiskLimits(max_gross_exposure=1000,max_pos_per_symbol=5000,max_leverage=2),account_equity=100000) is None
@pytest.mark.parametrize('workers',[2,8,16,32])
def test_concurrent_signal_claim_has_exactly_one_winner(workers,tmp_path):
 path=tmp_path/'claims.duckdb';root=HybridStore(duckdb_path=path);barrier=threading.Barrier(workers);results=[];errors=[]
 def worker():
  local=None
  try:local=HybridStore(duckdb_path=path);barrier.wait(timeout=8);results.append(local.try_claim_signal('AAPL|buy||',300))
  except Exception as e:errors.append(repr(e))
  finally:
   if local is not None:local.con.close()
 threads=[threading.Thread(target=worker) for _ in range(workers)]
 for t in threads:t.start()
 for t in threads:t.join(timeout=10)
 assert not any(t.is_alive() for t in threads);assert not errors;assert sum(results)==1 and len(results)==workers;root.con.close()
@pytest.mark.parametrize('workers',[2,8,16,32])
def test_concurrent_position_buys_preserve_quantity_and_basis(workers,tmp_path):
 path=tmp_path/'fills.duckdb';root=HybridStore(duckdb_path=path);barrier=threading.Barrier(workers);errors=[]
 def worker(i):
  local=None
  try:local=HybridStore(duckdb_path=path);barrier.wait(timeout=8);local._apply_fill_to_position('AAPL','buy',1,100+i)
  except Exception as e:errors.append(repr(e))
  finally:
   if local is not None:local.con.close()
 threads=[threading.Thread(target=worker,args=(i,)) for i in range(workers)]
 for t in threads:t.start()
 for t in threads:t.join(timeout=10)
 assert not any(t.is_alive() for t in threads);assert not errors;p=root.get_position('AAPL');assert p['qty']==workers;assert p['avg_price']==pytest.approx(100+(workers-1)/2);root.con.close()
def test_failed_position_write_does_not_leave_a_committed_fill(store,monkeypatch):
 o=store.submit_order('AAPL','buy',1);monkeypatch.setattr(store,'_apply_fill_to_position',Mock(side_effect=RuntimeError('disk failure')))
 with pytest.raises(RuntimeError):store.log_fill(o,'AAPL','buy',1,100)
 assert store.fetchall('SELECT count(*) FROM fills')[0][0]==0;assert store.fetchall('SELECT status FROM orders WHERE id=?',(o,))[0][0]!='filled'
def test_restart_retains_claim_seen_cursor_kill_switch_and_positions(tmp_path):
 p=tmp_path/'restart.duckdb';s=HybridStore(duckdb_path=p);s.try_claim_signal('AAPL|buy||',300);s.mark_ingest_seen('patreon','item');s.set_kill_switch(True,'validation');s._apply_fill_to_position('AAPL','buy',2,100);s.con.close();s=HybridStore(duckdb_path=p);assert not s.try_claim_signal('AAPL|buy||',300);assert s.get_kill_switch()==(True,'validation');assert s.get_position('AAPL')['qty']==2;assert s.fetchall("SELECT count(*) FROM ingest_seen WHERE item_id='item'")[0][0]==1;s.con.close()
@pytest.mark.parametrize('symbol,multiplier',[('AAPL',1),('AAPL261016C00200000',100)])
@pytest.mark.parametrize('exit_price',[90,100,110])
def test_report_roundtrip_pnl(symbol,multiplier,exit_price,store,monkeypatch):
 monkeypatch.setattr(tr,'fetch_price_history',lambda *a:None);o=store.submit_order(symbol,'buy',2);store.log_fill(o,symbol,'buy',2,100);store.log_fill(o,symbol,'sell',2,exit_price);r=tr.build_report(store);assert r['closed_round_trips']==1;assert r['total_pnl']==(exit_price-100)*2*multiplier
def test_report_links_separate_equity_entry_and_exit_orders(store,monkeypatch):
 monkeypatch.setattr(tr,'fetch_price_history',lambda *a:None);a=store.submit_order('AAPL','buy',2);b=store.submit_order('AAPL','sell',2);store.log_fill(a,'AAPL','buy',2,100);store.log_fill(b,'AAPL','sell',2,110);r=tr.build_report(store);assert r['closed_round_trips']==1 and r['total_pnl']==20;assert r['open_entries_no_recorded_exit']==0
def test_report_partial_exit_keeps_residual_open_quantity(store,monkeypatch):
 monkeypatch.setattr(tr,'fetch_price_history',lambda *a:None);o=store.submit_order('AAPL','buy',10);store.log_fill(o,'AAPL','buy',10,100);store.log_fill(o,'AAPL','sell',4,110);r=tr.build_report(store);assert r['total_pnl']==40;assert r['open_entries_no_recorded_exit']==1;assert r['open_entries'][0]['qty']==6
def test_report_net_pnl_includes_recorded_fees(store,monkeypatch):
 monkeypatch.setattr(tr,'fetch_price_history',lambda *a:None);o=store.submit_order('AAPL','buy',1);store.log_fill(o,'AAPL','buy',1,100,fee=1);store.log_fill(o,'AAPL','sell',1,110,fee=1);assert tr.build_report(store)['total_pnl']==8
