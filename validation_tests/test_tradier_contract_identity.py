from datetime import date, timedelta
import pytest
from lavish_core.trade import options_broker_tradier as ob
from lavish_core.validation.options_replay import Response

@pytest.fixture
def boundary(monkeypatch):
    expiry = date.today() + timedelta(days=7)
    row = dict(symbol=ob.build_occ_symbol('AAPL', expiry, 'CALL', 100),
               option_type='call', strike=100, expiration_date=expiry.isoformat(), underlying='AAPL')
    monkeypatch.setattr(ob, '_check_keys', lambda: None)
    monkeypatch.setattr(ob.SESSION, 'get', lambda *a, **k: Response({'options': {'option': [row]}}))
    return row, expiry

@pytest.mark.parametrize('fault', ['ticker', 'day', 'side', 'symbol_strike', 'wrong_strike',
                                  'missing_symbol', 'missing_strike', 'nan', 'zero', 'metadata_day', 'metadata_ticker'])
def test_invalid_chain_contract_rejected(boundary, fault):
    row, expiry = boundary
    if fault == 'ticker': row['symbol'] = ob.build_occ_symbol('NVDA', expiry, 'CALL', 100)
    elif fault == 'day': row['symbol'] = ob.build_occ_symbol('AAPL', expiry + timedelta(days=1), 'CALL', 100)
    elif fault == 'side': row['symbol'] = ob.build_occ_symbol('AAPL', expiry, 'PUT', 100)
    elif fault == 'symbol_strike': row['symbol'] = ob.build_occ_symbol('AAPL', expiry, 'CALL', 101)
    elif fault == 'wrong_strike': row.update(strike=101, symbol=ob.build_occ_symbol('AAPL', expiry, 'CALL', 101))
    elif fault == 'missing_symbol': row.pop('symbol')
    elif fault == 'missing_strike': row.pop('strike')
    elif fault == 'nan': row['strike'] = 'nan'
    elif fault == 'zero': row['strike'] = 0
    elif fault == 'metadata_day': row['expiration_date'] = (expiry + timedelta(days=1)).isoformat()
    else: row['underlying'] = 'NVDA'
    assert ob.find_contract('AAPL', expiry, 'CALL', 100) is None

def test_valid_exact_and_explicit_tolerance(boundary):
    row, expiry = boundary
    assert ob.find_contract('AAPL', expiry, 'CALL', 100)['symbol'] == row['symbol']
    row.update(strike=101, symbol=ob.build_occ_symbol('AAPL', expiry, 'CALL', 101))
    assert ob.find_contract('AAPL', expiry, 'CALL', 100, 2)['symbol'] == row['symbol']

@pytest.mark.parametrize('payload', ['malformed', {'option': [None, 'invalid', 123]}])
def test_malformed_chain_fails_closed(boundary, monkeypatch, payload):
    _, expiry = boundary
    monkeypatch.setattr(ob.SESSION, 'get', lambda *a, **k: Response({'options': payload}))
    assert ob.find_contract('AAPL', expiry, 'CALL', 100) is None

@pytest.mark.parametrize('single', [True, False])
def test_singleton_and_mixed_chain_preserve_valid_contract(boundary, monkeypatch, single):
    row, expiry = boundary
    payload = row if single else [None, {'option_type': 'call', 'strike': 'nan'}, row]
    monkeypatch.setattr(ob.SESSION, 'get', lambda *a, **k: Response({'options': {'option': payload}}))
    assert ob.find_contract('AAPL', expiry, 'CALL', 100)['symbol'] == row['symbol']

@pytest.mark.parametrize('quote', [
    {'bid': -1, 'ask': 1}, {'bid': 2, 'ask': 1}, {'bid': 'nan', 'ask': 1},
    {'bid': 1, 'ask': 'inf'}, {'bid': True, 'ask': 1}, {'bid': 1, 'ask': None},
])
def test_invalid_option_quote_fails_closed(boundary, monkeypatch, quote):
    monkeypatch.setattr(ob.SESSION, 'get', lambda *a, **k: Response({'quotes': {'quote': quote}}))
    assert ob.latest_option_quote('AAPL260101C00100000') is None

def test_valid_zero_bid_option_quote_is_preserved(boundary, monkeypatch):
    monkeypatch.setattr(ob.SESSION, 'get', lambda *a, **k: Response({'quotes': {'quote': {'bid': 0, 'ask': 0.05}}}))
    assert ob.latest_option_quote('AAPL260101C00100000') == {'bid': 0.0, 'ask': 0.05}
