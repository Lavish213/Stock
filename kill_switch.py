#!/usr/bin/env python3
# kill_switch.py
# Remote "stop all new entries" control, reachable over SSH without
# touching code or redeploying - run this on the box the bot is actually
# running on (it reads the same DuckDB file run_ingest.py uses). Blocks
# new BUY entries (equity and options) immediately on the next alert;
# existing open positions keep being watched and can still exit normally -
# a kill switch that could trap you IN a position would be worse than no
# kill switch at all. For an actual emergency flatten of everything
# already open, run flatten_all.py separately (it also turns this kill
# switch on by default) - this script only stops new risk, it doesn't
# close existing risk.
#
# Usage:
#   python3 kill_switch.py --on "reason text"
#   python3 kill_switch.py --off
#   python3 kill_switch.py --status
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from lavish_core.db.hybrid_store import HybridStore, DEFAULT_DB
from lavish_core.utils.alerts import post_discord
from lavish_core.utils.control_file import read_control,write_control


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--on", metavar="REASON", help="halt all new entries, with a reason recorded for the log/alert")
    g.add_argument("--off", action="store_true", help="resume normal trading")
    g.add_argument("--status", action="store_true", help="print the current state without changing it")
    p.add_argument("--db",type=Path,default=None,help="Override bot database/control path")
    args = p.parse_args()

    db_path=args.db or DEFAULT_DB

    if args.status:
        state=read_control(db_path)
        if state is None:
            try:
                store=HybridStore(db_path)
                state=store.get_kill_switch()
                store.con.close()
            except Exception:
                state=(True,"Database locked and control state unavailable")
        enabled,reason=state
        if enabled:
            print(f"KILL SWITCH IS ON - new entries are blocked. Reason: {reason!r}")
        else:
            print("Kill switch is off - trading normally.")
        return

    if args.on:
        write_control(db_path,True,args.on)
        print(f"Kill switch turned ON. New entries will be refused starting with the next alert. Reason: {args.on!r}")
        print("Existing open positions are unaffected and will keep exiting normally.")
        post_discord(f"🛑 Kill switch turned ON: {args.on}. New entries blocked; open positions keep exiting normally.")
    else:
        write_control(db_path,False,"")
        print("Kill switch turned OFF. Trading resumes normally.")
        post_discord("✅ Kill switch turned OFF. Trading resumes normally.")


if __name__ == "__main__":
    main()
