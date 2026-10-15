from copy import deepcopy
from datetime import datetime,timedelta
import pytest
from lavish_core.validation.monthly_alert_replay import prepare
from lavish_core.validation.spy_options_month import build_plan,replay,validated_options
from validation_tests.test_monthly_alert_replay import session_fixture


def scenario():
    rows,calendar=session_fixture()
    sessions=prepare(rows,calendar,'2026-09')
    alert=dict(day='2026-09-01',symbol='SPY260901C00750000',side='CALL',
        target=800,stop=700,gate={'confirmed':True},decision_at='2026-09-01T09:46:00-04:00',
        close='2026-09-01T16:00:00-04:00')
    start=datetime.fromisoformat(alert['decision_at'])
    bars=[dict(t=(start+timedelta(minutes=i)).isoformat(),o=1.,h=1.,l=1.,c=1.,v=100) for i in range(345)]
    return sessions,alert,bars


def test_real_parser_keeps_exact_zero_dte_side_strike_and_expiry():
    rows,calendar=session_fixture()
    sessions=prepare(rows,calendar,'2026-09')
    alert=build_plan(sessions)[0]
    assert alert['parsed']['side']=='PUT'
    assert alert['parsed']['expiry']=='2026-09-01'
    assert alert['parsed']['strike']==750
    assert alert['symbol']=='SPY260901P00750000'


def test_fake_alert_contract_does_not_use_later_prices():
    rows,calendar=session_fixture()
    original=build_plan(prepare(rows,calendar,'2026-09'))[0]
    later=deepcopy(rows)
    for b in later[15:]:b.update(o=850.,h=851.,l=849.,c=850.)
    changed=build_plan(prepare(later,calendar,'2026-09'))[0]
    assert original['text']==changed['text']
    assert original['symbol']==changed['symbol']


def test_all_in_reserves_roundtrip_fees_and_forces_expiry_close():
    sessions,alert,bars=scenario()
    result=replay([alert],sessions,{alert['symbol']:bars})
    trade=result['ledger'][0]
    assert trade['qty']==9
    assert trade['premium_spent']+trade['qty']*.65<=1000
    assert trade['exit_at'].endswith('15:30:00-04:00')
    assert trade['reason']=='expiry_force_close'
    assert result['final_equity']==pytest.approx(1000+trade['net_pnl'])
    assert result['final_positions']==0


def test_premium_stop_uses_completed_bar_then_next_open_and_can_gap_worse():
    sessions,alert,bars=scenario()
    bars[1].update(o=.4,h=.4,l=.4,c=.4)
    bars[2].update(o=.3,h=.3,l=.3,c=.3)
    trade=replay([alert],sessions,{alert['symbol']:bars})['ledger'][0]
    assert trade['reason']=='premium_stop_50pct'
    assert trade['exit_at'].endswith('09:48:00-04:00')
    assert trade['exit_price']==.29


def test_premium_trailing_stop_does_not_use_unclosed_minute():
    sessions,alert,bars=scenario()
    bars[0].update(h=1.5,c=1.5)
    bars[1].update(o=1.5,h=1.5,l=1.2,c=1.2)
    bars[2].update(o=1.1,h=1.1,l=1.1,c=1.1)
    trade=replay([alert],sessions,{alert['symbol']:bars})['ledger'][0]
    assert trade['reason']=='premium_trailing_stop'
    assert trade['exit_at'].endswith('09:48:00-04:00')


def test_missing_open_position_minute_is_not_invented_liquidation():
    sessions,alert,bars=scenario()
    with pytest.raises(ValueError,match='Missing option minute'):
        replay([alert],sessions,{alert['symbol']:bars[:1]+bars[2:]})


def test_no_affordable_contract_stays_in_cash():
    sessions,alert,bars=scenario()
    for b in bars:b.update(o=20.,h=20.,l=20.,c=20.)
    result=replay([alert],sessions,{alert['symbol']:bars})
    assert result['trades']==0 and result['final_equity']==1000
    assert result['outcomes'][0]['result']=='cannot_afford_one_contract'


def test_gate_rejection_does_not_trade_even_with_option_data():
    sessions,alert,bars=scenario()
    alert['gate']={'confirmed':False,'reason':'invalidated_wrong_direction'}
    result=replay([alert],sessions,{alert['symbol']:bars})
    assert result['trades']==0 and result['final_equity']==1000


def test_crossed_creator_levels_do_not_open_then_instantly_close():
    sessions,alert,bars=scenario()
    alert['target']=749
    result=replay([alert],sessions,{alert['symbol']:bars})
    assert result['outcomes'][0]['result']=='source_levels_already_crossed'


def test_option_ohlc_and_ordering_are_validated():
    _,_,bars=scenario()
    with pytest.raises(ValueError):validated_options(bars[:1]*2)
    bars[0]['c']=float('nan')
    with pytest.raises(ValueError):validated_options(bars[:1])
