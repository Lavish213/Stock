import math
from unittest.mock import Mock
import pytest
from hypothesis import given,settings,strategies as st
from lavish_core.trading import alert_handler as ah,trade_handler as th
from lavish_core.vision.extract_signal import parse_alert,parse_text
NONACTIONABLE=['Do not buy AAPL',"Don't buy AAPL",'Never buy AAPL','Do not sell AAPL','AAPL watch only, no entry','AAPL no trade','If AAPL breaks 200, buy it','Buy AAPL only if it breaks 200','I might buy AAPL tomorrow','Should I buy AAPL?','AAPL buy?','AAPL close to resistance','keeping an eye on AAPL into the close','AAPL long term outlook',"AAPL calls were yesterday's trade",'AAPL buy order canceled','Cancel my AAPL buy','AAPL buy signal was wrong, ignore it','AAPL failed entry, stay out','AAPL sell was a mistake, disregard','AAPL do not enter','AAPL exit strategy discussion']
@pytest.mark.parametrize('text',NONACTIONABLE)
@pytest.mark.parametrize('variant',['original','lower','upper','multiline'])
def test_nonactionable_text_never_reaches_execution(text,variant,monkeypatch):
 text={'original':lambda s:s,'lower':str.lower,'upper':str.upper,'multiline':lambda s:s.replace(' ','\n')}[variant](text)
 d=Mock();monkeypatch.setattr(ah,'execute_trade_from_post',d);ah.handle_alert_text(text,source='adversarial');assert not d.called
@pytest.mark.parametrize('text',['AAPL calls','AAPL puts','AAPL 200 calls','AAPL 200 puts','BUY AAPL 200 calls','SELL AAPL 200 puts','AAPL calls 200 exp 12/31/2028','AAPL $0 calls','BUY AAPL puts exp 10/99','AAPL -200 calls'])
def test_incomplete_option_never_becomes_equity(text,monkeypatch):
 d=Mock();monkeypatch.setattr(ah,'execute_trade_from_post',d);ah.handle_alert_text(text,source='adversarial')
 if d.called:
  s=d.call_args.args[0];assert s.get('side') in ('CALL','PUT') and s.get('strike') and s.get('expiry')
@pytest.mark.parametrize('text,expected',[('AAPL grabbing shares here','BUY'),('adding AAPL on this pullback','BUY'),('AAPL trimming the position here','SELL'),('buying AAPL now','BUY'),('selling AAPL now','SELL'),('BUY AAPL now','BUY')])
def test_plain_share_intent_reaches_correct_execution(text,expected,monkeypatch):
 d=Mock();monkeypatch.setattr(ah,'execute_trade_from_post',d);ah.handle_alert_text(text,source='adversarial');assert d.call_count==1;assert d.call_args.args[0]['action']==expected
@pytest.mark.parametrize('field,value',[(f,v) for f in ['confidence','amount_usd'] for v in [float('nan'),float('inf'),-float('inf'),-1,0,'corrupt',{},[]]]+[('confidence',1.1),('confidence','nan'),('amount_usd','inf')])
def test_bad_numeric_signal_cannot_submit(field,value,paper_boundary):
 s={'symbol':'AAPL','action':'BUY','source':'validation','confidence':.9};s[field]=value;th.execute_trade_from_post(s);assert not paper_boundary.called
@pytest.mark.parametrize('value',[None,0,-1,float('nan'),float('inf'),-float('inf'),'corrupt','100',{},[]])
def test_invalid_quote_never_submits(value,paper_boundary,monkeypatch):
 monkeypatch.setattr(th,'latest_quote',lambda *a:value);th.execute_trade_from_post({'symbol':'AAPL','action':'BUY','confidence':.9,'source':'validation'});assert not paper_boundary.called
@pytest.mark.parametrize('text,image',[('AAPL 200 calls','MSFT 300 puts exp 10/16'),('BUY AAPL','MSFT 300 calls exp 10/16'),('AAPL calls','AAPL 300 puts exp 10/16')])
def test_conflicting_caption_and_image_require_review(text,image,monkeypatch):
 from pathlib import Path
 import lavish_core.vision.extract_signal as es
 monkeypatch.setattr(es,'parse_image',lambda *a:parse_text(image));assert parse_alert(text=text,img_path=Path('synthetic.png')).get('needs_human_review') is True
@given(st.text(max_size=1200))
@settings(max_examples=1500,deadline=None,derandomize=True)
def test_unicode_parser_never_crashes_or_returns_nonfinite_fields(text):
 r=parse_text(text);assert 0<=r['confidence']<=1
 for f in ['strike','entry_price_est','target_hint','stop_hint']:
  if r.get(f) is not None:assert math.isfinite(r[f])
@pytest.mark.parametrize('strike',['9'*400,'1e309','NaN','Infinity','0','-200','1,900.50'])
def test_malformed_option_numbers_do_not_create_equity(strike,monkeypatch):
 d=Mock();monkeypatch.setattr(ah,'execute_trade_from_post',d);ah.handle_alert_text(f'BUY AAPL {strike} calls exp 10/16',source='validation')
 if d.called:
  s=d.call_args.args[0];assert s.get('side') in ('CALL','PUT');assert math.isfinite(s['strike']) and s['strike']>0
