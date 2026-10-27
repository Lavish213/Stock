# Native bracket repeated-receipt integrity — October 27, 2026

Base: verified default `aa415897a3f764c9a771c3c806b3ac9dc84bee6f`.
That default already includes the reviewed #19–#40 repair stack via #41;
this is a separate, unmerged follow-up, not another integration claim.

## Reproduction

Three targeted synthetic mutations failed on the default: a repeated filled
leg changed its price, regressed its cumulative fee, or increased the fee
without recording the additional commission. All still returned `exited`.
An unchanged/sparse-fee positive repeat passed. These four invented tests
are not authenticated creator samples or a representative error rate.

## Repair

Join a duplicate leg receipt to its persisted fill and verify entry order,
instrument, sell side, quantity and price before declaring it verified.
Reject price conflicts and regressing fees without changing the old fill.
Missing fee fields preserve confirmed commissions. Apply a validated late
cumulative fee increase exactly once, without another execution or inventory
change and without counting it as another trade outcome.

Persist an entry halt **before** updating the commission. This prevents a
crash between fee correction and halt from silently retaining an old
loss-streak/outcome decision. Return `fee_reconciled_review_required` for the
correction; a later identical receipt stays idempotent but never clears the
halt. Outcome/loss-streak reconciliation still requires review; this repair
does not claim to reconstruct it automatically.

Focused tests, full-suite completion and exact-commit CI are recorded in the
PR. Initial default-focused regression set: 541 passed. The new regression
file reproduced three failures plus one positive control before the change.

## Boundaries

No authentic broker receipt, creator alert or historical quote was obtained
for these mutations. No actual new fee discrepancy is attributed to the user.
The October 14 posted $0.12 fees / zero cash reconciliation error remain
resolved. No orders, deployment, live mode, account reset, halt bypass,
allocation or strategy changes were made. This does not complete conditional
entry promotion, creator trim/close orchestration, native partial-fill or
manual-order coordination, authentic creator replay or operational readiness.
