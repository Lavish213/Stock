# Options exit reconciliation during quote outages

Parent: PR #27, remote commit c4f42f604629e58db5a5e33559eb6be569580566.

Two added synthetic variants failed against that monitor: after a simulated lost submission acknowledgment, a missing option quote delayed reconciliation until a later quote; a zero bid returned manual review without discovering that the existing exit had filled. A positive-quote control passed. These are mocked broker cases, not authentic fills or profitability samples.

The monitor now permits existing pending/unknown order reconciliation without a usable option quote. ExitState has an explicit allow_submit=False mode, which can resolve an existing order but cannot submit a replacement. A cancellation regression verifies that the remaining quantity is preserved and no replacement is sent. New orders still require the existing usable-quote checks. No sizing, budget, halt or strategy changes; no real orders.

Focused verification: 251 tests passed in 6.06 seconds across latch, protection recovery, fill lifecycle, broker faults and screenshot-trigger checks. The before-fix lost-ack comparison was two failed / one passed. Full validation was started on the parent revision before this follow-up; its result must not be presented as full validation of these edits.

The frozen 108-candidate synthetic research comparison was rerun with an independent $100 per-alert budget. This is six scenarios / 648 candidate trials, not a month or year of actual options trading, and not a shared or compounded account. Unresolved exits remain excluded from profitability comparison. Previously known close/trim coordination, durable pre-order intent recovery, replay ledger anomaly and authentic creator replay remain unfinished.
