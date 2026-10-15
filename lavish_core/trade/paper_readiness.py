"""Supervisor gate: missing evidence halts entries and preserves exit workers.

Lab successes cannot mint approvals. A valid approval never clears a halt.
"""
import os
from pathlib import Path

from lavish_core.trade.live_readiness import validate_approval
from lavish_core.utils.safety import finite_number


def enforce(store, *, account=None, path=None):
    if os.getenv('TRADE_MODE', 'dry') != 'paper':
        return {'paper_ready': False, 'reasons': ['not_paper_mode']}
    path = path or os.getenv('LAVISH_PAPER_READINESS_FILE', str(Path(__file__).resolve().parents[2]/'data/paper-readiness.json'))
    try:
        if account is None:
            from lavish_core.trade.broker import get_account
            account = get_account()
        identity = account.get('id') or account.get('account_number')
        if not isinstance(identity, str) or not identity:
            raise ValueError('Paper account identity unavailable')
        approval = validate_approval(path, account_id=identity, mode='paper')
        if not approval['paper_ready']:
            raise ValueError('Paper evidence not approved')
        limits = approval['limits']
        budgets = [finite_number(os.getenv(k, '0'), positive=True) for k in ('DEFAULT_TRADE_DOLLARS', 'DEFAULT_OPTION_TRADE_DOLLARS')]
        if max(budgets) > limits['max_trade_usd']:
            raise ValueError('Paper budgets exceed approval')
        risk = store.get_risk_limits()
        baseline = finite_number(risk.get('circuit_breaker_day_baseline_equity'), positive=True)
        equity = finite_number(account.get('equity'), positive=True)
        if baseline - equity >= limits['max_daily_loss_usd']:
            raise ValueError('Paper daily loss limit reached')
        # Tighten existing exposure caps; never enlarge them during approval.
        # Write back only the keys actually changed (not the whole risk_limits
        # table) since this runs on every watchdog tick.
        for key, cap in (('max_gross_exposure', limits['max_gross_exposure']), ('max_pos_per_symbol', limits['max_trade_usd'])):
            store.set_risk_limit(key, min(finite_number(risk.get(key, cap), positive=True), cap))
        return approval
    except Exception:
        reason = 'Automatic paper entries blocked: verified source, lifecycle, deployment, budgets and account evidence required'
        try:
            store.set_kill_switch(True, reason)
        except Exception:
            pass
        return {'paper_ready': False, 'reasons': [reason]}
