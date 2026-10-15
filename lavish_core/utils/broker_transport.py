"""Constrain credential-bearing broker HTTP to approved HTTPS origins."""
from urllib.parse import urlsplit
import requests

_HOSTS = {
    'alpaca': frozenset({'paper-api.alpaca.markets', 'api.alpaca.markets', 'data.alpaca.markets'}),
    'tradier': frozenset({'sandbox.tradier.com', 'api.tradier.com'}),
}

class BrokerSession(requests.Session):
    def __init__(self, provider):
        super().__init__()
        self.allowed_hosts = _HOSTS[provider]

    def request(self, method, url, **kwargs):
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.hostname not in self.allowed_hosts
                or parts.port not in (None, 443) or parts.username is not None
                or parts.password is not None or parts.fragment):
            raise ValueError('Unapproved broker HTTP origin')
        # Custom API-key headers are not covered by requests' standard
        # Authorization stripping on redirects. Never follow any redirect.
        kwargs['allow_redirects'] = False
        return super().request(method, url, **kwargs)
