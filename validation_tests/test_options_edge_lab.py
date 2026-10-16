import copy
import socket
from datetime import datetime, timedelta, timezone
import pytest
from lavish_core.validation.options_edge_lab import evaluate, trial, validate, stamp


@pytest.fixture(autouse=True)
def no_external_connections(monkeypatch):
    def blocked(*args, **kwargs): raise AssertionError('Research tests must stay offline')
    monkeypatch.setattr(socket.socket, 'connect', blocked)


def fixture():
    start = datetime(2026, 10, 1, 15, tzinfo=timezone.utc)
    def at(seconds): return (start+timedelta(seconds=seconds)).isoformat()
    contracts = [dict(symbol=f'SYNTHETIC-SPY-{days}', underlying='SPY', side='CALL', strike=770,
                      multiplier=100, expiry_at=at(days*86400)) for days in (3, 14, 30)]
    ticks = [dict(underlying='SPY', timestamp=at(s), available_at=at(s),
                  price=769 if s == 60 else 773 if s >= 300 else 771) for s in range(0, 1201, 30)]
    quotes = [dict(symbol=c['symbol'], timestamp=at(s), available_at=at(s),
                   bid=.80 if s >= 300 else .49, ask=.82 if s >= 300 else .50,
                   bid_size=10, ask_size=10) for c in contracts for s in range(0, 1201, 30)]
    return dict(source_kind='synthetic', source_id='synthetic-edge-lab-fixture-v1', contracts=contracts,
                ticks=ticks, quotes=quotes, alerts=[dict(post_id='synthetic-1', version_id='v1',
                published_at=at(0), observed_at=at(0), session_open=at(0), session_close=at(1200),
                underlying='SPY', side='CALL', level=770, target=772, stop=768,
                regime='synthetic_trending')])


@pytest.mark.parametrize('mode', ['hold', 'retest_hold'])
@pytest.mark.parametrize('bucket', [(1, 7), (8, 21), (22, 45)])
def test_positive_entries_and_cost_conservation(mode, bucket):
    data = fixture()
    result = trial(data, validate(data), data['alerts'][0], mode=mode, bucket=bucket)
    assert result['status'] == 'closed'
    assert result['net'] == 26.70  # .51 buy, .79 sell, two .65 fees
    assert sum(f['qty'] if f['side']=='buy' else -f['qty'] for f in result['fills']) == 0


def test_grid_is_not_profitability_or_a_selected_winner():
    report = evaluate(fixture())
    assert report['candidate_count'] == 108
    assert sum(r['closed'] for r in report['outcomes']) > 0
    assert report['profitability_established'] is False
    assert report['real_orders'] == 0
    assert 'winner' not in report


def test_false_break_has_no_entry():
    data = fixture()
    for i, t in enumerate(data['ticks']): t['price'] = 769 if i % 2 else 771
    assert trial(data, validate(data), data['alerts'][0])['status'] == 'no_confirmed_trigger'


def test_missing_exit_never_fabricates_profit():
    data = fixture()
    data['quotes'] = [q for q in data['quotes'] if stamp(q['timestamp']).minute < 5]
    result = trial(data, validate(data), data['alerts'][0])
    assert result['status'] == 'unresolved_exit' and result['net'] is None
    report = evaluate(data)
    assert any(r['unresolved'] and not r['eligible_for_comparison'] for r in report['outcomes'])


def test_quote_known_only_later_cannot_fill_entry():
    data = fixture()
    for q in data['quotes']: q['available_at'] = data['alerts'][0]['session_close']
    assert trial(data, validate(data), data['alerts'][0])['status'] == 'no_eligible_contract_quote'


def test_exact_creator_contract_not_replaced_by_cheaper_expiry():
    data = fixture(); data['alerts'][0]['contract_symbol'] = 'SYNTHETIC-SPY-30'
    assert trial(data, validate(data), data['alerts'][0])['status'] == 'no_eligible_contract_quote'


def test_whole_contract_affordability_includes_fees():
    data = fixture()
    assert trial(data, validate(data), data['alerts'][0], budget=51)['status'] == 'unaffordable'


def test_exit_delay_uses_later_adverse_bid_not_trigger_bid():
    data = fixture()
    for q in data['quotes']:
        if stamp(q['timestamp']) > stamp(data['ticks'][10]['timestamp']): q['bid'] = .20
    result = trial(data, validate(data), data['alerts'][0])
    assert result['status'] == 'closed' and result['net'] == -33.30


def test_insufficient_exit_depth_stays_unresolved():
    data = fixture()
    for q in data['quotes']: q['bid_size'] = 0
    assert trial(data, validate(data), data['alerts'][0])['status'] == 'unresolved_exit'


def test_creator_close_uses_receipt_time_and_latency():
    data = fixture()
    data['alerts'][0]['creator_close_at'] = data['ticks'][9]['timestamp']
    result = trial(data, validate(data), data['alerts'][0], exit_rule='premium_45')
    assert result['fills'][-1]['reason'] == 'creator_close'
    assert stamp(result['fills'][-1]['at']) > stamp(data['alerts'][0]['creator_close_at'])


def test_scale_never_sells_fractional_contracts():
    data = fixture()
    result = trial(data, validate(data), data['alerts'][0], budget=300, exit_rule='scale_80_runner')
    assert result['status'] == 'closed'
    assert [f['qty'] for f in result['fills']] == [5, 4, 1]


@pytest.mark.parametrize('change', ['naive', 'future_alert', 'duplicate_quote', 'crossed', 'nan', 'negative_size'])
def test_bad_input_rejected(change):
    data = copy.deepcopy(fixture())
    if change == 'naive': data['alerts'][0]['published_at'] = '2026-10-01T15:00:00'
    if change == 'future_alert': data['alerts'][0]['published_at'] = '2026-10-02T15:00:00Z'
    if change == 'duplicate_quote': data['quotes'].append(copy.deepcopy(data['quotes'][0]))
    if change == 'crossed': data['quotes'][0]['bid'] = 1
    if change == 'nan': data['quotes'][0]['bid'] = float('nan')
    if change == 'negative_size': data['quotes'][0]['ask_size'] = -1
    with pytest.raises(ValueError): validate(data)


def test_declared_source_is_not_automatically_authenticated():
    data = fixture(); data['source_kind'] = 'declared_opra'
    report = evaluate(data)
    assert not report['authenticity_independently_verified']
    assert not report['profitability_established']


def test_close_received_before_entry_cancels_trial():
    data = fixture(); data['alerts'][0]['creator_close_at'] = data['ticks'][1]['timestamp']
    assert trial(data, validate(data), data['alerts'][0])['status'] == 'creator_already_closed'


def test_put_direction_is_symmetric():
    data = fixture()
    data['alerts'][0].update(side='PUT', target=768, stop=772)
    for c in data['contracts']: c['side'] = 'PUT'
    for tick in data['ticks']: tick['price'] = 1540-tick['price']
    result = trial(data, validate(data), data['alerts'][0])
    assert result['status'] == 'closed' and result['net'] == 26.70


def test_structural_stop_between_option_quotes_is_not_forgotten():
    data = fixture()
    data['ticks'][8]['price'] = 767  # 15:04:00; recovery at 15:04:30
    data['quotes'] = [q for q in data['quotes'] if stamp(q['timestamp']) != stamp(data['ticks'][8]['timestamp'])]
    result = trial(data, validate(data), data['alerts'][0])
    assert result['fills'][-1]['reason'] == 'structural_stop'
    assert result['fills'][-1]['at'] == data['ticks'][9]['timestamp']


def test_creator_close_between_quotes_starts_latency_at_receipt():
    data = fixture()
    data['alerts'][0]['creator_close_at'] = (stamp(data['ticks'][8]['timestamp'])+timedelta(seconds=1)).isoformat()
    result = trial(data, validate(data), data['alerts'][0])
    assert result['fills'][-1]['reason'] == 'creator_close'
    assert result['fills'][-1]['at'] == data['ticks'][9]['timestamp']


def test_session_exit_request_does_not_wait_for_next_quote_to_start_clock():
    data = fixture()
    data['alerts'][0]['target'] = 800
    entry_cutoff = stamp(data['ticks'][8]['timestamp'])
    end = stamp(data['alerts'][0]['session_close'])
    data['quotes'] = [q for q in data['quotes'] if stamp(q['timestamp']) <= entry_cutoff or stamp(q['timestamp']) == end]
    result = trial(data, validate(data), data['alerts'][0])
    assert result['status'] == 'closed'
    assert result['fills'][-1]['reason'] == 'session_exit'


def test_later_version_cannot_count_as_another_independent_alert():
    data = fixture()
    revised = copy.deepcopy(data['alerts'][0]); revised['version_id'] = 'v2'
    data['alerts'].append(revised)
    with pytest.raises(ValueError, match='edit-lifecycle'):
        evaluate(data)
