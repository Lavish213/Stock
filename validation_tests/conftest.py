import socket, urllib.request
from unittest.mock import Mock
import pytest, requests
from lavish_core.db.hybrid_store import HybridStore
from lavish_core.trading import trade_handler as th
@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
 def blocked(*a,**k): raise AssertionError('External I/O blocked by test harness')
 monkeypatch.setenv('TRADE_MODE','dry');monkeypatch.setenv('DISCORD_WEBHOOK_URL','')
 monkeypatch.setattr(requests.sessions.Session,'request',blocked);monkeypatch.setattr(urllib.request,'urlopen',blocked);monkeypatch.setattr(socket.socket,'connect',blocked)
 import curl_cffi.requests
 monkeypatch.setattr(curl_cffi.requests.Session,'request',blocked)
@pytest.fixture
def store(tmp_path):
 s=HybridStore(duckdb_path=tmp_path/'validation.duckdb');yield s;s.con.close()
@pytest.fixture
def paper_boundary(tmp_path,monkeypatch):
 monkeypatch.setattr(th,'DEFAULT_DB',tmp_path/'execution.duckdb');monkeypatch.setattr(th,'TRADE_MODE','paper');monkeypatch.setattr(th,'GATEKEEPER_ENABLED',False)
 monkeypatch.setattr(th,'get_account',lambda:{'equity':100000});monkeypatch.setattr(th,'get_clock',lambda:{'is_open':True});monkeypatch.setattr(th,'latest_quote',lambda *a:100.)
 for n in ['check_correlation_ok','check_pdt_ok','circuit_breaker_check_ok']:monkeypatch.setattr(th,n,lambda *a:(True,'ok'))
 monkeypatch.setattr(th,'broker_get_position',lambda *a:{'qty':20})
 for n in ['watch_bracket_async','watch_and_exit_active_async','watch_and_exit_async','post_discord']:monkeypatch.setattr(th,n,Mock())
 submit=Mock(return_value={'status':'filled','order_id':'fake-paper','legs':[]});monkeypatch.setattr(th,'place_trade',submit);return submit
