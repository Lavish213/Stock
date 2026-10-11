# lavish_core/trade/broker.py
# Single switch point for which broker backend is live, controlled by
# BROKER_PROVIDER (alpaca|tradier, default alpaca). Every other module in
# this codebase (trade_handler.py, trade_agent.py, circuit_breaker.py,
# pdt_guard.py, reconcile.py, account_monitor.py, portfolio_risk.py, both
# exit monitors) imports its broker functions from HERE instead of
# directly from broker_alpaca.py/options_broker.py or
# broker_tradier.py/options_broker_tradier.py - so the rest of the bot
# runs against whichever broker is configured without knowing which one
# it is.
#
# Both backends expose identical function names/signatures (see each
# module's own docstring) and return/accept Alpaca-shaped dicts -
# broker_tradier.py/options_broker_tradier.py do the field-name
# translation themselves, so nothing downstream of this file needs any
# broker-specific branching... except the one place that genuinely can't
# be papered over: Alpaca manages bracket (take_profit/stop_loss) exits
# server-side, and Tradier has no single-call equivalent. See
# SUPPORTS_NATIVE_BRACKETS below - trade_handler.py uses it to decide
# whether to expect bracket legs back from place_order(), or start the
# active equity exit monitor (equity_exit_monitor.watch_and_exit_active)
# instead.
from __future__ import annotations
import os

BROKER_PROVIDER = os.getenv("BROKER_PROVIDER", "alpaca").strip().lower()

SUPPORTS_NATIVE_BRACKETS = BROKER_PROVIDER == "alpaca"

if BROKER_PROVIDER == "tradier":
    from lavish_core.trade.broker_tradier import (
        get_account,
        get_position,
        get_positions,
        place_order,
        get_order,
        get_order_by_client_id,
        cancel_order,
        latest_quote,
        get_clock,
    )
    from lavish_core.trade.options_broker_tradier import (
        find_contract,
        latest_option_quote,
        place_option_order,
        resolve_and_price_contract,
    )
elif BROKER_PROVIDER == "alpaca":
    from lavish_core.trade.broker_alpaca import (
        get_account,
        get_position,
        get_positions,
        place_order,
        get_order,
        get_order_by_client_id,
        cancel_order,
        latest_quote,
        get_clock,
    )
    from lavish_core.trade.options_broker import (
        find_contract,
        latest_option_quote,
        place_option_order,
        resolve_and_price_contract,
    )
else:
    raise RuntimeError(
        f"Unknown BROKER_PROVIDER={BROKER_PROVIDER!r} - must be 'alpaca' or 'tradier'"
    )
