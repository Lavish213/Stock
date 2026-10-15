from pathlib import Path
from unittest.mock import Mock
import pytest
from lavish_core.feeds.research_context import classify
from lavish_core.validation.options_creator_month import offline,parse
from lavish_core.validation.screenshot_trigger_study import study,hold


@pytest.mark.parametrize('text,kind',[
 ('OPEN Whales are still in and still buying. A whale purchased $1 million of this position.','flow_context'),
 ('AMD Whale loads up $300k on this position. It could be inside trade.','flow_context'),
 ('OPEN Free trade up nicely. Take 80% profits and ride the rest.','position_management'),
 ('NVDA reclaim the $218-$220 range and hold above this area with buyers stepping in.','conditional_setup'),
])
def test_source_roles_and_no_entry(text,kind):
    assert classify(text)['kind']==kind
    with offline():assert parse(text) is None


@pytest.mark.parametrize('text',[
 'NVDA 220 CALL exp 2026-11-20. Whales are still in and still buying.',
 'NVDA 220 CALL exp 2026-11-20. Take 80% profits and ride the rest.',
])
def test_context_plus_complete_contract_does_not_rebuy(text):
    with offline():assert parse(text) is None


def test_explicit_entry_still_works_despite_flow_context():
    with offline():
        s=parse('BTO NVDA 220 CALL exp 2026-11-20 target 225 stop 218. Unusual flow noted.')
    assert s and s['ticker']=='NVDA'


@pytest.mark.parametrize('target,stop',[(8,5),(12,5),(.8,.5)])
def test_low_price_targets_and_stops_do_not_conflict(target,stop):
    from lavish_core.vision.extract_signal import parse_alert
    result=parse_alert(text=f'BTO HOOD 7 CALL exp 2026-11-20 target {target} stop {stop}')
    assert result['target_hint']==target and result['stop_hint']==stop
    assert not result.get('field_conflicts')


def test_all_ten_sources_and_positive_synthetic_fixtures():
    corpus=Path(__file__).resolve().parents[1]/'docs/evidence/screenshot-trigger-corpus-20261015.json'
    result=study(corpus)
    assert result['actual_source_captions']['cases']==10
    assert result['actual_source_captions']['entry_dispatches']==0
    assert result['synthetic_alerts']['cases']==result['synthetic_alerts']['correct']==8
    conditional=result['synthetic_conditional']
    assert conditional['initial_dispatches']==0
    assert conditional['hold_results']['touch_only']['state']=='waiting'
    assert conditional['hold_results']['false_break']['state']=='waiting'
    assert conditional['hold_results']['confirmed']['state']=='condition_confirmed_review'
    scale=result['synthetic_scaling']
    assert scale['entry_contracts']==5 and scale['trim_contracts']==4 and scale['remaining_after_trim']==1
    assert scale['runner_exit_contracts']==1 and scale['positions']=={}
    assert scale['duplicate_order_same_identity'] and scale['final_cash']==158.5
    assert result['real_orders']==0 and not result['production_conditional_or_scaling_wiring']


@pytest.mark.parametrize('suffix',[
 'Wait for confirmation.','Waiting for market confirmation.',
 'This is not an automatic entry.','These setups are not automatic entries.',
 'Entry requires market confirmation.','This setup needs confirmation.',
 'We only enter trades based on market confirmation.',
])
@pytest.mark.parametrize('prefix',['','BTO '])
def test_complete_contract_never_overrides_confirmation_constraint(suffix,prefix):
    text=prefix+'NVDA 220 CALL exp 2026-11-20 target 225 stop 218. '+suffix
    assert classify(text)['kind']=='conditional_setup'
    with offline():assert parse(text) is None


def test_confirmation_requirement_in_image_also_blocks_caption_bto(monkeypatch):
    from lavish_core.vision import extract_signal as es
    from lavish_core.trading import alert_handler as ah
    monkeypatch.setattr(es,'parse_image',lambda *a:es.parse_text('NVDA 220 CALL exp 2026-11-20. Wait for confirmation.'))
    dispatch=Mock();monkeypatch.setattr(ah,'execute_trade_from_post',dispatch)
    ah.handle_alert_text('BTO NVDA 220 CALL exp 2026-11-20',image_path='synthetic.png')
    dispatch.assert_not_called()


def test_144_phrase_matrix_keeps_positive_entries():
    from lavish_core.validation.screenshot_trigger_study import phrase_matrix
    r=phrase_matrix()
    assert r['cases']==144 and r['positive_cases']==32 and r['negative_cases']==112
    assert r['positive_errors']==r['unsafe_entry_dispatches']==r['incorrect']==0


def test_trim_partial_restart_then_runner_never_oversells(store):
    from lavish_core.trade.exit_state import ExitState
    symbol='OPEN261120C00007000'
    entry=store.submit_order(symbol,'buy',5)
    store.log_fill(entry,symbol,'buy',5,.15,fee=3.25)
    state=ExitState(4,db_path=store.db_path,symbol=symbol)
    submit=Mock(return_value={'id':'trim','status':'partially_filled','filled_qty':2,'filled_avg_price':.3,'filled_fee':1.3})
    def attempt(s,post,lookup,get):return s.attempt(post,lookup,get,lambda *a:True,1,lambda:None)
    assert attempt(state,submit,lambda *a:None,lambda *a:{'status':'partially_filled','filled_qty':2,'filled_avg_price':.3,'filled_fee':1.3}) is None
    assert store.get_position(symbol)['qty']==3
    restarted=ExitState(2,db_path=store.db_path,symbol=symbol)
    no_post=Mock(side_effect=AssertionError('Existing trim must be adopted'))
    assert attempt(restarted,no_post,lambda *a:None,lambda *a:None) is None
    result=attempt(restarted,no_post,lambda *a:{'id':'trim','status':'filled','filled_qty':4,'filled_avg_price':.31,'filled_fee':2.6},lambda *a:None)
    assert result['status']=='exited' and store.get_position(symbol)['qty']==1
    no_post.assert_not_called()
    runner=ExitState(1,db_path=store.db_path,symbol=symbol)
    result=attempt(runner,lambda *a:{'id':'runner','status':'filled','filled_qty':1,'filled_avg_price':.2,'filled_fee':.65},lambda *a:None,lambda *a:None)
    assert result['status']=='exited' and store.get_position(symbol) is None
    assert store.fetchall("SELECT sum(qty),sum(fee) FROM fills WHERE side='sell'")[0]==(5,3.25)
