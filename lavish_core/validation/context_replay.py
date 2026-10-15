"""Research-only conditional replay. No broker, option fill or P&L inference."""
from __future__ import annotations

from math import isfinite
from lavish_core.feeds.research_context import timestamp


def replay_break_hold(*, published_at: str | None, expires_at: str | None,
                      as_of: str, level: float, bars: list[dict],
                      confirmations: int = 2, direction: str = 'above') -> dict:
    """Confirm consecutive closed bars strictly after publication.

    Two closes is a simulator assumption, not a claim about creator intent.
    A bar includes start/end, available_at and close. Inputs must be ordered;
    duplicates, malformed candles and missing timestamps refuse the replay.
    """
    result = {'state': 'review', 'orders': 0, 'profitability_evidence': False,
              'confirmations_assumption': confirmations}
    if not published_at or not expires_at:
        return {**result, 'reason': 'Exact publication and setup expiry required'}
    if isinstance(confirmations, bool) or not isinstance(confirmations, int) or confirmations < 1:
        raise ValueError('Positive confirmation count required')
    if direction not in ('above', 'below') or not isfinite(level) or level <= 0:
        raise ValueError('Invalid condition')
    published, expiry, cutoff = map(timestamp, (published_at, expires_at, as_of))
    if expiry <= published:
        raise ValueError('Setup expiry must follow publication')
    if cutoff < published:
        return {**result, 'state': 'not_published'}
    streak = 0
    previous_end = None
    confirmed_at = None
    streak_available = None
    for bar in bars:
        start, end, available = map(timestamp, (bar['start'], bar['end'], bar['available_at']))
        close = float(bar['close'])
        if end <= start or available < end or not isfinite(close) or close <= 0:
            raise ValueError('Invalid closed bar')
        if previous_end and start < previous_end:
            raise ValueError('Duplicate, overlapping or unordered bars')
        if previous_end and start != previous_end:
            streak = 0
            streak_available = None
        previous_end = end
        if start < published or available > cutoff or end > expiry or available > expiry:
            # Unknown/unusable bars break the observable consecutive streak.
            # An eventual close cannot bridge a bar that was unavailable at
            # the decision cutoff, nor arrive after the setup's deadline.
            streak = 0
            streak_available = None
            continue
        streak = streak + 1 if (close > level if direction == 'above' else close < level) else 0
        streak_available = max(streak_available, available) if streak and streak_available else available
        if streak >= confirmations:
            confirmed_at = streak_available.isoformat()
            break
    if confirmed_at:
        return {**result, 'state': 'condition_confirmed_review', 'confirmed_at': confirmed_at}
    return {**result, 'state': 'expired' if cutoff >= expiry else 'waiting'}
