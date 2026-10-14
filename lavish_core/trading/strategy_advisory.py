"""Translate strategy research into reviewable bot decisions, never orders."""
from __future__ import annotations
from math import isfinite


def review_strategy(report: dict, *, budget: float, price: float | None = None,
                    fractional_supported: bool = False, instrument: str = 'equity') -> dict:
    if not isfinite(budget) or budget<=0:raise ValueError('Positive finite budget required')
    if price is not None and (not isfinite(price) or price<=0):raise ValueError('Invalid price')
    name=report['selection_locked_before_evaluation']
    reasons=[key for key,value in report['promotion_gates'].items() if value is not True]
    if instrument!='equity':reasons.append('Underlying stock research does not establish option performance')
    if price is None:reasons.append('Current executable price missing')
    elif not fractional_supported and budget<price:reasons.append('Budget cannot buy one whole SPY share')
    return {'candidate':name,'action':'RESEARCH_ONLY','execution_authorized':False,
            'failed_or_unverified_gates':reasons,'budget':budget,
            'strategy_generation_enabled':False,
            'next_step':'Fixed-rule forward paper observation with actual quote and fill evidence',
            'creator_alert_rules_changed':False}
