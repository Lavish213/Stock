from unittest.mock import Mock
import pytest
from lavish_core.feeds.research_context import ResearchJournal, classify
from lavish_core.validation.context_replay import replay_break_hold


@pytest.mark.parametrize('text,kind', [
    ('GOOGL $350C, October 9. Break and hold above $346.', 'conditional_setup'),
    ('JPM is at the second target', 'progress_update'),
    ('GOOGL 350 CALL exp 2026-10-09 target hit', 'progress_update'),
    ('JPM was up $6 at opening. The contracts were high as $8.10.', 'progress_update'),
    ('Tomorrow is the inflation report. Headline CPI annual 3.6% 3.4%. SPY QQQ Amazon set up.', 'macro_context'),
    ('BUY AAPL now target 105 stop 95', 'instruction_candidate'),
    ('BUY SPY now ahead of CPI', 'instruction_candidate'),
])
def test_context_tags(text, kind):
    assert classify(text)['kind'] == kind
    assert not classify(text)['execution_authorized']


@pytest.mark.parametrize('source', ['reddit', 'reddit:wallstreetbets', 'WSB', 'wallstreetbets'])
def test_reddit_even_complete_contract_cannot_dispatch(source, monkeypatch):
    from lavish_core.trading import alert_handler as ah
    dispatch = Mock()
    monkeypatch.setattr(ah, 'execute_trade_from_post', dispatch)
    ah.handle_alert_text('BTO NVDA 100 CALL exp 2026-10-16 target 105 stop 95', source=source)
    dispatch.assert_not_called()


def test_versions_duplicates_restart_and_no_future_edit_leak(tmp_path):
    path = tmp_path/'research.sqlite'
    journal = ResearchJournal(path)
    post = dict(source='patreon', post_id='p1', published_at='2026-10-08T13:30:00Z')
    journal.record(**post, text='watch GOOGL above 346', observed_at='2026-10-08T13:31:00Z')
    journal.record(**post, text='watch GOOGL above 346', observed_at='2026-10-08T13:32:00Z')
    journal.record(**post, text='cancel GOOGL', observed_at='2026-10-08T14:00:00Z')
    restored = ResearchJournal(path)
    assert len(restored.as_of('2026-10-08T13:59:00Z')) == 1
    assert len(restored.as_of('2026-10-08T14:00:00Z')) == 2
    journal.record(text='screenshot: 6 days ago', source='screenshot', observed_at='2026-10-14T12:00:00Z')
    assert restored.as_of('2026-10-14T12:00:00Z')[-1]['published_at'] is None
    assert len(restored.as_of('2026-10-08T14:00:00Z')) == 2


@pytest.mark.parametrize('published,observed', [
    ('2026-10-08', '2026-10-08T13:00:00Z'),
    ('2026-10-08T14:00:00Z', '2026-10-08T13:00:00Z'),
])
def test_invalid_timestamps_refused(tmp_path,published,observed):
    with pytest.raises(ValueError):
        ResearchJournal(tmp_path/'r.sqlite').record(text='BUY AAPL',source='test',published_at=published,observed_at=observed)


def bars(closes):
    return [dict(start=f'2026-10-08T13:{31+i:02}:00Z',end=f'2026-10-08T13:{32+i:02}:00Z',
                 available_at=f'2026-10-08T13:{32+i:02}:01Z',close=close) for i,close in enumerate(closes)]


def replay(candles, **kwargs):
    fields=dict(published_at='2026-10-08T13:31:00Z',expires_at='2026-10-08T20:00:00Z',
                as_of='2026-10-08T13:40:00Z',level=346.,bars=candles)
    fields.update(kwargs)
    return replay_break_hold(**fields)


def test_break_hold_no_touch_or_future_confirmation():
    assert replay(bars([346,346]))['state']=='waiting'
    assert replay(bars([347,345,347]))['state']=='waiting'
    assert replay(bars([347,348]))['state']=='condition_confirmed_review'
    assert replay(bars([347,348]),as_of='2026-10-08T13:33:00Z')['state']=='waiting'
    assert replay(bars([347,348]),published_at=None)['state']=='review'
    assert replay(bars([345,344]),direction='below')['state']=='condition_confirmed_review'
    assert replay(bars([345,344]),as_of='2026-10-08T21:00:00Z')['state']=='expired'
    assert replay(bars([347,348]),published_at='2026-10-08T13:32:00Z')['state']=='waiting'


@pytest.mark.parametrize('mutation', ['duplicate','nan','availability','order'])
def test_replay_refuses_bad_market_evidence(mutation):
    candles=bars([345,345])
    if mutation=='duplicate': candles[1]=candles[0].copy()
    if mutation=='nan': candles[0]['close']=float('nan')
    if mutation=='availability': candles[0]['available_at']=candles[0]['start']
    if mutation=='order': candles.reverse()
    with pytest.raises(ValueError): replay(candles)


def test_late_bar_arrival_cannot_backdate_confirmation():
    candles=bars([347,348])
    candles[0]['available_at']='2026-10-08T13:38:00Z'
    assert replay(candles)['confirmed_at']=='2026-10-08T13:38:00+00:00'


def test_missing_candle_breaks_confirmation_streak():
    candles=bars([347,348,349])
    assert replay([candles[0],candles[2]])['state']=='waiting'


@pytest.mark.parametrize('unknown_close',[345,347])
def test_unavailable_middle_bar_cannot_bridge_observed_confirmation(unknown_close):
    candles=bars([347,unknown_close,348])
    candles[1]['available_at']='2026-10-08T13:50:00Z'
    assert replay(candles)['state']=='waiting'
    # Authentic later availability may establish the condition later, but
    # only when every required close was above the threshold.
    later=replay(candles,as_of='2026-10-08T13:51:00Z')
    assert later['state']==('condition_confirmed_review' if unknown_close==347 else 'waiting')
    if unknown_close==347:
        assert later['confirmed_at']=='2026-10-08T13:50:00+00:00'


def test_bar_arrival_after_setup_expiry_cannot_confirm():
    candles=bars([347,348])
    candles[1]['available_at']='2026-10-08T20:00:01Z'
    assert replay(candles,as_of='2026-10-08T20:01:00Z')['state']=='expired'


def test_confirmation_arriving_exactly_at_expiry_is_retained():
    candles=bars([347,348])
    candles[1]['available_at']='2026-10-08T20:00:00Z'
    result=replay(candles,as_of='2026-10-08T20:01:00Z')
    assert result['state']=='condition_confirmed_review'
    assert result['confirmed_at']=='2026-10-08T20:00:00+00:00'


def test_handler_persists_news_before_rejecting(tmp_path,monkeypatch):
    from lavish_core.trading import alert_handler as ah
    path=tmp_path/'r.sqlite'
    monkeypatch.setenv('RESEARCH_CONTEXT_DB',str(path))
    dispatch=Mock()
    monkeypatch.setattr(ah,'execute_trade_from_post',dispatch)
    ah.handle_alert_text('Tomorrow CPI. SPY QQQ',source='patreon',post_id='cpi')
    assert len(ResearchJournal(path).as_of('2099-01-01T00:00:00Z'))==1
    dispatch.assert_not_called()


def test_patreon_multisymbol_news_retained_before_trade_card_checks(tmp_path,monkeypatch):
    from lavish_core.patreon import patreon_trigger as pt
    path=tmp_path/'r.sqlite'
    monkeypatch.setenv('RESEARCH_CONTEXT_DB',str(path))
    dispatch=Mock()
    monkeypatch.setattr(pt,'handle_alert_text',dispatch)
    pt._handle_post({'id':'news','attributes':{'title':'CPI tomorrow','content':'Watch SPY and QQQ'}})
    assert len(ResearchJournal(path).as_of('2099-01-01T00:00:00Z'))==1
    dispatch.assert_not_called()


def test_actual_source_excerpts_and_synthetic_mutations():
    from lavish_core.validation.social_study import source_cases, stress_cases, evaluate
    for rows in (source_cases(),stress_cases()):
        result=evaluate(rows)
        assert result['incorrect']==0, [r for r in result['results'] if not r['correct']]


def test_jpm_explicit_entry_is_recognized(monkeypatch):
    from lavish_core.trading import alert_handler as ah
    dispatch=Mock()
    monkeypatch.setattr(ah,'execute_trade_from_post',dispatch)
    ah.handle_alert_text('BUY JPM now target 105 stop 95',source='patreon')
    assert dispatch.call_count==1
    assert dispatch.call_args.args[0]['symbol']=='JPM'


def test_stop_loss_language_does_not_become_a_past_loss_report(monkeypatch):
    from lavish_core.trading import alert_handler as ah
    dispatch=Mock()
    monkeypatch.setattr(ah,'execute_trade_from_post',dispatch)
    ah.handle_alert_text('BUY AAPL now target 105 stop loss 95',source='patreon')
    assert dispatch.call_count==1
    assert dispatch.call_args.args[0]['stop_hint']==95


def test_ocr_progress_caption_cannot_open_a_complete_contract(monkeypatch):
    from lavish_core.trading import alert_handler as ah
    dispatch=Mock()
    monkeypatch.setattr(ah,'execute_trade_from_post',dispatch)
    monkeypatch.setattr(ah,'parse_alert',lambda **kwargs: dict(ticker='GOOGL',side='CALL',strike=350,
        expiry='2026-10-16',confidence=.99,instruction_text='GOOGL 350 CALL exp 2026-10-16 target hit'))
    ah.handle_alert_text('',image_path='synthetic-card.png',source='patreon')
    dispatch.assert_not_called()
