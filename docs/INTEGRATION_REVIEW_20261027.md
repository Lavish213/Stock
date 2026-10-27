# Integrated repair review — October 27, 2026

## Scope and lineage

Operator explicitly requested review, merge and combined-default testing of
the open repair stack. The integration candidate includes PRs #19–#40,
including side branches #19, #22 and #29. It retains their source commits as
parents; no squash erases the research corrections or provenance. Default
before integration is `69f50f5aaa644b1fec96244576b4e70a61a58063` and main
repair tip is `697bea3801a32021f95bb1b9fc4922ba3cf4c458`.

All 22 PR review-thread/review inventories were checked. No unresolved inline
threads or changes-requested submissions were found. Existing #33/#34 findings
are addressed by the lifecycle-start lookup, quarantined-journal checks and
bounded quantity tolerance in the candidate. This is an agent code review,
not independent human approval. Repository workflows were inspected: normal
push/PR automation runs offline validation, not a deployment. No broker or
deployment command was invoked.

The three side branches add research diagnostics/reports, not execution
configuration. Historical research reports are not current readiness
certificates. Earlier replay P&L corrections and missing-evidence caveats
remain in place. The consumed SPY holdout is unchanged.

## Additional ownership repair

- Persist entry order → position epoch inside the confirmed-fill transaction.
  Option and equity entry launchers pass that receipt instead of rebinding
  to current inventory when a delayed worker starts.
- Native bracket watchers bind the original entry receipt. Before booking a
  nonduplicate fill they recheck epoch and remaining inventory under the
  shared position lock. Duplicate receipts remain idempotent after flattening.
- A broker quantity mismatch observed by the enforcing state audit revokes
  the local epoch and halts new entries. Existing epoch-bound workers cannot
  subsequently sell that uncertain inventory. Diagnostic-only audits do not
  change ownership. Neither audit creates an external execution or adjusts
  the ledger to match an unexplained broker position.

Six new synthetic regressions cover delayed startup, same-size replacement,
replacement inside broker lookup, a valid bracket and duplicate receipt,
observed external changes, and nonmutating/matching audits. Focused tests and
final full-suite/CI receipts are recorded on the integration PR. Test fixtures
are invented; no market-wide error-rate or profitability estimate follows.

## Do not mark these unfinished items complete

Ownership is still not a broker-atomic global coordinator. A manual round trip
that leaves the same quantity between snapshots can evade quantity-only
comparison. Authenticated broker execution/activity reconciliation and intended
host race evidence are required. Native bracket partial fills/late fee changes
and coordinated cancellation before creator-directed exits need further work;
this patch does not authorize a competing source close while server legs live.

Conditional promotion and coordinated creator trims/closes remain engineering
work. Existing prose restrictions stay in place. Unknown hold/retest rules,
missing contract years, incomplete source updates and infeasible fractional
contract splits are not supplied by guesswork. No automatic allocation or
risk policy change was made to make examples execute.

## Authentic replay evidence audit

`CREATOR_SCREENSHOT_REPLAY_20261014.md` records ten manually supplied images,
not ten authenticated original deliveries. The GOOGL October 9 / 350-call /
346 break-and-hold excerpt lacks year, original publication/receipt sequence,
target levels, stop and premium quotes. No executable historical GOOGL
contract or fill is certified. The later screenshot corpus preserves image
hashes, observation times and null original post/publication fields rather
than inventing them. JPM update linkage and CPI scenario versions likewise
need original timestamped records before authentic lifecycle replay.

No new authorized original post/version export or point-in-time options quote
dataset was available for this integration. Member posts and Instagram sign-in
remain outside verified coverage. A self-declared data-source label, screenshot
gain, synthetic quote tape or ask/bid model cannot authenticate a historical
fill. Quote-based models are hypothetical execution estimates unless matched
to authentic execution receipts. Therefore authentic creator replay remains
blocked, not passed, and complete YTD coverage is not claimed.

No orders, activation, account resets, halt bypasses, deployment, risk-limit
changes or automatic capital allocations occurred. The October 14 posted
$0.12 fees / zero cash reconciliation error remain resolved.
