"""Offline Nautilus 1.x options execution smoke, NOT creator strategy replay.

Fixed invented quotes/orders/capital/fees. No production bot or broker imports.
No credentials or live adapter. Run in its dedicated pinned environment.
"""
from __future__ import annotations

import argparse
import json
import socket
from datetime import datetime, timezone
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path


def run(latency_ns: int = 0, quantity: int = 1) -> dict:
    if type(latency_ns) is not int or not 0 <= latency_ns <= 2_000_000_000:
        raise ValueError("Smoke latency must be 0..2 seconds")
    if type(quantity) is not int or not 1 <= quantity <= 3:
        raise ValueError("Smoke quantity must be 1..3 whole contracts")
    if version("nautilus_trader") != "1.231.0":
        raise RuntimeError("Use the isolated pinned Nautilus 1.231.0 environment")
    # Prevent accidental external connections, including future modifications.
    original_connect = socket.socket.connect
    def blocked(*args, **kwargs):
        raise RuntimeError("Research smoke prohibits network connections")
    socket.socket.connect = blocked
    try:
        return _run(latency_ns, quantity)
    finally:
        socket.socket.connect = original_connect


def _run(latency_ns: int, quantity: int) -> dict:
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.backtest.models import LatencyModel, PerContractFeeModel
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
    from nautilus_trader.model.currencies import USD
    from nautilus_trader.model.data import QuoteTick
    from nautilus_trader.model.enums import AccountType, AssetClass, OmsType, OptionKind, OrderSide
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import OptionContract
    from nautilus_trader.model.objects import Money, Price, Quantity
    from nautilus_trader.trading.strategy import Strategy

    def ns(value):
        return int(datetime.fromisoformat(value).timestamp()) * 1_000_000_000
    venue = Venue("SIM")
    instrument = OptionContract(
        instrument_id=InstrumentId(Symbol("AAPL261030C00345000"), venue),
        raw_symbol=Symbol("AAPL261030C00345000"), asset_class=AssetClass.EQUITY,
        exchange="SIM", currency=USD, price_precision=2,
        price_increment=Price.from_str("0.01"), multiplier=Quantity.from_int(100),
        lot_size=Quantity.from_int(1), underlying="AAPL", option_kind=OptionKind.CALL,
        strike_price=Price.from_str("345.00"), activation_ns=ns("2026-10-01T00:00:00+00:00"),
        expiration_ns=ns("2026-10-30T20:00:00+00:00"), ts_event=0, ts_init=0,
    )

    class RoundTrip(Strategy):
        def __init__(self):
            super().__init__()
            self.seen = 0
        def on_start(self):
            self.subscribe_quote_ticks(instrument.id)
        def on_quote_tick(self, tick):
            self.seen += 1
            if self.seen in (1, 6):
                order = self.order_factory.market(
                    instrument_id=instrument.id,
                    order_side=OrderSide.BUY if self.seen == 1 else OrderSide.SELL,
                    quantity=Quantity.from_int(quantity),
                )
                self.submit_order(order)

    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    try:
        engine.add_venue(venue=venue, oms_type=OmsType.NETTING,
                         account_type=AccountType.CASH, base_currency=USD,
                         starting_balances=[Money(1000, USD)],
                         fee_model=PerContractFeeModel(commission=Money(.65, USD)),
                         latency_model=LatencyModel(base_latency_nanos=latency_ns))
        engine.add_instrument(instrument)
        start = ns("2026-10-20T13:30:00+00:00")
        quotes = []
        for i in range(12):
            bid = Decimal("1.00") + Decimal(i) / 100
            quotes.append(QuoteTick(instrument_id=instrument.id,
                bid_price=Price.from_str(str(bid)), ask_price=Price.from_str(str(bid + Decimal(".02"))),
                bid_size=Quantity.from_int(100), ask_size=Quantity.from_int(100),
                ts_event=start + i * 1_000_000_000, ts_init=start + i * 1_000_000_000))
        engine.add_data(quotes)
        engine.add_strategy(RoundTrip())
        engine.run()
        fills = engine.trader.generate_order_fills_report()
        account = engine.cache.account_for_venue(venue)
        cash = float(account.balance_total(USD).as_decimal())
        normalized = []
        for _, row in fills.iterrows():
            normalized.append({**{key: str(row[key]) for key in
                ("side", "filled_qty", "avg_px", "commissions", "ts_last")},
                "timestamp_ns": int(row["ts_last"].value)})
        # Independent cash arithmetic; do not trust an engine's P&L label alone.
        expected = Decimal("1000")
        for row in normalized:
            sign = -1 if row["side"] == "BUY" else 1
            expected += sign * Decimal(row["filled_qty"]) * Decimal(row["avg_px"]) * 100
            expected -= Decimal(".65") * Decimal(row["filled_qty"])
        flat = len(engine.cache.positions_open()) == 0
        checks = dict(two_fills=len(normalized) == 2, flat=flat,
            cash_conserved=abs(cash - float(expected)) < 1e-7,
            correct_sides=[row["side"] for row in normalized] == ["BUY", "SELL"],
            whole_quantity=all(Decimal(row["filled_qty"]) == quantity for row in normalized),
            no_early_fills=len(normalized) == 2 and all(
                row["timestamp_ns"] >= start + offset * 1_000_000_000 + latency_ns
                for row, offset in zip(normalized, (0, 5))))
        passed = all(checks.values())
        return dict(schema_version=1, synthetic=True, real_broker_orders=0,
            creator_replay=False, profitability_evidence=False, production_configuration_changed=False,
            engine="nautilus_trader", engine_version=version("nautilus_trader"),
            observed_at=datetime.now(timezone.utc).isoformat(), latency_ns=latency_ns,
            quantity=quantity, quote_count=len(quotes), fills=normalized,
            final_cash=cash, independently_expected_cash=float(expected), flat=flat,
            checks=checks, passed=passed,
            limitations=["Invented fixed tape and entry/exit events; not the production parser or strategy",
                         "Deep synthetic top-of-book liquidity; no calibrated queue or partial-fill behavior",
                         "Fees, capital and latency are lab assumptions, not approved paper/live limits"])
    finally:
        engine.dispose()


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--latency-ns", type=int, default=0)
    cli.add_argument("--quantity", type=int, default=1)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    if args.output.exists():
        cli.error("Refusing to overwrite existing evidence")
    result = run(args.latency_ns, args.quantity)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(result, output, indent=2)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
