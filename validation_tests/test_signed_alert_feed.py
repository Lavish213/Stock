import io,json,time,threading
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from lavish_core.feeds import alert_feed as feed
from lavish_core.db.hybrid_store import HybridStore

SECRET='a'*64
NOW=int(time.time())
def payload(identity='event-1',text='watch only SPY',age=0):return dict(id=identity,text=text,created_at=datetime.fromtimestamp(NOW-age,timezone.utc).isoformat())
def encode(data):return json.dumps(data,separators=(',',':')).encode()

class Connection:
    def __init__(self,data):self.data=data;self.output=bytearray()
    def makefile(self,*args):return io.BytesIO(self.data)
    def settimeout(self,*args):pass
    def sendall(self,data):self.output.extend(data)

@pytest.fixture
def inbox(tmp_path,monkeypatch):
    monkeypatch.setenv('TRADE_MODE','dry');monkeypatch.setenv('ALERT_FEED_EXECUTE','false')
    return feed.AlertInbox(tmp_path/'inbox.duckdb')

def request(inbox,body=None,*,path='/v1/alerts',method='POST',timestamp=None,signature=None,content_type='application/json',extra=None,omit_length=False,limiter=None):
    body=encode(payload()) if body is None else body;timestamp=str(NOW) if timestamp is None else timestamp
    signature=feed.sign(SECRET,timestamp,body) if signature is None else signature
    headers=[f'{method} {path} HTTP/1.1','Host: localhost',f'Content-Type: {content_type}',f'X-Alert-Timestamp: {timestamp}',f'X-Alert-Signature: {signature}']
    if not omit_length:headers.append('Content-Length: '+str(len(body)))
    headers.extend(extra or [])
    connection=Connection(('\r\n'.join(headers)+'\r\n\r\n').encode()+body)
    feed.handler_for(inbox,SECRET,clock=lambda:NOW,limiter=limiter)(connection,('127.0.0.1',1234),SimpleNamespace(server_name='localhost',server_port=8877))
    head,result=bytes(connection.output).split(b'\r\n\r\n',1)
    return int(head.split()[1]),json.loads(result)

def count(inbox):
    store=HybridStore(inbox.db_path)
    try:return store.fetchall('SELECT count(*) FROM alert_inbox')[0][0]
    finally:store.con.close()

def test_signed_review_accepts_without_dispatch_and_deduplicates(inbox):
    assert request(inbox)[0]==202
    status,result=request(inbox);assert status==200 and result['duplicate'] and result['status']=='review'
    callback=Mock();assert inbox.dispatch_once(callback,execute=False,now=NOW) is None
    callback.assert_not_called();assert count(inbox)==1

@pytest.mark.parametrize('timestamp,signature',[(str(NOW-121),None),(str(NOW+1),None),('bad',None),(str(NOW),'0'*64),(str(NOW),'bad'),(str(NOW),'')])
def test_bad_signature_or_clock_is_rejected_before_journal(inbox,timestamp,signature):
    assert request(inbox,timestamp=timestamp,signature=signature)[0]==401
    assert count(inbox)==0

def test_signature_covers_exact_body(inbox):
    original=encode(payload());changed=encode(payload(text='BUY SPY'))
    assert request(inbox,changed,signature=feed.sign(SECRET,str(NOW),original))[0]==401

@pytest.mark.parametrize('data',[{},[],dict(payload(),amount_usd=100000),dict(payload(),source='trusted'),dict(payload(),id='bad id'),dict(payload(),id=1),dict(payload(),text=''),dict(payload(),text=[]),dict(payload(),created_at='2026-01-01'),dict(payload(),created_at=None),payload(age=121),payload(age=-1)])
def test_invalid_or_stale_alert_payload(inbox,data):
    assert request(inbox,encode(data))[0]==422;assert count(inbox)==0

@pytest.mark.parametrize('body',[b'not json',b'\xff',b'{"id":"a","id":"b","text":"watch","created_at":"bad"}'])
def test_malformed_json_or_duplicate_keys(inbox,body):assert request(inbox,body)[0]==422

def test_identity_reuse_with_changed_payload_conflicts(inbox):
    assert request(inbox)[0]==202
    assert request(inbox,encode(payload(text='BUY SPY')))[0]==409
    assert count(inbox)==1

def test_content_and_transport_guards(inbox):
    assert request(inbox,content_type='text/plain')[0]==415
    assert request(inbox,omit_length=True)[0]==411
    assert request(inbox,b'x'*(feed.MAX_BYTES+1))[0]==413
    assert request(inbox,extra=['Transfer-Encoding: chunked'])[0]==400
    assert request(inbox,extra=['Content-Length: 1'])[0]==400
    assert request(inbox,path='/other')[0]==404

def test_request_rate_is_bounded(inbox):
    limiter=feed.RateLimit(maximum=1,clock=lambda:1)
    assert request(inbox,limiter=limiter)[0]==202
    assert request(inbox,limiter=limiter)[0]==429

def test_execute_queue_dispatches_once_and_records_receipt_not_fill(inbox,monkeypatch):
    monkeypatch.setenv('ALERT_FEED_EXECUTE','true');assert request(inbox)[1]['status']=='queued'
    callback=Mock();assert inbox.dispatch_once(callback,execute=True,now=NOW)=='dispatched'
    callback.assert_called_once_with(text='watch only SPY',source='private-alert-feed',note='feed:event-1',post_id='event-1')
    assert inbox.dispatch_once(callback,execute=True,now=NOW) is None
    assert request(inbox)[1]['status']=='dispatched'

def test_transport_identity_survives_actual_research_journal(inbox,monkeypatch,tmp_path):
    from lavish_core.feeds.research_context import ResearchJournal
    from lavish_core.trading.alert_handler import handle_alert_text
    path=tmp_path/'research.sqlite'
    monkeypatch.setenv('RESEARCH_CONTEXT_DB',str(path))
    monkeypatch.setenv('ALERT_FEED_EXECUTE','true')
    assert request(inbox)[0]==202
    assert inbox.dispatch_once(handle_alert_text,execute=True,now=NOW)=='dispatched'
    records=ResearchJournal(path).as_of('2099-01-01T00:00:00Z')
    assert len(records)==1
    assert records[0]['post_id']=='event-1'
    assert records[0]['source']=='private-alert-feed'
    # Sender creation time is not authenticated creator publication time.
    assert records[0]['published_at'] is None

def test_stale_queued_alert_is_not_dispatched(inbox,monkeypatch):
    monkeypatch.setenv('ALERT_FEED_EXECUTE','true');request(inbox)
    callback=Mock();assert inbox.dispatch_once(callback,execute=True,now=NOW+121)=='expired';callback.assert_not_called()

def test_interrupted_dispatch_quarantines_and_halts(inbox,monkeypatch):
    monkeypatch.setenv('ALERT_FEED_EXECUTE','true');request(inbox)
    store=HybridStore(inbox.db_path);store.execute("UPDATE alert_inbox SET status='processing'");store.con.close()
    restarted=feed.AlertInbox(inbox.db_path);assert restarted.recover()==1
    callback=Mock();assert restarted.dispatch_once(callback,execute=True,now=NOW) is None;callback.assert_not_called()
    store=HybridStore(inbox.db_path);assert store.get_kill_switch()[0];store.con.close()

def test_dispatch_exception_is_not_retried(inbox,monkeypatch):
    monkeypatch.setenv('ALERT_FEED_EXECUTE','true');request(inbox)
    callback=Mock(side_effect=TimeoutError('synthetic ambiguity'))
    assert inbox.dispatch_once(callback,execute=True,now=NOW)=='review_required'
    assert inbox.dispatch_once(callback,execute=True,now=NOW) is None;assert callback.call_count==1

def test_live_mode_rejected_at_receive_and_dispatch(inbox,monkeypatch):
    monkeypatch.setenv('ALERT_FEED_EXECUTE','true');request(inbox)
    monkeypatch.setenv('TRADE_MODE','live');assert request(inbox)[0]==503
    with pytest.raises(ValueError):inbox.dispatch_once(Mock(),execute=True,now=NOW)

@pytest.mark.parametrize('key,value',[('ALERT_FEED_HOST','0.0.0.0'),('ALERT_FEED_HOST','example.com'),('ALERT_FEED_PORT','0'),('ALERT_FEED_PORT','65536'),('ALERT_FEED_SECRET','short'),('TRADE_MODE','live')])
def test_bad_receiver_configuration(key,value,monkeypatch):
    monkeypatch.setenv('ALERT_FEED_SECRET',SECRET);monkeypatch.setenv('TRADE_MODE','paper');monkeypatch.setenv('ALERT_FEED_HOST','127.0.0.1');monkeypatch.setenv('ALERT_FEED_PORT','8877');monkeypatch.setenv(key,value)
    with pytest.raises(ValueError):feed.settings()

def test_concurrent_delivery_keeps_one_inbox_record(inbox):
    raw=encode(payload());results=[]
    threads=[threading.Thread(target=lambda:results.append(inbox.enqueue(payload(),raw))) for _ in range(12)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert sum(status==202 for _,status in results)==1 and count(inbox)==1

def test_source_enters_actual_parser_with_production_guards(inbox,monkeypatch,paper_boundary):
    from lavish_core.trading.alert_handler import handle_alert_text
    from lavish_core.trading import trade_handler as th
    monkeypatch.setenv('ALERT_FEED_EXECUTE','true')
    request(inbox,encode(payload(text='BUY AAPL now')))
    assert inbox.dispatch_once(handle_alert_text,execute=True,now=NOW)=='dispatched'
    assert paper_boundary.called

def test_whitespace_and_field_order_changes_do_not_duplicate(inbox):
    data=payload();assert request(inbox,encode(data))[0]==202
    reordered=json.dumps({key:data[key] for key in reversed(data)},indent=2).encode()
    assert request(inbox,reordered)[1]['duplicate'] is True and count(inbox)==1

@pytest.mark.parametrize('body',[b'{'+b'"text":'+b'['*1100+b'0'+b']'*1100+b'}',encode(payload(text='BUY\x00 SPY')),encode(payload(text='\ud800'))])
def test_deep_json_control_bytes_and_surrogates_fail_closed(inbox,body):
    assert request(inbox,body)[0]==422
    assert count(inbox)==0

@pytest.mark.parametrize('url',['http://example.com/v1/alerts','https://user:password@example.com/v1/alerts','https://example.com/v1/alerts?secret=x','https://example.com/other'])
def test_sender_rejects_unsafe_target_before_io(url):
    from lavish_core.feeds.send_alert import send_alert
    with pytest.raises(ValueError):send_alert(url,SECRET,payload())
