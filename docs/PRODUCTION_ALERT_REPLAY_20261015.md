# Fake creator alerts through production execution — October 15, 2026

**Performance figures superseded:** the replay originally left the contract parser on the computer's real date. Its expiry horizon rejected later virtual-year contracts. See `OPTIONS_RISK_COMPARISON_20261015.md` for the clock correction and new paired scenarios. Earlier cash figures do not establish annual or monthly performance. Individual fault/recovery checks retain only their explicitly tested scope.

This replaces the scope error in the earlier toy yearly study. The earlier
PR22 results remain a separate model, not this bot replay.

## What actually runs

Signed HTTP request bytes enter the real `alert_feed.handler_for`, signature
verification and durable `AlertInbox`. Its dispatcher calls `handle_alert_text`,
then the production parser, `execute_trade_from_post`, candle gatekeeper,
contract resolver, risk checks, fee-aware sizing, broker order payload builder,
fill confirmation, `watch_and_exit`, `ExitState`, fill ledger and callbacks.
No execution callback is replaced by a fake success. Only broker/data I/O,
virtual clock and worker scheduling are substituted. The HTTP connection is an
in-memory transport, not a public creator page or deployed webhook server.

The simulated exchange checks cash, ownership, limits, whole contracts, fees
and order client IDs. It cannot open a short position. External HTTP/socket
requests are blocked. No credentials, real alerts or real accounts are used.
Production risk checks remain active. No halts are cleared to force a result.

## Full-year results

Two continuous accounts, October 1, 2026 through September 30, 2027. Each gets
365 synthetic daily messages, exact retransmissions, and forged messages:
730 daily messages plus retransmission/forgery traffic. NYSE's published holiday
and early-close calendar produces 251 open days per account. Weekends and
holidays receive messages too, and production market-open checks block entries.

| Measure | $100 start | $1,000 start |
|---|---:|---:|
| Confirmed entries and completed exits | 17 | 18 |
| Recovered lost monitors, including partial-exit cases | 4 | 4 |
| Duplicate entries | 0 | 0 |
| Rejected forged messages | 365 | 365 |
| Rejected stale messages | 22 | 22 |
| Cash reconciliation failures | 0 | 0 |
| Broker/local position mismatches | 0 | 0 |
| Creator close instructions with no creator-driven exit | 3 | 3 |
| Scenario-only final cash | $69.70 | $1,178.60 |

These balances are NOT expected returns or strategy research. Scenario paths
deliberately select targets, stops and faults. Entry bid/ask is a synthetic
$0.10/$0.10; target outcomes use preset positive quotes, and losses use preset
negative quotes. No option valuation, market prediction or realistic spread
distribution is established. The same replay seed is not a fresh holdout.

The sparse trade count is real replay behavior, not 730 successful trades:
market hours, PDT, circuit-breaker, conditional/context and contract checks
block messages. Partial-entry and creator-trim cases happen to be blocked in
these yearly sequences. They are therefore NOT credited as year-long coverage.

## Isolated lifecycle probes

Fresh isolated ledgers exercise all 16 scenario types without clearing an
existing halt. Positive cases reach the real order and exit paths. Observed:

- Target exit, independent premium stop and underlying structural stop.
- Partial entry: multiple actual exchange fills before monitoring confirmed
  quantity. No fictitious requested-size fill.
- Lost acknowledgement after entry commits: client-ID lookup recovers the
  existing order; no second entry.
- Exit outage before write: durable unknown submission resolves and retries.
- Partial exit then worker interruption: broker fill exists before local
  acknowledgement; production pending-order reconciliation imports the delta,
  a fresh monitor adopts the durable close, and only remaining contracts sell.
- Restart after entry: lost in-memory worker is recovered from durable state
  and original target/stop. This is in-process worker reconstruction, not an
  operating-system crash certification.
- False breakout, incomplete confirmation, stale alert, forged signature and
  mismatched contract prevent entries. Exact retransmissions do not re-dispatch.

17 focused harness/calendar integration tests pass. They assert both positive
entries and blocked cases, ledger agreement, actual partial execution, recovery
and exposed unsupported creator actions. A test that detects a missing feature
is not evidence the feature works.

## Reproduced fix: fee-aware sizing

Before: a $100 budget at $0.10 premium requests ten contracts. The exchange
charges $0.65 per contract on entry, so a $100 account cannot fund the $106.50
entry. No trade occurs despite nominal premium affordability.

Added `OPTION_FEE_RESERVE_PER_CONTRACT`, a configurable per-side estimate used
to reserve entry and exit fees in contract sizing. The replay explicitly sets
it to the exchange's $0.65 fee. At $100, eight contracts reserve $90.40 including
both sides' fees and the real production path can enter and exit. Invalid
estimates fail closed. The default is zero to preserve existing deployments;
actual fee configuration still needs an authenticated account schedule. This
does not guarantee fees or future exit losses and does not change live settings.

Budget milestones persist in the simulation's explicit allocation argument:
100% before first $500, 50% after that milestone, 10% after first $1,000. The
production circuit-breaker/PDT behavior is retained; this replay does not
replace it with an unverified next-day reset policy.

## Failures still exposed

1. An open position's creator `STC` message does not submit its requested close.
2. An open position's creator 80% trim message does not submit the trim or
   establish a coordinated runner. Both isolated probes expose this gap; the
   annual run does not exercise trim while holding because its risk checks
   block those initial entries. Independent protection still closes later.
3. Conditional source setups remain review-only; generic candle confirmation
   on a separate BTO is not implementation of a creator's reclaim/hold policy.

These prevent claiming complete creator-following autonomy. No grade of A or
profitability claim is supported. Serial virtual scheduling does not measure
network latency or certify races. Real process-death/concurrency checks remain
separate tests, not events hidden inside every synthetic trade.

## Reproduce and inspect

`python -m lavish_core.validation.production_alert_replay --days 365 --initial 1000 --output NEW_DIRECTORY`

Repeat with `--initial 100`. For a fresh isolated scenario call
`replay(NEW_DIRECTORY, days=1, scenario_offset=N)` for N from 0 through 15.

Evidence: `docs/evidence/production-alert-replay-results-20261015.json` contains
aggregates and isolated results. `production-alert-replay-traces-20261015.json.gz`
contains all daily receipts, source text/timestamps, fills, risk state, monitor
results and notifications. The summary records SHA256 hashes of the compressed
and decompressed trace. Durable DuckDB files are created in each output folder.

Calendar source, observed October 15, 2026:
https://www.nyse.com/trade/hours-calendars
The replay uses the underlying's conservative 4 p.m. Eastern close (1 p.m. on
published short days); it does not assume all options trade to 4:15 p.m.
