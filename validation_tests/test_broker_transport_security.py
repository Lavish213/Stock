"""Credentials must never follow redirects or reach unapproved origins."""
from unittest.mock import Mock
import pytest
import requests
from lavish_core.trade import broker_alpaca as ba, broker_tradier as bt

@pytest.mark.parametrize('module', [ba, bt])
@pytest.mark.parametrize('url', [
    'https://attacker.example/collect', 'http://paper-api.alpaca.markets/v2/account',
    'https://paper-api.alpaca.markets.attacker.example/collect',
    'https://paper-api.alpaca.markets:8443/v2/account',
    'https://user:password@paper-api.alpaca.markets/v2/account',
])
def test_unapproved_origins_fail_before_transport(monkeypatch, module, url):
    boundary = Mock()
    monkeypatch.setattr(requests.sessions.Session, 'request', boundary)
    with pytest.raises(ValueError):
        module.SESSION.get(url, headers={'APCA-API-SECRET-KEY': 'synthetic'})
    boundary.assert_not_called()

@pytest.mark.parametrize('module,url', [
    (ba, 'https://paper-api.alpaca.markets/v2/account'),
    (ba, 'https://data.alpaca.markets/v2/stocks/quotes/latest'),
    (bt, 'https://sandbox.tradier.com/v1/markets/quotes'),
])
@pytest.mark.parametrize('method', ['get', 'post', 'delete'])
def test_redirects_disabled_for_every_broker_method(monkeypatch, module, url, method):
    boundary = Mock(return_value=Mock(status_code=302))
    monkeypatch.setattr(requests.sessions.Session, 'request', boundary)
    getattr(module.SESSION, method)(url, allow_redirects=True, headers={'Authorization': 'synthetic'})
    assert boundary.call_args.kwargs['allow_redirects'] is False
