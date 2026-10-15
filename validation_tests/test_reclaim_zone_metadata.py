"""Research metadata is not permission to trade or a historical fill."""
import json
from unittest.mock import Mock

import pytest

from lavish_core.feeds.research_context import ResearchJournal, classify
from lavish_core.trading import alert_handler


@pytest.mark.parametrize('separator', ['-', '–', '—', 'to'])
@pytest.mark.parametrize('prefix', ['', 'BTO NVDA 220 CALL exp 2026-11-20. '])
def test_source_shaped_reclaim_preserves_zone_without_inventing_hold(separator, prefix, monkeypatch):
    text = prefix + f'Reclaim the $218 {separator} $220 range and hold above this area with buyers stepping in.'
    tags = classify(text)
    assert tags['kind'] == 'conditional_setup'
    assert tags['condition'] == dict(direction='above', level=220, zone_low=218,
        zone_high=220, trigger_type='reclaim_and_hold', hold_duration=None,
        buyers_confirmation_requested=True, volume_policy=None, review_required=True)
    assert not tags['execution_authorized']
    dispatch = Mock()
    monkeypatch.setattr(alert_handler, 'execute_trade_from_post', dispatch)
    alert_handler.handle_alert_text(text, source='synthetic_creator')
    dispatch.assert_not_called()


@pytest.mark.parametrize('text,direction,level', [
    ('GOOGL $350C October9. Break and hold above $346.', 'above', 346),
    ('Break and hold below $340.', 'below', 340),
    ('Break and hold above $0.80.', 'above', .8),
])
def test_existing_single_level_condition_remains_compatible(text, direction, level):
    assert classify(text)['condition'] == {'direction': direction, 'level': level}


@pytest.mark.parametrize('text,error', [
    ('Reclaim $220-$218 and hold above this zone.', 'invalid_reclaim_zone'),
    ('Reclaim $220-$220 and hold above this zone.', 'invalid_reclaim_zone'),
    ('Reclaim $0-$220 and hold above this zone.', 'invalid_reclaim_zone'),
    ('Reclaim $-218 to $220 and hold above this zone.', 'invalid_reclaim_zone'),
    ('Reclaim $218-$220 and hold above $230.', 'ambiguous_reclaim_threshold'),
    ('Reclaim $218-$220 and hold above resistance.', 'unresolved_reclaim_threshold'),
    ('Reclaim $218-$220 and hold above NaN.', 'unresolved_reclaim_threshold'),
    ('Break and hold above $0.', 'invalid_condition_level'),
    ('Break and hold above $-1.', 'invalid_condition_level'),
    ('Break and hold above346 or break and hold below340.', 'multiple_conditions_require_review'),
    ('Reclaim $218-$220 and hold above; reclaim $222-$224 and hold above.', 'multiple_conditions_require_review'),
])
def test_invalid_or_multiple_conditions_are_not_silently_selected(text, error):
    tags = classify(text)
    assert tags['condition'] is None and tags['condition_error'] == error
    assert not tags['execution_authorized']


def test_repeated_identical_zone_deduplicates_without_adding_policy():
    tags = classify('Reclaim $218-$220 and hold above. Reclaim $218-$220 and hold above.')
    assert tags['condition']['level'] == 220
    assert 'condition_error' not in tags
    assert tags['condition']['hold_duration'] is None


def test_literal_equal_hold_threshold_is_allowed_but_does_not_confirm():
    tags = classify('Reclaim $218-$220 and hold above $220.')
    assert tags['condition']['level'] == 220 and tags['condition']['review_required']
    assert tags['condition']['volume_policy'] is None


def test_spot_and_option_strike_are_not_reclaim_triggers():
    assert classify('NVDA 220 CALL exp 2026-11-20. Share price228.43.')['condition'] is None


def test_unknown_timestamp_and_later_edit_remain_separate_after_restart(tmp_path):
    path = tmp_path / 'research.sqlite'
    journal = ResearchJournal(path)
    fields = dict(source='synthetic_creator', post_id='synthetic-post', published_at=None)
    first = journal.record(**fields, text='Reclaim $218-$220 and hold above.', observed_at='2026-10-15T13:31:00Z')
    second = journal.record(**fields, text='Reclaim $222-$224 and hold above.', observed_at='2026-10-15T13:40:00Z')
    assert first != second
    restored = ResearchJournal(path)
    early = restored.as_of('2026-10-15T13:39:00Z')
    assert len(early) == 1 and early[0]['published_at'] is None
    assert json.loads(early[0]['tags'])['condition']['level'] == 220
    later = restored.as_of('2026-10-15T13:40:00Z')
    assert len(later) == 2 and json.loads(later[-1]['tags'])['condition']['level'] == 224


def test_positive_explicit_entry_still_dispatches(monkeypatch):
    dispatch = Mock()
    monkeypatch.setattr(alert_handler, 'execute_trade_from_post', dispatch)
    alert_handler.handle_alert_text('BTO NVDA 220 CALL exp 2026-11-20 target225 stop218', source='synthetic_creator')
    assert dispatch.call_count == 1
    assert dispatch.call_args.args[0]['ticker'] == 'NVDA'
