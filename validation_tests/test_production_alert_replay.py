from datetime import datetime, timezone
from lavish_core.validation.production_alert_replay import replay,session_clock
import pytest


def test_calendar_blocks_holiday_weekend_and_early_close():
    for stamp in ('2026-11-26T15:00:00+00:00','2026-10-03T15:00:00+00:00','2026-11-27T18:00:00+00:00'):
        assert not session_clock(datetime.fromisoformat(stamp))['is_open']
    assert session_clock(datetime(2026,11,27,17,tzinfo=timezone.utc))['is_open']


def test_real_pipeline_positive_entries_exits_duplicates_and_restart(tmp_path):
    result=replay(tmp_path/'run',days=30)
    sessions=result['sessions']
    assert len(sessions)==30 and result['real_orders']==0
    assert any(r['entry_orders'] for r in sessions)
    assert all(r['entry_orders']<=1 for r in sessions)
    assert all(abs(r['cash_error'])<.000001 and r['position_match'] for r in sessions)
    assert all(not r['entry_orders'] for r in sessions if not r['market_open'])
    assert any(r['recovered_monitors']==1 for r in sessions)
    assert any(e['reason']=='her_target' for r in sessions for e in r['exit_results'])
    assert any(e['reason']=='our_stop_loss' for r in sessions for e in r['exit_results'])
    assert any(r['creator_exit_gap'] for r in sessions)


@pytest.mark.parametrize('offset,kind',[(0,'target'),(1,'premium_stop'),(2,'partial_entry'),
    (3,'lost_ack'),(4,'exit_outage'),(5,'false_break'),(6,'conditional'),
    (9,'restart'),(10,'creator_trim_lifecycle'),(11,'creator_close_lifecycle'),(12,'partial_exit_restart'),
    (13,'structural_stop'),(14,'stale'),(15,'wrong_contract')])
def test_isolated_scenario_reaches_actual_path(tmp_path,offset,kind):
    result=replay(tmp_path/'one',days=1,scenario_offset=offset)
    row=result['sessions'][0]
    assert row['kind']==kind and row['position_match'] and row['cash_error']==0
    if kind=='stale':
        assert row['feed_receipt']['http_status']==422 and row['feed_receipt']['dispatch'] is None
    else:
        assert row['feed_receipt']['http_status']==202 and row['feed_receipt']['dispatch']=='dispatched'
        assert row['duplicate_receipt']['http_status']==200 and row['duplicate_receipt']['receipt']['duplicate']
    assert row['invalid_receipt']['http_status']==401
    assert row['entry_orders']==(0 if kind in ('false_break','conditional','stale','wrong_contract') else 1)
    if row['entry_orders']:assert row['exit_results'][0]['status']=='exited'
    if kind=='restart':assert row['recovered_monitors']==1
    if kind=='structural_stop':assert row['exit_results'][0]['reason']=='her_stop'
    if kind=='premium_stop':assert row['exit_results'][0]['reason']=='our_stop_loss'
    if kind=='partial_exit_restart':
        assert row['recovered_monitors']==1
        sells=[e for e in row['fills'] if e['side']=='SELL']
        assert len(sells)==2 and sells[0]['qty']==1
    if kind.startswith('creator_'):assert row['creator_exit_gap']
    if kind=='partial_entry':
        buys=[e for e in result['exchange_events'] if e['side']=='BUY']
        assert len(buys)>1 and buys[0]['qty']==1


def test_fee_reserve_keeps_small_account_entry_feasible(tmp_path):
    result=replay(tmp_path/'small',days=1,initial=100)
    buys=[e for e in result['exchange_events'] if e['side']=='BUY']
    assert buys and sum(e['qty']*e['price']*100+2*e['fee'] for e in buys)<=100
    assert result['sessions'][0]['cash_error']==0
