"""Loopback HMAC-authenticated durable alert inbox; dry/paper execution only."""
from __future__ import annotations
import hashlib,hmac,json,os,re,socket,time
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from collections import deque
from lavish_core.db.hybrid_store import HybridStore,_positions_lock
from lavish_core.utils.safety import finite_number

MAX_BYTES=32768
MAX_AGE=120

def enabled():return os.getenv('ALERT_FEED_ENABLED','false').lower() in ('true','1','yes','on')
def execution_enabled():return os.getenv('ALERT_FEED_EXECUTE','false').lower() in ('true','1','yes','on')
def settings():
    if os.getenv('TRADE_MODE','dry') not in ('paper','dry'):raise ValueError('Private feed supports dry/paper only')
    secret=os.getenv('ALERT_FEED_SECRET','')
    if not re.fullmatch(r'[a-fA-F0-9]{64,128}',secret):raise ValueError('Configure a random 32-byte or stronger hex feed secret')
    host=os.getenv('ALERT_FEED_HOST','127.0.0.1')
    if host not in ('127.0.0.1','::1'):raise ValueError('Feed must bind loopback; use an HTTPS reverse proxy for remote input')
    port=int(os.getenv('ALERT_FEED_PORT','8877'))
    if not 1<=port<=65535:raise ValueError('Invalid feed port')
    return secret,host,port

def sign(secret,timestamp,body):
    return hmac.new(secret.encode(),str(timestamp).encode()+b'\n'+body,hashlib.sha256).hexdigest()

def authenticate(secret,timestamp,signature,body,now=None):
    now=time.time() if now is None else finite_number(now,positive=True)
    if not isinstance(timestamp,str) or not re.fullmatch(r'[0-9]{10,11}',timestamp):return False
    age=now-int(timestamp)
    if not 0<=age<=MAX_AGE:return False
    if not isinstance(signature,str) or not re.fullmatch(r'[a-fA-F0-9]{64}',signature):return False
    return hmac.compare_digest(sign(secret,timestamp,body),signature.lower())

def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate JSON field')
        result[key]=value
    return result

def decode_alert(body,now=None):
    if not isinstance(body,bytes) or not 0<len(body)<=MAX_BYTES:raise ValueError('Invalid body size')
    alert=json.loads(body.decode('utf-8'),object_pairs_hook=_unique_object)
    if not isinstance(alert,dict) or set(alert)!={'id','created_at','text'}:raise ValueError('Required fields: id, created_at, text only')
    if not isinstance(alert['id'],str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',alert['id']):raise ValueError('Invalid alert identity')
    if not isinstance(alert['text'],str) or not alert['text'].strip() or len(alert['text'])>12000:raise ValueError('Invalid alert text')
    alert['text'].encode('utf-8')
    if any(ord(ch)<32 and ch not in '\t\r\n' for ch in alert['text']):raise ValueError('Control characters prohibited')
    stamp=datetime.fromisoformat(alert['created_at'].replace('Z','+00:00'))
    if stamp.tzinfo is None:raise ValueError('Timezone required')
    age=(time.time() if now is None else now)-stamp.timestamp()
    if not 0<=age<=MAX_AGE:raise ValueError('Alert stale or future dated')
    return alert

class AlertInbox:
    def __init__(self,db_path):
        self.db_path=Path(db_path)
        with _positions_lock:
            store=HybridStore(self.db_path)
            try:
                store.execute('CREATE TABLE IF NOT EXISTS alert_inbox(event_id VARCHAR PRIMARY KEY,payload_sha256 VARCHAR NOT NULL,text VARCHAR NOT NULL,created_at VARCHAR NOT NULL,received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,status VARCHAR NOT NULL,reason VARCHAR)')
                os.chmod(self.db_path,0o600)
            finally:store.con.close()

    def enqueue(self,alert,body,*,execute=False):
        digest=hashlib.sha256(json.dumps(alert,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()
        with _positions_lock:
            store=HybridStore(self.db_path)
            try:
                rows=store.fetchall('SELECT payload_sha256,status FROM alert_inbox WHERE event_id=?',(alert['id'],))
                if rows:
                    if rows[0][0]!=digest:return {'status':'id_conflict'},409
                    return {'status':rows[0][1],'duplicate':True,'id':alert['id']},200
                if store.fetchall('SELECT count(*) FROM alert_inbox')[0][0]>=50000:return {'status':'inbox_capacity_review_required'},503
                if execute and store.fetchall("SELECT count(*) FROM alert_inbox WHERE status='queued'")[0][0]>=1000:return {'status':'queue_full'},503
                state='queued' if execute else 'review'
                store.execute('INSERT INTO alert_inbox(event_id,payload_sha256,text,created_at,status) VALUES (?,?,?,?,?)',(alert['id'],digest,alert['text'],alert['created_at'],state))
                return {'status':state,'duplicate':False,'id':alert['id'],'execution_confirmed':False},202
            finally:store.con.close()

    def recover(self):
        with _positions_lock:
            store=HybridStore(self.db_path)
            try:
                rows=store.fetchall("SELECT event_id FROM alert_inbox WHERE status='processing'")
                if rows:
                    store.execute("UPDATE alert_inbox SET status='review_required',reason='interrupted_dispatch' WHERE status='processing'")
                    store.set_kill_switch(True,'alert feed interrupted dispatch; review broker outcome before retry')
                return len(rows)
            finally:store.con.close()

    def dispatch_once(self,callback,*,execute=False,now=None):
        if not execute:return None
        if os.getenv('TRADE_MODE','dry') not in ('paper','dry'):raise ValueError('Live feed execution prohibited')
        now=time.time() if now is None else now
        with _positions_lock:
            store=HybridStore(self.db_path)
            try:
                rows=store.fetchall("SELECT event_id,text,created_at FROM alert_inbox WHERE status='queued' ORDER BY received_at,event_id LIMIT 1")
                if not rows:return None
                identity,text,created=rows[0]
                age=now-datetime.fromisoformat(created.replace('Z','+00:00')).timestamp()
                state='processing' if 0<=age<=MAX_AGE else 'expired'
                store.execute('UPDATE alert_inbox SET status=? WHERE event_id=?',(state,identity))
            finally:store.con.close()
        if state=='expired':return state
        try:
            callback(text=text,source='private-alert-feed',note='feed:'+identity)
            state='dispatched';reason=None
        except Exception:
            state='review_required';reason='dispatch_error'
        with _positions_lock:
            store=HybridStore(self.db_path)
            try:
                store.execute('UPDATE alert_inbox SET status=?,reason=? WHERE event_id=?',(state,reason,identity))
                if state=='review_required':store.set_kill_switch(True,'alert dispatch failed; review broker outcome before retry')
            finally:store.con.close()
        return state

class RateLimit:
    def __init__(self,maximum=120,clock=time.monotonic):self.maximum=maximum;self.clock=clock;self.events=deque()
    def allow(self):
        now=self.clock()
        while self.events and self.events[0]<=now-60:self.events.popleft()
        if len(self.events)>=self.maximum:return False
        self.events.append(now);return True

def handler_for(inbox,secret,*,clock=time.time,limiter=None):
    limiter=limiter or RateLimit()
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup();self.connection.settimeout(5)
        def log_message(self,*args):pass  # No bodies, signatures or secrets in access logs.
        def respond(self,status,payload):
            body=json.dumps(payload).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.send_header('Connection','close');self.end_headers();self.wfile.write(body);self.close_connection=True
        def do_GET(self):
            if self.path!='/health':self.respond(404,{'status':'not_found'});return
            self.respond(200,{'status':'listening','mode':'execute' if execution_enabled() else 'review','live_execution':False})
        def do_POST(self):
            if self.path!='/v1/alerts':self.respond(404,{'status':'not_found'});return
            if not limiter.allow():self.respond(429,{'status':'rate_limited'});return
            if os.getenv('TRADE_MODE','dry') not in ('paper','dry'):self.respond(503,{'status':'live_execution_prohibited'});return
            if any(len(self.headers.get_all(name,[]))>1 for name in ('Content-Length','Content-Type','X-Alert-Timestamp','X-Alert-Signature')):self.respond(400,{'status':'duplicate_header'});return
            if self.headers.get('Transfer-Encoding'):self.respond(400,{'status':'chunked_body_not_supported'});return
            if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':self.respond(415,{'status':'json_required'});return
            try:length=int(self.headers.get('Content-Length',''))
            except ValueError:self.respond(411,{'status':'content_length_required'});return
            if not 0<length<=MAX_BYTES:self.respond(413,{'status':'invalid_body_size'});return
            try:body=self.rfile.read(length)
            except (OSError,TimeoutError):self.respond(408,{'status':'read_timeout'});return
            if len(body)!=length:self.respond(400,{'status':'incomplete_body'});return
            if not authenticate(secret,self.headers.get('X-Alert-Timestamp'),self.headers.get('X-Alert-Signature'),body,clock()):self.respond(401,{'status':'invalid_signature_or_timestamp'});return
            try:alert=decode_alert(body,clock())
            except (ValueError,TypeError,AttributeError,OverflowError,RecursionError):self.respond(422,{'status':'invalid_or_stale_alert'});return
            try:payload,status=inbox.enqueue(alert,body,execute=execution_enabled())
            except Exception:self.respond(503,{'status':'journal_unavailable'});return
            self.respond(status,payload)
    return Handler

def make_server(db_path):
    secret,host,port=settings();inbox=AlertInbox(db_path);inbox.recover()
    cls=HTTPServer
    if host=='::1':
        class IPv6Server(HTTPServer):address_family=socket.AF_INET6
        cls=IPv6Server
    server=cls((host,port),handler_for(inbox,secret));server.timeout=1
    return server,inbox

def run_server(server):
    from lavish_core.utils.worker_health import WORKER_HEALTH
    try:
        while True:server.handle_request();WORKER_HEALTH.beat('alert-feed-server')
    finally:server.server_close()

def run_dispatcher(inbox):
    from lavish_core.utils.worker_health import WORKER_HEALTH
    from lavish_core.trading.alert_handler import handle_alert_text
    while True:
        inbox.dispatch_once(handle_alert_text,execute=execution_enabled())
        WORKER_HEALTH.beat('alert-feed-dispatcher');time.sleep(1)

def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--env-file',type=Path,required=True);parser.add_argument('--db',type=Path,required=True);args=parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(args.env_file,override=False);os.umask(0o077)
    if execution_enabled():parser.error('Standalone receiver is review-only; execute through supervised run_ingest.py')
    server,_=make_server(args.db);print('Private alert receiver listening in review mode; no orders will be submitted.',flush=True);run_server(server)
if __name__=='__main__':main()
