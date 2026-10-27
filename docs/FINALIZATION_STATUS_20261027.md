# Trading bot checklist — October 27, 2026

## Verdict and verified baseline

**Not end-to-end complete.** This checklist records actual dispositions, not
permission to trade. No orders, deployment, live activation, account reset,
halt bypass or risk/allocation changes were performed.

- Verified default HEAD: `69f50f5aaa644b1fec96244576b4e70a61a58063`.
- Creator research branch: `d134977adfd51a9ec3260313d69549ca8b8c763c`.
- Base of this repair: PR #39, `53aeb4cbbd6d24afec63c55ee24fab00c9d80686`.
- PR #39 full offline result: 3,576 passed, 2 skipped; CI 40503189084 passed.
- Repair branches remain stacked and unmerged. Default does not contain them.

## New reproduced lifecycle repair

Two deterministic synthetic races failed before this change: an old active
ExitState submitted a sell after a same-symbol/same-quantity position closed
and reopened; a close/reopen during the broker callback let the old fill consume
the new position. A third targeted probe showed generic pending-order
reconciliation could bypass the new active-worker guard and book the old sell.
These are adversarial mechanics, not representative market error rates.

The ledger now persists a random position epoch when a confirmed fill starts
a new long lifecycle. Scale-ins and trims retain it; flat/reopen rotates it.
Epoch changes are inside the existing fill transaction. Active options/equity
ExitState workers bind that epoch, save it in closing-order metadata, validate
it before submission/adoption and recheck under the position lock before fills.
Generic reconciliation enforces the same rule. Delayed async worker starts
receive the identity captured before scheduling, rather than rebinding to a
replacement position. Active options workers cannot latch or clear another
lifecycle's durable exit intent. Quarantined journals cannot be revived merely
because their epoch still matches.

Missing epoch ownership is not silently invented: legacy positions or old
unbound exit journals require reconciliation and cannot be auto-adopted for
selling. No user account or persistent deployment was migrated in this task.

Positive controls cover scale-in/trim identity stability, persistence across
store reopen, a valid close, partial-fill restart, fee conservation, duplicate
entry deduplication and delayed scheduling. Six old monitor test cases needed
real synthetic entry fills plus an explicit epoch attribute; their protection
assertions were retained. An exploratory run with the incomplete doubles failed
and stalled; it is not counted as a completed pass. Broad runs started before
the final edits are not certification of the final tree. Final focused, full
suite completion and CI outcomes are recorded in the PR after they finish.

## Current checklist and remaining closure gates

| Item | Disposition | Exact remaining work/evidence |
|---|---|---|
| Signed feed IDs and confirmation-language guards | Implemented in repair stack, offline-tested | Review/merge dependent repairs; retain source-shaped and positive-entry regressions |
| Closed-window aggregation | Implemented pure validator | Wire only an explicit supported policy through production; no inferred creator hold rule |
| Active ExitState lifecycle ownership | Implemented scoped repair | Final-tree suite/CI; entry-to-launch and all non-ExitState paths still need lifecycle-wide coordination |
| Native bracket / manual/external coordination | Open | Bind every bracket and external fill to the correct lifecycle; observe broker-side races without minting approvals |
| Conditional-entry promotion | Open engineering | Explicit setup policy; point-in-time closed/available bars; invalidation/deadline/replacement/duplicate/cash/restart tests through production ingestion |
| Creator trim / close orchestration | Open engineering | Authenticated instruction-to-owned-position mapping; whole-contract trim semantics, concurrent monitor coordination and durable partial/cancel/recovery tests |
| Production-to-Nautilus differential replay | Open engineering | Same normalized intents/tape through both engines; CALL/PUT, fees, latency, liquidity, cash and event-by-event differences explained |
| Nautilus preparation | Research lab implemented, smoke only | Existing nine invented CALL variations are not differential parity, market samples or authentic fills |
| Persistent source authentication | Unverified | Authorized publisher identity and actual timestamped delivery into receiver; never export cookies or bypass access controls |
| Creator history/YTD completeness | Incomplete | Post/comment/version inventory with readable/locked/missing dispositions; unknown original times remain null |
| Paper risk plan | Unverified for execution | Applicable explicit operator-approved capital, trade/exposure/daily-loss limits; old simulation requests are not execution authorization |
| Quote entitlement and authentic replay | Unverified | Authorized point-in-time contract quotes, original availability times, real fees/slippage and gaps; no invented fills |
| Intended-host recovery and notifications | Unverified | Observe delivery, process/network death, supervisor recovery and host restart on the intended persistent host |
| Prospective evaluation | Not established | Predeclared acceptance criteria plus authentic forward quote/fill/latency receipts; cannot reuse the consumed holdout |
| Final integration / release | Open | Review and merge stack in dependency order, final integrated CI, schema/recovery review and existing readiness gates satisfied |

The old $0.05 concern remains resolved by authenticated posted $0.12 fees and
zero reconciliation error on October 14. Do not reopen it without new evidence.
PR #14's SPY report/results remain the baseline: the selected blend failed later
superiority gates and tested ORB/VWAP variants lost money. The 2025–October 2026
evaluation is consumed. Fixed trend/cash and volatility-capped trend remain
prospective paper research hypotheses; no option-profit inference is justified.

## Scope limits

The in-process position lock cannot atomically control a remote broker or
manual orders. A race after the pre-submit check can still reach the broker;
the receipt is quarantined instead of being booked to a replacement position.
This repair is not a global creator lifecycle coordinator, native-bracket
ownership implementation, approval system, deployment or profitability proof.
No overall A grade or representative less-than-3% error claim is supported.
