import asyncio
import io
import threading
import sys
from itertools import permutations
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from PIL import ImageDraw, ImageFont, ImageOps

from lavish_core.discord_ingest.message_processor import DiscordProcessor, message_text, visible_text
from lavish_core.discord_ingest.message_processor import fetch_embed_image
from lavish_core.db.hybrid_store import HybridStore


def embed(**body):
    return SimpleNamespace(to_dict=lambda: body)


def message(identity=1, age=0, **change):
    values=dict(id=identity, channel=SimpleNamespace(id=10), author=SimpleNamespace(id=7),
                webhook_id=None, content='BUY AAPL now', embeds=[], attachments=[],
                created_at=datetime.now(timezone.utc)-timedelta(seconds=age))
    values.update(change)
    return SimpleNamespace(**values)


def processor(path, callback, **change):
    settings=dict(channels={10}, authors={7}, webhooks={9}, callback=callback, own_user_id=99)
    settings.update(change)
    return DiscordProcessor(path, **settings)


def image_attachment(data=None, **change):
    if data is None:
        buffer=io.BytesIO();Image.new('RGB',(16,16),'white').save(buffer,format='PNG');data=buffer.getvalue()
    async def read():return data
    values=dict(id=11,filename='trade.png',size=len(data),content_type='image/png',read=read)
    values.update(change)
    return SimpleNamespace(**values)


@pytest.mark.parametrize('change',[
    {'channel':SimpleNamespace(id=12)}, {'author':SimpleNamespace(id=99)},
    {'author':SimpleNamespace(id=8)}, {'webhook_id':8},
    {'author':SimpleNamespace(id=7), 'webhook_id':8},
])
def test_wrong_channel_self_untrusted_author_or_webhook_never_dispatches(tmp_path,change):
    callback=Mock();p=processor(tmp_path/'discord.db',callback)
    assert asyncio.run(p.process(message(**change))) in ('ignored','untrusted_source')
    callback.assert_not_called()


def test_trusted_webhook_embed_reaches_real_options_parser(tmp_path,monkeypatch):
    from lavish_core.trading import alert_handler
    callback=Mock();monkeypatch.setattr(alert_handler,'execute_trade_from_post',callback)
    p=processor(tmp_path/'discord.db',alert_handler.handle_alert_text)
    text='BTO AAPL 250 CALL 2026-10-16'
    m=message(webhook_id=9,author=SimpleNamespace(id=123),content='',
              embeds=[embed(title='Trade',description=text,fields=[{'name':'Target','value':'260'},{'name':'Stop','value':'240'}])])
    assert asyncio.run(p.process(m))=='dispatched'
    signal=callback.call_args.args[0]
    assert (signal['ticker'],signal['side'],signal['strike'],signal['expiry'],signal['action'])==('AAPL','CALL',250,'2026-10-16','BUY')
    assert signal['source']=='discord' and signal['note']=='discord:10:1'


@pytest.mark.parametrize('age',[-60,121,901,3600])
def test_future_or_stale_message_is_durable_and_never_dispatches(tmp_path,age):
    callback=Mock();p=processor(tmp_path/'discord.db',callback)
    assert asyncio.run(p.process(message(age=age)))=='expired';callback.assert_not_called()


@pytest.mark.parametrize('stamp',[None,'bad',datetime(2026,10,13)])
def test_invalid_timestamp_never_dispatches(tmp_path,stamp):
    callback=Mock();p=processor(tmp_path/'discord.db',callback)
    assert asyncio.run(p.process(message(created_at=stamp)))=='invalid_timestamp';callback.assert_not_called()


def test_duplicates_and_restart_dispatch_once(tmp_path):
    callback=Mock();path=tmp_path/'discord.db';m=message()
    p=processor(path,callback)
    assert asyncio.run(p.process(m))=='dispatched'
    assert asyncio.run(p.process(m))=='duplicate'
    assert asyncio.run(processor(path,callback).process(m))=='duplicate'
    callback.assert_called_once()


@pytest.mark.parametrize('change',[
    {'content':'SELL AAPL now'},
    {'embeds':[embed(description='SELL AAPL now')]},
    {'attachments':[image_attachment()]},
])
def test_message_edit_after_dispatch_never_reexecutes_and_halts(tmp_path,change):
    callback=Mock();path=tmp_path/'discord.db';p=processor(path,callback);m=message()
    assert asyncio.run(p.process(m))=='dispatched'
    for key,value in change.items():setattr(m,key,value)
    assert asyncio.run(p.process(m))=='review_required';callback.assert_called_once()
    store=HybridStore(path);assert store.get_kill_switch()[0];store.con.close()


def test_twenty_concurrent_duplicate_deliveries_claim_once(tmp_path):
    callback=Mock();p=processor(tmp_path/'discord.db',callback);m=message()
    async def run():return await asyncio.gather(*(p.process(m) for _ in range(20)))
    states=asyncio.run(run());assert states.count('dispatched')==1;callback.assert_called_once()


def test_exception_and_restart_quarantine_without_retry(tmp_path):
    callback=Mock(side_effect=TimeoutError('synthetic unknown broker outcome'))
    path=tmp_path/'discord.db';p=processor(path,callback);m=message()
    assert asyncio.run(p.process(m))=='review_required'
    assert asyncio.run(processor(path,callback).process(m))=='duplicate'
    callback.assert_called_once()
    store=HybridStore(path);assert store.get_kill_switch()[0];store.con.close()


def test_interrupted_dispatch_recovered_without_replay(tmp_path):
    callback=Mock();path=tmp_path/'discord.db';p=processor(path,callback)
    assert p._claim('10:1','synthetic-digest','processing')=='processing'
    processor(path,callback)
    store=HybridStore(path)
    assert store.get_kill_switch()[0]
    assert store.fetchall('SELECT status,reason FROM discord_receipts')==[('review_required','interrupted_dispatch')]
    store.con.close();callback.assert_not_called()


@pytest.mark.parametrize('text',[
    '> BUY TSLA now\nBUY AAPL now',
    '```\nSELL TSLA now\n```\nBUY AAPL now',
    '~~SELL TSLA now~~\nBUY AAPL now',
])
def test_quoted_code_and_struck_instructions_are_excluded(text):
    assert visible_text(text)=='BUY AAPL now'


@pytest.mark.parametrize('text',['>>> Historical alert\nBUY AAPL now','Old excerpt ```BUY AAPL now```'])
def test_multiline_and_inline_quoted_alerts_never_dispatch_trade(tmp_path,monkeypatch,text):
    from lavish_core.trading import alert_handler
    callback=Mock();monkeypatch.setattr(alert_handler,'execute_trade_from_post',callback)
    p=processor(tmp_path/'discord.db',alert_handler.handle_alert_text)
    assert asyncio.run(p.process(message(content=text)))=='dispatched';callback.assert_not_called()


@pytest.mark.parametrize('change',[
    {'content':'BUY AAPL and SELL TSLA'}, {'content':'BUY AAPL\x00'},
    {'content':'x'*12001}, {'content':'```\nBUY AAPL'},
    {'content':'','embeds':[embed(image={'url':'http://127.0.0.1/card'})]},
    {'embeds':[embed(description='BUY TSLA')]},
    {'embeds':[embed(fields=[{'name':'Symbol','value':'TSLA'}])]},
    {'embeds':[embed(fields='invalid')]},
    {'embeds':[embed(fields=[None])]},
])
def test_ambiguous_malformed_or_unavailable_payload_quarantined(tmp_path,change):
    callback=Mock();p=processor(tmp_path/'discord.db',callback)
    assert asyncio.run(p.process(message(**change)))=='review_required';callback.assert_not_called()


@pytest.mark.parametrize('attachments',[
    [image_attachment(),image_attachment()],
    [image_attachment(content_type='image/svg+xml')],
    [image_attachment(size=10*1024*1024+1)],
    [image_attachment(size=True)],
    [image_attachment(data=b'not an image')],
    [image_attachment(size=1)],
])
def test_bad_or_multiple_attachments_never_dispatch(tmp_path,attachments):
    callback=Mock();p=processor(tmp_path/'discord.db',callback)
    assert asyncio.run(p.process(message(attachments=attachments)))=='review_required';callback.assert_not_called()


@pytest.mark.parametrize('failure',[False,True])
def test_verified_image_exists_during_dispatch_and_is_always_removed(tmp_path,failure):
    paths=[]
    def callback(**kwargs):
        path=Path(kwargs['image_path']);assert path.is_file();paths.append(path)
        if failure:raise TimeoutError('synthetic')
    p=processor(tmp_path/'discord.db',callback)
    result=asyncio.run(p.process(message(attachments=[image_attachment()])))
    assert result==('review_required' if failure else 'dispatched')
    assert len(paths)==1 and not paths[0].exists()


def test_attachment_read_failure_quarantines(tmp_path):
    async def read():raise TimeoutError('synthetic')
    callback=Mock();p=processor(tmp_path/'discord.db',callback)
    assert asyncio.run(p.process(message(attachments=[image_attachment(read=read)])))=='review_required';callback.assert_not_called()


def test_broker_callback_does_not_block_gateway_loop(tmp_path):
    started,release=threading.Event(),threading.Event()
    def callback(**kwargs):started.set();assert release.wait(5)
    p=processor(tmp_path/'discord.db',callback)
    async def run():
        task=asyncio.create_task(p.process(message()))
        assert await asyncio.to_thread(started.wait,5)
        # This timer can progress while the actual broker callback is blocked.
        await asyncio.sleep(.01);assert not task.done()
        release.set();assert await task=='dispatched'
    asyncio.run(run())


def test_cancellation_keeps_ocr_file_until_running_dispatch_finishes(tmp_path):
    started,release=threading.Event(),threading.Event();paths=[]
    def callback(**kwargs):
        path=Path(kwargs['image_path']);paths.append(path);started.set()
        assert release.wait(5);assert path.is_file()
    path=tmp_path/'discord.db';p=processor(path,callback)
    async def run():
        task=asyncio.create_task(p.process(message(attachments=[image_attachment()])))
        assert await asyncio.to_thread(started.wait,5)
        task.cancel();await asyncio.sleep(.01)
        assert paths[0].exists();release.set()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(run());assert not paths[0].exists()
    store=HybridStore(path);assert store.get_kill_switch()[0];store.con.close()


@pytest.mark.parametrize('change',[{'channels':set()},{'authors':set(),'webhooks':set()},{'max_age':0},{'max_age':901},{'max_age':True}])
def test_incomplete_or_invalid_source_configuration_blocks_startup(tmp_path,change):
    with pytest.raises(ValueError):processor(tmp_path/'discord.db',Mock(),**change)


@pytest.mark.parametrize('instruction',['STC','STO','BTC','SELL','CLOSE','EXIT','Maybe BUY','Yesterday BUY'])
def test_discord_options_exit_short_and_commentary_never_become_entries(tmp_path,monkeypatch,instruction):
    from lavish_core.trading import alert_handler
    callback=Mock();monkeypatch.setattr(alert_handler,'execute_trade_from_post',callback)
    p=processor(tmp_path/'discord.db',alert_handler.handle_alert_text)
    assert asyncio.run(p.process(message(content=f'{instruction} AAPL 250 CALL 2026-10-16')))=='dispatched'
    callback.assert_not_called()


def test_outbound_webhook_errors_redact_url_token_and_body(monkeypatch,capsys):
    from lavish_core.utils import alerts
    import requests
    marker='SYNTHETIC_SECRET_SENTINEL'
    monkeypatch.setattr(alerts,'WEBHOOK','https://discord.com/api/webhooks/1/'+marker)
    monkeypatch.setattr(alerts.requests,'post',Mock(side_effect=requests.Timeout(marker)))
    assert alerts.post_discord('test') is False
    assert marker not in capsys.readouterr().out
    post=Mock(return_value=SimpleNamespace(status_code=429,text=marker))
    monkeypatch.setattr(alerts.requests,'post',post)
    assert alerts.post_discord('@everyone') is False
    assert marker not in capsys.readouterr().out
    assert post.call_args.kwargs['allow_redirects'] is False
    assert post.call_args.kwargs['json']['allowed_mentions']=={'parse':[]}


@pytest.mark.parametrize('order',list(permutations(['patreon','discord','signed-relay'])))
def test_same_alert_across_all_three_ingest_paths_submits_once(order,paper_boundary,monkeypatch):
    """Real source adapters, shared parser, atomic claims and trade routing."""
    from lavish_core.patreon import patreon_trigger
    from lavish_core.patreon.ingest_safety import PostJournal
    from lavish_core.feeds.alert_feed import AlertInbox
    from lavish_core.trading import alert_handler,trade_handler
    import json
    path=trade_handler.DEFAULT_DB
    store=HybridStore(path);journal=PostJournal(store);journal.finish_seed()
    discord=processor(path,alert_handler.handle_alert_text)
    inbox=AlertInbox(path)
    stamp=datetime.now(timezone.utc).isoformat()
    post={'id':'987','type':'post','attributes':{'title':'','content':'<p>BUY AAPL now</p>','published_at':stamp}}
    m=message()
    payload={'id':'forwarded-alert','created_at':stamp,'text':'BUY AAPL now'}
    monkeypatch.delenv('PATRON_DOLLARS',raising=False)
    def deliver(source):
        if source=='patreon':patreon_trigger._process_snapshot([post],journal)
        elif source=='discord':assert asyncio.run(discord.process(m)) in ('dispatched','duplicate')
        else:
            inbox.enqueue(payload,json.dumps(payload).encode(),execute=True)
            inbox.dispatch_once(alert_handler.handle_alert_text,execute=True)
    try:
        for source in order:deliver(source)
        for source in reversed(order):deliver(source)
        assert paper_boundary.call_count==1
        assert store.fetchall('SELECT count(*) FROM signals')[0][0]==1
    finally:store.con.close()


def test_listener_uses_real_self_library_api_shape_and_handles_events_offline(tmp_path,monkeypatch):
    from lavish_core.discord_ingest import listener
    import lavish_core.db.hybrid_store as hs
    events={};callback=Mock();m=message()
    class Client:
        # No Intents, self_bot argument or external login in this API boundary.
        def __init__(self):self.user=SimpleNamespace(id=99)
        def event(self,fn):events[fn.__name__]=fn;return fn
        def run(self,token):
            assert token=='synthetic-test-token'
            async def replay():
                await events['on_ready']()
                await events['on_message'](m)
                await events['on_message'](m)
                m.content='SELL AAPL now'
                await events['on_message_edit'](None,m)
            asyncio.run(replay())
    monkeypatch.setitem(sys.modules,'discord',SimpleNamespace(Client=Client))
    monkeypatch.setattr(listener,'DISCORD_USER_TOKEN','synthetic-test-token')
    monkeypatch.setattr(listener,'ACCEPT_TOS_RISK',True)
    monkeypatch.setattr(listener,'WATCH_CHANNEL_IDS',{10})
    monkeypatch.setattr(listener,'WATCH_AUTHOR_IDS',{7})
    monkeypatch.setattr(listener,'WATCH_WEBHOOK_IDS',set())
    monkeypatch.setattr(listener,'_handle_alert_text',callback)
    monkeypatch.setattr(hs,'DEFAULT_DB',tmp_path/'listener.db')
    listener.run();callback.assert_called_once()
    store=HybridStore(tmp_path/'listener.db');assert store.get_kill_switch()[0];store.con.close()


@pytest.mark.parametrize('url',[
    'http://cdn.discordapp.com/card.png','http://127.0.0.1/card.png',
    'https://evil.invalid/card.png','https://cdn.discordapp.com.evil.invalid/card.png',
    'https://cdn.discordapp.com@evil.invalid/card.png',
    'https://user:secret@cdn.discordapp.com/card.png',
    'https://cdn.discordapp.com:444/card.png','https://cdn.discordapp.com/card.png#secret',
])
def test_untrusted_embed_image_rejected_before_external_io(monkeypatch,url):
    import requests
    get=Mock();monkeypatch.setattr(requests,'get',get)
    with pytest.raises(ValueError):fetch_embed_image(url)
    get.assert_not_called()


@pytest.mark.parametrize('status',[200,301,302,401,403,404,429,500,503])
def test_embed_image_transport_status_and_close(monkeypatch,status):
    import requests
    response=Mock(status_code=status,iter_content=lambda _:iter([b'test']))
    get=Mock(return_value=response);monkeypatch.setattr(requests,'get',get)
    if status==200:assert fetch_embed_image('https://cdn.discordapp.com/card.png')==b'test'
    else:
        with pytest.raises(ValueError):fetch_embed_image('https://cdn.discordapp.com/card.png')
    response.close.assert_called_once()
    assert get.call_args.kwargs['allow_redirects'] is False
    assert get.call_args.kwargs['stream'] is True


def test_oversized_embed_image_stopped_and_closed(monkeypatch):
    import requests
    response=Mock(status_code=200,iter_content=lambda _:iter([b'x'*(10*1024*1024+1)]))
    monkeypatch.setattr(requests,'get',Mock(return_value=response))
    with pytest.raises(ValueError):fetch_embed_image('https://cdn.discordapp.com/card.png')
    response.close.assert_called_once()


def test_discord_cdn_embed_card_and_rotated_signature_do_not_replay(tmp_path,monkeypatch):
    import lavish_core.discord_ingest.message_processor as mp
    attachment=image_attachment();data=asyncio.run(attachment.read())
    download=Mock(return_value=data);monkeypatch.setattr(mp,'fetch_embed_image',download)
    paths=[]
    def callback(**kwargs):
        path=Path(kwargs['image_path']);assert path.exists();paths.append(path)
    p=processor(tmp_path/'discord.db',callback);m=message(content='',embeds=[embed(image={'url':'https://cdn.discordapp.com/attachments/1/card.png?sig=old'})])
    assert asyncio.run(p.process(m))=='dispatched'
    m.embeds=[embed(image={'url':'https://cdn.discordapp.com/attachments/1/card.png?sig=new'})]
    assert asyncio.run(p.process(m))=='duplicate'
    download.assert_called_once();assert len(paths)==1 and not paths[0].exists()


def test_embed_reference_to_uploaded_card_does_not_download_it_twice(tmp_path):
    callback=Mock();p=processor(tmp_path/'discord.db',callback)
    m=message(attachments=[image_attachment()],embeds=[embed(image={'url':'attachment://trade.png'})])
    assert asyncio.run(p.process(m))=='dispatched';callback.assert_called_once()


def test_different_embedded_trade_cards_require_review(tmp_path):
    callback=Mock();p=processor(tmp_path/'discord.db',callback)
    m=message(embeds=[embed(image={'url':'https://cdn.discordapp.com/a.png'},thumbnail={'url':'https://cdn.discordapp.com/b.png'})])
    assert asyncio.run(p.process(m))=='review_required';callback.assert_not_called()


@pytest.mark.parametrize('reason',['message_deleted','uncached_message_edit'])
def test_deleted_or_uncached_corrected_dispatched_message_halts_without_new_trade(tmp_path,reason):
    callback=Mock();path=tmp_path/'discord.db';p=processor(path,callback);m=message()
    assert asyncio.run(p.process(m))=='dispatched'
    assert asyncio.run(p.retract(10,1,reason))=='review_required'
    assert asyncio.run(p.retract(10,1,reason))=='ignored'
    callback.assert_called_once()
    store=HybridStore(path);assert store.get_kill_switch()[0]
    assert store.fetchall('SELECT reason FROM discord_receipts')==[(reason,)];store.con.close()


@pytest.mark.parametrize('kind',['unknown','other-channel','expired'])
def test_unacted_on_message_deletion_does_not_halt_or_trade(tmp_path,kind):
    callback=Mock();path=tmp_path/'discord.db';p=processor(path,callback)
    if kind=='expired':assert asyncio.run(p.process(message(age=3600)))=='expired'
    assert asyncio.run(p.retract(11 if kind=='other-channel' else 10,1))=='ignored'
    callback.assert_not_called()
    store=HybridStore(path);assert not store.get_kill_switch()[0];store.con.close()


@pytest.mark.parametrize('source',['discord','patreon'])
@pytest.mark.parametrize('side',['CALL','PUT'])
@pytest.mark.parametrize('variant',['clear','dark'])
def test_actual_ocr_card_through_source_and_shared_parser(source,side,variant,tmp_path,monkeypatch):
    from lavish_core.trading import alert_handler
    from lavish_core.vision import extract_signal
    from lavish_core.patreon import patreon_trigger
    image=Image.new('RGB',(1400,300),'white')
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',42)
    draw=ImageDraw.Draw(image)
    draw.text((20,35),f'BTO AAPL 250 {side} exp 10/16/2026',font=font,fill='black')
    draw.text((20,140),'Target 260  Stop 240',font=font,fill='black')
    if variant=='dark':image=ImageOps.invert(image)
    buffer=io.BytesIO();image.save(buffer,format='PNG');data=buffer.getvalue()
    execution=Mock();monkeypatch.setattr(alert_handler,'execute_trade_from_post',execution)
    monkeypatch.setattr(extract_signal,'OUT_DIR',tmp_path)
    if source=='discord':
        p=processor(tmp_path/'discord.db',alert_handler.handle_alert_text)
        assert asyncio.run(p.process(message(content='Trade card',attachments=[image_attachment(data=data)])))=='dispatched'
    else:
        path=tmp_path/'card.png';path.write_bytes(data)
        monkeypatch.setattr(patreon_trigger,'VISION_AUTO',True)
        monkeypatch.setattr(patreon_trigger,'_download_images_from_post',lambda _: [path])
        monkeypatch.delenv('PATRON_DOLLARS',raising=False)
        patreon_trigger._handle_post({'id':'123','attributes':{'title':'Trade card','content':'<img src="https://patreonusercontent.com/card.png">'}})
    execution.assert_called_once()
    signal=execution.call_args.args[0]
    assert (signal['ticker'],signal['side'],signal['strike'],signal['expiry'],signal['action'])==('AAPL',side,250,'2026-10-16','BUY')
    assert signal['target_hint']==260 and signal['stop_hint']==240
