#!/usr/bin/env python3
# run_sims_loop.py
# Continuous simulation runner - repeats simulate_ticker_watch.py's
# generate -> classify -> backtest pipeline indefinitely with a fresh
# random seed each round, appending one structured JSONL result line per
# round to --log so trends (classification mix, win rate, P&L) are
# visible across many runs instead of read once and discarded. Meant to
# be left running on a machine with real internet access (this sandbox's
# network proxy blocks both Alpaca and Yahoo Finance - confirmed directly -
# so every round run there would show 0 simulated trades; this is only
# useful run somewhere with a real connection).
#
# This never touches a real broker or submits any order - it's pure
# backtesting against historical price data, same as
# simulate_ticker_watch.py itself, just looped and logged. For real paper
# orders, use simulate_alerts.py --live or the actual bot (run_ingest.py).
#
# Usage:
#   python3 run_sims_loop.py                       # forever, 5 min between rounds
#   python3 run_sims_loop.py --interval 60          # every 60s
#   python3 run_sims_loop.py --iterations 20        # stop after 20 rounds
#   python3 run_sims_loop.py --log sims.jsonl        # custom log path
from __future__ import annotations
import argparse
import json
import random
import signal
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from simulate_ticker_watch import (
    TOP_TICKERS, _build_price_lookups, generate_synthetic_alerts, classify_and_route,
)
from ml.backtester import BacktestSignal, run_backtest
from lavish_core.reporting.option_estimator import estimate_option_pnl

_stop = False


def _handle_stop(signum, frame):
    global _stop
    _stop = True
    print("\nStopping after this round (signal received)...")


def _run_one_round(seed: int, days: int, alerts_per_ticker: int, max_hold_days: int) -> dict:
    rng = random.Random(seed)
    end = date.today()
    start = end - timedelta(days=days)

    price_lookups = _build_price_lookups(TOP_TICKERS, start, end)
    real_price_tickers = [t for t in TOP_TICKERS if price_lookups.get(t) is not None]

    alerts = generate_synthetic_alerts(TOP_TICKERS, start, end, alerts_per_ticker, rng,
                                        price_lookups=price_lookups)
    results = [classify_and_route(a) for a in alerts]

    counts: dict = {}
    for r in results:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1

    equity_signals = [
        BacktestSignal(symbol=r["ticker"], entry_date=a.date, source="synthetic")
        for a, r in zip(alerts, results) if r["outcome"] == "trade:equity" and r["action"] == "BUY"
    ]
    backtest = run_backtest(equity_signals, max_hold_days=max_hold_days) if equity_signals else None

    options_trades = [(a, r) for a, r in zip(alerts, results) if r["outcome"] == "trade:options"]
    option_pnls = []
    for a, r in options_trades[:25]:  # capped - Black-Scholes estimate per contract is not free
        try:
            expiry = a.intended_expiry or date.fromisoformat(r["expiry"])
            dte = max(1, (expiry - a.date).days)
            est = estimate_option_pnl(ticker=r["ticker"], dte_days=dte, entry_date=a.date, exit_date=expiry,
                                       strike=r["strike"], option_type=r["side"].lower())
            if est:
                option_pnls.append(est.pnl_pct_est)
        except Exception:
            continue

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "window_days": days,
        "real_price_tickers": len(real_price_tickers),
        "total_tickers": len(TOP_TICKERS),
        "n_alerts": len(alerts),
        "classification": counts,
        "equity_backtest": backtest,
        "options_estimate_count": len(option_pnls),
        "options_avg_pnl_pct_est": round(sum(option_pnls) / len(option_pnls), 4) if option_pnls else None,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--interval", type=float, default=300.0, help="seconds between rounds (default 300 = 5 min)")
    p.add_argument("--iterations", type=int, default=0, help="stop after this many rounds (0 = run forever)")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--alerts-per-ticker", type=int, default=25)
    p.add_argument("--max-hold-days", type=int, default=10)
    p.add_argument("--log", default="sims_loop_results.jsonl")
    args = p.parse_args()

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    log_path = Path(args.log)
    round_num = 0
    cumulative = {"would_trade": 0, "total_alerts": 0, "simulated_trades": 0, "total_pnl": 0.0}

    print(f"Logging each round to {log_path.resolve()}. Ctrl+C to stop cleanly.\n")

    while not _stop:
        round_num += 1
        seed = random.SystemRandom().randint(1, 2**31 - 1)
        try:
            result = _run_one_round(seed, args.days, args.alerts_per_ticker, args.max_hold_days)
        except Exception as e:
            print(f"[round {round_num}] FAILED: {e}")
            result = {"ts": datetime.now(timezone.utc).isoformat(), "seed": seed, "error": str(e)}

        with open(log_path, "a") as f:
            f.write(json.dumps(result, default=str) + "\n")

        would_trade = sum(v for k, v in result.get("classification", {}).items() if k.startswith("trade:"))
        cumulative["would_trade"] += would_trade
        cumulative["total_alerts"] += result.get("n_alerts", 0)
        bt = result.get("equity_backtest")
        if bt:
            cumulative["simulated_trades"] += bt.get("simulated_trades", 0)
            cumulative["total_pnl"] += bt.get("total_pnl", 0) or 0

        print(f"[round {round_num}] seed={seed} alerts={result.get('n_alerts')} "
              f"would_trade={would_trade} real_price_tickers={result.get('real_price_tickers')}/{result.get('total_tickers')} "
              f"backtest_trades={bt.get('simulated_trades') if bt else 0} "
              f"cum_pnl=${cumulative['total_pnl']:.2f}")

        if args.iterations and round_num >= args.iterations:
            break
        if _stop:
            break
        time.sleep(args.interval)

    print(f"\nStopped after {round_num} round(s). Cumulative: {cumulative}")
    print(f"Full history in {log_path.resolve()}")


if __name__ == "__main__":
    main()
