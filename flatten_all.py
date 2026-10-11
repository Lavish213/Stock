#!/usr/bin/env python3
# flatten_all.py
# Emergency "close EVERYTHING right now" control - run this on the box the
# bot is actually running on. Closes every open position at the real
# broker (equity AND options - see TradeAgent.flatten_all()'s docstring
# for the real bug this used to have: it only ever read this bot's own
# local ledger, which never recorded options positions at all). Also turns
# the kill switch ON by default, since flattening everything but leaving
# new entries enabled would let the bot immediately re-open a position
# right after this runs - defeating the point of an emergency stop.
#
# This is DESTRUCTIVE and irreversible once orders are filled - requires
# --confirm. Respects LIVE_TRADE the same way TradeAgent always has
# (LIVE_TRADE=true -> real broker orders; unset/false -> dry mode, safe to
# run with no real effect, e.g. to see what WOULD be closed).
#
# Usage:
#   python3 flatten_all.py --confirm --reason "manual emergency"
#   python3 flatten_all.py --confirm --reason "runaway bug" --no-kill-switch
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from lavish_core.trade.trade_agent import TradeAgent
from lavish_core.db.hybrid_store import HybridStore, DEFAULT_DB
from lavish_core.utils.alerts import post_discord


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--confirm", action="store_true", required=True,
                    help="required - this closes real positions, there is no undo")
    p.add_argument("--reason", default="manual_flatten", help="recorded in the order meta/log/alert")
    p.add_argument("--no-kill-switch", action="store_true",
                    help="skip turning the kill switch on (new entries stay enabled after flattening)")
    args = p.parse_args()

    agent = TradeAgent()
    print(f"Flattening all positions (mode={agent.default_mode}, reason={args.reason!r})...")
    result = agent.flatten_all(reason=args.reason)

    n_ok = sum(1 for d in result.get("details", []) if isinstance(d, dict) and d.get("status") != "error")
    n_err = len(result.get("details", [])) - n_ok
    print(f"Done: {result['status']}, {n_ok} closed, {n_err} failed, out of {result['n']} position(s).")
    for d in result.get("details", []):
        print(" ", d)

    if not args.no_kill_switch:
        store = HybridStore(duckdb_path=str(DEFAULT_DB))
        store.set_kill_switch(True, f"flatten_all: {args.reason}")
        print("Kill switch turned ON - new entries are now blocked too.")

    post_discord(f"🚨 flatten_all ran (reason: {args.reason}): {n_ok} position(s) closed, "
                  f"{n_err} failed. {'Kill switch turned ON.' if not args.no_kill_switch else 'Kill switch left unchanged.'}")

    if n_err:
        print(f"\n{n_err} position(s) FAILED to close - check the log and close them manually.")
        sys.exit(1)


if __name__ == "__main__":
    main()
