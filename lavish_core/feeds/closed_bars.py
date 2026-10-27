"""Point-in-time candle aggregation. Pure data validation; never submits orders."""
from datetime import timedelta
from math import isfinite

from lavish_core.feeds.research_context import timestamp


def aggregate_closed_minutes(bars, *, as_of, minutes=5):
    """Aggregate complete UTC-aligned windows of explicitly timestamped minutes.

    Every input must carry start/end/available_at and OHLC. Missing minutes do
    not get forward-filled. Availability is the latest constituent receipt,
    never the nominal candle end. No session or creator policy is inferred.
    """
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 60:
        raise ValueError('minutes must be an integer from 1 to 60')
    cutoff = timestamp(as_of)
    groups = {}
    previous = None
    for bar in bars:
        start, end, available = (timestamp(bar[key]) for key in ('start', 'end', 'available_at'))
        if start.second or start.microsecond or end - start != timedelta(minutes=1):
            raise ValueError('Expected aligned one-minute bars')
        if available < end or (previous is not None and start <= previous):
            raise ValueError('Invalid availability, duplicate or unordered bars')
        previous = start
        values = []
        for key in ('open', 'high', 'low', 'close'):
            value = bar[key]
            if isinstance(value, bool):
                raise ValueError('Invalid OHLC')
            value = float(value)
            if not isfinite(value) or value <= 0:
                raise ValueError('Invalid OHLC')
            values.append(value)
        opening, high, low, close = values
        if low > min(opening, close) or high < max(opening, close) or low > high:
            raise ValueError('Inconsistent OHLC')
        bucket = int(start.timestamp()) // (minutes * 60)
        groups.setdefault(bucket, []).append((start, end, available, opening, high, low, close))
    result = []
    for bucket, rows in groups.items():
        beginning = rows[0][0]
        if len(rows) != minutes or int(beginning.timestamp()) != bucket * minutes * 60:
            continue
        if any(row[0] != beginning + timedelta(minutes=i) for i, row in enumerate(rows)):
            continue
        available = max(row[2] for row in rows)
        if available > cutoff:
            continue
        result.append(dict(start=beginning.isoformat(), end=rows[-1][1].isoformat(),
                           available_at=available.isoformat(), open=rows[0][3],
                           high=max(row[4] for row in rows), low=min(row[5] for row in rows),
                           close=rows[-1][6]))
    return result
