"""Historical SPY month + synthetic alerts; all order boundaries are blocked.

Production replay preserves whole-share sizing. The fractional comparison is
an independent research model, not a feature or profitability claim for the bot.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from lavish_core.validation.alert_following import send

ET = ZoneInfo("America/New_York")
ALERT_TIMES = ("09:45", "10:45", "13:45")


class ReplayClock:
    def __init__(self):
        self.epoch = 0.0
        self.elapsed = 0.0

    def set(self, stamp):
        self.epoch = stamp.timestamp()
        self.elapsed = 0.0

    def time(self):
        return self.epoch + self.elapsed

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds
        if self.elapsed > 930:
            raise AssertionError("Gate exceeded bounded replay clock")

    def now(self):
        return datetime.fromtimestamp(self.time(), timezone.utc)


def prepare(data, calendar, month):
    """Require every scheduled regular-session minute before using a month."""
    sessions = [s for s in calendar if s["date"].startswith(month)]
    if not sessions:
        raise ValueError("No market calendar for requested month")
    days = {}
    for raw in data:
        stamp = datetime.fromisoformat(raw["t"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Ambiguous timestamp")
        local = stamp.astimezone(ET)
        day = local.date().isoformat()
        if day.startswith(month):
            values = [float(raw[k]) for k in ("o", "h", "l", "c", "v")]
            if not all(math.isfinite(v) for v in values) or min(values[:4]) <= 0 or values[4] < 0:
                raise ValueError("Invalid market data")
            if raw["h"] < max(raw[k] for k in ("o", "l", "c")) or raw["l"] > min(raw[k] for k in ("o", "h", "c")):
                raise ValueError("Inconsistent OHLC")
            bucket = days.setdefault(day, {})
            if stamp in bucket:
                raise ValueError("Duplicate candle")
            bucket[stamp] = dict(raw, t=stamp.isoformat())
    output = []
    for session in sessions:
        opening = datetime.fromisoformat(session["date"] + "T" + session["open"]).replace(tzinfo=ET)
        closing = datetime.fromisoformat(session["date"] + "T" + session["close"]).replace(tzinfo=ET)
        stamps = [opening + timedelta(minutes=i) for i in range(int((closing-opening).total_seconds()/60))]
        if any(t not in days.get(session["date"], {}) for t in stamps):
            raise ValueError("Missing regular-session minute: " + session["date"])
        output.append(dict(date=session["date"], closing=closing,
                           bars=[days[session["date"]][t] for t in stamps], stamps=stamps))
    return output


def make_alerts(sessions):
    """Fixed times and levels, using only prices already closed at posting."""
    alerts = []
    for session in sessions:
        for local_time in ALERT_TIMES:
            posted = datetime.fromisoformat(session["date"]+"T"+local_time).replace(tzinfo=ET)
            if posted >= session["closing"]:
                continue
            index = bisect_right(session["stamps"], posted - timedelta(minutes=1)) - 1
            price = session["bars"][index]["c"]
            target, stop = round(price * 1.005, 2), round(price * .997, 2)
            alerts.append(dict(id="fake-SPY-"+session["date"]+"-"+local_time,
                posted=posted, price=price, target=target, stop=stop, session=session,
                text=f"BUY SPY now target {target:.2f} stop {stop:.2f}"))
    return alerts


def production_replay(sessions, journal):
    from lavish_core.db import hybrid_store as hs
    from lavish_core.feeds.alert_feed import AlertInbox
    from lavish_core.trade import candle_gatekeeper as gate
    from lavish_core.trading import trade_handler as handler, alert_handler
    import requests

    if Path(journal).exists():
        raise ValueError("Replay journal must be new")
    clock = ReplayClock()
    active = {}
    gates, notifications, receipts, forbidden_calls = [], [], [], []
    cash = 1000.0

    class HistoricalDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = clock.now()
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    class InlineThread:
        def __init__(self, target, args=(), kwargs=None, **unused):
            self.target, self.args, self.kwargs = target, args, kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    def closed_bars(*args, **kwargs):
        session = active["session"]
        end = bisect_right(session["stamps"], clock.now()-timedelta(minutes=1))
        return session["bars"][max(0, end-15):end]

    def quote(*args, **kwargs):
        return closed_bars()[-1]["c"]

    def confirm(*args, **kwargs):
        result = gate.confirm_entry(*args, **kwargs)
        gates.append(dict(result, decision_at=clock.now().isoformat()))
        return result

    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("Month replay must not submit an order or access network")

    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"TRADE_MODE":"paper", "DEFAULT_TRADE_DOLLARS":"100",
            "DISCORD_WEBHOOK_URL":"", "REDIS_URL":"", "ALERT_FEED_EXECUTE":"true"}))
        settings = {"DEFAULT_DB":Path(journal), "TRADE_MODE":"paper", "GATEKEEPER_ENABLED":True,
            "POSITION_SIZE_SCALE":1., "datetime":HistoricalDateTime, "time":clock,
            "threading":SimpleNamespace(Thread=InlineThread), "confirm_entry":confirm,
            "latest_quote":quote, "get_account":lambda:{"equity":cash,"cash":cash},
            "get_clock":lambda:{"is_open":True,"next_close":active["session"]["closing"].isoformat()},
            "place_trade":forbidden, "post_discord":lambda message:notifications.append(message)}
        for name, value in settings.items():
            stack.enter_context(patch.object(handler, name, value))
        for name, value in {"time":clock,"datetime":HistoricalDateTime,"get_bars":closed_bars,
                "ENABLED":True,"MAX_WAIT_SECONDS":900,"POLL_INTERVAL_SEC":15,
                "MIN_REFERENCE_RANGE_BPS":1}.items():
            stack.enter_context(patch.object(gate, name, value))
        stack.enter_context(patch.object(hs, "_utcnow", clock.now))
        stack.enter_context(patch.object(requests.sessions.Session, "request", forbidden))
        inbox = AlertInbox(journal)
        for alert in make_alerts(sessions):
            clock.set(alert["posted"])
            active["session"] = alert["session"]
            original_epoch = clock.time()
            before = len(gates)
            status, _ = send(inbox, alert["text"], alert["id"], clock, created_epoch=original_epoch)
            if status != 202:
                raise AssertionError("Fresh alert not accepted by signed HTTP receiver")
            inbox.dispatch_once(alert_handler.handle_alert_text, execute=True, now=clock.time())
            if len(gates) != before + 1:
                raise AssertionError("Fresh SPY alert did not reach real candle gate")
            # Duplicate arrives immediately after the original HTTP receipt,
            # before the virtual gate finishes; do not make it stale artificially.
            elapsed = clock.elapsed
            clock.elapsed = 0
            duplicate_status, duplicate = send(inbox, alert["text"], alert["id"], clock,
                                                created_epoch=original_epoch)
            clock.elapsed = elapsed
            if duplicate_status != 200 or not duplicate.get("duplicate"):
                raise AssertionError("Duplicate not recognized")
            inbox.dispatch_once(alert_handler.handle_alert_text, execute=True, now=clock.time())
            if len(gates) != before + 1:
                raise AssertionError("Duplicate invoked gate again")
            result = gates[-1]
            if result["confirmed"] and math.floor(cash*.1/quote()) != 0:
                raise AssertionError("This dataset no longer represents unaffordable whole shares")
            receipts.append(dict(id=alert["id"], posted=alert["posted"].isoformat(),
                text=alert["text"], budget=cash*.1, http_status=status,
                duplicate_http_status=duplicate_status, gate=result,
                result="whole_share_budget_skip" if result["confirmed"] else "gate_skip"))
        store = hs.HybridStore(journal)
        try:
            fills = store.fetchall("SELECT count(*) FROM fills")[0][0]
            holdings = store.fetchall("SELECT count(*) FROM positions")[0][0]
        finally:
            store.con.close()
        if notifications or fills or holdings or forbidden_calls:
            raise AssertionError("Unexpected error, order/network attempt, fill or holding")
    return dict(starting_cash=1000., final_cash=cash, net_pnl=0., return_pct=0.,
        alerts=len(receipts), duplicate_deliveries=len(receipts), fills=fills, trades=0,
        final_positions=holdings, gate_outcomes=dict(Counter(x["gate"]["reason"] for x in receipts)),
        outcomes=dict(Counter(x["result"] for x in receipts)), receipts=receipts,
        scope="Production signed HTTP handler, inbox, parser, claims, candle gate and whole-share sizing; inline worker and historical clock. No orders submitted, so fills/exits are not exercised.")


def fractional_comparison(alerts, receipts, cost_bps=5., allocation_fraction=.1):
    """Long-only capped equity allocation, one position, next-open execution.

    No account/PDT/settlement eligibility claim. Cost is a stress assumption,
    not a measured broker fee. Stops win ambiguous same-minute TP/SL touches.
    """
    if not math.isfinite(allocation_fraction) or not 0 < allocation_fraction <= 1:
        raise ValueError("Allocation must be finite and in (0, 1]")
    cash, peak, drawdown, costs = 1000., 1000., 0., 0.
    busy_until = datetime.min.replace(tzinfo=timezone.utc)
    trades, skipped = [], Counter()
    cost = cost_bps/10000
    for alert, receipt in zip(alerts, receipts):
        if alert["posted"] < busy_until:
            skipped["existing_position"] += 1
            continue
        if not receipt["gate"]["confirmed"]:
            skipped["gate_skip"] += 1
            continue
        session = alert["session"]
        decision = datetime.fromisoformat(receipt["gate"]["decision_at"])
        index = bisect_right(session["stamps"], decision - timedelta(microseconds=1))
        if index >= len(session["bars"]):
            skipped["no_next_open"] += 1
            continue
        entry_bar = session["bars"][index]
        raw_entry = entry_bar["o"]
        if not alert["stop"] < raw_entry < alert["target"]:
            skipped["crossed_source_levels"] += 1
            continue
        budget = cash*allocation_fraction
        entry = raw_entry*(1+cost)
        qty = math.floor(budget/entry*1e8)/1e8
        if qty*entry > budget+1e-8:
            raise AssertionError("Allocation cap exceeded")
        exit_raw, reason, exit_index = session["bars"][-1]["c"], "session_close", len(session["bars"])-1
        ambiguous = False
        for j in range(index, len(session["bars"])):
            b = session["bars"][j]
            if b["o"] <= alert["stop"]:
                exit_raw, reason, exit_index = b["o"], "gap_stop", j
                break
            if b["o"] >= alert["target"]:
                # The opening print precedes the minute's later extremes.
                # Conservative limit fill gets target, not favorable gap price.
                exit_raw, reason, exit_index = alert["target"], "target", j
                break
            stop_hit, target_hit = b["l"] <= alert["stop"], b["h"] >= alert["target"]
            if stop_hit:
                exit_raw, reason, exit_index = alert["stop"], "stop", j
                ambiguous = target_hit
                break
            if target_hit:
                exit_raw, reason, exit_index = alert["target"], "target", j
                break
        exit_price = exit_raw*(1-cost)
        pnl = qty*(exit_price-entry)
        trade_cost = qty*(raw_entry*cost+exit_raw*cost)
        # Include intratrade low marks before the exit bar. Final bar uses
        # modeled exit rather than its possibly post-exit low.
        for j in range(index, exit_index):
            mark = cash + qty*(session["bars"][j]["l"]-entry)
            drawdown = max(drawdown, (peak-mark)/peak*100)
        cash += pnl
        costs += trade_cost
        peak = max(peak,cash)
        drawdown = max(drawdown,(peak-cash)/peak*100)
        busy_until = session["stamps"][exit_index]+timedelta(minutes=1)
        trades.append(dict(id=alert["id"],entry_at=session["stamps"][index].isoformat(),
            exit_at=busy_until.isoformat(),budget=budget,qty=qty,entry_price=entry,exit_price=exit_price,
            reason=reason,ambiguous_bar_stop_first=ambiguous,cost=trade_cost,net_pnl=pnl,cash_after=cash))
    return dict(hypothetical_fractional=True, live_bot_support=False, cost_bps_per_side=cost_bps,
        allocation_fraction=allocation_fraction,
        starting_cash=1000.,final_cash=cash,net_pnl=cash-1000,return_pct=(cash/1000-1)*100,
        trades=len(trades),wins=sum(t["net_pnl"]>0 for t in trades),
        losses=sum(t["net_pnl"]<0 for t in trades),estimated_cost=costs,
        conservative_drawdown_pct=drawdown,skips=dict(skipped),final_positions=0,ledger=trades)


def run(data_path, calendar_path, journal, month="2026-09"):
    source = Path(data_path)
    body = json.loads(source.read_text())
    if body.get("timeframe") != "1Min":
        raise ValueError("Production gate requires genuine 1-minute candles")
    sessions = prepare(body["bars"], json.loads(Path(calendar_path).read_text()), month)
    primary = production_replay(sessions, journal)
    return dict(month=month, initial_equity=1000, allocation_fraction=.1, real_orders=0,
        synthetic_creator=True, source=body["source"],source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        regular_session_bars=sum(len(s["bars"]) for s in sessions), sessions=len(sessions),
        plan="Three synthetic BUY SPY alerts per session at 09:45, 10:45, 13:45 ET. Target +0.5%, stop -0.3% from last closed minute. 15-minute production candle gate. No retrospective tuning.",
        production=primary, fractional_comparisons=[fractional_comparison(make_alerts(sessions),primary["receipts"],c) for c in (1,5,10,25)],
        limitations=["Synthetic creator skill, not Stock Alley performance or a prediction.",
            "Fractional comparison is separate research logic, unsupported by current bot sizing/brackets.",
            "Fractional model does not enforce broker account/PDT/settlement eligibility.",
            "No authentic option chain, premium, spread or Greek history: no option return inferred.",
            "Minute OHLC cannot resolve intraminute ordering; ambiguous bars use stop first.",
            "Inline scheduling does not prove thread/crash reliability or real network latency."])


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data",type=Path,required=True)
    parser.add_argument("--calendar",type=Path,required=True)
    parser.add_argument("--journal",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    result=run(args.data,args.calendar,args.journal)
    args.output.write_text(json.dumps(result,indent=2))
    print(json.dumps({"month":result["month"],"sessions":result["sessions"],
        "production":{k:v for k,v in result["production"].items() if k!="receipts"},
        "fractional":[{k:v for k,v in r.items() if k!="ledger"} for r in result["fractional_comparisons"]]},indent=2))
