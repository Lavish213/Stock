import threading
from datetime import date,datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from lavish_core.db.hybrid_store import HybridStore
from lavish_core.trade import candle_gatekeeper as gk
from lavish_core.trading import trade_handler as th
class Clock:
 def __init__(self):self.value=0
 def time(self):self.value+=1;return self.value
 def sleep(self,*a):pass
BASE=datetime.now(timezone.utc).replace(second=0,microsecond=0)-timedelta(minutes=4)
def bar(t,close=100,high=101,low=99):
 # Fixture candles are closed real ISO timestamps; future corruption stays future.
 if t.startswith('2026-10-12T14:'):t=(BASE+timedelta(minutes=int(t[14:16]))).isoformat()
 return {'t':t,'o':100,'h':high,'l':low,'c':close,'v':1000}
@pytest.mark.parametrize('direction',['bullish','bearish'])
@pytest.mark.parametrize('close,expected',[(98,'down'),(99,'inside'),(100,'inside'),(101,'inside'),(102,'up')])
def test_gate_direction_and_wick_boundary(direction,close,expected,monkeypatch):
 monkeypatch.setattr(gk,'time',Clock());ref=bar('2026-10-12T14:00:00Z');n=bar('2026-10-12T14:01:00Z',close,high=max(101,close),low=min(99,close));responses=iter([[ref],[ref,n]]);monkeypatch.setattr(gk,'get_bars',lambda *a,**k:next(responses,[ref,n]));r=gk.confirm_entry('AAPL',direction,max_wait_seconds=10);assert r['confirmed'] is ((direction=='bullish' and expected=='up') or (direction=='bearish' and expected=='down'))
@pytest.mark.parametrize('corruption',['infinite_close','nan_reference_high','reversed_range','future_timestamp','out_of_order'])
def test_gate_does_not_confirm_corrupt_bars(corruption,monkeypatch):
 monkeypatch.setattr(gk,'time',Clock());ref=bar('2026-10-12T14:00:00Z');b=bar('2026-10-12T14:02:00Z',102,high=103);r=[ref,b]
 if corruption=='infinite_close':b['c']=float('inf')
 elif corruption=='nan_reference_high':ref['h']=float('nan')
 elif corruption=='reversed_range':ref['h'],ref['l']=99,101
 elif corruption=='future_timestamp':b['t']='2099-01-01T00:00:00Z'
 elif corruption=='out_of_order':r=[ref,b,bar('2026-10-12T14:01:00Z',98,low=97)]
 responses=iter([[ref],r]);monkeypatch.setattr(gk,'get_bars',lambda *a,**k:next(responses,r));assert gk.confirm_entry('AAPL','bullish',max_wait_seconds=12)['confirmed'] is False
@pytest.mark.parametrize('change',['kill_switch','market_closed'])
def test_actual_gate_thread_rechecks_safety_after_wait(change,paper_boundary,monkeypatch):
 monkeypatch.setattr(th,'GATEKEEPER_ENABLED',True);monkeypatch.setattr(th,'confirm_entry',gk.confirm_entry);monkeypatch.setattr(gk,'time',Clock());waiting,release=threading.Event(),threading.Event();threads=[];calls=[0]
 def get_bars(*a,**k):
  calls[0]+=1;ref=bar('2026-10-12T14:00:00Z')
  if calls[0]==1:return [ref]
  waiting.set();assert release.wait(timeout=4);return [ref,bar('2026-10-12T14:01:00Z',102,high=103)]
 def spawn(*a,**k):t=threading.Thread(*a,**k);threads.append(t);return t
 monkeypatch.setattr(gk,'get_bars',get_bars);monkeypatch.setattr(th,'threading',SimpleNamespace(Thread=spawn));th.execute_trade_from_post({'symbol':'AAPL','action':'BUY','confidence':.9,'source':'validation'});assert waiting.wait(timeout=4)
 try:
  if change=='kill_switch':
   s=HybridStore(duckdb_path=th.DEFAULT_DB);s.set_kill_switch(True,'during wait');s.con.close()
  else:monkeypatch.setattr(th,'get_clock',lambda:{'is_open':False})
 finally:
  release.set()
  for t in threads:t.join(timeout=4)
 assert not any(t.is_alive() for t in threads);assert not paper_boundary.called
@pytest.mark.parametrize('status,filled',[('filled',2),('partially_filled',1),('accepted',None),('pending_new',None),('rejected',None),('canceled',None),('expired',None)])
def test_option_fill_state_monitoring(status,filled,paper_boundary,monkeypatch):
 monkeypatch.setattr(th.time,'sleep',lambda *a:None);monkeypatch.setattr(th,'resolve_and_price_contract',lambda *a,**k:{'symbol':'AAPL261016C00200000','mid_price':1.,'bid':.98,'ask':1.02});r={'id':'synthetic-option','status':status,'filled_qty':str(filled) if filled else None,'filled_avg_price':'1' if filled else None};monkeypatch.setattr(th,'place_option_order',Mock(return_value=r));monkeypatch.setattr(th,'broker_get_order',lambda *a:r);cancel=Mock(return_value=True);monkeypatch.setattr(th,'broker_cancel_order',cancel);th.execute_trade_from_post({'ticker':'AAPL','side':'CALL','strike':200,'expiry':(date.today()+timedelta(days=4)).isoformat(),'confidence':.9,'amount_usd':200,'source':'validation'})
 if filled:assert th.watch_and_exit_async.call_count==1;assert th.watch_and_exit_async.call_args.kwargs['qty']==filled
 else:assert not th.watch_and_exit_async.called
 if status!='filled':assert cancel.called
@pytest.mark.parametrize('field,value',[('filled_qty',None),('filled_qty','0'),('filled_qty','-1'),('filled_qty','nan'),('filled_qty','inf'),('filled_avg_price',None),('filled_avg_price','-1'),('filled_avg_price','nan'),('filled_avg_price','inf')])
def test_invalid_confirmed_option_fill_not_invented(field,value,paper_boundary,monkeypatch):
 monkeypatch.setattr(th,'resolve_and_price_contract',lambda *a,**k:{'symbol':'AAPL261016C00200000','mid_price':1.,'bid':.98,'ask':1.02});r={'id':'synthetic-option','status':'filled','filled_qty':'1','filled_avg_price':'1'};r[field]=value;monkeypatch.setattr(th,'place_option_order',lambda **k:r);th.execute_trade_from_post({'ticker':'AAPL','side':'CALL','strike':200,'expiry':(date.today()+timedelta(days=4)).isoformat(),'confidence':.9});assert not th.watch_and_exit_async.called
def test_duplicate_option_exit_callback_is_idempotent(paper_boundary,monkeypatch):
 monkeypatch.setattr(th,'resolve_and_price_contract',lambda *a,**k:{'symbol':'AAPL261016C00200000','mid_price':1.,'bid':.98,'ask':1.02});monkeypatch.setattr(th,'place_option_order',lambda **k:{'id':'synthetic-option','status':'filled','filled_qty':'1','filled_avg_price':'1'});th.execute_trade_from_post({'ticker':'AAPL','side':'CALL','strike':200,'expiry':(date.today()+timedelta(days=4)).isoformat(),'confidence':.9});cb=th.watch_and_exit_async.call_args.kwargs['on_exit'];cb({'status':'exited','option_price':1.2,'reason':'validation'});cb({'status':'exited','option_price':1.2,'reason':'replay'});s=HybridStore(duckdb_path=th.DEFAULT_DB);assert s.get_position('AAPL261016C00200000') is None;assert s.fetchall("SELECT count(*) FROM fills WHERE side='sell'")[0][0]==1;s.con.close()
@pytest.mark.parametrize('expiry',['2020-01-01','not-a-date','2026-02-30'])
def test_expired_or_invalid_option_never_submits(expiry,paper_boundary,monkeypatch):
 monkeypatch.setattr(th,'resolve_and_price_contract',Mock(return_value={'symbol':'AAPL200101C00200000','mid_price':1.,'bid':.98,'ask':1.02}));d=Mock(return_value={'id':'invalid','status':'rejected'});monkeypatch.setattr(th,'place_option_order',d);monkeypatch.setattr(th.time,'sleep',lambda *a:None);monkeypatch.setattr(th,'broker_get_order',lambda *a:{'status':'rejected'});monkeypatch.setattr(th,'broker_cancel_order',Mock());th.execute_trade_from_post({'ticker':'AAPL','side':'CALL','strike':200,'expiry':expiry,'confidence':.9});assert not d.called
