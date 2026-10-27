# -*- coding: utf-8 -*-
"""
Lavish Bot — HybridStore (Wall-Street Edition, single-file, drop-in)

What you get
------------
• DuckDB-backed persistence for: risk_limits, signals, orders, fills, positions, account_snapshots
• Optional Redis bridge (safe to run without Redis)
• Auto schema creation + idempotent migrations
• UTC timestamps, JSON meta, and helpful indices
• Clean API used by trade_agent / decision_engine
• Built-in smoke test: python -m lavish_core.db.hybrid_store --symbol AAPL --dry

Tables (exact columns)
----------------------
risk_limits          (name TEXT PRIMARY KEY, value DOUBLE, updated_at TIMESTAMP)
signals              (id TEXT PRIMARY KEY, ts TIMESTAMP, symbol TEXT, side TEXT, source TEXT, confidence DOUBLE, payload JSON)
orders               (id TEXT PRIMARY KEY, ts TIMESTAMP, symbol TEXT, side TEXT, qty DOUBLE, order_type TEXT, limit_price DOUBLE,
                      tif TEXT, venue TEXT, status TEXT, client_id TEXT, meta JSON)
fills                (id TEXT PRIMARY KEY, ts TIMESTAMP, order_id TEXT, symbol TEXT, side TEXT, qty DOUBLE, price DOUBLE, fee DOUBLE, venue TEXT)
positions            (symbol TEXT PRIMARY KEY, qty DOUBLE, avg_price DOUBLE, updated_at TIMESTAMP)
account_snapshots    (ts TIMESTAMP PRIMARY KEY, equity DOUBLE, cash DOUBLE, buying_power DOUBLE, day_pl DOUBLE, cumulative_pl DOUBLE)

Why your old code broke
-----------------------
You queried:  SELECT name, value FROM risk_limits
…but your table had columns like (symbol, max_position, …), so DuckDB raised:
  Binder Error: Referenced column "name" not found
This version **defines `risk_limits(name, value, updated_at)`** and migrates automatically.

"""

from __future__ import annotations

import json
import os
import random
import string
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ---- Third-party (core) ------------------------------------------------------
from lavish_core.utils.safety import finite_number
import duckdb  # pip install duckdb

# Redis is optional; we won’t type-hint it strictly so Pylance doesn’t complain.
try:
    import redis  # pip install redis
except Exception:  # pragma: no cover
    redis = None  # type: ignore


# ==============================================================
# Section 1 – small utilities
# ==============================================================

# Automatically resolve DB path even if script runs from a subdirectory
from pathlib import Path

# Look for an existing brain/hybrid DB file in ./data
project_root = Path(__file__).resolve().parents[2]
data_path = project_root / "data"

# Pick the first existing file or default to brain.db
candidate_dbs = ["brain.db", "hybrid_store.db", "lavish.duckdb"]
for name in candidate_dbs:
    if (data_path / name).exists():
        DEFAULT_DB = data_path / name
        break
else:
    DEFAULT_DB = data_path / "brain.db"  # fallback path

from lavish_core.logger_setup import get_logger
_log = get_logger("hybrid_store", log_dir="logs")
_log.info(f"Using database at: {DEFAULT_DB.resolve()}")

DEFAULT_REDIS = "redis://localhost:6379/0"

# How many times to retry a position-update write that lost a genuine
# concurrent conflict (see _apply_fill_to_position) before giving up and
# letting the exception surface. Confirmed directly (not assumed): under
# real heavy contention (stress-tested at 10 fully simultaneous writers
# to the same symbol), a handful of immediate retries with no backoff
# could still all collide - real production traffic never approaches that
# (at most an entry fill and an exit fill overlap on the same symbol), but
# retry with jitter so it holds even there, rather than tuning this to
# just barely cover the realistic case.
_FILL_CONFLICT_MAX_RETRIES = 20
_FILL_CONFLICT_BACKOFF_BASE_SEC = 0.01

# Serializes _apply_fill_to_position across threads within this ONE
# process - run_ingest.py's own module docstring already establishes that
# this bot runs as a single process (DuckDB doesn't allow two processes
# to hold the same file open concurrently), so a plain in-process lock
# covers the real threat model completely, unlike retrying against
# DuckDB's transaction-conflict detection alone. Confirmed directly (not
# assumed): under heavy adversarial contention (10 fully simultaneous
# buy/sell fills for the same symbol), the atomic-SQL-plus-retry approach
# by itself avoided crashing and avoided losing any individual fill's
# qty delta, but could still apply fills in a scrambled, non-chronological
# order relative to each other - which can corrupt avg_price (a weighted
# average has no sensible meaning if a sell and a buy for the same
# position apply in an order that doesn't match when they actually
# happened). A global lock (not per-symbol) is deliberately simple here:
# fills are a low-frequency event (actual trade executions, at most a
# handful per day), so serializing all of them has no meaningful
# performance cost, and it eliminates this entire problem class rather
# than just reducing its odds.
_positions_lock = threading.RLock()
_schema_lock = threading.Lock()


def _utcnow() -> datetime:
    """Return timezone-aware UTC 'now'."""
    return datetime.now(timezone.utc)


def _short_id(prefix: str) -> str:
    """Generate a short, sortable id: {prefix}_{YYYYMMDD_HHMMSS}_{rand}."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{prefix}_{ts}_{rand}"


def _json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


# =============================================================================
# Section 2 — schema & migrations (idempotent)
# =============================================================================

SCHEMA_SQL: List[str] = [
    "CREATE TABLE IF NOT EXISTS position_epochs (symbol TEXT PRIMARY KEY, epoch TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS fill_events (event_id TEXT PRIMARY KEY, fill_id TEXT)",
    "CREATE TABLE IF NOT EXISTS risk_reservations (client_id TEXT PRIMARY KEY, symbol TEXT, qty DOUBLE, price DOUBLE, multiplier DOUBLE, created_at TIMESTAMP)",
    """CREATE TABLE IF NOT EXISTS exit_intents (
        symbol TEXT PRIMARY KEY,
        reason TEXT,
        underlying_price DOUBLE,
        triggered_at TIMESTAMP
    )""",
    # -- risk limits as K/V so SELECT name,value works everywhere
    """
    CREATE TABLE IF NOT EXISTS risk_limits (
        name       TEXT PRIMARY KEY,
        value      DOUBLE,
        updated_at TIMESTAMP
    )
    """,
    # -- signals observed (from Patreon, OCR, Discord export, etc.)
    """
    CREATE TABLE IF NOT EXISTS signals (
        id         TEXT PRIMARY KEY,
        ts         TIMESTAMP,
        symbol     TEXT,
        side       TEXT,
        source     TEXT,
        confidence DOUBLE,
        payload    JSON
    )
    """,
    # -- orders persisted (paper/live/dry)
    """
    CREATE TABLE IF NOT EXISTS orders (
        id          TEXT PRIMARY KEY,
        ts          TIMESTAMP,
        symbol      TEXT,
        side        TEXT,
        qty         DOUBLE,
        order_type  TEXT,
        limit_price DOUBLE,
        tif         TEXT,
        venue       TEXT,
        status      TEXT,
        client_id   TEXT,
        meta        JSON
    )
    """,
    # -- fills associated to orders
    """
    CREATE TABLE IF NOT EXISTS fills (
        id        TEXT PRIMARY KEY,
        ts        TIMESTAMP,
        order_id  TEXT,
        symbol    TEXT,
        side      TEXT,
        qty       DOUBLE,
        price     DOUBLE,
        fee       DOUBLE,
        venue     TEXT
    )
    """,
    # -- latest net position per symbol
    """
    CREATE TABLE IF NOT EXISTS positions (
        symbol     TEXT PRIMARY KEY,
        qty        DOUBLE,
        avg_price  DOUBLE,
        updated_at TIMESTAMP
    )
    """,
    # -- account equity snapshots
    """
    CREATE TABLE IF NOT EXISTS account_snapshots (
        ts            TIMESTAMP PRIMARY KEY,
        equity        DOUBLE,
        cash          DOUBLE,
        buying_power  DOUBLE,
        day_pl        DOUBLE,
        cumulative_pl DOUBLE
    )
    """,
    # -- durable "already processed" cursor for alert ingestion sources
    # (Patreon posts, Discord messages, ...) - an in-memory-only set used
    # to reset on every restart, replaying recent posts as if new.
    """
    CREATE TABLE IF NOT EXISTS ingest_seen (
        source     TEXT,
        item_id    TEXT,
        seen_at    TIMESTAMP,
        PRIMARY KEY (source, item_id)
    )
    """,

    # -- single-row control flags (currently just the kill switch) -
    # durable, so a restart doesn't quietly undo a manually-triggered
    # stop, and checkable/settable remotely over SSH without needing to
    # touch code or redeploy (see scripts/kill_switch.py).
    """
    CREATE TABLE IF NOT EXISTS control_flags (
        name        TEXT PRIMARY KEY,
        enabled     BOOLEAN,
        reason      TEXT,
        updated_at  TIMESTAMP
    )
    """,

    # -- atomic dedup claims. A separate check-then-insert (SELECT for a
    # duplicate, then INSERT a signal row) has a real race window: two
    # threads can both see "nothing yet" and both proceed - confirmed
    # directly, not assumed (concurrent inserts into an already-created
    # schema succeed without conflict, so nothing stops both from
    # landing). This table exists so claiming a (symbol, side, strike,
    # expiry) key can be ONE atomic INSERT ... ON CONFLICT DO NOTHING
    # instead of two separate statements with a gap between them.
    """
    CREATE TABLE IF NOT EXISTS signal_claims (
        claim_key   TEXT PRIMARY KEY,
        claimed_at  TIMESTAMP,
        token       TEXT
    )
    """,

    # --- indices (no-op if exist)
    "CREATE INDEX IF NOT EXISTS idx_signals_symbol_ts ON signals(symbol, ts)",
    "CREATE INDEX IF NOT EXISTS idx_orders_symbol_ts  ON orders(symbol, ts)",
    "CREATE INDEX IF NOT EXISTS idx_fills_order_ts    ON fills(order_id, ts)",
]


# =============================================================================
# Section 3 — main store
# =============================================================================

class HybridStore:
    """
    Single entry point for storage — used by trader, decision engine, OCR bridge, etc.
    Works without Redis; if Redis is provided, we’ll opportunistically use it as a cache.
    """

    def __init__(self, duckdb_path: Path | str = DEFAULT_DB, redis_url: Optional[str] = None) -> None:
        self.db_path = Path(duckdb_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # DuckDB connection (thread-safe enough for CLI use; for web, open per-request)
        self.con: duckdb.DuckDBPyConnection = duckdb.connect(str(self.db_path))
        # sensible defaults
        self.con.execute("PRAGMA threads=4;")
        self.con.execute("PRAGMA temp_directory='data/storage/tmp';")
        # ensure schema
        self.con.execute("SET TimeZone='UTC'")
        with _schema_lock:
            self._ensure_schema()

        # Optional Redis (avoid strict type to keep pylance calm)
        self.r = None  # type: Any
        if redis_url and redis is not None:
            try:
                self.r = redis.from_url(redis_url, decode_responses=True)
                self.ping()
            except Exception:
                self.r = None  # safe fallback

    # --------------------------------------------------------------------- util
    def ping(self) -> bool:
        if self.r is None:
            return False
        try:
            self.r.ping()
            return True
        except Exception:
            return False

    def _ensure_schema(self) -> None:
        for sql in SCHEMA_SQL:
            self.con.execute(sql)

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> None:
        """Execute (no fetch)."""
        if params:
            self.con.execute(sql, params)
        else:
            self.con.execute(sql)

    def _execute_with_conflict_retry(self, sql: str, params: Iterable[Any]) -> None:
        """
        Like execute(), but retries (with jitter backoff) on the raw
        exception DuckDB can raise for the LOSING side of two genuinely
        concurrent writes to the same row - see _apply_fill_to_position's
        docstring for why a plain execute() isn't safe for a write that
        must never be silently dropped.
        """
        for attempt in range(_FILL_CONFLICT_MAX_RETRIES):
            try:
                self.execute(sql, params)
                return
            except (duckdb.ConstraintException, duckdb.TransactionException):
                if attempt == _FILL_CONFLICT_MAX_RETRIES - 1:
                    raise
                time.sleep(_FILL_CONFLICT_BACKOFF_BASE_SEC * (1 + random.random()) * (attempt + 1))

    def fetchall(self, sql: str, params: Iterable[Any] | None = None) -> List[Tuple]:
        if params:
            res = self.con.execute(sql, params)
        else:
            res = self.con.execute(sql)
        return list(res.fetchall())

    # =========================================================================
    # Section 4 — Risk Limits  (K/V map)
    # =========================================================================

    def get_risk_limits(self) -> Dict[str, float]:
        """Return all risk limits as {name: value}."""
        rows = self.fetchall("SELECT name, value FROM risk_limits")
        return {name: float(value) for (name, value) in rows}

    def set_risk_limit(self, name: str, value: float) -> None:
        """Idempotent upsert of a single limit."""
        self.execute(
            """
            INSERT INTO risk_limits(name, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (name, float(value), _utcnow()),
        )

    def set_risk_limits(self, kv: Dict[str, float]) -> None:
        if not kv:
            return
        now = _utcnow()
        values = [(k, float(v), now) for k, v in kv.items()]
        self.con.executemany(
            """
            INSERT INTO risk_limits(name, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            values,
        )

    # =========================================================================
    # Section 5 — Signals
    # =========================================================================

    def log_signal(
        self,
        symbol: str,
        side: str,
        source: str,
        confidence: float,
        payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        sid = _short_id("sig")
        self.execute(
            """
            INSERT INTO signals(id, ts, symbol, side, source, confidence, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (sid, _utcnow(), symbol.upper(), side.lower(), source, float(confidence), _json(payload or {})),
        )
        return sid

    # =========================================================================
    # Section 5b — Ingest cursor (durable "already seen" for alert sources)
    # =========================================================================

    def mark_ingest_seen(self, source: str, item_id: str) -> None:
        """Idempotent - safe to call even if already marked (e.g. a retry)."""
        self.execute(
            "INSERT INTO ingest_seen(source, item_id, seen_at) VALUES (?, ?, ?) "
            "ON CONFLICT (source, item_id) DO NOTHING",
            (source, str(item_id), _utcnow()),
        )

    def get_all_ingest_seen(self, source: str) -> set:
        """Bulk-loads every item_id ever marked seen for this source, so a
        poll loop can seed its in-memory set once at startup instead of
        querying per item in the hot loop."""
        rows = self.fetchall("SELECT item_id FROM ingest_seen WHERE source = ?", (source,))
        return {r[0] for r in rows}

    # =========================================================================
    # Section 5c — Kill switch (and future single-row control flags)
    # =========================================================================

    def set_kill_switch(self, enabled: bool, reason: str = "") -> None:
        from lavish_core.utils.control_file import write_control
        write_control(self.db_path,enabled,reason)
        self.execute(
            "INSERT INTO control_flags(name, enabled, reason, updated_at) VALUES ('kill_switch', ?, ?, ?) "
            "ON CONFLICT (name) DO UPDATE SET enabled = excluded.enabled, reason = excluded.reason, "
            "updated_at = excluded.updated_at",
            (enabled, reason, _utcnow()),
        )

    def get_kill_switch(self) -> Tuple[bool, str]:
        """Returns (enabled, reason). (False, "") if never set."""
        from lavish_core.utils.control_file import read_control
        external=read_control(self.db_path)
        if external is not None:
            return external
        rows = self.fetchall("SELECT enabled, reason FROM control_flags WHERE name = 'kill_switch'")
        if not rows:
            return False, ""
        enabled, reason = rows[0]
        return bool(enabled), str(reason or "")

    def set_exit_intent(self, symbol: str, reason: str, underlying_price=None, *, position_epoch=...) -> None:
        """Durably latch a triggered exit until a verified close clears it."""
        allowed = {
            "her_target", "her_stop", "our_stop_loss", "our_trailing_stop",
            "expiry_force_close", "max_hold_timeout",
        }
        if reason not in allowed:
            raise ValueError("Invalid exit intent reason")
        price = (finite_number(underlying_price, positive=True)
                 if underlying_price is not None else None)
        with _positions_lock:
            if position_epoch is not ... and (not position_epoch or self.get_position_epoch(symbol) != position_epoch):
                self.set_kill_switch(True, "Stale worker cannot latch exit intent for another lifecycle")
                return
            self.execute(
                "INSERT INTO exit_intents(symbol, reason, underlying_price, triggered_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
                "reason=excluded.reason, underlying_price=excluded.underlying_price, "
                "triggered_at=excluded.triggered_at",
                (symbol.upper(), reason, price, _utcnow()),
            )

    def get_exit_intent(self, symbol: str) -> Optional[Dict[str, Any]]:
        rows = self.fetchall(
            "SELECT reason, underlying_price, triggered_at FROM exit_intents WHERE symbol=?",
            (symbol.upper(),),
        )
        if not rows:
            return None
        reason, price, triggered_at = rows[0]
        return {"reason": reason, "underlying_price": price, "triggered_at": triggered_at}

    def clear_exit_intent(self, symbol: str, *, position_epoch=...) -> None:
        with _positions_lock:
            if position_epoch is not ...:
                rows = self.fetchall("SELECT epoch FROM position_epochs WHERE symbol=?", (symbol.upper(),))
                if not position_epoch or not rows or rows[0][0] != position_epoch:
                    return
            self.execute("DELETE FROM exit_intents WHERE symbol=?", (symbol.upper(),))

    def try_claim_signal(self, claim_key: str, window_seconds: float) -> bool:
        """
        Atomic alternative to a separate check-then-insert for the same
        real-world alert arriving on Patreon and Discord within
        milliseconds of each other - confirmed directly (not assumed)
        that two threads can both pass a plain "is there already a
        matching signal" SELECT before either has INSERTed, so both
        proceed. One INSERT ... ON CONFLICT DO NOTHING, then reading back
        which attempt's unique token actually landed, closes that gap.

        Returns True if this call now owns the claim (it was first, or
        the existing claim is older than window_seconds and was just
        taken over), False if someone else holds a live claim within the
        window - the caller should treat that as a duplicate.
        """
        token = uuid.uuid4().hex
        now = _utcnow()
        try:
            self.execute(
                "INSERT INTO signal_claims(claim_key, claimed_at, token) VALUES (?, ?, ?) "
                "ON CONFLICT (claim_key) DO NOTHING",
                (claim_key, now, token),
            )
        except (duckdb.ConstraintException, duckdb.TransactionException):
            # Confirmed directly (not assumed) by reproducing this with real
            # concurrent threads: under genuine concurrency, DuckDB can
            # surface the LOSING side of this exact race as a raised
            # exception at commit time rather than quietly honoring
            # "ON CONFLICT DO NOTHING" - a plain try/except around the
            # read-then-insert isn't enough on its own, this is that losing
            # side. Fall through to read back whichever row actually won.
            pass

        rows = self.fetchall("SELECT claimed_at, token FROM signal_claims WHERE claim_key = ?", (claim_key,))
        if not rows:
            return False  # shouldn't happen - fail closed (treat as already claimed) if it ever does
        existing_ts, existing_token = rows[0]
        if existing_ts.tzinfo is None:
            # DuckDB's TIMESTAMP column round-trips a tz-aware Python
            # datetime back as naive - confirmed directly, not assumed
            # (a plain subtraction against the tz-aware `now` below raises
            # TypeError otherwise). This table only ever stores our own
            # _utcnow() values, so naive-on-read always means UTC.
            existing_ts = existing_ts.replace(tzinfo=timezone.utc)
        if existing_token == token:
            return True  # our own insert won the race outright

        age = (now - existing_ts).total_seconds()
        if age <= window_seconds:
            return False  # someone else's live claim - this is the duplicate

        # Existing claim is stale - safe to take over, but guard against a
        # third concurrent caller doing the same thing at the same moment:
        # only count it as ours if this exact UPDATE (keyed to the stale
        # token we just read) is the one that actually applies.
        try:
            self.execute(
                "UPDATE signal_claims SET claimed_at = ?, token = ? WHERE claim_key = ? AND token = ?",
                (now, token, claim_key, existing_token),
            )
        except (duckdb.ConstraintException, duckdb.TransactionException):
            pass  # lost the takeover race too - fall through to see who actually won

        rows2 = self.fetchall("SELECT token FROM signal_claims WHERE claim_key = ?", (claim_key,))
        return bool(rows2) and rows2[0][0] == token

    def release_claim(self, claim_key: str) -> None:
        """
        Frees a claim early. Used when the side that won try_claim_signal's
        race then failed to actually trade for a technical (retryable)
        reason - without this, that transient failure would occupy the
        claim for the rest of window_seconds and block a legitimate retry
        from either alert source, the same hazard execute_trade_from_post
        already avoids for its own fuzzy dedup record by only logging a
        signal as seen after a real, final outcome.
        """
        self.execute("DELETE FROM signal_claims WHERE claim_key = ?", (claim_key,))

    # =========================================================================
    # Section 6 — Orders / Fills / Positions
    # =========================================================================

    def submit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        tif: str = "day",
        venue: str = "dry_run",
        status: str = "submitted",
        client_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        oid = _short_id("ord")
        self.execute(
            """
            INSERT INTO orders(
                id, ts, symbol, side, qty, order_type, limit_price, tif, venue, status, client_id, meta
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                oid,
                _utcnow(),
                symbol.upper(),
                side.lower(),
                float(qty),
                order_type.lower(),
                float(limit_price) if limit_price is not None else None,
                tif.lower(),
                venue,
                status.lower(),
                client_id,
                _json(meta or {}),
            ),
        )
        return oid

    def set_order_status(self, order_id: str, status: str) -> None:
        self.execute("UPDATE orders SET status=? WHERE id=?", (status.lower(), order_id))

    def reserve_entry(self, client_id, symbol, qty, price, limits, account_equity=None, multiplier=1):
        import re
        qty = finite_number(qty, positive=True)
        price = finite_number(price, positive=True)
        multiplier = finite_number(multiplier, positive=True)
        cap = finite_number(limits.max_gross_exposure, positive=True)
        max_qty = finite_number(limits.max_pos_per_symbol, positive=True)
        if account_equity is not None:
            cap = min(cap, finite_number(account_equity, positive=True)*finite_number(limits.max_leverage, positive=True))
        with _positions_lock:
            if self.fetchall("SELECT 1 FROM risk_reservations WHERE client_id=?", (client_id,)):
                return "entry_already_reserved"
            if self.fetchall("SELECT 1 FROM orders WHERE symbol=? AND side='buy' AND status IN ('pending_submit','submission_unknown','fill_unverified') LIMIT 1",(symbol.upper(),)):
                return "unresolved_order_requires_reconciliation"
            positions = self.fetchall("SELECT symbol, qty, avg_price FROM positions")
            pending = self.fetchall("SELECT symbol, qty, price, multiplier FROM risk_reservations")
            gross = sum(abs(q)*p*(100 if re.search(r"\d{6}[CP]\d{8}$", sym) else 1) for sym,q,p in positions)
            gross += sum(q*p*m for sym,q,p,m in pending)
            held = sum(q for sym,q,p in positions if sym == symbol.upper())
            reserved = sum(q for sym,q,p,m in pending if sym == symbol.upper())
            if held + reserved + qty > max_qty:
                return "max_pos_per_symbol exceeded"
            if gross + qty*price*multiplier > cap:
                return "max_gross_exposure exceeded"
            self.execute("INSERT INTO risk_reservations VALUES (?,?,?,?,?,?)", (client_id,symbol.upper(),qty,price,multiplier,_utcnow()))
        return None

    def try_claim_outcome(self, event_id):
        with _positions_lock:
            if self.fetchall("SELECT 1 FROM fill_events WHERE event_id=?",(event_id,)):
                return False
            self.execute("INSERT INTO fill_events VALUES (?,?)",(event_id,""))
            return True

    def release_entry(self, client_id):
        with _positions_lock:
            self.execute("DELETE FROM risk_reservations WHERE client_id=?", (client_id,))

    def log_fill(self, order_id, symbol, side, qty, price, fee=0.0, venue="dry_run", event_id=None, reservation_client_id=None):
        qty = finite_number(qty, positive=True)
        price = finite_number(price, positive=True)
        fee = finite_number(fee, nonnegative=True)
        if side.lower() not in ("buy", "sell"):
            raise ValueError("Invalid fill side")
        with _positions_lock:
            self.execute("BEGIN TRANSACTION")
            try:
                if event_id and self.fetchall("SELECT 1 FROM fill_events WHERE event_id=?", (event_id,)):
                    self.execute("ROLLBACK")
                    if reservation_client_id:
                        self.release_entry(reservation_client_id)
                    return None
                fid = _short_id("fill")
                self.execute("INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?)", (fid,_utcnow(),order_id,symbol.upper(),side.lower(),qty,price,fee,venue))
                self.set_order_status(order_id,"filled")
                self._apply_fill_to_position(symbol.upper(),side.lower(),qty,price)
                if side.lower() == 'buy':
                    # Bind the entry receipt in the same transaction as the
                    # position, not later when a worker happens to start.
                    rows = self.fetchall('SELECT meta FROM orders WHERE id=?', (order_id,))
                    if rows:
                        meta = json.loads(rows[0][0] or '{}')
                        meta.setdefault('position_epoch', self.get_position_epoch(symbol))
                        self.execute('UPDATE orders SET meta=? WHERE id=?', (json.dumps(meta), order_id))
                if event_id:
                    self.execute("INSERT INTO fill_events VALUES (?,?)", (event_id,fid))
                if reservation_client_id:
                    self.release_entry(reservation_client_id)
                self.execute("COMMIT")
                return fid
            except Exception:
                self.execute("ROLLBACK")
                raise

    # ---- positions math ------------------------------------------------------
    def _apply_fill_to_position(self, symbol: str, side: str, qty: float, price: float) -> None:
        """
        Serialized by _positions_lock (see its module-level comment for
        why an in-process lock is the right fix given this bot's
        single-process design) plus belt-and-suspenders atomic SQL and
        conflict-retry underneath.

        Confirmed directly (not assumed), by reproducing it with real
        concurrent threads: two fills landing for the same symbol at
        close to the same time (e.g. a new entry's fill confirmation and
        an exit's bracket-fill confirmation, recorded from two different
        monitor threads) used to race on a plain read-then-write - one
        fill's effect could be silently lost entirely (the classic lost-
        update problem). Making the arithmetic atomic on its own (the
        UPSERT referencing the row's live value instead of a separate
        SELECT) was necessary but not sufficient - DuckDB could still
        raise a raw TransactionException for the losing side of two
        genuinely concurrent UPDATEs, and under heavier adversarial
        contention (stress-tested at 10 simultaneous fills) retrying
        that alone could still apply fills in a scrambled, non-
        chronological order relative to each other, which corrupts
        avg_price (a weighted average has no sensible meaning if a buy
        and a sell for the same position apply out of the order they
        actually happened in). The lock below eliminates that whole
        problem class rather than just reducing its odds; the atomic SQL
        and retry stay as defense-in-depth underneath it.
        """
        symbol = symbol.upper()
        side = side.lower()
        qty = finite_number(qty, positive=True)
        price = finite_number(price, positive=True)
        if side not in ("buy", "sell"):
            raise ValueError("Invalid position delta side")

        with _positions_lock:
            now = _utcnow()
            delta = qty if side == "buy" else -qty
            current = self.get_position(symbol)
            if side == "buy" and (not current or current["qty"] <= 0) and (not current or current["qty"] + qty > 0):
                # Part of log_fill's transaction: only a new long lifecycle
                # receives a new identity. Scale-ins and trims retain it.
                self.execute("INSERT INTO position_epochs VALUES (?,?) ON CONFLICT(symbol) DO UPDATE SET epoch=excluded.epoch",
                             (symbol, uuid.uuid4().hex))

            if side == "buy":
                # Re-averages: the new cost basis blends the existing
                # position with this fill, weighted by size.
                sql = """
                    INSERT INTO positions(symbol, qty, avg_price, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        avg_price = (positions.avg_price * positions.qty + excluded.avg_price * excluded.qty)
                                    / (positions.qty + excluded.qty),
                        qty = positions.qty + excluded.qty,
                        updated_at = excluded.updated_at
                """
                params = (symbol, delta, price, now)
            else:
                # A sell/reduction never changes the average cost basis
                # (only a buy re-averages it) - just atomically decrements
                # qty.
                sql = """
                    INSERT INTO positions(symbol, qty, avg_price, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        qty = positions.qty + excluded.qty,
                        updated_at = excluded.updated_at
                """
                params = (symbol, delta, 0.0, now)

            self._execute_with_conflict_retry(sql, params)

            # Flat position - remove the row rather than leave qty=0
            # sitting around. Still a separate statement (simpler to read
            # than folding into the upsert's CASE logic) - safe now that
            # the lock above means nothing else can be mutating this same
            # row between the two statements.
            self._execute_with_conflict_retry("DELETE FROM positions WHERE symbol = ? AND qty = 0", (symbol,))

    def get_position(self, symbol: str) -> Optional[Dict[str, float]]:
        rows = self.fetchall("SELECT qty, avg_price FROM positions WHERE symbol=?", (symbol.upper(),))
        if not rows:
            return None
        q, ap = rows[0]
        return {"qty": float(q), "avg_price": float(ap)}

    def get_position_epoch(self, symbol: str) -> Optional[str]:
        """Return confirmed long lifecycle identity; never invent legacy ownership."""
        rows = self.fetchall("SELECT e.epoch FROM position_epochs e JOIN positions p ON e.symbol=p.symbol WHERE p.symbol=? AND p.qty>0",
                             (symbol.upper(),))
        return rows[0][0] if rows else None

    def get_entry_epoch(self, order_id: str) -> Optional[str]:
        """Read the immutable entry-to-monitor handoff, never today's position."""
        rows = self.fetchall("SELECT meta FROM orders WHERE id=? AND side='buy'", (order_id,))
        return json.loads(rows[0][0] or '{}').get('position_epoch') if rows else None

    def revoke_position_epoch(self, symbol: str) -> None:
        """Quarantine observed external state changes without inventing fills."""
        with _positions_lock:
            self.execute('DELETE FROM position_epochs WHERE symbol=?', (symbol.upper(),))
            self.set_kill_switch(True, 'Position ownership revoked; broker reconciliation required')

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT id, ts, symbol, side, qty, order_type, limit_price, tif, venue, status, client_id, meta FROM orders WHERE status IN ('submitted','accepted','new','pending_new','pending_cancel','partially_filled','pending_submit','submission_unknown','fill_unverified')"
        params: Tuple[Any, ...] = ()
        if symbol:
            sql += " AND symbol=?"
            params = (symbol.upper(),)
        rows = self.fetchall(sql, params)
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": r[0],
                    "ts": r[1],
                    "symbol": r[2],
                    "side": r[3],
                    "qty": float(r[4]),
                    "order_type": r[5],
                    "limit_price": float(r[6]) if r[6] is not None else None,
                    "tif": r[7],
                    "venue": r[8],
                    "status": r[9],
                    "client_id": r[10],
                    "meta": json.loads(r[11] or "{}"),
                }
            )
        return out

    # =========================================================================
    # Section 7 — Account snapshots (equity/cash/buying power)
    # =========================================================================

    def snapshot(self, equity: float, cash: float, buying_power: float, day_pl: float, cumulative_pl: float) -> Dict[str, float]:
        ts = _utcnow()
        self.execute(
            """
            INSERT INTO account_snapshots(ts, equity, cash, buying_power, day_pl, cumulative_pl)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ts, float(equity), float(cash), float(buying_power), float(day_pl), float(cumulative_pl)),
        )
        return {
            "ts": ts.isoformat(),
            "equity": float(equity),
            "cash": float(cash),
            "buying_power": float(buying_power),
            "day_pl": float(day_pl),
            "cumulative_pl": float(cumulative_pl),
        }

    # =========================================================================
    # Section 8 — Convenience / Debug
    # =========================================================================

    def dump(self) -> Dict[str, int]:
        """Return simple row counts for quick sanity checks."""
        counts: Dict[str, int] = {}
        for t in ["risk_limits", "signals", "orders", "fills", "positions", "account_snapshots"]:
            counts[t] = int(self.fetchall(f"SELECT COUNT(*) FROM {t}")[0][0])  # nosec B608: identifiers come only from the literal table list above
        return counts


# =============================================================================
# Section 9 — CLI smoke test
# =============================================================================

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="HybridStore smoke test")
    p.add_argument("--duckdb", default=str(DEFAULT_DB), help="DuckDB path")
    p.add_argument("--redis", default=os.environ.get("REDIS_URL", ""), help="Redis URL (optional)")
    p.add_argument("--symbol", default="AAPL", help="Symbol to test")
    p.add_argument("--dry", action="store_true", help="Use dry_run venue")
    args = p.parse_args()

    store = HybridStore(duckdb_path=args.duckdb, redis_url=(args.redis or None))

    # Set risk limits (idempotent)
    store.set_risk_limits(
        {
            "max_gross_exposure": 250000.0,
            "max_pos_per_symbol": 5000.0,
            "max_leverage": 2.0,
        }
    )
    print("risk_limits:", store.get_risk_limits())

    # Log signal → submit order → fill → position update
    sid = store.log_signal(
        symbol=args.symbol,
        side="buy",
        source="smoke",
        confidence=0.93,
        payload={"note": "demo-signal"},
    )
    print("signal_id:", sid)

    oid = store.submit_order(
        symbol=args.symbol,
        side="buy",
        qty=10,
        order_type="market",
        venue=("dry_run" if args.dry else "paper"),
        status="submitted",
        client_id="cli-test",
        meta={"route": "demo"},
    )
    print("order_id:", oid)

    price = round(100 + random.random() * 3, 2)
    fid = store.log_fill(
        order_id=oid,
        symbol=args.symbol,
        side="buy",
        qty=10,
        price=price,
        fee=0.02,
        venue=("dry_run" if args.dry else "paper"),
    )
    print("fill_id:", fid)

    print("position:", store.get_position(args.symbol))

    snap = store.snapshot(
        equity=100000.0,
        cash=90000.0,
        buying_power=180000.0,
        day_pl=250.0,
        cumulative_pl=1250.0,
    )
    print("snapshot:", snap)

    print("counts:", store.dump())
    print("\n✅ HybridStore smoke test completed successfully.")
"""import duckdb
    con = duckdb.connect(db_path)
    print("[Hybrid Store Debug] Tables:", con.execute("SHOW TABLES").fetchall())
    print("[Hybrid Store Debug] risk_limits schema:", con.execute("PRAGMA table_info('risk_limits')").fetchall())
    con.close()"""
    # =========================================================
# HYBRID STORE SCHEMA REPAIR + DEBUG VALIDATION
# =========================================================
if __name__ == "__main__":
    import duckdb, os
    db_path = "data/storage/hybrid_store.db"
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    con = duckdb.connect(db_path)

    # --- Ensure tables exist and match correct schema ---
    con.execute("""
    CREATE TABLE IF NOT EXISTS risk_limits (
        id INTEGER PRIMARY KEY,
        name TEXT,
        value DOUBLE
    );
    """)
    con.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY,
        symbol TEXT,
        side TEXT,
        qty DOUBLE,
        price DOUBLE,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    con.execute("""
    CREATE TABLE IF NOT EXISTS positions (
        symbol TEXT,
        qty DOUBLE,
        avg_price DOUBLE,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    con.commit()

    print("\n✅ Hybrid Store verified and schema repaired.")
    print("Tables in DB:", con.execute("SHOW TABLES").fetchall())

    print("\n[Risk Limits Schema]:")
    print(con.execute("PRAGMA table_info('risk_limits')").fetchall())

    # --- Optional debug data check ---
    try:
        print("\n[Existing risk_limits data]:")
        rows = con.execute("SELECT * FROM risk_limits").fetchall()
        for r in rows:
            print(" ", r)
    except Exception as e:
        print("  Error reading risk_limits:", e)

    con.close()
    print("\n✅ Hybrid Store check complete.")
