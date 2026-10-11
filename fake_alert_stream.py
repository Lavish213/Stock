#!/usr/bin/env python3
# fake_alert_stream.py
# Posts a stream of realistic-looking fake trade alerts into a Discord
# channel on a randomized interval, so you can watch the real bot
# (run_ingest.py's Discord listener) read, parse, risk-check, and act on
# them close to live, without waiting for the real creator to post.
# Discord's listener is a live websocket connection (near-instant), unlike
# Patreon's ~20s poll cycle - this is the fast, satisfying way to watch
# the bot actually move.
#
# IMPORTANT - channel separation, read this before running:
#   This posts via a Discord WEBHOOK, which is NOT the same as
#   DISCORD_WEBHOOK_URL in your .env (that one is the bot's OWN outgoing
#   alerts - circuit breaker trips, kill switch toggles, etc.). Create a
#   SEPARATE webhook for this script, in your OWN test server/channel -
#   never point this at a real creator's server (you'd need to own that
#   channel to add a webhook there anyway, so this can't be used to spam
#   someone else's server). And make sure this script's target channel is
#   NOT the same channel DISCORD_WEBHOOK_URL posts the bot's own alerts
#   into, if that channel is also in DISCORD_WATCH_CHANNEL_IDS - otherwise
#   the bot would read its own "circuit breaker tripped" messages back as
#   if they were new trading alerts and try to parse them.
#
# Setup:
#   1. Create your own test Discord server (free, instant).
#   2. Add a channel, e.g. #test-alerts.
#   3. Server Settings -> Integrations -> Webhooks -> New Webhook, pick
#      that channel, copy its URL.
#   4. Point DISCORD_WATCH_CHANNEL_IDS (in .env, for run_ingest.py) at
#      that SAME channel's ID (Developer Mode -> right-click channel ->
#      Copy Channel ID).
#   5. Run this script with that webhook URL - separately from the bot,
#      in another terminal, while run_ingest.py is running.
#
# Usage:
#   python3 fake_alert_stream.py --webhook-url https://discord.com/api/webhooks/...
#   python3 fake_alert_stream.py --webhook-url ... --min-interval 10 --max-interval 60
#   python3 fake_alert_stream.py --webhook-url ... --count 20
from __future__ import annotations
import argparse
import os
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")


def _next_friday(from_date: date | None = None) -> date:
    d = from_date or date.today()
    days_ahead = (4 - d.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return d + timedelta(days=days_ahead)


def _whitelist_tickers() -> list[str]:
    raw = os.getenv(
        "WHITELIST_TICKERS",
        "AAPL,MSFT,AMD,NVDA,META,TSLA,SPY,QQQ,GOOGL,CRM,MSTR,MU,HOOD,ATAI,NVO,UNH,AVGO,CRWV,NFLX,AMZN,DDOG",
    )
    return [t.strip().upper() for t in raw.split(",") if t.strip()]


_OFF_WHITELIST_NOISE = ["XYZ", "FOOBAR", "ZZZZ"]


def _random_strike(base: float) -> float:
    # A round-ish number near a plausible price, like a real alert.
    return round(base * random.uniform(0.85, 1.15) / 5) * 5


def generate_alert(tickers: list[str]) -> str:
    """One randomly-shaped alert, mixing options/equity/skip-worthy noise -
    same phrasing variety as simulate_alerts.py's reviewed sample set, just
    randomized instead of fixed, so a long-running stream doesn't repeat
    the exact same 8 lines forever."""
    fri = _next_friday()
    fri_str = f"{fri.month}/{fri.day}"
    ticker = random.choice(tickers)
    base_price = {"SPY": 580, "QQQ": 490, "AAPL": 230, "MSFT": 430, "NVDA": 135,
                  "TSLA": 250, "AMD": 165, "META": 580, "AMZN": 190, "GOOGL": 170}.get(ticker, 150)
    strike = _random_strike(base_price)

    roll = random.random()
    if roll < 0.45:
        # Clean options call/put, price-first.
        side = random.choice(["calls", "Put"])
        target = strike + random.choice([5, 10, 15, 20])
        stop = strike - random.choice([5, 10, 15])
        if side == "calls":
            return f"{ticker} ${strike:.0f} {side} target {target:.0f} stop {stop:.0f} exp {fri_str}"
        return f"{ticker} ${strike:.0f} {side} {fri_str} stop loss ${stop:.0f}"
    elif roll < 0.55:
        # Side-before-price phrasing.
        side = random.choice(["Calls", "Puts"])
        return f"{ticker} {side} . ${strike:.0f} {fri_str}"
    elif roll < 0.65:
        # Bare shorthand ("grabbing 200c").
        letter = random.choice(["c", "p"])
        verb = random.choice(["grabbing", "in on", "adding"])
        return f"{ticker} {verb} {strike:.0f}{letter} here, {random.choice(['breaking out', 'looking strong', 'momentum building'])}"
    elif roll < 0.77:
        # Equity buy/sell, casual phrasing.
        if random.random() < 0.5:
            return f"BUY {ticker} now, {random.choice(['breaking out', 'strong volume', 'adding here'])}"
        return f"SELL {ticker}, {random.choice(['taking profit', 'closing here', 'locking it in'])}"
    elif roll < 0.85:
        # Hedged/speculative equity phrasing - clear ticker and direction
        # word present, but her own hedging language. Should still get
        # flagged with a LOWER confidence than a decisive call, and often
        # skipped outright (see HEDGE_CONFIDENCE_PENALTY in
        # extract_signal.py) - watch for this one specifically to see the
        # new confidence-scoring gradient in action, not just a pass/skip.
        hedge = random.choice(["maybe", "might", "thinking about", "not sure but", "possibly"])
        return f"{hedge} buying {ticker} here, {random.choice(['watching for now', 'testing the waters', 'small starter position'])}"
    elif roll < 0.93:
        # Ambiguous - should be skipped (no clear direction).
        return f"{ticker} looking {random.choice(['spicy', 'interesting', 'volatile'])} today, watch this level"
    elif roll < 0.97:
        # Perp/leveraged noise - should be skipped with a warning.
        return f"{random.choice(tickers)}/USDC-P long here, {random.choice([5, 10, 20])}x leverage, big move incoming"
    else:
        # Off-whitelist ticker - should be silently skipped.
        fake_ticker = random.choice(_OFF_WHITELIST_NOISE)
        return f"{fake_ticker} {strike:.0f} calls target {strike+10:.0f} stop {strike-10:.0f}"


def post_alert(webhook_url: str, text: str) -> bool:
    try:
        r = requests.post(webhook_url, json={"content": text}, timeout=10)
        return r.status_code < 300
    except Exception as e:
        print(f"  [ERROR] failed to post: {e}")
        return False


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--webhook-url", default=os.getenv("FAKE_ALERT_WEBHOOK_URL"),
                    help="Discord webhook URL for your OWN test channel (never a real creator's server). "
                         "Can also be set via FAKE_ALERT_WEBHOOK_URL in .env.")
    p.add_argument("--min-interval", type=float, default=15.0, help="minimum seconds between posts")
    p.add_argument("--max-interval", type=float, default=90.0, help="maximum seconds between posts")
    p.add_argument("--count", type=int, default=0, help="stop after this many posts (0 = run forever)")
    args = p.parse_args()

    if not args.webhook_url:
        print("ERROR: no webhook URL given. Pass --webhook-url or set FAKE_ALERT_WEBHOOK_URL in .env.")
        print("This must be a webhook for YOUR OWN test channel - see this script's module docstring.")
        sys.exit(1)

    tickers = _whitelist_tickers()
    print(f"Posting fake alerts to your test channel every {args.min_interval:.0f}-{args.max_interval:.0f}s "
          f"(tickers: {', '.join(tickers)}). Ctrl+C to stop.\n")

    posted = 0
    try:
        while not args.count or posted < args.count:
            text = generate_alert(tickers)
            ok = post_alert(args.webhook_url, text)
            posted += 1
            status = "sent" if ok else "FAILED"
            print(f"[{posted}] ({status}) {text}")
            if args.count and posted >= args.count:
                break
            time.sleep(random.uniform(args.min_interval, args.max_interval))
    except KeyboardInterrupt:
        pass

    print(f"\nStopped after posting {posted} fake alert(s).")


if __name__ == "__main__":
    main()
