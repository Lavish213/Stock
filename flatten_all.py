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
# --confirm. Mode comes from TRADE_MODE (the same variable the running bot
# uses): paper/live close real broker positions. TRADE_MODE=dry refuses to
# run unless --dry-run is passed, because a dry "flatten" closes nothing at
# the broker - previously it printed "flattened" and alerted that positions
# were closed while every real position stayed open. A --dry-run preview
# changes no state and sends no alert. If the broker cannot be read, the
# kill switch is still turned on (unless --no-kill-switch), a FAILED alert
# is sent and the exit code is non-zero.
#
# Usage:
#   python3 flatten_all.py --confirm --reason "manual emergency"
#   python3 flatten_all.py --confirm --reason "runaway bug" --no-kill-switch
#   python3 flatten_all.py --confirm --dry-run      # TRADE_MODE=dry preview only
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


FILLED = {"filled"}
FAILED_STATUSES = {"error", "rejected", "canceled", "cancelled", "expired", "done_for_day", "suspended"}


def classify_closes(details):
    """(confirmed_filled, submitted_unconfirmed, failed). Only an explicit
    "filled" counts as closed: a rejected/canceled close, or one still
    "new"/"accepted"/"partially_filled" at the broker, leaves exposure open
    and must never be reported to the operator as closed."""
    n_ok = n_pending = n_err = 0
    for d in details:
        status = str(d.get("status", "")).lower() if isinstance(d, dict) else ""
        if status in FILLED:
            n_ok += 1
        elif not status or status in FAILED_STATUSES:
            n_err += 1
        else:
            n_pending += 1
    return n_ok, n_pending, n_err


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--confirm", action="store_true", required=True,
                    help="required - this closes real positions, there is no undo")
    p.add_argument("--reason", default="manual_flatten", help="recorded in the order meta/log/alert")
    p.add_argument("--no-kill-switch", action="store_true",
                    help="skip turning the kill switch on (new entries stay enabled after flattening)")
    p.add_argument("--dry-run", action="store_true",
                    help="required when TRADE_MODE=dry: preview against the local ledger only; "
                         "closes nothing at the broker, changes no state, sends no alert")
    args = p.parse_args()

    agent = TradeAgent()
    mode = agent.default_mode
    if mode == "dry" and not args.dry_run:
        print("REFUSING: TRADE_MODE is 'dry' (or unset), so nothing would be closed at the broker.\n"
              "Set TRADE_MODE=paper or TRADE_MODE=live to match the running bot, "
              "or pass --dry-run for a local-ledger preview.", file=sys.stderr)
        sys.exit(2)
    if mode != "dry" and args.dry_run:
        print(f"REFUSING: --dry-run given but TRADE_MODE={mode!r} would place real broker orders.", file=sys.stderr)
        sys.exit(2)

    label = "DRY RUN - nothing is sent to the broker" if mode == "dry" else f"mode={mode}"
    print(f"Flattening all positions ({label}, reason={args.reason!r})...")
    result = agent.flatten_all(reason=args.reason)

    details = result.get("details", [])
    n_ok, n_pending, n_err = classify_closes(details)
    failed = result.get("status") != "flattened"
    if failed:
        print(f"FAILED: {result.get('reason', result.get('status'))} - broker positions were NOT closed.")
    else:
        print(f"Done: {n_ok} confirmed filled, {n_pending} submitted but NOT confirmed, "
              f"{n_err} failed, out of {result['n']} position(s).")
    for d in details:
        print(" ", d)

    if mode == "dry":
        print("Dry run complete: kill switch unchanged, no alert sent.")
        return

    if not args.no_kill_switch:
        store = HybridStore(duckdb_path=str(DEFAULT_DB))
        store.set_kill_switch(True, f"flatten_all: {args.reason}")
        print("Kill switch turned ON - new entries are now blocked too.")

    kill_note = "Kill switch turned ON." if not args.no_kill_switch else "Kill switch left unchanged."
    if failed:
        post_discord(f"🚨 flatten_all FAILED (reason: {args.reason}, mode={mode}): "
                      f"{result.get('reason', result.get('status'))}. Positions were NOT closed - "
                      f"close them manually at the broker. {kill_note}")
        sys.exit(1)

    post_discord(f"🚨 flatten_all ran (reason: {args.reason}, mode={mode}): {n_ok} position(s) confirmed closed, "
                  f"{n_pending} submitted but not confirmed, {n_err} failed. {kill_note}")

    if n_err or n_pending:
        print(f"\n{n_err} failed and {n_pending} unconfirmed close(s) - verify at the broker "
              f"and close anything still open manually.")
        sys.exit(1)


if __name__ == "__main__":
    main()
