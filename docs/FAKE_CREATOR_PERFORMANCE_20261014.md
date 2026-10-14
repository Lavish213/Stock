# Fake creator alert sender and execution performance test

This is a reusable **actual localhost HTTP publisher**. It sends signed JSON
creator-style alerts to the production `/v1/alerts` handler, persists inbox
receipts, invokes the production parser, candle gate, sizing, broker adapters,
fill journal and closing monitors. Broker HTTP is replaced with an isolated
offline exchange; every other Requests HTTP call is blocked. No real orders,
credentials, account budget or production database is used.

Run in its own process with the pinned validation environment:

```bash
TRADE_MODE=dry DISCORD_WEBHOOK_URL='' python -m lavish_core.validation.fake_creator --directory NEW_OUTPUT_DIRECTORY
```

`results.json` checkpoints after every case, with original generated text,
instrument, expiry, simulated budget, receipt status, candle outcome, expected
quantity, entry/exit events, wall-clock dispatch time and final cash/positions.
The output directory must be new. No persistent service is claimed.

## What gets triggered

26 scenarios cover AAPL shares and NVDA calls, four simulated budgets
(99/100/250/500), targets and stops, candle confirmation/invalidation/timeout,
future/stale alerts and closed markets. Every message is delivered twice over
TCP/HTTP to test duplicates. Twelve accepted cycles must buy and close the
correct symbol and quantity; fourteen blocked cases must submit nothing.
At budget 250, the generated fixture buys two shares or two standard call
contracts. All completed cycles must end flat with reconciled model cash.

This uses actual wall-clock sleeps with an **accelerated synthetic market**:
monitor polling is 50 ms; modeled triggers become eligible after two polls.
Ordinary runtime polling settings are changed only in the isolated simulator.
Gate-worker scheduling is inline, and exit monitors run synchronously inside
each case; this is not a production daemon/thread scheduling certification.
Existing tests separately check asynchronous exit-worker supervision.

## Honest grading

- Any incorrect case yields F, regardless of reported profits.
- Rejecting all alerts cannot earn A: supported entries and blocked cases
  must both be present.
- A timing benchmark requires zero dispatches over the chosen 3,000 ms cap
  and zero exits more than 500 ms after the first eligible model trigger.
- Correct execution with timing misses earns B.
- These thresholds are experiment criteria, not promised market latency.
- Loss-making correct executions are not classified as software mistakes.
- Actual creator accuracy, autonomous readiness and profitability are not
  established by this benchmark.

The corrected mixed target/stop run completed all 26 cases: zero case errors,
12 accepted round trips and 14 blocked cases. While other tests ran, two
dispatches exceeded 3 seconds and one exit was 541 ms late; timing grade B.
Median dispatch was 2,275 ms, maximum 5,883 ms. No timing failure is hidden
by a profitable outcome. An earlier measurement incorrectly included duplicate
processing in exit delay; the metric now starts at actual trigger eligibility.

Modeled gross P&L is **-8.00** for this balanced target/stop mixture. The model
uses zero fees, zero spread and immediate fills; slippage only appears through
the adverse stop-price jump. It is not historical or forecast trading profit.
Fee-aware options adapter/journal simulations exist separately; fees, spread,
partial liquidity and real source timing still need integrated observations.

Focused pipeline regression: 113 passed. Eight grading tests passed, including
invalid timing budgets, blanket rejection, profitable failures and correct
losses. Bandit found zero medium/high findings; dependencies and whitespace
checks passed. The final sequential timing observation is recorded below.

Sequential observation (no concurrent tests launched by this run): all 26
cases correct, 12 completed cycles and 14 blocked cases, but four dispatches
over 3 seconds and two exits over 500 ms. Median dispatch 1,489 ms; maximum
12,429 ms; maximum exit delay 1,634 ms. Timing grade remains **B**. These
variable local delays warrant investigation and do not justify a real-market
timing guarantee. The first run's passing accuracy does not erase these misses.
