"""Sign an alert using a private environment file; never print the secret."""
import argparse,json,time,uuid,urllib.request,urllib.error
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
from lavish_core.feeds.alert_feed import sign,decode_alert

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None

def send_alert(url,secret,alert):
    parts=urlsplit(url)
    if parts.username or parts.password or parts.query or parts.fragment or parts.path!='/v1/alerts':raise ValueError('Invalid alert endpoint')
    if parts.scheme!='https' and not (parts.scheme=='http' and parts.hostname in ('127.0.0.1','::1','localhost')):raise ValueError('Remote alert endpoints require HTTPS')
    body=json.dumps(alert,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8');decode_alert(body)
    timestamp=str(int(time.time()))
    request=urllib.request.Request(url,data=body,headers={'Content-Type':'application/json','X-Alert-Timestamp':timestamp,'X-Alert-Signature':sign(secret,timestamp,body)},method='POST')
    try:
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=10) as response:return response.status,json.loads(response.read(4096))
    except urllib.error.HTTPError as exc:return exc.code,{'status':'receiver_rejected_request'}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,required=True)
    inputs=parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--text-file',type=Path)
    inputs.add_argument('--event-file',type=Path,help='Resend the original id/time/text; do not regenerate on retries')
    parser.add_argument('--save-event',type=Path,help='Save the generated immutable event for safe retries')
    parser.add_argument('--id',default=None)
    parser.add_argument('--url',default='http://127.0.0.1:8877/v1/alerts')
    args=parser.parse_args()
    from dotenv import dotenv_values
    secret=dotenv_values(args.env_file).get('ALERT_FEED_SECRET')
    if not secret:parser.error('Feed secret missing from the private environment file')
    path=args.event_file or args.text_file
    if path.stat().st_size>32768:parser.error('Alert file exceeds limit')
    if args.event_file:
        alert=json.loads(path.read_text())
        if args.id or args.save_event:parser.error('Event-file retries preserve identity; do not provide --id/--save-event')
    else:
        alert={'id':args.id or uuid.uuid4().hex,'created_at':datetime.now(timezone.utc).isoformat(),'text':path.read_text()}
    decode_alert(json.dumps(alert,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
    if args.save_event:
        import os
        descriptor=os.open(args.save_event,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w') as handle:handle.write(json.dumps(alert,ensure_ascii=False,indent=2)+'\n')
    status,result=send_alert(args.url,secret,alert);print(json.dumps({'http_status':status,**result}));return 0 if status in (200,202) else 1
if __name__=='__main__':raise SystemExit(main())
