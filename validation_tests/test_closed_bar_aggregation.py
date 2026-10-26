"""Synthetic candle mechanics, not authentic creator or fill evidence."""
from datetime import datetime, timedelta, timezone

import pytest

from lavish_core.feeds.closed_bars import aggregate_closed_minutes


def tape():
    beginning = datetime(2026, 10, 26, 13, 30, tzinfo=timezone.utc)
    return [dict(start=(beginning + timedelta(minutes=i)).isoformat(),
                 end=(beginning + timedelta(minutes=i+1)).isoformat(),
                 available_at=(beginning + timedelta(minutes=i+1, seconds=1)).isoformat(),
                 open=100+i, high=102+i, low=99+i, close=101+i) for i in range(5)]


def aggregate(bars, **kwargs):
    return aggregate_closed_minutes(bars, as_of='2026-10-26T13:40:00Z', **kwargs)


def test_complete_positive_and_receipt_cutoff():
    bars = tape()
    result = aggregate(bars)
    assert len(result) == 1
    assert [result[0][key] for key in ('open', 'high', 'low', 'close')] == [100, 106, 99, 105]
    assert result[0]['available_at'] == '2026-10-26T13:35:01+00:00'
    assert aggregate_closed_minutes(bars, as_of='2026-10-26T13:35:00Z') == []


@pytest.mark.parametrize('missing', range(5))
def test_each_missing_minute_refuses_confirmation(missing):
    bars = tape()
    del bars[missing]
    assert aggregate(bars) == []


@pytest.mark.parametrize('mutation', ['duplicate', 'reverse', 'nan', 'range', 'early', 'unaligned', 'bool'])
def test_malformed_evidence_rejected(mutation):
    bars = tape()
    if mutation == 'duplicate': bars.insert(1, bars[0].copy())
    if mutation == 'reverse': bars.reverse()
    if mutation == 'nan': bars[0]['close'] = float('nan')
    if mutation == 'range': bars[0]['low'] = 200
    if mutation == 'early': bars[0]['available_at'] = bars[0]['start']
    if mutation == 'unaligned': bars[0]['start'] = '2026-10-26T13:30:01Z'
    if mutation == 'bool': bars[0]['open'] = True
    with pytest.raises(ValueError): aggregate(bars)


def test_delayed_constituent_not_backdated():
    bars = tape()
    bars[0]['available_at'] = '2026-10-26T13:39:00Z'
    assert aggregate(bars)[0]['available_at'] == '2026-10-26T13:39:00+00:00'
    assert aggregate_closed_minutes(bars, as_of='2026-10-26T13:38:00Z') == []


@pytest.mark.parametrize('minutes', [True, 0, 61, 1.5])
def test_bad_period(minutes):
    with pytest.raises(ValueError): aggregate(tape(), minutes=minutes)
