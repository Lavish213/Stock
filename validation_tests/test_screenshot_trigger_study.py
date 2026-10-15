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
