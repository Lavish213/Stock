from datetime import date,timedelta
import pytest
from lavish_core.trade import broker_alpaca as ba,options_broker as ob
from lavish_core.validation.options_replay import Response

@pytest.fixture
def contract_boundary(monkeypatch):
    expiry=date.today()+timedelta(days=7)
    row=dict(symbol=ob.build_occ_symbol('AAPL',expiry,'CALL',100),underlying_symbol='AAPL',
             expiration_date=expiry.isoformat(),type='call',strike_price='100',tradable=True,size='100')
    monkeypatch.setenv('TRADE_MODE','paper');monkeypatch.setattr(ba,'API_KEY','synthetic');monkeypatch.setattr(ba,'API_SECRET','synthetic')
    monkeypatch.setattr(ob.SESSION,'get',lambda *a,**k:Response({'option_contracts':[row]}))
    return row,expiry

@pytest.mark.parametrize('fault',['ticker','symbol_ticker','symbol_day','symbol_side','symbol_strike','wrong_strike','missing_strike','nan_strike','zero_strike'])
def test_corrupt_contract_identity_cannot_be_resolved(contract_boundary,fault):
    row,expiry=contract_boundary
    if fault=='ticker':row['underlying_symbol']='NVDA'
    elif fault=='symbol_ticker':row['symbol']=ob.build_occ_symbol('NVDA',expiry,'CALL',100)
    elif fault=='symbol_day':row['symbol']=ob.build_occ_symbol('AAPL',expiry+timedelta(days=7),'CALL',100)
    elif fault=='symbol_side':row['symbol']=ob.build_occ_symbol('AAPL',expiry,'PUT',100)
    elif fault=='symbol_strike':row['symbol']=ob.build_occ_symbol('AAPL',expiry,'CALL',101)
    elif fault=='wrong_strike':row.update(strike_price='101',symbol=ob.build_occ_symbol('AAPL',expiry,'CALL',101))
    elif fault=='missing_strike':row.pop('strike_price')
    elif fault=='nan_strike':row['strike_price']='nan'
    else:row['strike_price']='0'
    assert ob.find_contract('AAPL',expiry,'CALL',100,strike_tolerance=0) is None

def test_exact_contract_and_explicit_tolerance_resolve_correctly(contract_boundary):
    row,expiry=contract_boundary
    assert ob.find_contract('AAPL',expiry,'CALL',100)['symbol']==row['symbol']
    row.update(strike_price='101',symbol=ob.build_occ_symbol('AAPL',expiry,'CALL',101))
    assert ob.find_contract('AAPL',expiry,'CALL',100,strike_tolerance=2)['symbol']==row['symbol']
