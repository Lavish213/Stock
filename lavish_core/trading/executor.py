# lavish_core/trading/executor.py
import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

from lavish_core.utils.quarantine import refuse_broker_path

TRADE_MODE = os.getenv("TRADE_MODE", "mock").lower()
DEFAULT_QTY = int(os.getenv("DEFAULT_QTY", "1"))

@dataclass
class TradeSignal:
    action: str     # BUY / SELL
    symbol: str     # e.g. AAPL
    qty: int        # shares
    reason: str     # free text – “from OCR”, “from Patreon”, etc.

class TradeExecutor:
    def __init__(self):
        self.mode = TRADE_MODE
        if self.mode != "mock":
            # Quarantined: this used alpaca-py's TradingClient for market
            # orders with no endpoint check, idempotency, journal or kill
            # switch. Only the mock mode remains.
            refuse_broker_path("lavish_core.trading.executor.TradeExecutor")
        self.client = None  # mock

    def place(self, sig: TradeSignal) -> dict:
        """Places or simulates a market order. Returns a dict summary."""
        summary = {
            "mode": self.mode, "action": sig.action, "symbol": sig.symbol,
            "qty": sig.qty, "reason": sig.reason
        }

        if self.mode == "mock":
            summary["status"] = "SIMULATED"
            print(f"🟡 [MOCK] {sig.action} {sig.qty} {sig.symbol} — {sig.reason}")
            return summary

        refuse_broker_path("lavish_core.trading.executor.TradeExecutor.place")