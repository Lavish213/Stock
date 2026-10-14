# $100 SPY month — requested sizing and first-loss halt

This is an offline, synthetic fractional-share experiment. It does not fund
an account, place orders, enable fractional production execution or activate
trading. The production text parser is used; the production broker executor,
authenticated alert ingestion and actual broker fill timing are not exercised.

## Confirmed user rules

- Start with $100 simulated cash.
- Below $1,000, allocate the entire available balance, reserving modeled fees.
- After first reaching $1,000, permanently switch to 10% of the current balance
  per entry. The threshold is a goal, not a guaranteed outcome.
- After the first completed trade with negative P&L after costs, block all new
  entries and emit a local review/relearning request. No automatic retraining,
  strategy changes or resume occurs. Risk-reducing closure remains allowed.
- Long fractional SPY shares only, no options, shorts, margin or leverage.

“First to32nd” is interpreted as 32 simulated calendar days, October1 through
November1, 2026—not a nonexistent calendar32nd. Every day has a random labeled
fake creator alert, including weekends; modeled weekday sessions alone can
enter. Signals include explicit entries, conditional setups, recaps and wrong
tickers. Every alert is retried to test duplicate protection. Conditional setups
without a defined confirmation policy remain review-only rather than guessed.

One position is closed before or at the modeled 1p.m. Pacific equity-session
deadline each day. Settlement, real exchange holiday calendars, broker outage
latency and authentic SPY bid/ask are not certified by this experiment.

## Reported run—not cherry-picked

First predetermined seed20261014: **$100 → $99.50**. The first trade entered
October2 at10:21a.m. PDT, hit its simulated stop and exited at11a.m. PDT. Net
loss was $0.50 including $0.02 modeled round-trip fees. It created the relearning
request and blocked entries for the rest of the32-day run; alerts continued to
be observed. All positions were flat. It never reached $1,000.

The initial adapter run incorrectly rejected every entry because it expected a
`side` field instead of the production equity parser's `action`. That run was
discarded as an invalid harness result, not counted as successful safety or
profit performance. The corrected harness asserts actual entry and completed
trade coverage, including a production-parser-to-fractional-adapter positive
case, so blanket rejection cannot pass.

The200 consecutive predetermined seeds, starting20261014 (including the first run), returned:

| Metric | Synthetic result |
|---|---:|
| Ending above $100 | 55/200 |
| Halted after first net loss | 199/200 |
| Reached $1,000 | 0/200 |
| Median ending balance | $99.745 |
| Lowest ending balance | $99.25 |
| Highest ending balance | $102.82 |
| Actual broker orders | 0 |

These frequencies describe the invented generator only, not the probability of
making money with SPY or this bot. This is not a historical backtest. A tenfold
capital goal cannot be justified by these results. A first-loss latch limits
further simulated trading; it does not make prior losses impossible.

## Explicit model assumptions and limitations

- Prices begin around770 and evolve via uncalibrated random draws, not historical
  SPY data. Three-minute log returns have mean0 and standard deviation0.0008.
- Creator targets are0.3–0.8% above the hypothetical entry mid; stops0.3–0.6%
  below. The parsed numeric levels govern exits. No future prices are in alerts.
- Two-basis-point full spread, one-basis-point adverse slippage on each side,
  and $0.01 hypothetical fee on each side. These are stress assumptions, not an
  authenticated broker tariff. Gaps fill at the observed modeled price, not
  magically at the stop level.
- Fractional quantities round down to nine decimal places. Charges round up,
  proceeds down to cents. The opening allocation reserves closing fees and
  cannot borrow. Flat-account cash reconciles against cumulative net P&L.
- Filled prices and the near-instant model exits do not measure real latency.
  No self-learning model was trained from randomly generated profitable paths.

Tests cover all-in affordability, fee reserve, threshold crossing, persistent
10% mode, first-loss halt, duplicate/conflicting IDs, no unsupported entry,
positive production parser dispatch,12 full randomized months and same-day
flatness. Real production execution still rounds equity quantities to whole
shares; this adapter is not permission to enable unverified fractional orders.

Run with a fresh output directory:

```sh
python -m lavish_core.validation.small_account_month --output /tmp/spy-month-unique --trials 200 --seed 20261014
python -m pytest validation_tests/test_small_account_month.py -q
```

Artifacts: `month.json` contains the first run's32-day trace; `summary.json`
contains the cohort result; `trials.json` retains each trial's trades, cash and
review requests. Existing results are not reset or overwritten. Production
budgets, live mode and operational readiness approvals are not changed.
