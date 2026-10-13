from itertools import product
import pytest
from lavish_core.validation.alert_following import run_case

@pytest.mark.parametrize('instrument,ticker,budget,trigger',list(product(['equity','call'],['AAPL','NVDA','SPY'],[99,100,250,500],['target','stop'])))
def test_signed_alert_entry_and_exit_at_production_broker_adapter(tmp_path,instrument,ticker,budget,trigger):
    result=run_case(tmp_path/'following.db',instrument=instrument,ticker=ticker,budget=budget,trigger=trigger)
    assert result['passed'] and result['real_broker_orders']==0
    assert result['final_positions']==0

@pytest.mark.parametrize('instrument,age',list(product(['equity','call'],[-5,121,3600])))
def test_late_or_future_synthetic_alert_never_enters(tmp_path,instrument,age):
    result=run_case(tmp_path/'following.db',instrument=instrument,age=age)
    assert not result['entry_expected'] and result['fill_count']==0

@pytest.mark.parametrize('dte',[-1,3,7,21])
def test_call_expiry_and_contract_identity(tmp_path,dte):
    result=run_case(tmp_path/'following.db',instrument='call',dte=dte)
    assert result['passed'] and result['entry_expected'] is (dte>=0)

@pytest.mark.parametrize('instrument',['equity','call'])
def test_closed_market_never_places_synthetic_entry(tmp_path,instrument):
    result=run_case(tmp_path/'following.db',instrument=instrument,market_open=False)
    assert not result['entry_expected'] and result['fill_count']==0
