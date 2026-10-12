# Real-money readiness

Live entries are blocked until an operator explicitly approves evidence bound to the running code and intended account. Offline simulations cannot supply this evidence. Passing tests does not establish profitable trading or eliminate execution risk.

## Completed engineering controls

Paper/live endpoint validation; whole option contracts and 100-share multiplier; verified execution quantities/prices; transactional reservations; ambiguous submission quarantine; durable closing-order state; independent cross-process kill control; fresh quotes/closed candles; broker-vs-ledger audit; expiring live approvals and bounded live buy limits; account-bound trade/exposure/daily-loss caps. Exits remain available when live entry approval is missing.

## Evidence still required

| Check | Evidence required |
|---|---|
| authenticated_paper_lifecycle | Actual paper broker entry, partial/cancel behavior, exit, restart and cash/position reconciliation |
| deployment_recovery | Host restart, disconnect, process death and restored ledger/exit supervision observations |
| notifications | Actual alert delivery and escalation observations |
| strategy_evaluation | Independently verified forward broker exports, realistic costs, drawdown and sufficient out-of-sample observation against predefined acceptance criteria |
| key_rotation | Operator review confirming previously exposed keys were rotated and permissions limited |
| option_settlement_review | Broker exercise/expiry handling, funding, shares and next-day account activity reviewed |

No credentials, target deployment, forward exports or authenticated paper receipts were available for this engineering run. These checks remain incomplete.

## Run validation

Use the pinned `requirements-validation.txt` environment. Run `python -m pytest -q`; tests block external I/O. `lavish_core.validation.options_replay.replay` runs production options adapter, exit journal and FIFO reporting against a deterministic offline exchange. Synthetic prices and resulting profits are not strategy evidence.

For a read-only broker check, configure **paper** credentials privately, `TRADE_MODE=paper`, and `ALPACA_BASE_URL=https://paper-api.alpaca.markets`, then run `python -m lavish_core.validation.paper_probe --output paper-connectivity.json`. It submits zero orders. Connectivity is only a prerequisite, not a lifecycle approval.

Normalized closed-trade CSV analysis: `python -m lavish_core.validation.trade_export trades.csv --output analysis.json`. Required columns: trade_id, entry_at, exit_at (timezone-aware ISO), qty, multiplier (1 or 100), entry_price, exit_price, fees. The analyzer does not certify completeness/provenance or approve live trading.

## Approval format

Configure `LAVISH_READINESS_FILE` to a private JSON path. Schema version 1 requires explicit `operator_approved: true`, aware `issued_at` and `expires_at` (maximum seven days), current `runtime_sha256` from `lavish_core.trade.live_readiness.runtime_digest()`, SHA256 of the intended live account identifier, and positive operator-selected `limits`: max_trade_usd, max_gross_exposure, max_daily_loss_usd.

Each check above requires status `passed`, an allowed source from `live_readiness.REQUIRED`, an evidence_path and the SHA256 of its exact bytes. Evidence JSON requires the same check, allowed source, `synthetic: false` and `passed: true`. This is an operator attestation with integrity checks, not independent cryptographic proof of broker provenance. Do not relabel simulated or incomplete results as observed evidence. Changes to covered runtime files invalidate approval. Approvals are never auto-created.

On the deployment host, halt new entries with `python kill_switch.py --on "reason" --db /absolute/bot.duckdb`. This works while the bot holds the database open. Inspect with `--status`; only resume with `--off` after reconciling the cause. This halts entries and does not flatten positions.

Dashboard servers now bind localhost; expose through a properly configured authenticated reverse proxy only. Optional vision downloads require full 40-character model commit SHA environment variables and safetensors. Existing pickle memory is deliberately ignored; rebuild data-only memory from source records.
