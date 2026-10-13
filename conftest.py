"""Repository tests must never contact real feeds, brokers or notifications."""
import socket
import urllib.request
import pytest
import requests


@pytest.fixture(autouse=True)
def block_unmocked_external_io(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("External I/O blocked: provide a synthetic boundary")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    original_connect = socket.socket.connect
    def connect(sock, address):
        if sock.family == socket.AF_UNIX:
            return original_connect(sock, address)
        return blocked(sock, address)
    monkeypatch.setattr(socket.socket, "connect", connect)
    import curl_cffi.requests
    monkeypatch.setattr(curl_cffi.requests.Session, "request", blocked)
