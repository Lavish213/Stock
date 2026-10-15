# SPY calls: synthetic creator alert month

Replaces the instrument mismatch in the earlier fractional-share experiment. This is a separate, isolated long-call experiment on base ea3e63ea596051483369d6c0a03949ad6ae94414. It neither enables trading nor changes production budget, halt, or strategy settings.

## What runs

A local synthetic creator emits original timestamped daily option entry texts through the production `handle_alert_text` parser. Entry dispatch is intercepted into an isolated Decimal-cash call adapter. Entry requires SPY, CALL, BUY, a positive strike and unexpired explicit expiration, weekday session, fresh event and an uninvalidated underlying target/stop. Whole contracts have a standard 100 multiplier. IDs deduplicate retries and conflicting versions are rejected. This is a local alert system; no public creator page, Discord message or Patreon post is published.

Buy at invented ask plus $0.01 adverse premium slippage; sell at invented bid less $0.01, with a minimum executable penny. Invented full premium spread is $0.04 and hypothetical fees are $0.65 per contract per side. Closing fees are reserved at entry. $100 cannot buy a $1.00 premium contract with these costs. No leverage, fractional contracts or short options.

Targets and stops are underlying levels parsed from the alert, not premium levels. A typed synthetic creator exit specifies the held strike/expiry; a mismatched contract cannot close it. Exit signals are not sent through production's entry-only parser. Target, stop, creator exit or 12:45 Pacific cutoff requests a sell at the subsequent available modeled quote. A zero bid is not a filled sell. A remaining out-of-the-money position may expire worthless at 13:00 Pacific; an unresolved in-the-money position fails the experiment rather than silently exercising. No automatic exercise or share delivery is modeled.

## User rules and assumptions

Start $100, spend available cash below first $500 milestone, then 50%, then 10% after first $1,000 milestone; reduced tiers persist after drawdown. Ordinary net losses continue. Unspecified big-loss and repeated-loss definitions retain the explicit test assumptions of 10% of day-start balance, single or cumulative, and three consecutive net losses. Pause the rest of the modeled day and resume the next weekday when flat. Reviews persist; no automatic training occurs. These thresholds are not confirmed production settings.

Dates are October 1–November 1, 2026: 32 calendar dates, not a nonexistent October32nd. One attempted position/day, weekdays only. This does not certify exchange holidays, early closes, broker restrictions or settlement readiness; next-weekday proceeds reuse is an explicit simplification.

## Price generation and reproducibility

Every seed's complete future path is generated before it trades. Underlying moves have zero expected drift and toy volatility initially25% annualized, converted to minute increments over252x390 minutes. Volatility itself fluctuates with invented noise. A European call formula generates premium time decay and changing volatility; it is not a calibrated American SPY valuation or authentic options chain. Strikes are fixed10–22 dollars above the first price, with same-day expiration; no strike is selected after observing returns. Synthetic daily SPY anchors are independent rather than an authentic continuous history. Alerts carry no future path information and confer no informational edge.

Paired runs use the exact same generated paths at0,60 and180 seconds of entry/exit delay, sampled on a minute grid. Full fills and available positive bid/ask liquidity are assumptions. Actual quotes, queue priority, partial fills, outages and durable process restart are not modeled by this month adapter. Existing broker-adapter and recovery tests are checked separately; they do not turn toy fills into authenticated fills.

Run from repository root:

```bash
python -m lavish_core.validation.options_creator_month --output /tmp/options-creator-new-run --trials 200
```

The output directory must be new. Network calls are blocked during execution; the parser's production execution dispatch is intercepted. Seeds20261015 through20261214 are fixed in advance. No retuning occurred on PR14's consumed historical evaluation, and no previously rejected SPY strategy was promoted.

## Results, including losses

| Alert delay | Profitable months /200 | Median final | Worst | Best | Reached500 |
|---|---:|---:|---:|---:|---:|
|0 seconds|0|$10.00|$1.20|$45.70|0|
|60 seconds|0|$9.675|$1.20|$45.30|0|
|180 seconds|1|$9.55|$1.00|$251.90|0|

First immediate-delivery month: $100 ->$16.30, five trades, two net wins. Immediate cohort contains957 completed trades and2584 unaffordable-contract rejections. All runs end flat with zero cash reconciliation error and zero real orders. One delayed seed's luck is not evidence that delays improve performance.

The supplied screenshot's .27 ->1.07 premium claim is also used as a clearly labeled positive arithmetic fixture with invented contract identity and timestamps: $100 sizes three contracts and gains $230.10 after this model's costs. A contrasting .27 ->.01 fixture loses $84.90. Neither verifies the screenshot's original execution. This preserves positive entry/profit cases without assuming every random creator alert wins.

## Grade and limits

Synthetic execution logic: narrowly verified by focused contract, sizing, duplicate, date, expiry, pause/resume, target invalidation, cash, zero-bid and positive-entry tests. These have zero failures in the tested fixtures; this is not an estimated field error rate. Actual-source replay sample size0; creator authenticity and original quote availability unverified. Profitability grade: unsupported; this random synthetic feed loses substantially. Autonomous operational readiness: unverified source authentication, explicit actual budget, persistent deployment and authentic quote/fill/latency evidence. No overall A grade is justified.

Evidence is in `docs/evidence/options-creator-month-20261015/`: first complete month trace, cohort summary and every seed's outcomes. Model assumptions, not real expected returns, determine this distribution. OCC's official educational references for contract multipliers, leverage, time decay and premium loss: https://www.optionseducation.org/optionsoverview/options-basics and https://www.optionseducation.org/strategies/all-strategies/long-call .
