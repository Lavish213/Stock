# Native bracket cumulative execution accounting

## Reproduced on caf2b52

Four targeted synthetic tests failed before repair: cancelled partial execution
was omitted from inventory, partial-to-final completion did not retain a review
halt, regressing partial quantity was ignored, and a direct no-leg watcher did
not halt. These are selected adversarial regressions, not a population error
rate, actual broker executions or creator replay.

## Implemented

- Convert each observed child leg's cumulative quantity, average price and fee
  into incremental durable sell fills. Cancellation/expiry does not erase an
  already confirmed partial execution.
- Recover cumulative accounting from fill-event identities on restart. Preserve
  existing full-receipt event IDs so pre-change repeats do not duplicate sells.
- Reject changed identity/price, regressing quantity/fees, invalid terminal
  quantity, stale lifecycle, overselling inventory or sibling legs exceeding
  the original entry (even when unrelated scale-ins share its position epoch).
- Persist a halt before booking a partial fill. Continue observing the native
  leg; do not submit, replace or cancel orders. A cancelled partial returns
  `partial_fill_review_required`, not `no_fill` or `exited`.
- Retain late fee correction semantics from PR42. Never clear an existing halt.
- A missing-leg direct watcher also halts, without relying on its supervisor.

Eleven new tests include completed partial-to-final execution and idempotent
restart controls; they do not reward blanket rejection. Focused related tests:
44 passed. Full-suite and CI results must be checked on the published commit.

## Explicit limits

This accounts for observed native partial fills. It does not authorize a
replacement protective order, creator trim/close, manual account reconciliation,
or clear an operational halt. Changed child requested quantities still require
review. Receipt discovery is polling of the original known leg IDs, not an
authenticated broker-activity stream or proof of same-quantity external trades.

Conditional promotion, coordinated creator management, production-to-Nautilus
differential replay, complete authenticated creator coverage, original edit
history, historical quotes, approved execution budgets and intended-host
deployment/recovery evidence remain separate unfinished gates. No trading,
risk allocation or strategy setting was enabled or changed. The Oct14 posted
$0.12-fee/zero-drift reconciliation remains resolved; these are invented tests.
