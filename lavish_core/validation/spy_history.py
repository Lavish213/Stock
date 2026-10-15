"""Read-only SPY history acquisition; fixed official hosts, no broker orders."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests
from dotenv import dotenv_values


def merge_pages(pages):
    unique={};duplicates=0
    for rows in pages:
        for row in rows:
            stamp=row['t']
            if stamp in unique:
                if unique[stamp]!=row:raise ValueError('Conflicting duplicate bars')
                duplicates+=1
            else:unique[stamp]=row
    return sorted(unique.values(),key=lambda row:row['t']),duplicates


def fetch(directory: Path,env_file: Path):
    env=dotenv_values(env_file)
    key,secret=env.get('ALPACA_API_KEY'),env.get('ALPACA_SECRET_KEY')
    if not key or not secret:raise ValueError('Read-only data credentials are missing')
    headers={'APCA-API-KEY-ID':key,'APCA-API-SECRET-KEY':secret}
    directory.mkdir(parents=True,exist_ok=True)
    def bars(start,end,timeframe,adjustment):
        params=dict(timeframe=timeframe,start=start+'T00:00:00Z',end=end+'T00:00:00Z',
                    feed='sip',adjustment=adjustment,limit=10000,sort='asc')
        rows=[];seen=set()
        with requests.Session() as session:
            for _ in range(60):
                response=session.get('https://data.alpaca.markets/v2/stocks/SPY/bars',headers=headers,params=params,timeout=40,allow_redirects=False)
                if response.status_code!=200:raise RuntimeError(f'Historical data status {response.status_code}')
                body=response.json();page=body.get('bars',[])
                if rows and page and page[0]['t']<=rows[-1]['t']:raise ValueError('Overlapping pagination')
                rows.extend(page);token=body.get('next_page_token')
                if not token:return rows
                if token in seen:raise ValueError('Repeated pagination token')
                seen.add(token);params['page_token']=token
        raise RuntimeError('Pagination bound exceeded')
    def year(y):
        start='2016-10-14' if y==2016 else f'{y}-01-01'
        end='2026-10-14' if y==2026 else f'{y+1}-01-01'
        rows=bars(start,end,'15Min','split')
        (directory/f'spy-15min-{y}.json').write_text(json.dumps(rows))
        return rows
    daily=bars('2016-10-14','2026-10-14','1Day','all')
    (directory/'spy-daily.json').write_text(json.dumps({'source':'Alpaca historical SIP','adjustment':'all','bars':daily},indent=2))
    with ThreadPoolExecutor(max_workers=4) as pool:
        intraday,duplicates=merge_pages(pool.map(year,range(2016,2027)))
    (directory/'spy-15min.json').write_text(json.dumps({'source':'Alpaca historical SIP','adjustment':'split',
        'identical_boundary_duplicates_removed':duplicates,'bars':intraday}))
    response=requests.get('https://paper-api.alpaca.markets/v2/calendar',headers=headers,
                          params={'start':'2016-10-14','end':'2026-10-13'},timeout=40,allow_redirects=False)
    if response.status_code!=200:raise RuntimeError(f'Calendar access status {response.status_code}')
    (directory/'calendar.json').write_text(json.dumps(response.json(),indent=2))
    return {'daily_bars':len(daily),'intraday_bars':len(intraday),'identical_boundary_duplicates':duplicates,'orders':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--env-file',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(fetch(args.directory,args.env_file)))
