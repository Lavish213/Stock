# Market-open hardening — October 13, 2026

Verified against repair branch parent `183df166de3e92bab8967c0f5950e3b3a860344c`.

## Results

- Baseline: 2,428 passed, 2 skipped.
- Added 11 broker-response fault regressions; final suite: 2,439 passed, 2 skipped in 96.74 seconds.
- Focused entry/exit recovery and monitor checks: 96 passed.
- All 330 tracked Python files compiled; dependency check passed.
- Bandit medium/high scan: zero findings. This is not a complete security certification.
- Authenticated read-only paper account and fresh SPY quote checks passed during the open market. Account had zero positions and zero open orders. No orders submitted in this session.
- Two real systemd Unix-socket notification tests remain skipped in this environment; they need verification on the deployment host.

## Reproduced defects and repairs

1. Paper-cycle validation accepted an entry response with a wrong side, quantity or client ID, and followed a changed order ID while polling. It now checks symbol, side, requested quantity, client ID, broker ID and filled quantity before recording a fill, following an order ID or cancelling an entry. A mismatch preserves the isolated journal and halts entries.
2. Production `ExitState` merged conflicting remote identity fields into the pending close and could record a false completed exit. It now rejects a changed broker ID or contradictory supplied symbol, side, client ID or requested quantity before recording fills or cancelling an order. It preserves exposure and the unresolved order and sets the persistent entry halt. Sparse broker adapters remain supported; absence of identity fields is not full identity certification.

## Tests exercised

The full suite includes parsing, OCR, source authentication, signed alert ingestion, source deduplication/replay, sizing and risk limits, market-hours and price-drift gates, call/put entry cases, quotes and spread faults, volatility/liquidity paths, broker failures, ambiguous submissions, partial fills, cancellation races, durable exit recovery, process death/restart, storage concurrency, kill switches, accounting, and reporting. Tests that emulate a broker are synthetic, not evidence of actual broker execution or profitability.

## Remaining operational blockers

- Existing paper round trip bought at $1.49 and sold at $1.48. Account cash previously differed by an additional $0.05. This session's activity snapshot after that cycle contained only its two fills and no posted fee activity. The existing posted-fee reconciliation repair remains unable to certify that difference without broker evidence.
- Alpaca documents intraday deductions for accrued fees and end-of-day charging: https://docs.alpaca.markets/us/v1.1/docs/regulatory-fees . Delayed fees are a plausible explanation, not verified attribution for this account.
- No authenticated incoming creator-alert source was demonstrated. A signing secret alone does not establish delivery or source authentication.
- Both configured default entry budgets were zero; automatic entry is not enabled.
- A persistent deployment host, working notification channel and deployed watchdog/restart recovery remain unverified.
- Whole-repository coverage was about 30% in the baseline; large experimental/orphan modules are included. Passing tests do not justify claiming that every code path is covered.

## Continued work

An hourly hardening automation was created to inspect this repair branch, reproduce defects and verify fixes. It does not place orders, arm live trading, reset accounts or disable halts. It is separate from a persistent trading service.

Automatic market execution remains blocked until source authentication, explicit budgets, accounting and host readiness are verified. No claim of profitability or live readiness is made.
