from unittest.mock import Mock
import pytest,requests
from lavish_core.trade import trade_agent as ta,reconcile as rc
from lavish_core.trade.exit_state import ExitState

def prepare(monkeypatch):
 monkeypatch.setattr(ta,'_soft_quote',lambda *a:100)
 monkeypatch.setattr(ta,'get_account',lambda:{'equity':100000})
 monkeypatch.setattr(ta,'_circuit_breaker_check_ok',lambda *a:(True,'ok'))

def test_unknown_submission_retains_reservation_across_restart(store,monkeypatch):
 prepare(monkeypatch);submit=Mock(side_effect=requests.Timeout());monkeypatch.setattr(ta,'broker_place_order',submit);monkeypatch.setattr(ta,'broker_get_order_by_client_id',Mock(side_effect=requests.Timeout()))
 result=ta.place_trade(store,'AAPL','buy',1,mode='paper',client_id='unknown')
 assert result['status']=='submission_unknown';assert submit.call_count==1
 assert store.fetchall('SELECT client_id FROM risk_reservations')==[('unknown',)]
 assert not store.fetchall('SELECT * FROM fills')
 from lavish_core.db.hybrid_store import HybridStore
 reopened=HybridStore(store.db_path);assert reopened.get_open_orders()[0]['status']=='submission_unknown';reopened.con.close()

@pytest.mark.parametrize('response',[{}, {'status':'filled','filled_qty':'1','filled_avg_price':'100'},{'id':'o','status':'filled','filled_qty':'2','filled_avg_price':'100'},{'id':'o','status':'filled','filled_qty':'1','filled_avg_price':None}])
def test_invalid_successful_broker_response_is_quarantined(response,store,monkeypatch):
 prepare(monkeypatch);monkeypatch.setattr(ta,'broker_place_order',lambda **k:response)
 result=ta.place_trade(store,'AAPL','buy',1,mode='paper',client_id='malformed')
 assert result['status'] in ('submission_unknown','fill_unverified')
 assert store.fetchall('SELECT count(*) FROM risk_reservations')[0][0]==1
 assert not store.fetchall('SELECT * FROM fills')

def test_reconcile_partial_to_full_is_incremental_and_idempotent(store,monkeypatch):
 limits=ta.RiskLimits();assert store.reserve_entry('recover','AAPL',5,100,limits,100000) is None
 oid=store.submit_order('AAPL','buy',5,venue='paper',status='submission_unknown',client_id='recover')
 response={'id':'broker','status':'partially_filled','filled_qty':'2','filled_avg_price':'100'}
 monkeypatch.setattr(rc,'get_order_by_client_id',lambda *a:response)
 assert rc.reconcile_pending_orders(store)==0;assert store.get_position('AAPL')['qty']==2
 rc.reconcile_pending_orders(store);assert store.get_position('AAPL')['qty']==2
 response.update(status='filled',filled_qty='5',filled_avg_price='106')
 assert rc.reconcile_pending_orders(store)==1;assert store.get_position('AAPL')=={'qty':5.,'avg_price':106.}
 rc.reconcile_pending_orders(store);assert store.fetchall('SELECT count(*) FROM fills')[0][0]==2
 assert not store.fetchall('SELECT * FROM risk_reservations')

@pytest.mark.parametrize('remote',[None,{}, {'id':'remote','status':'filled','filled_qty':None,'filled_avg_price':'100'}])
def test_inconclusive_reconciliation_never_releases_funds(remote,store,monkeypatch):
 assert store.reserve_entry('unresolved','AAPL',1,100,ta.RiskLimits(),100000) is None
 store.submit_order('AAPL','buy',1,venue='paper',status='submission_unknown',client_id='unresolved')
 monkeypatch.setattr(rc,'get_order_by_client_id',lambda *a:remote);rc.reconcile_pending_orders(store)
 assert store.fetchall('SELECT count(*) FROM risk_reservations')[0][0]==1;assert not store.fetchall('SELECT * FROM fills')

def attempt(state,submit,lookup,get,cancel):return state.attempt(submit,lookup,get,cancel,1,lambda:None)

@pytest.mark.parametrize('filled',[0,1,3])
def test_inconsistent_terminal_exit_preserves_exposure_and_halts(store,filled):
 oid=store.submit_order('AAPL','buy',2);store.log_fill(oid,'AAPL','buy',2,100)
 state=ExitState(2,db_path=store.db_path,symbol='AAPL')
 submit=Mock(return_value={'id':'close','status':'filled','filled_qty':filled,'filled_avg_price':110})
 result=attempt(state,submit,Mock(),Mock(),Mock())
 assert result['status']=='fill_unverified'
 assert store.get_position('AAPL')['qty']==2
 assert store.fetchall("SELECT count(*) FROM fills WHERE side='sell'")[0][0]==0
 assert store.get_kill_switch()[0]
 assert submit.call_count==1

def test_inconsistent_terminal_after_partial_fill_does_not_book_more(store):
 oid=store.submit_order('AAPL','buy',3);store.log_fill(oid,'AAPL','buy',3,100)
 state=ExitState(3,db_path=store.db_path,symbol='AAPL')
 submit=Mock(return_value={'id':'close','status':'partially_filled','filled_qty':1,'filled_avg_price':110})
 first=attempt(state,submit,Mock(),lambda _: {'status':'partially_filled','filled_qty':1,'filled_avg_price':110},Mock())
 assert first is None and store.get_position('AAPL')['qty']==2
 result=attempt(state,submit,Mock(),lambda _: {'status':'filled','filled_qty':2,'filled_avg_price':110},Mock())
 assert result['status']=='fill_unverified'
 assert store.get_position('AAPL')['qty']==2
 assert store.fetchall("SELECT sum(qty) FROM fills WHERE side='sell'")[0][0]==1
 assert store.get_kill_switch()[0]
 assert submit.call_count==1

@pytest.mark.parametrize('change',[{'id':'foreign'}, {'symbol':'MSFT'}, {'side':'buy'}, {'client_order_id':'foreign'}, {'qty':2}])
def test_exit_conflicting_poll_identity_is_quarantined_without_fill_or_cancel(store,change):
 oid=store.submit_order('AAPL','buy',1);store.log_fill(oid,'AAPL','buy',1,100)
 state=ExitState(1,db_path=store.db_path,symbol='AAPL')
 def submit(qty,cid):return {'id':'closing','status':'new','symbol':'AAPL','side':'sell','client_order_id':cid,'qty':qty,'filled_qty':0}
 remote={'status':'filled','filled_qty':1,'filled_avg_price':110};remote.update(change)
 cancel=Mock()
 result=attempt(state,submit,Mock(),lambda *a:remote,cancel)
 assert result['status']=='fill_unverified'
 assert store.get_position('AAPL')['qty']==1
 assert store.fetchall("SELECT count(*) FROM fills WHERE side='sell'")[0][0]==0
 assert store.get_kill_switch()[0]
 cancel.assert_not_called()

@pytest.mark.parametrize('cancel',[Mock(return_value=False),Mock(side_effect=requests.Timeout()),Mock(return_value=True)])
def test_exit_never_resubmits_after_unconfirmed_cancellation(cancel):
 state=ExitState(2);submit=Mock(return_value={'id':'pending','status':'pending_new'});get=Mock(return_value={'status':'pending_new'})
 for _ in range(5):assert attempt(state,submit,lambda *a:None,get,cancel) is None
 assert submit.call_count==1

def test_exit_unknown_lookup_failure_never_resubmits():
 state=ExitState(1);submit=Mock(side_effect=requests.Timeout());lookup=Mock(side_effect=requests.Timeout())
 for _ in range(5):assert attempt(state,submit,lookup,Mock(),Mock()) is None
 assert submit.call_count==1

def test_exit_after_partial_cancel_submits_only_remaining_quantity():
 state=ExitState(5);submit=Mock(side_effect=[{'id':'first','status':'canceled','filled_qty':'2','filled_avg_price':'100'},{'id':'second','status':'filled','filled_qty':'3','filled_avg_price':'110'}])
 assert attempt(state,submit,Mock(),Mock(),Mock()) is None
 result=attempt(state,submit,Mock(),Mock(),Mock());assert [call.args[0] for call in submit.call_args_list]==[5,3]
 assert result['filled_qty']==5 and result['price']==106
@pytest.mark.parametrize('field,value',[('filled_qty',None),('filled_qty','nan'),('filled_qty','2'),('filled_avg_price',None),('filled_avg_price','nan'),('filled_avg_price','-1')])
def test_invalid_closing_fill_is_not_estimated(field,value):
 state=ExitState(1);reply={'id':'close','status':'filled','filled_qty':'1','filled_avg_price':'100'};reply[field]=value
 result=attempt(state,lambda *a:reply,Mock(),Mock(),Mock());assert result['status']=='fill_unverified'

def test_durable_exit_can_resume_after_partial_fill_and_restart(store):
 oid=store.submit_order('AAPL','buy',5);store.log_fill(oid,'AAPL','buy',5,100)
 state=ExitState(5,db_path=store.db_path,symbol='AAPL')
 submit=Mock(return_value={'id':'closing','status':'partially_filled','filled_qty':'2','filled_avg_price':'110'})
 assert attempt(state,submit,Mock(),lambda *a:{'status':'partially_filled','filled_qty':'2','filled_avg_price':'110'},Mock(return_value=True)) is None
 assert store.get_position('AAPL')['qty']==3
 restarted=ExitState(3,db_path=store.db_path,symbol='AAPL');no_post=Mock(side_effect=AssertionError('must adopt original close'))
 assert attempt(restarted,no_post,Mock(),Mock(),Mock()) is None
 remote={'id':'closing','status':'filled','filled_qty':'5','filled_avg_price':'112'}
 result=attempt(restarted,no_post,lambda *a:remote,Mock(),Mock())
 assert result['status']=='exited';assert result['filled_qty']==3
 assert store.get_position('AAPL') is None;no_post.assert_not_called()
 assert store.fetchall("SELECT sum(qty) FROM fills WHERE side='sell'")[0][0]==5

def test_restart_quarantines_multiple_open_exit_journals(store,monkeypatch):
 oid=store.submit_order('AAPL','buy',2);store.log_fill(oid,'AAPL','buy',2,100)
 for client_id in ('exit-first','exit-second'):
  store.submit_order(
   'AAPL','sell',2,venue='paper',status='submission_unknown',
   client_id=client_id,meta={'exit_state':True},
  )
 state=ExitState(2,db_path=store.db_path,symbol='AAPL')
 submit=Mock(side_effect=AssertionError('ambiguous journals must never submit'))
 lookup=Mock(side_effect=AssertionError('ambiguous journal identity must be reviewed'))
 result=attempt(state,submit,lookup,Mock(),Mock())
 assert result=={'status':'fill_unverified','reason':'reconciliation_required'}
 assert store.fetchall(
  "SELECT status FROM orders WHERE side='sell' ORDER BY client_id"
 )==[('fill_unverified',),('fill_unverified',)]
 assert store.get_position('AAPL')['qty']==2
 assert store.get_kill_switch()[0]
 submit.assert_not_called();lookup.assert_not_called()
 broker_lookup=Mock(return_value={
  'id':'broker-close','status':'filled','symbol':'AAPL','side':'sell',
  'qty':2,'filled_qty':2,'filled_avg_price':110,'filled_fee':0,
 })
 monkeypatch.setattr(rc,'get_order_by_client_id',broker_lookup)
 assert rc.reconcile_pending_orders(store)==0
 assert store.get_position('AAPL')['qty']==2
 assert not store.fetchall("SELECT 1 FROM fills WHERE side='sell'")
 broker_lookup.assert_not_called()

@pytest.mark.parametrize(
 ('venue','client_id'),
 [('live','live-exit'),('paper',None)],
 ids=['different-venue','missing-client-id'],
)
def test_restart_rejects_exit_journal_with_untrusted_local_identity(store,venue,client_id):
 oid=store.submit_order('AAPL','buy',1);store.log_fill(oid,'AAPL','buy',1,100)
 close=store.submit_order(
  'AAPL','sell',1,venue=venue,status='submission_unknown',
  client_id=client_id,meta={'exit_state':True},
 )
 state=ExitState(1,db_path=store.db_path,symbol='AAPL',venue='paper')
 submit=Mock(side_effect=AssertionError('cross-venue journal must never submit'))
 lookup=Mock(side_effect=AssertionError('cross-venue journal must never be queried'))
 result=attempt(state,submit,lookup,Mock(),Mock())
 assert result=={'status':'fill_unverified','reason':'reconciliation_required'}
 assert store.fetchall("SELECT status FROM orders WHERE id=?",(close,))==[('fill_unverified',)]
 assert store.get_position('AAPL')['qty']==1
 assert store.get_kill_switch()[0]
 submit.assert_not_called();lookup.assert_not_called()

def test_report_matches_entry_before_window_to_exit_inside_window(store,monkeypatch):
 from datetime import datetime,timezone,timedelta
 from lavish_core.reporting import track_record as report
 monkeypatch.setattr(report,'fetch_price_history',lambda *a:None)
 entry=store.submit_order('AAPL','buy',2);fill=store.log_fill(entry,'AAPL','buy',2,100,fee=2)
 old=datetime.now(timezone.utc)-timedelta(days=60)
 store.execute('UPDATE fills SET ts=? WHERE id=?',(old,fill))
 exit_order=store.submit_order('AAPL','sell',2);store.log_fill(exit_order,'AAPL','sell',2,110,fee=2)
 result=report.build_report(store,days=30)
 assert result['closed_round_trips']==1 and result['total_pnl']==16
 assert result['open_entries_no_recorded_exit']==0

@pytest.mark.parametrize('status',['pending_submit','submission_unknown','fill_unverified'])
def test_new_client_id_cannot_bypass_same_symbol_quarantine(status,store):
 store.submit_order('AAPL','buy',1,venue='paper',status=status,client_id='old')
 assert store.reserve_entry('new','AAPL',1,100,ta.RiskLimits(),100000)=='unresolved_order_requires_reconciliation'
 assert not store.fetchall('SELECT * FROM risk_reservations')

def test_replayed_fill_releases_temporary_reservation_without_double_count(store):
 oid=store.submit_order('AAPL','buy',1);store.log_fill(oid,'AAPL','buy',1,100,event_id='entry:broker')
 assert store.reserve_entry('repeat','AAPL',1,100,ta.RiskLimits(),100000) is None
 assert store.log_fill(oid,'AAPL','buy',1,100,event_id='entry:broker',reservation_client_id='repeat') is None
 assert store.get_position('AAPL')['qty']==1
 assert not store.fetchall('SELECT * FROM risk_reservations')

def test_confirmed_absent_alpaca_exit_retry_reuses_client_id():
 state=ExitState(1);submit=Mock(side_effect=[requests.Timeout(),{'id':'o','status':'filled','filled_qty':'1','filled_avg_price':'100'}])
 assert attempt(state,submit,lambda *a:None,Mock(),Mock()) is None
 result=attempt(state,submit,lambda *a:None,Mock(),Mock());assert result['status']=='exited'
 assert submit.call_args_list[0].args[1]==submit.call_args_list[1].args[1]

def test_tradier_exit_does_not_assume_tag_is_idempotent():
 state=ExitState(1);state.provider='tradier';submit=Mock(side_effect=requests.Timeout())
 for _ in range(4):assert attempt(state,submit,lambda *a:None,Mock(),Mock()) is None
 assert submit.call_count==1

@pytest.mark.parametrize('malicious',[b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss><channel><item><title>&x;</title></item></channel></rss>',b'<!DOCTYPE rss [<!ENTITY x "payload">]><rss><channel><item><title>&x;</title></item></channel></rss>'])
def test_rss_entity_documents_are_rejected(monkeypatch,malicious):
 from lavish_core.data import news_failover as news
 service=object.__new__(news.NewsFailover);service.timeout=1
 monkeypatch.setattr(news,'_req',lambda *a,**k:Mock(content=malicious,text=malicious.decode()))
 assert service._yahoo_rss(1)==[]

def test_plain_rss_still_parses(monkeypatch):
 from lavish_core.data import news_failover as news
 service=object.__new__(news.NewsFailover);service.timeout=1
 text='<rss><channel><item><title>Example headline</title><link>https://example.invalid/article</link></item></channel></rss>'
 monkeypatch.setattr(news,'_req',lambda *a,**k:Mock(content=text.encode(),text=text))
 assert service._yahoo_rss(1)[0]['headline']=='Example headline'

def test_canonical_config_supports_mapping_and_attribute_access(monkeypatch):
 from lavish_core.config import get_settings
 monkeypatch.setenv('NEWS_TIMEOUT','17');monkeypatch.setenv('DB_URL','sqlite:///isolated.db')
 settings=get_settings();assert settings.NEWS_TIMEOUT==17;assert settings.get('DB_URL')=='sqlite:///isolated.db'
@pytest.mark.parametrize('timeout',['0','-1','nan'])
def test_invalid_network_timeouts_fail_configuration(monkeypatch,timeout):
 from lavish_core.config import get_settings
 monkeypatch.setenv('NEWS_TIMEOUT',timeout)
 with pytest.raises(ValueError):get_settings()
