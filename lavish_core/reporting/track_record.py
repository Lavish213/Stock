# lavish_core/reporting/track_record.py
# Orders/fills/signals were being logged to DuckDB all along (HybridStore)
# but nothing ever read them back as a track record - the only way to know
# whether the bot was actually making money was to eyeball log files.
#
# Usage:
#   python -m lavish_core.reporting.track_record [--days 30] [--db path]
#
# Both paths are now fully recorded: options round trips (entry + the exit
# monitor's exit fill, always logged under the same order_id), and equity
# bracket round trips (entry + whichever take_profit/stop_loss leg fills,
# watched by equity_exit_monitor.py and logged under the same order_id).
# An entry that still shows up under "open entries" below means exactly
# that - no exit fill has been observed by this bot yet, not "untracked."
# The one case genuinely outside what this bot can see: a position closed
# by something other than its own bracket (manual intervention outside the
# bot, a day order expiring unfilled with the position closed some other
# way) - equity_exit_monitor.py reports that as "no_fill" in its own logs
# rather than fabricating an exit fill that never happened.
from __future__ import annotations
import argparse, json, re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from lavish_core.db.hybrid_store import HybridStore, DEFAULT_DB
from ml.backtester import fetch_price_history

_OCC_RE = re.compile(r"^([A-Z]+)\d{6}[CP]\d{8}$")


def _underlying(symbol: str) -> str:
    m = _OCC_RE.match(symbol.upper())
    return m.group(1) if m else symbol.upper()


def _is_option(symbol: str) -> bool:
    return bool(_OCC_RE.match(symbol.upper()))


def _to_date(ts: Any) -> date:
    return ts.date() if hasattr(ts, "date") else datetime.fromisoformat(str(ts)).date()


def _nearest_close(hist, target: date) -> Optional[float]:
    """Nearest available close on/after target, falling back to the last
    available row if target is beyond the fetched history's end."""
    if hist is None or hist.empty:
        return None
    on_or_after = hist.loc[hist.index >= target]
    if not on_or_after.empty:
        return float(on_or_after.iloc[0]["Close"])
    return float(hist.iloc[-1]["Close"])


def _load_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def build_report(store: HybridStore, days: int = 30, benchmark: str = "SPY") -> Dict[str, Any]:
    if isinstance(days,bool) or not isinstance(days,int) or days < 1:
        raise ValueError("days must be a positive integer")
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=days)
    # Entry lots may precede the report window. Consume all history to
    # establish cost basis, then include realized exits in the window.
    fills = store.fetchall("SELECT order_id,symbol,side,qty,price,ts,fee FROM fills ORDER BY ts ASC,id ASC")
    orders = store.fetchall("SELECT id,meta,status FROM orders")
    order_meta: Dict[str, Dict[str, Any]] = {oid: _load_json(meta) for oid, meta, _status in orders}

    # FIFO lots pair separate broker orders and retain partially exited lots.
    lots = defaultdict(list)
    matches = defaultdict(list)
    unmatched_exits = []
    for oid, symbol, side, qty, price, stamp, fee in fills:
        qty, price, fee = float(qty), float(price), float(fee or 0)
        if side == "buy":
            lots[symbol].append(dict(order_id=oid,qty=qty,price=price,ts=stamp,fee_per_qty=fee/qty))
            continue
        remaining = qty
        # Linked exits take their own entry first; other exits consume FIFO.
        candidates = sorted(lots[symbol], key=lambda lot: lot["order_id"] != oid)
        for lot in candidates:
            if remaining <= 0:
                break
            used = min(remaining, lot["qty"])
            if used <= 0:
                continue
            multiplier = 100 if _is_option(symbol) else 1
            pnl = (price-lot["price"])*used*multiplier - used*(lot["fee_per_qty"]+fee/qty)
            if stamp >= cutoff:
                matches[lot["order_id"]].append(dict(symbol=symbol,qty=used,entry=lot["price"],exit=price,pnl=pnl,entry_ts=lot["ts"],exit_ts=stamp))
            lot["qty"] -= used
            remaining -= used
        if remaining > 0 and stamp >= cutoff:
            unmatched_exits.append(dict(order_id=oid,symbol=symbol,qty=remaining))
    round_trips = []
    open_entries = []
    for oid, rows in matches.items():
        quantity = sum(row["qty"] for row in rows)
        entry = sum(row["entry"]*row["qty"] for row in rows)/quantity
        exit_price = sum(row["exit"]*row["qty"] for row in rows)/quantity
        symbol = rows[0]["symbol"]
        meta = order_meta.get(oid,{})
        expected = meta.get("expected_price")
        round_trips.append(dict(order_id=oid,symbol=symbol,underlying=_underlying(symbol),source=meta.get("source","unknown"),entry_price=round(entry,4),exit_price=round(exit_price,4),qty=quantity,pnl=round(sum(row["pnl"] for row in rows),2),slippage=round(entry-float(expected),4) if expected else None,entry_date=_to_date(min(row["entry_ts"] for row in rows)),exit_date=_to_date(max(row["exit_ts"] for row in rows)),trade_return_pct=round((exit_price-entry)/entry*100,2)))
    residual = defaultdict(list)
    for symbol, entries in lots.items():
        for lot in entries:
            if lot["qty"] > 0:
                residual[(symbol,lot["order_id"])].append(lot)
    for (symbol,oid), entries in residual.items():
        quantity = sum(lot["qty"] for lot in entries)
        entry = sum(lot["qty"]*lot["price"] for lot in entries)/quantity
        meta = order_meta.get(oid,{})
        expected = meta.get("expected_price")
        open_entries.append(dict(order_id=oid,symbol=symbol,source=meta.get("source","unknown"),entry_price=round(entry,4),qty=quantity,slippage=round(entry-float(expected),4) if expected else None))

    # Benchmark comparison (the "ExcessReturn vs SPY" idea from Quiver's
    # congressional-trading data, which attaches this to every single
    # disclosed trade automatically rather than as an afterthought) - one
    # fetch covering the whole window, not one per trade. Naive comparison
    # (trade % return vs SPY's % return over the same calendar dates), same
    # as Quiver's field does - not risk-adjusted, and deliberately not
    # pretending an option premium's % move is the same risk profile as
    # holding SPY. It answers "did this beat just holding the market over
    # the same days," nothing more.
    if round_trips:
        earliest_entry = min(t["entry_date"] for t in round_trips)
        latest_exit = max(t["exit_date"] for t in round_trips)
        spy_hist = fetch_price_history(benchmark, earliest_entry, latest_exit)
        for t in round_trips:
            spy_entry = _nearest_close(spy_hist, t["entry_date"])
            spy_exit = _nearest_close(spy_hist, t["exit_date"])
            if spy_entry and spy_exit and t["trade_return_pct"] is not None:
                spy_return_pct = round((spy_exit - spy_entry) / spy_entry * 100, 2)
                t["spy_return_pct"] = spy_return_pct
                t["excess_return_vs_spy_pct"] = round(t["trade_return_pct"] - spy_return_pct, 2)
            else:
                t["spy_return_pct"] = None
                t["excess_return_vs_spy_pct"] = None

    total_pnl = sum(t["pnl"] for t in round_trips)
    wins = [t for t in round_trips if t["pnl"] > 0]
    losses = [t for t in round_trips if t["pnl"] <= 0]
    win_rate = (len(wins) / len(round_trips)) if round_trips else None

    by_symbol: Dict[str, float] = defaultdict(float)
    by_source: Dict[str, float] = defaultdict(float)
    for t in round_trips:
        by_symbol[t["underlying"]] += t["pnl"]
        by_source[t["source"]] += t["pnl"]

    slippages = [t["slippage"] for t in round_trips if t["slippage"] is not None]
    avg_slippage = round(sum(slippages) / len(slippages), 4) if slippages else None

    excess_returns = [t["excess_return_vs_spy_pct"] for t in round_trips if t["excess_return_vs_spy_pct"] is not None]
    avg_excess_return = round(sum(excess_returns) / len(excess_returns), 2) if excess_returns else None
    beat_benchmark = len([e for e in excess_returns if e > 0])

    return {
        "window_days": days,
        "benchmark": benchmark,
        "closed_round_trips": len(round_trips),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 3) if win_rate is not None else None,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl_per_trade": round(total_pnl / len(round_trips), 2) if round_trips else None,
        "avg_entry_slippage": avg_slippage,
        "avg_excess_return_vs_benchmark_pct": avg_excess_return,
        "trades_beating_benchmark": f"{beat_benchmark}/{len(excess_returns)}" if excess_returns else None,
        "pnl_by_underlying": dict(sorted(by_symbol.items(), key=lambda kv: -kv[1])),
        "pnl_by_source": dict(by_source),
        "open_entries_no_recorded_exit": len(open_entries),
        "open_entries": open_entries,
        "round_trips": round_trips,
        "unmatched_exits": unmatched_exits,
    }


def print_report(report: Dict[str, Any]) -> None:
    print(f"\n=== Lavish_bot track record (last {report['window_days']} days) ===")
    print(f"Closed round trips: {report['closed_round_trips']} "
          f"(wins={report['wins']} losses={report['losses']} "
          f"win_rate={report['win_rate']})")
    print(f"Total P&L: ${report['total_pnl']:.2f}" if report['total_pnl'] is not None else "Total P&L: n/a")
    if report['avg_pnl_per_trade'] is not None:
        print(f"Avg P&L/trade: ${report['avg_pnl_per_trade']:.2f}")
    if report['avg_entry_slippage'] is not None:
        print(f"Avg entry slippage (actual - expected): ${report['avg_entry_slippage']:.4f}")
    if report['avg_excess_return_vs_benchmark_pct'] is not None:
        print(f"Avg excess return vs {report['benchmark']} (same holding period, not risk-adjusted): "
              f"{report['avg_excess_return_vs_benchmark_pct']:+.2f}%")
        print(f"Trades beating {report['benchmark']}: {report['trades_beating_benchmark']}")
    print(f"\nP&L by underlying:")
    for sym, pnl in report["pnl_by_underlying"].items():
        print(f"  {sym:6s} ${pnl:.2f}")
    print(f"\nP&L by source:")
    for src, pnl in report["pnl_by_source"].items():
        print(f"  {src:12s} ${pnl:.2f}")
    print(f"\nOpen entries with no recorded exit fill: {report['open_entries_no_recorded_exit']}")
    print("(still genuinely open, or closed by something other than the bot's own bracket/exit "
          "monitor - see module docstring)")


def main() -> None:
    p = argparse.ArgumentParser(description="Lavish_bot track record / audit report")
    p.add_argument("--days", type=int, default=30, help="lookback window in days (default 30)")
    p.add_argument("--db", type=str, default=str(DEFAULT_DB), help="path to the DuckDB file")
    p.add_argument("--json", action="store_true", help="print raw JSON instead of a formatted report")
    args = p.parse_args()

    store = HybridStore(duckdb_path=args.db)
    report = build_report(store, days=args.days)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
