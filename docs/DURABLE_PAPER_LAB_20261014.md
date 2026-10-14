# Durable fake-creator lab and automatic paper readiness

This change builds a runnable synthetic alert system, durable execution
controller, realistic fill mechanics and a supervisor gate. It does not certify
autonomous trading. The typed lab has no broker imports, no broker credentials,
and no route that can place a real or broker-paper order. The existing production
text parser and broker pipeline remain separate, with their existing safeguards.

## Implemented behavior

| Requirement | Implemented and tested | Operational limit |
|---|---|---|
| Alert delivery | Loopback HTTP, timestamp/body HMAC, strict schema, bounded body, duplicate header rejection | Local synthetic publisher authentication does not prove creator identity |
| Source history | Original event IDs, full immutable event payload, source publication time and local observation time in SQLite receipts | Synthetic publication times are never relabeled as historical creator times |
| Entry confirmation | Explicit trigger, count of closed bars, explicit interval, consecutive confirmations, session and quote freshness checks | “Break and hold” without an interval/count still requires review in production |
| Correct instrument | Exact equity ticker or OCC-format long call with unexpired date | No inferred option strike, expiry, underlying stop, or missing target |
| Sizing | Integer units, 100-share call multiplier, per-trade/gross/cash caps, pending-entry cash reservation | CLI allocations are simulation settings, not Angelo's approved paper allocations |
| Execution | Buy at ask, sell at bid, explicit fee per unit per fill, fill delay, per-quote available liquidity, partial fills | Invented executable quotes; no historical or profitability evidence |
| Exits | Allocated target ladder, stop gaps, expiry intent, owned partial/full creator closes, one pending intent per setup | Production text-directed partial/option closes remain quarantined until an authenticated typed adapter is implemented and reviewed |
| Amendments | Increasing source version, immutable old receipts, trailing stop updates, pending-exit conflicts rejected | Update cannot revive a terminal setup |
| Recovery | SQLite FULL synchronous transactions, persistent pending intents, restart without account reset, identical receipt retries | Atomic synthetic ledger recovery is not a substitute for broker-side ambiguous-order reconciliation |
| Risk | Cash basis and realized P&L in integer cents, daily realized-loss latch, persistent halt, exits allowed while halted | Lab day bucket is UTC; real exchange sessions and calendars come from the existing broker pipeline |
| Deployment | Separate systemd state directory, secret preflight, READY/WATCHDOG notifications, restart and sandbox settings | Unit is prepared; no persistent host deployment or reboot observation occurred |
| Automatic paper gate | Startup and periodic watchdog checks for source/account/code-bound, expiring evidence, explicit budgets, approved caps and daily baseline | Missing evidence latches entry halt; valid approval never clears a halt |

## Run a fresh complete campaign

From the repository root:

```sh
python -m lavish_core.validation.paper_lab_campaign --output /tmp/lavish-campaign-unique
```

The directory must be fresh. Existing state is never reset. The command creates
a random ephemeral signing secret, starts a loopback receiver, publishes
synthetic setups/quotes/candles/amendments/closes, retries identical messages,
reopens the controller after partial entries, and produces `results.json`,
`transcript.json`, and a durable `campaign.sqlite`. It stops its receiver after
the run. No operational approval is written.

Verified October 14 campaign: four complete equity/call lifecycles, six rejected
HTTP cases, 50 events and 44 identical retry deliveries. All positions ended
flat. Cash error was zero. Modeled fees were $10.40, and modeled net P&L was
$30.22. Median receipt latency was 1.177 ms and maximum 2.420 ms on this host.
Those timings measure this small typed lab, not the production text parser or
authenticated delivery. The former production text benchmark remains separate
and previously had slow entries and late exits. Neither benchmark earns an
overall autonomous-readiness grade from synthetic success.

## Run the isolated receiver

Set `PAPER_LAB_SECRET` to a randomly generated hex secret of at least 32 bytes
in a private environment file. Never put it in URLs, source control or logs.

```sh
python -m lavish_core.validation.durable_paper_lab --db /tmp/lavish-lab.sqlite --cash 10000 --trade-budget 250 --gross-limit 1000 --daily-loss 100 --check
python -m lavish_core.validation.durable_paper_lab --db /tmp/lavish-lab.sqlite --cash 10000 --trade-budget 250 --gross-limit 1000 --daily-loss 100
```

It listens only on `127.0.0.1:8878`. `GET /health` states simulation-only status.
`POST /v1/events` requires one `Content-Length`, `X-Timestamp` (integer Unix
seconds), and `X-Signature`. Signature is the hex HMAC-SHA256 of timestamp,
newline and exact body, using the hex secret string as key. Both transport and
publication time must be no more than 120 seconds old and never future dated.
Use `paper_lab_campaign.publish` as a local publisher. Its destination is fixed
to loopback. Authentication is mandatory even for synthetic input.

Every event has exactly `id`, `kind`, `published_at`, plus these fields:

| Kind | Required fields |
|---|---|
| setup | plan, version=1, symbol, asset=`equity` or `call`, expires_at, trigger, hold_bars, bar_seconds, stop, targets |
| quote | symbol, bid, ask, underlying, market_open, liquidity, available |
| candle | plan, closed_at, close, market_open |
| update | plan, next version, stop, targets |
| close | plan, next version, quantity of owned whole units |
| tick | no additional fields |
| halt | no additional fields |

Use timezone-aware ISO timestamps and decimal-string prices. Targets are a
strictly increasing list of `{price, fraction}` records whose fractions sum to
one. Prices and stops refer to the underlying; bid/ask refer to the tradable
equity or call premium. A target cannot be interpreted as a premium unless a
different reviewed protocol explicitly says so. Entry buys are capped at their
initial ask plus modeled fees; a worse quote cancels the remaining entry rather
than exceeding the budget. Target quantities round down; the final target exits
the remaining units. Stops and expiry exit all owned remaining units.

Quote events and explicit ticks advance execution. This intentionally does not
invent market observations while idle. An expired setup cannot enter; an open
position waits for an available, fresh, open-session quote before an expiry
exit. Simulated unavailability cannot fabricate a successful fill.

## Automatic paper activation evidence

`run_ingest.py` enforces `LAVISH_PAPER_READINESS_FILE`, defaulting to
`data/paper-readiness.json`, at startup and each watchdog cycle. Paper approval
uses the existing evidence validator with `mode: paper`, a paper-account hash,
runtime hash, configured source hash, aware issue/expiry dates (maximum seven
days), explicit operator approval and positive limits:
`max_trade_usd`, `max_gross_exposure`, `max_daily_loss_usd`.

Required nonsynthetic, hash-bound evidence: authenticated source delivery,
cash reconciliation, authenticated paper lifecycle, deployed restart recovery,
deployed notifications, and options settlement review. Evidence must match
source/account/runtime identities, precede review and be at most 30 days old.
Both `DEFAULT_TRADE_DOLLARS` and `DEFAULT_OPTION_TRADE_DOLLARS` must be explicit,
positive and within the approved cap. Daily account baseline must exist.
Approved caps tighten existing risk caps. Any missing proof or broker-read
failure persists an entry halt while reconciliation and exit workers continue.
An operator must separately review and clear a halt; approval does not do that.
This supervisor gate supplements existing broker risk controls; it is not a
replacement for per-order live approval.

No activation document was created. Actual creator authentication, Angelo's
paper allocations, persistent host recovery/notification evidence, and authentic
point-in-time creator replay still need verification. The prior five-cent cash
question was already resolved October 14 with authenticated total posted fees
of $0.12 and zero reconciliation error. This change does not reopen that issue.

Before claiming complete autonomy, promote the typed plan lifecycle through a
reviewed authenticated creator adapter and real broker gateway, coordinate it
with existing native brackets/ExitState, and verify a broker-paper lifecycle
with authentic quotes and account evidence. The current synthetic controller
must not be described as that integration. Live trading additionally requires
the separate existing live approval and strategy evaluation.
