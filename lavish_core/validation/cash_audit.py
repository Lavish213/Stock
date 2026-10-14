"""Read-only broker cash reconciliation for an isolated paper round trip.

Fee activities can arrive after fills. Only broker-reported, matching-day fees
are recorded; an unexplained cash difference never becomes an invented fee.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path

from lavish_core.db.hybrid_store import HybridStore, _positions_lock
from lavish_core.utils.safety import check_trading_endpoint


def money(value):
    if value is None or isinstance(value, bool):
        raise ValueError("Missing cash value")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid cash value") from exc
    if not number.is_finite() or abs(number) > Decimal("1e15"):
        raise ValueError("Nonfinite cash value")
    return number


def fetch_activities(broker, after):
    stamp = datetime.fromisoformat(after.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Activity window requires timezone")
    params = {"after": after, "direction": "asc", "page_size": 100}
    rows, tokens = [], set()
    for _ in range(10):
        response = broker.SESSION.get(broker.BASE_URL + "/v2/account/activities",
                                      headers=broker.HEADERS, params=dict(params),
                                      timeout=15, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError("Activity snapshot unavailable")
        page = response.json()
        if not isinstance(page, list) or len(page) > 100 or any(not isinstance(row, dict) for row in page):
            raise ValueError("Malformed activity page")
        rows.extend(page)
        if len(page) < 100:
            return rows
        token = page[-1].get("id")
        if not isinstance(token, str) or not token or token in tokens:
            raise ValueError("Invalid or cycling activity cursor")
        tokens.add(token)
        params["page_token"] = token
    raise ValueError("Incomplete activity snapshot")


def reconcile(store, receipt, account, activities, order_ids):
    """Requires a dedicated account and the two confirmed broker order IDs."""
    account_hash = hashlib.sha256(str(account.get("id")).encode()).hexdigest()
    if account_hash != receipt.get("account_id_hash"):
        raise ValueError("Receipt belongs to another paper account")
    start = datetime.fromisoformat(receipt["observed_at"].replace("Z", "+00:00"))
    if start.tzinfo is None or len(set(order_ids)) != 2:
        raise ValueError("Verified round-trip identities required")
    trade_day = start.astimezone(timezone.utc).date().isoformat()
    fees = {}
    unknown = []
    for row in activities:
        kind = row.get("activity_type")
        if kind == "FILL":
            if row.get("order_id") not in order_ids:
                unknown.append("unrelated_fill")
            continue
        if kind != "FEE":
            unknown.append("unallocated_cash_activity")
            continue
        identity = row.get("id")
        if not isinstance(identity, str) or not identity or row.get("date") != trade_day:
            unknown.append("fee_day_unverified")
            continue
        if row.get("order_id") and row["order_id"] not in order_ids:
            unknown.append("unrelated_order_fee")
            continue
        if row.get("status") != "executed":
            unknown.append("fee_not_posted")
            continue
        amount = money(row.get("net_amount"))
        if amount >= 0:
            raise ValueError("Fee debit required; rebates require separate attribution")
        if identity in fees and fees[identity] != amount:
            raise ValueError("Conflicting broker activity identity")
        fees[identity] = amount
    booked = money(receipt.get("recorded_fees", 0))
    if booked < 0:
        raise ValueError("Invalid recorded fee")
    # An order's aggregate fee cannot be blindly counted again as a cash fee.
    if booked and fees:
        unknown.append("fill_and_cash_fee_attribution_required")
    fee_delta = sum(fees.values(), Decimal(0)) if not booked else Decimal(0)
    gross = (money(receipt["exit_price"]) - money(receipt["entry_price"])) * money(receipt["entry_filled_qty"]) * 100
    cash_change = money(account.get("cash")) - money(receipt["cash_before"])
    error = cash_change - (gross - booked + fee_delta)
    state = "reconciled" if abs(error) <= Decimal(".02") and not unknown else "pending_cash_activity_review"
    with _positions_lock:
        store.execute("CREATE TABLE IF NOT EXISTS broker_cash_activities(event_id VARCHAR PRIMARY KEY,amount VARCHAR NOT NULL,trade_date VARCHAR NOT NULL,account_hash VARCHAR NOT NULL)")
        store.execute("BEGIN TRANSACTION")
        try:
            for identity, amount in (fees.items() if not booked else []):
                existing = store.fetchall("SELECT amount,trade_date,account_hash FROM broker_cash_activities WHERE event_id=?", (identity,))
                if existing and (money(existing[0][0]) != amount or existing[0][1:] != (trade_day, account_hash)):
                    raise ValueError("Persisted activity conflicts with broker evidence")
                store.execute("INSERT INTO broker_cash_activities VALUES (?,?,?,?) ON CONFLICT(event_id) DO NOTHING", (identity, str(amount), trade_day, account_hash))
            store.execute("COMMIT")
        except Exception:
            store.execute("ROLLBACK")
            raise
        if state != "reconciled":
            store.set_kill_switch(True, "Cash activity reconciliation pending; inspect broker evidence")
    return {"status": state, "passed": state == "reconciled", "live_ready": False,
            "gross_price_pnl": float(gross), "recorded_fill_fees": float(booked),
            "posted_cash_fees": float(-sum(fees.values(), Decimal(0))),
            "cash_before": float(money(receipt["cash_before"])), "cash_after": float(money(account["cash"])),
            "cash_reconciliation_error": float(error), "fee_activity_ids": sorted(fees),
            "review_reasons": sorted(set(unknown)), "orders_submitted": 0}


def recheck(receipt, db_path):
    if os.getenv("TRADE_MODE") != "paper" or os.getenv("BROKER_PROVIDER", "alpaca") != "alpaca":
        raise ValueError("Explicit Alpaca paper mode required")
    from lavish_core.trade import broker_alpaca as broker
    from lavish_core.trade.state_audit import audit_state
    check_trading_endpoint("paper", broker.BASE_URL)
    if not Path(db_path).is_file():
        raise ValueError("Existing isolated paper journal required")
    store = HybridStore(db_path)
    try:
        positions = broker.get_positions()
        response = broker.SESSION.get(broker.ORDERS_URL, headers=broker.HEADERS,
                                      params={"status": "open"}, timeout=15, allow_redirects=False)
        if response.status_code != 200 or response.json() != [] or positions != []:
            raise ValueError("Flat account and no open orders required for isolated recheck")
        rows = store.fetchall("SELECT symbol,side,client_id FROM orders WHERE qty>0 ORDER BY ts")
        ids = []
        for symbol, side, client in rows:
            remote = broker.get_order_by_client_id(client)
            if (not remote or remote.get("status") != "filled" or remote.get("symbol") != symbol
                    or remote.get("side", "").lower() != side.lower() or not remote.get("id")):
                raise ValueError("Broker order identity or completion unverified")
            ids.append(remote["id"])
        account = broker.get_account()
        result = reconcile(store, receipt, account, fetch_activities(broker, receipt["observed_at"]), ids)
        result["state_audit"] = audit_state(store, positions)
        result["passed"] = result["passed"] and result["state_audit"]["ok"]
        result["observed_at"] = datetime.now(timezone.utc).isoformat()
        return result
    finally:
        try:
            store.execute("CHECKPOINT")
        finally:
            store.con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve() in {args.receipt.resolve(), args.db.resolve(), Path(str(args.db.resolve()) + ".control.json"), Path(str(args.db.resolve()) + ".wal")}:
        parser.error("Use a new output path separate from the receipt and journal")
    from dotenv import load_dotenv
    load_dotenv(args.env_file)
    os.umask(0o077)
    try:
        result = recheck(json.loads(args.receipt.read_text()), args.db)
    except Exception as exc:
        result = {"passed": False, "live_ready": False, "orders_submitted": 0, "error_type": type(exc).__name__}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
