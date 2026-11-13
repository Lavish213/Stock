"""Hard stop for legacy order paths that bypass the production broker gate.

The only supported way to reach a broker is lavish_core/trade/broker.py,
which dispatches to broker_alpaca / options_broker (or the Tradier
equivalents). Those enforce the TRADE_MODE/endpoint binding, the live
readiness approval, a stable client_order_id, the pre-submit journal and
the kill switch. Several older scripts placed orders directly with the same
ALPACA_API_KEY/ALPACA_SECRET_KEY and none of those protections; each now
calls refuse_broker_path() before any network call.

tests/test_broker_side_doors.py fails if a new module outside the broker
layer gains an order endpoint or TradingClient without this guard.
"""
from __future__ import annotations


class QuarantinedBrokerPath(RuntimeError):
    """Raised instead of submitting an order from an ungated legacy path."""


def refuse_broker_path(path: str) -> None:
    raise QuarantinedBrokerPath(
        f"{path} is quarantined: it places orders without the production safety "
        "gate (mode/endpoint binding, live approval, idempotency, journal, kill "
        "switch). Route orders through lavish_core.trade.broker instead."
    )
