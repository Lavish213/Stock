# run_ingest.py
# Single entry point for watching both alert sources (Patreon + Discord) at
# once. This has to be one process, not two: they share one DuckDB file
# (lavish_core/db/hybrid_store.py) for the order/fill/signal audit trail and
# the cross-source dedup check in trade_handler.execute_trade_from_post(),
# and DuckDB does not allow two separate processes to hold the same
# database file open concurrently - running the Patreon poller and the
# Discord listener as two "python -m ..." processes would fight over the
# file lock. Patreon polling runs in a background thread; the Discord
# client owns the main thread (discord.py-self manages its own event loop
# and blocks on client.run()).
#
# Discord only actually connects if DISCORD_USER_TOKEN and
# DISCORD_INGEST_ACCEPT_TOS_RISK=1 are both set - see
# lavish_core/discord_ingest/listener.py for why. Without them, this runs
# Patreon-only, same as always.
from __future__ import annotations
import os, threading
from pathlib import Path

# Has to happen before ANY lavish_core import, not just before use - most
# of the modules imported below read their config (ALPACA_API_KEY,
# TRADE_MODE, every risk/guardrail threshold) as module-level constants
# via os.getenv(...) at import time. One module deeper in the chain
# (patreon_refresh.py) already called load_dotenv() on its own, but by
# the time Python reaches that import, alert_handler -> trade_handler ->
# broker_alpaca has already been imported and already read (empty)
# environment variables - a real .env file's values would never actually
# reach the process. Loading here, first, guarantees every module below
# sees the real values from the very first line it executes.
#
# Explicit path, not load_dotenv()'s bare default - the no-args form
# resolves relative to wherever Python's call stack says the *caller's
# frame* lives, not the process's working directory. That's usually the
# same as this file's own directory in normal use, but a systemd unit
# with a WorkingDirectory that doesn't match, or running this via a
# symlink/wrapper, silently breaks that assumption - confirmed by testing
# this from a different cwd than the repo root, which reproduced exactly
# that silent-no-op failure. Anchoring to this file's own directory makes
# it correct regardless of cwd or how the process was launched.
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

import time

from lavish_core.logger_setup import get_logger
from lavish_core.patreon import patreon_trigger
from lavish_core.discord_ingest import listener as discord_listener
from lavish_core.trade.reconcile import reconcile_open_positions, run_periodic_reconciliation
from lavish_core.trade.account_monitor import run_periodic_snapshot
from lavish_core.utils import sd_notify
from lavish_core.db.hybrid_store import HybridStore, DEFAULT_DB
from lavish_core.utils.alerts import post_discord

log = get_logger("run_ingest", log_dir="logs")

# Must stay well under deploy/lavish-bot.service's WatchdogSec (90s) - a
# missed ping or two shouldn't trip a restart, only an actually-stuck
# process should. A no-op everywhere this isn't run under systemd
# Type=notify (see sd_notify.py) - the thread still runs, it just never
# sends anything.
WATCHDOG_PING_INTERVAL_SECONDS = int(os.getenv("WATCHDOG_PING_INTERVAL_SECONDS", "30"))
if not 1 <= WATCHDOG_PING_INTERVAL_SECONDS <= 30:
    raise ValueError("Watchdog ping interval must be 1 to 30 seconds")


def _run_watchdog_pings(workers=None) -> None:
    from lavish_core.utils.worker_health import WORKER_HEALTH
    from lavish_core.utils.control_file import write_control
    while True:
        health=WORKER_HEALTH.status({name:thread.is_alive() for name,thread in workers.items()} if workers is not None else None)
        if not health["healthy"]:
            reason="worker health failure: "+", ".join(health["reasons"])
            log.error(reason)
            try:
                write_control(DEFAULT_DB,True,reason)
            except Exception as exc:
                log.error("Unable to persist worker halt: %s",type(exc).__name__)
            # Terminate so systemd can restart and restore durable exit supervision.
            # A foreground launch exits visibly and requires an operator restart.
            os._exit(1)
            return
        sd_notify.notify_watchdog()
        time.sleep(WATCHDOG_PING_INTERVAL_SECONDS)


def _log_startup_config() -> None:
    """
    One consolidated line at boot with the config that actually matters for
    trading behavior - previously the only way to know what a running
    process was actually configured to do was to go read its env vars
    directly; this makes it visible in the log stream itself, which is
    what anyone monitoring the bot is actually tailing.
    """
    whitelist = os.getenv("WHITELIST_TICKERS", "")
    discord_on = bool(discord_listener.DISCORD_USER_TOKEN and discord_listener.ACCEPT_TOS_RISK)
    log.info(
        "=== Lavish_bot startup config === "
        "TRADE_MODE=%s ALPACA_BASE_URL=%s tickers=%d "
        "discord_ingest=%s options_own_stop=%s%% daily_loss_limit=%s%% "
        "weekly_loss_limit=%s%% max_consecutive_losses=%s "
        "max_correlated_positions=%s position_size_scale=%s",
        os.getenv("TRADE_MODE", "dry"),
        os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        len([t for t in whitelist.split(",") if t.strip()]),
        discord_on,
        round(float(os.getenv("OPTIONS_OWN_STOP_LOSS_PCT", "0.50")) * 100, 2),
        round(float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.03")) * 100, 2),
        round(float(os.getenv("WEEKLY_LOSS_LIMIT_PCT", "0.07")) * 100, 2),
        os.getenv("MAX_CONSECUTIVE_LOSSES", "5"),
        os.getenv("MAX_CORRELATED_POSITIONS", "3"),
        os.getenv("POSITION_SIZE_SCALE", "1.0"),
    )
    if os.getenv("TRADE_MODE", "dry").lower() == "live":
        log.warning("TRADE_MODE=live - this process will submit REAL orders with real money.")


def _reconcile_startup() -> None:
    """
    Every exit-guardrail thread lives only in this process's memory - on
    every start (including a crash-restart) any option position already
    open at the broker has no one watching it until this runs. Do it once
    immediately (main() also schedules run_periodic_reconciliation for
    ongoing coverage in case a single monitor thread dies without taking
    the whole process down with it). Split out from main() so this - the
    actual behavior, not the thread startup around it - is unit-testable.
    """
    try:
        recovered = reconcile_open_positions()
        if recovered:
            log.warning("Startup reconciliation recovered %d unmanaged position(s).", recovered)
            post_discord(f"⚠️ Lavish_bot started: recovered {recovered} unmanaged open position(s) "
                          f"left over from a previous crash/restart - they now have an exit guardrail watching them again.")
        else:
            log.info("Startup reconciliation: no unmanaged positions found.")
    except Exception as e:
        log.error("Startup reconciliation failed: %s", e)
        post_discord(f"🚨 Lavish_bot startup reconciliation FAILED ({e}) - any position left over from "
                      f"a previous crash may have no exit guardrail watching it. Check manually.")


def main() -> None:
    _log_startup_config()

    # Forces schema creation (CREATE TABLE IF NOT EXISTS, etc.) to happen
    # exactly once, synchronously, in the main thread - BEFORE any of the
    # threads below get a chance to each construct their own first
    # HybridStore() and race to create the schema concurrently. Confirmed
    # directly (not assumed): two threads racing to initialize a
    # HybridStore against a brand-new database file can raise
    # "TransactionException: Catalog write-write conflict" - a real crash
    # risk specifically on a fresh deploy's very first start, before this
    # fix. Every later HybridStore(...) call anywhere in the process just
    # opens a connection to an already-fully-created schema from here on.
    HybridStore(duckdb_path=str(DEFAULT_DB), redis_url=os.environ.get("REDIS_URL") or None)

    if os.getenv("TRADE_MODE","dry") in ("paper","live"):
        from lavish_core.trade.reconcile import synchronize_broker_state
        synchronize_broker_state()
    _reconcile_startup()
    from lavish_core.utils.worker_health import WORKER_HEALTH
    from lavish_core.trade.reconcile import RECONCILE_INTERVAL_SECONDS
    from lavish_core.trade.account_monitor import ACCOUNT_SNAPSHOT_INTERVAL_SECONDS
    from lavish_core.utils.safety import finite_number
    grace=finite_number(os.getenv("WORKER_OPERATION_GRACE_SECONDS","300"),positive=True)
    workers={}
    specs=[
        ("reconcile-loop",run_periodic_reconciliation,RECONCILE_INTERVAL_SECONDS),
        ("account-snapshot-loop",run_periodic_snapshot,ACCOUNT_SNAPSHOT_INTERVAL_SECONDS),
    ]
    has_patreon=bool(os.getenv("PATREON_ACCESS_TOKEN") or all(os.getenv(k) for k in ("PATREON_CLIENT_ID","PATREON_CLIENT_SECRET","PATREON_REFRESH_TOKEN")))
    if has_patreon:
        specs.append(("patreon-poller",patreon_trigger.poll_loop,patreon_trigger.POLL_SECONDS))
    from lavish_core.feeds import alert_feed
    if alert_feed.enabled():
        server,inbox=alert_feed.make_server(DEFAULT_DB)
        specs.extend([
            ("alert-feed-server",lambda:alert_feed.run_server(server),1),
            ("alert-feed-dispatcher",lambda:alert_feed.run_dispatcher(inbox),1),
        ])
    discord_on=bool(discord_listener.DISCORD_USER_TOKEN and discord_listener.ACCEPT_TOS_RISK)
    if not has_patreon and not alert_feed.enabled() and not discord_on:
        raise RuntimeError("Configure an authenticated alert source before launching")
    for name,target,interval in specs:
        WORKER_HEALTH.register(name,finite_number(interval,positive=True)+grace)
        thread=threading.Thread(target=target,name=name,daemon=True)
        workers[name]=thread;thread.start()
    threading.Thread(target=_run_watchdog_pings,args=(workers,),name="watchdog-ping-loop",daemon=True).start()
    log.info("Supervised broker workers and configured alert sources started.")
    sd_notify.notify_ready()
    if discord_on:
        log.info("Starting configured Discord listener.")
        discord_listener.run()
        if not has_patreon and not alert_feed.enabled():
            raise RuntimeError("Only configured alert source stopped")
        log.warning("Discord stopped; remaining configured source continues.")
    # Keep the one-process broker journal and source supervisors alive.
    while True:
        time.sleep(30)


if __name__ == "__main__":
    main()
