# Phase 0 — Broker-Path Baseline (2026-11-13)

Scope: read-only inventory of every code path that can reach a broker, how
credentials and paper/live mode are selected, and the emergency controls.
No source was changed, no broker was contacted, no orders were placed.

- Branch: `claude/focused-ramanujan-obf2dt`
- Commit: `994980bdbf52f34893dc263cbcad512078af4b92` (identical to the
  baseline commit recorded by the external forensic program).
- Method: static search and reading. Nothing below was executed against a
  broker; runtime claims are limited to what the source shows.
- The external forensic reports (`00`–`38`) were **not available** in this
  session. This document stands on its own and should be cross-checked
  against `33_MASTER_FINDINGS_LEDGER.md` and
  `35_LAVISH_TARGET_ARCHITECTURE.md` once they are imported.

## 1. Production path (what actually runs)

`deploy/lavish-bot.service` → `system_boot.py` (pre-flight) → `run_ingest.py`

`run_ingest.py` starts reconciliation and account-snapshot loops, then the
authenticated alert sources (Patreon poller, private alert feed, Discord).
Orders flow:

```
alert source → alert_handler → lavish_core/trading/trade_handler.py
            → lavish_core/trade/broker.py   (BROKER_PROVIDER switch)
            → broker_alpaca.py / options_broker.py   (or Tradier equivalents)
```

Controls observed on this path:

| Control | Where | Behaviour seen in source |
|---|---|---|
| Mode/endpoint binding | `utils/safety.py:check_trading_endpoint` | `TRADE_MODE` must match host (`paper-api` vs `api`); unknown modes and ports rejected |
| Live entry gate | `broker_alpaca.place_order` → `live_readiness.assert_live_ready` | Live buys need limit price, an expiring operator approval bound to runtime hash, source config, account hash, and per-trade cap |
| Idempotency | `broker_alpaca.place_order` | Always sets `client_order_id`; POST never auto-retried; lookup by client id for ambiguous results. Default id is **minute-bucketed**, so a retry crossing a minute boundary gets a new id |
| Journal before submit | `HybridStore.submit_order(... status="pending_submit")` | Local intent recorded before the broker call (note: `store.submit_order` is the journal, not a broker call) |
| Kill switch | `HybridStore.get/set_kill_switch` + `utils/control_file.py` | Atomic file + DuckDB; corrupt/unreadable control file fails **closed**; entries only, exits unaffected |
| Startup state audit | `reconcile.synchronize_broker_state` → `state_audit.audit_state` | Mismatch sets kill switch; broker-positions failure returns `ok=False` |
| Market clock | `trade_handler._market_closed_reason` | Fails closed unless `RISK_CHECK_FAIL_OPEN=true` |

## 2. Every broker entry point found

"Gated" = goes through `broker.py` → `check_trading_endpoint` (and, for live
buys, `assert_live_ready`).

| # | File | Mechanism | Gated? | Reachable how |
|---|---|---|---|---|
| 1 | `lavish_core/trade/broker_alpaca.py` | REST `POST /v2/orders` | Yes (is the gate) | Production path |
| 2 | `lavish_core/trade/options_broker.py` | REST orders (options) | Check separately in Phase 1 | Production path |
| 3 | `lavish_core/trade/broker_tradier.py`, `options_broker_tradier.py` | Tradier REST | Via `BROKER_PROVIDER=tradier` | Production path (alt) |
| 4 | `lavish_core/trade/trade_agent.py` (`place_trade`, `TradeAgent`, `run_loop`) | `broker.py` | Yes, but mode comes from **`LIVE_TRADE`**, not `TRADE_MODE` | `run_live_trader.py`, `flatten_all.py`, `super_core.py`, `main_vision.py` |
| 5 | `lavish_core/trading/executor.py` | `alpaca-py` `TradingClient.submit_order`, market orders | **No** — no endpoint check, no client id, no live gate; `url_override` from env | Importable by any script |
| 6 | `lavish_core/vision/king_vision.py` | `alpaca-py`, `paper=True`, market BUY of **every 2–5 capital-letter word OCR'd from an image** | **No** | `python -m lavish_core.vision.king_vision` |
| 7 | `lavish_core/trading/auto_signal_runner.py` | raw `requests.post /v2/orders`, notional market buy, endpoint from `ALPACA_ENDPOINT` | **No** | Has `__main__` |
| 8 | `lavish_core/superbot.py` (`AlpacaExecutor`) | raw `requests.post` | **No**; `--mode live` CLI flag | Has `__main__` |
| 9 | `lavish_core/legacy_dashboard_api/` | FastAPI `POST /orders` → raw REST; **no auth, `allow_origins=["*"]` with credentials** | **No** | Currently **not importable** (missing `backend.health`/`orders`, `..config`, `..database.db`) — latent, not active |
| 10 | `lavish_core/validation/paper_cycle.py`, `options_replay.py` | `options_broker` | Paper/validation tools | Manual |
| 11 | `research/nautilus/smoke.py` | Nautilus simulated | N/A — self-declared no broker imports | Research |

Rows 5–9 bypass the endpoint check, the live-readiness gate, the journal and
the kill switch. They share the same `ALPACA_API_KEY`/`ALPACA_SECRET_KEY`
environment variables as production, so on a host whose `.env` holds live
keys, each of them can trade that account.

## 3. Findings

Severity is a Phase 0 judgement from source only.

### P0-1 (High) — `flatten_all.py` is a silent no-op under the documented configuration
`TradeAgent.default_mode` is `"live"` only when `LIVE_TRADE=true`, otherwise
`"dry"` (`trade_agent.py:713`). `env.example` and the production path use
`TRADE_MODE`; `LIVE_TRADE` is not set anywhere in `env.example`. With
`TRADE_MODE=paper` or `live` and `LIVE_TRADE` unset, `flatten_all.py`:
reads the local equity-only `positions` table (`trade_agent.py:749`),
"closes" in dry mode, prints `Done: flattened …`, turns the kill switch on
and posts a Discord message saying positions were closed. Broker positions,
including all options, stay open. The mode is printed, which is the only
hint. The emergency control therefore fails open in the scenario it exists
for.

### P0-2 (High) — Ungated side-door executors share production credentials
Rows 5–8 above place market orders with no endpoint/mode binding, no
idempotency, no journal, no kill switch and no risk checks.
`king_vision.py` is the most dangerous: any uppercase 2–5 letter token in
any image dropped into `lavish_core/king_master/raw` is bought, forever, in
a loop. `paper=True` is hard-coded, so it cannot hit live, but it will
corrupt a paper account used for qualification evidence.

### P0-3 (Medium) — Two mode vocabularies
`TRADE_MODE` (production, endpoint-checked) and `LIVE_TRADE` (`TradeAgent`,
`flatten_all`, `run_live_trader.py`). They can disagree: an order labelled
`live` by `TradeAgent` can go to the paper host (endpoint check uses
`TRADE_MODE`), and P0-1 is the opposite case. One source of truth is needed.

### P0-4 (Low–Medium) — Startup reconciliation reports "nothing found" on broker failure
`reconcile_open_positions` returns `0` when `get_positions()` fails
(`reconcile.py:281-284`); `_reconcile_startup` then logs
"Startup reconciliation: no unmanaged positions found." and sends no alert,
although nothing was checked. New entries are still blocked in that case,
because the earlier `synchronize_broker_state()` call sets the kill switch
when positions are unavailable (`reconcile.py:493-495`). Its return value is
discarded at `run_ingest.py:165`, so this protection depends on the kill-switch
side effect, not on an explicit state. The risk is a misleading operator log
and a missing alert, and an open position may be left without exit
supervision until the periodic loop succeeds. Fix: return an explicit
`FAILED/UNKNOWN` state and alert on it.

### P0-5 (Low) — Minute-bucketed default `client_order_id`
Callers that omit `client_order_id` get `uuid5(symbol|side|qty|type|minute)`.
A retry across a minute boundary is not deduplicated, and two genuinely
distinct same-size orders inside one minute collide. The production
`trade_handler` path appears to pass explicit ids; verify in Phase 1.

### P0-6 (Low) — Dead or empty deployment artefacts
`infra/Dockerfile` and `infra/docker-compose.yml` are 0 bytes. `requirements.txt`
has 38 of 40 entries unpinned (`requirements-deploy.txt` is fully pinned
and is what the service uses). No committed secrets were found by a pattern
scan of tracked files (not exhaustive).

## 4. Recommended next steps (no implementation authorised yet)

Ordered by risk removed per line changed:

1. Make `flatten_all.py` derive mode from `TRADE_MODE` (or refuse to run when
   `TRADE_MODE` and `LIVE_TRADE` disagree) and fail loudly in dry mode.
2. Quarantine rows 5–9: move to `archive/` or make them raise on import
   unless an explicit research flag is set; add a test that fails if any
   module outside `lavish_core/trade/broker*.py`/`options_broker*.py`
   references `/v2/orders`, `TradingClient`, or `submit_order` on a broker
   client.
3. Act on `synchronize_broker_state()`'s result at startup explicitly (not only via its kill-switch side effect)
   and make `reconcile_open_positions` raise or return an
   explicit failure state instead of `0`.
4. Remove `LIVE_TRADE`; single `TRADE_MODE` everywhere.
5. Then proceed with roadmap Phase 1 (isolated gateway, durable intents,
   explicit `CLEAN/GAP/FAILED/UNKNOWN`).

Each of these is a source change and needs explicit authorisation.
