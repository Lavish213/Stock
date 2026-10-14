import math
from datetime import date
from unittest.mock import Mock
import pytest,requests
from lavish_core.trade import broker_alpaca as ba,options_broker as ob,trade_agent as ta
def response(status,body=None):
 r=Mock(status_code=status,text='synthetic fault response');r.json.return_value={} if body is None else body;return r
@pytest.fixture(autouse=True)
def dummy_keys(monkeypatch):monkeypatch.setattr(ba,'API_KEY','dummy');monkeypatch.setattr(ba,'API_SECRET','dummy')
GETTERS=[('account',ba.get_account,()),('clock',ba.get_clock,()),('positions',ba.get_positions,()),('position',ba.get_position,('AAPL',)),('order',ba.get_order,('id',)),('client_lookup',ba.get_order_by_client_id,('client',))]
@pytest.mark.parametrize('label,function,args',GETTERS,ids=[v[0] for v in GETTERS])
@pytest.mark.parametrize('status',[400,401,403,404,408,409,422,429,500,502,503,504])
def test_get_http_faults_are_not_success(label,function,args,status,monkeypatch):
 monkeypatch.setattr(ba.SESSION,'get',lambda *a,**k:response(status))
 if label in ('position','client_lookup') and status==404:assert function(*args) is None
 else:
  with pytest.raises(RuntimeError):function(*args)
@pytest.mark.parametrize('label,function,args',GETTERS,ids=[v[0] for v in GETTERS])
@pytest.mark.parametrize('error',[requests.Timeout,requests.ConnectionError,requests.exceptions.SSLError])
def test_transport_failure_propagates_to_risk_caller(label,function,args,error,monkeypatch):
 monkeypatch.setattr(ba.SESSION,'get',Mock(side_effect=error('fault')))
 with pytest.raises(error):function(*args)
@pytest.mark.parametrize('status',[200,201,400,401,403,404,408,409,422,429,500,502,503,504])
@pytest.mark.parametrize('instrument',['equity','option'])
def test_post_http_status_matrix(status,instrument,monkeypatch):
 post=Mock(return_value=response(status,{'id':'order','status':'accepted'}));monkeypatch.setattr(ba.SESSION,'post',post)
 fn=(lambda:ba.place_order('AAPL','buy',1,client_order_id='validation')) if instrument=='equity' else (lambda:ob.place_option_order('AAPL261016C00200000','buy',1,limit_price=1,client_order_id='validation'))
 if status in (200,201):assert fn()['id']=='order'
 else:
  with pytest.raises(RuntimeError):fn()
 assert post.call_count==1
@pytest.mark.parametrize('status',[200,204,400,401,403,404,408,422,429,500,503])
def test_cancel_http_status_matrix(status,monkeypatch):
 monkeypatch.setattr(ba.SESSION,'delete',lambda *a,**k:response(status))
 if status in (200,204):assert ba.cancel_order('id') is True
 elif status in (404,422):assert ba.cancel_order('id') is False
 else:
  with pytest.raises(RuntimeError):ba.cancel_order('id')
@pytest.mark.parametrize('price',[-1,0,float('nan'),float('inf'),-float('inf'),'bad',None])
def test_bad_trade_quote_rejected(price,monkeypatch):
 monkeypatch.setattr(ba.SESSION,'get',lambda *a,**k:response(200,{'trade':{'p':price}}));monkeypatch.setattr(ba,'_yfinance_fallback_quote',lambda *a:None)
 v=ba.latest_quote('AAPL');assert v is None or (math.isfinite(v) and v>0)
@pytest.mark.parametrize('bid,ask',[(102,100),(-1,100),(100,-1),(float('nan'),100),(100,float('inf')),(0,100),(100,0),('100','102')])
def test_equity_midpoint_requires_valid_uncrossed_quote(bid,ask,monkeypatch):
 monkeypatch.setattr(ba.SESSION,'get',lambda *a,**k:response(200,{'quote':{'bp':bid,'ap':ask}}));monkeypatch.setattr(ba,'_yfinance_fallback_quote',lambda *a:None);assert ba.latest_quote('AAPL') is None
@pytest.mark.parametrize('qty',[0,-1,float('nan'),float('inf'),-float('inf')])
@pytest.mark.parametrize('instrument',['equity','option'])
def test_invalid_order_quantity_never_reaches_post(qty,instrument,monkeypatch):
 post=Mock(return_value=response(201,{'id':'fake','status':'accepted'}));monkeypatch.setattr(ba.SESSION,'post',post)
 try:
  if instrument=='equity':ba.place_order('AAPL','buy',qty)
  else:ob.place_option_order('AAPL261016C00200000','buy',qty,limit_price=1)
 except (ValueError,TypeError,OverflowError):pass
 assert not post.called
@pytest.mark.parametrize('lookup',['found','not_found','unavailable'])
@pytest.mark.parametrize('fault',[requests.Timeout,requests.ConnectionError])
def test_lost_response_retry_does_not_duplicate_unknown_order(lookup,fault,monkeypatch):
 monkeypatch.setattr(ta.time,'sleep',lambda *a:None);submit=Mock(side_effect=fault('response lost'))
 resolver=Mock(return_value={'id':'existing','status':'filled'} if lookup=='found' else None)
 if lookup=='unavailable':resolver.side_effect=requests.Timeout('lookup unavailable')
 monkeypatch.setattr(ta,'broker_get_order_by_client_id',resolver)
 try:ta._submit_with_retries(submit,'stable')
 except RuntimeError:pass
 assert submit.call_count==(1 if lookup in ('found','unavailable') else ta.RETRY_MAX+1)
def test_retry_adapter_never_automatically_retries_post():
 for scheme in ['http://','https://']:
  policy=ba.SESSION.get_adapter(scheme).max_retries;assert 'GET' in policy.allowed_methods and 'POST' not in policy.allowed_methods
@pytest.mark.parametrize('side',['CALL','PUT'])
@pytest.mark.parametrize('strike',[1,1.125,10.5,200,9999.999])
def test_occ_contract_roundtrip(side,strike):
 from lavish_core.trade.reconcile import parse_occ_symbol
 p=parse_occ_symbol(ob.build_occ_symbol('aapl',date(2026,10,16),side,strike));assert p is not None;assert p['ticker']=='AAPL' and p['side']==side;assert p['strike']==pytest.approx(round(strike*1000)/1000)
