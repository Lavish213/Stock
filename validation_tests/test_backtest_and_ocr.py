import math
from datetime import date,timedelta
import numpy as np,pandas as pd,pytest
from hypothesis import given,settings,strategies as st
from PIL import Image,ImageDraw,ImageFont,ImageFilter,ImageEnhance
from ml.backtester import simulate_trade
import lavish_core.vision.extract_signal as es
ENTRY=date(2026,1,1)
def history(rows):return pd.DataFrame(rows,columns=['Open','High','Low','Close'],index=[ENTRY+timedelta(days=i) for i in range(len(rows))])
@pytest.mark.parametrize('rows,price,day,reason',[
 ([(100,103,99,101),(101,101,100,101)],102,0,'target'),([(100,101,97,99),(99,100,99,99)],98.5,0,'stop'),([(100,103,97,100),(100,101,99,100)],98.5,0,'stop'),([(100,100,100,100),(80,81,79,80)],80,1,'stop'),([(100,100,100,100),(120,121,119,120)],120,1,'target'),([(100,100,100,100),(100,103,97,100)],98.5,1,'stop'),([(100,100,100,100),(100,101,99,100.5)],100.5,1,'max_hold')])
def test_daily_backtest_independent_exit_oracle(rows,price,day,reason):
 r=simulate_trade(history(rows),ENTRY);assert r['exit_price']==pytest.approx(price);assert r['exit_date']==str(ENTRY+timedelta(days=day));assert r['exit_reason']==reason
@pytest.mark.parametrize('column',['Open','High','Low','Close'])
@pytest.mark.parametrize('value',[float('nan'),float('inf'),-float('inf'),0,-1,'corrupt'])
def test_invalid_ohlc_cannot_produce_a_trade(column,value):
 d=history([(100,101,99,100)]*2);d[column]=value
 try:r=simulate_trade(d,ENTRY)
 except (ValueError,TypeError):return
 assert r['exit_reason'] in ('no_data','invalid_data')
@pytest.mark.parametrize('change',['missing_open','missing_high','missing_low','missing_close','reversed','duplicate_dates','high_below_low','close_above_high'])
def test_malformed_history_rejected(change):
 d=history([(100,101,99,100)]*2)
 if change.startswith('missing_'):d=d.drop(columns=[change[8:].title()])
 elif change=='reversed':d=d.iloc[::-1]
 elif change=='duplicate_dates':d.index=[ENTRY,ENTRY]
 elif change=='high_below_low':d['High']=90
 elif change=='close_above_high':d['Close']=200
 try:r=simulate_trade(d,ENTRY)
 except (ValueError,TypeError):return
 assert r['exit_reason'] in ('no_data','invalid_data')
@pytest.mark.parametrize('target,stop',[(90,98.5),(100,98.5),(102,110),(102,100),(float('nan'),98.5),(102,float('nan')),(float('inf'),98.5),(102,-1)])
def test_invalid_exit_levels_rejected(target,stop):
 try:r=simulate_trade(history([(100,101,99,100),(100,103,97,100)]),ENTRY,target=target,stop=stop)
 except (ValueError,TypeError):return
 assert r['exit_reason'] in ('no_data','invalid_data')
@pytest.mark.parametrize('max_hold',[-10,-1,float('nan'),float('inf'),'corrupt'])
def test_invalid_hold_budget_is_rejected(max_hold):
 with pytest.raises((ValueError,TypeError,OverflowError)):simulate_trade(history([(100,101,99,100)]*2),ENTRY,max_hold_days=max_hold)
@given(st.floats(min_value=1,max_value=10000,allow_nan=False,allow_infinity=False),st.floats(min_value=.05,max_value=.9,allow_nan=False,allow_infinity=False))
@settings(max_examples=500,deadline=None,derandomize=True)
def test_gap_down_never_fills_above_open_after_stop_crossed(entry_price,gap_fraction):
 o=entry_price*(1-gap_fraction);r=simulate_trade(history([(entry_price,entry_price,entry_price,entry_price),(o,o*1.001,o*.999,o)]),ENTRY,stop=entry_price*.98,target=entry_price*1.02);assert r['exit_price']<=o+1e-8
@pytest.mark.parametrize('side',['calls','puts'])
@pytest.mark.parametrize('variant',['clear','dark','low_contrast','blur','small','rotated','noise'])
def test_real_ocr_trade_card_fields(side,variant,tmp_path,monkeypatch):
 font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',42);im=Image.new('RGB',(1100,260),'white');d=ImageDraw.Draw(im);d.text((25,35),f'AAPL 200 {side} exp 10/16',font=font,fill='black');d.text((25,120),'Target 210   Stop 190',font=font,fill='black')
 if variant=='dark':
  from PIL import ImageOps
  im=ImageOps.invert(im)
 elif variant=='low_contrast':im=ImageEnhance.Contrast(im).enhance(.25)
 elif variant=='blur':im=im.filter(ImageFilter.GaussianBlur(1.1))
 elif variant=='small':im=im.resize((550,130))
 elif variant=='rotated':im=im.rotate(3,expand=True,fillcolor='white')
 elif variant=='noise':
  a=np.asarray(im).astype(np.int16);a+=np.random.default_rng(42).normal(0,10,a.shape).astype(np.int16);im=Image.fromarray(np.clip(a,0,255).astype(np.uint8))
 monkeypatch.setattr(es,'OUT_DIR',tmp_path);p=tmp_path/f'{side}-{variant}.png';im.save(p);r=es.parse_image(p)
 assert r['ticker']=='AAPL';assert r['side']==('CALL' if side=='calls' else 'PUT');assert r['strike']==200;assert r['target_hint']==210 and r['stop_hint']==190
@pytest.mark.parametrize('dimensions',[(1,1),(10,10),(40,20),(500,200)])
def test_blank_image_creates_no_actionable_signal(dimensions,tmp_path,monkeypatch):
 monkeypatch.setattr(es,'OUT_DIR',tmp_path);p=tmp_path/'blank.png';Image.new('RGB',dimensions,'white').save(p);r=es.parse_image(p);assert not r.get('ticker') and not r.get('strike')
@pytest.mark.parametrize('kind',['missing','corrupt'])
def test_unreadable_image_creates_no_actionable_signal(kind,tmp_path,monkeypatch):
 monkeypatch.setattr(es,'OUT_DIR',tmp_path);p=tmp_path/'bad.png'
 if kind=='corrupt':p.write_bytes(b'not an image')
 r=es.parse_image(p);assert not r.get('ticker') and not r.get('strike')
