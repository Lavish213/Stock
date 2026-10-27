# Reconcile existing exits after quote-fetch exceptions

October 16, 2026. Parent PR #28, remote revision `0a2ebbd2d0eade5b5da2f1b6cc39ba5e4d533a09`.

## Reproduced defect

The existing lost-acknowledgment integration test was extended with a synthetic quote-fetch RuntimeError on the next poll. The new case failed against the parent: three quote calls were required instead of two because `_exit` propagated the exception before looking up the already accepted order. Positive-quote, missing-quote and zero-bid controls passed (one failure / three passes). Persistent quote exceptions could indefinitely prevent status recovery, even when the broker lookup was available. The test uses a fake broker and real local ExitState/store; no broker orders are sent.

## Repair and limits

Determine whether an exit is pending/unknown before fetching its quote. If that quote call raises an Exception, reconcile the existing order with `allow_submit=False`, logging only the exception class at this boundary. If no order exists, retain the original error/retry path. A quote exception does not authorize any replacement or new submission, even if reconciliation discovers cancellation. Broker lookup errors retain their existing handling. No fabricated quote or fill is introduced.

The lost-ack regression requires exactly one submission, two quote attempts, one booked sell fill, one fee application, flat local inventory and one callback. A separate positive-recovery test keeps dispatch blocked through two initial quote exceptions, then allows the normal attempt after a usable quote returns. The preexisting cancellation/no-replacement regression remains included.

Focused offline validation: **414 passed in 13.00 seconds** across ten files covering alert instruction integrity, signed ingestion/deduplication, candle/context rules, broker faults, partial-fill lifecycle, cash, equity restart, protection and options latching. The latch file now has nine collected cases. These are overlapping synthetic/regression samples, not 414 authentic alerts or evidence of profitability. Bandit reported no findings in the changed production module; `git diff --check` passed. A new full-suite run is not claimed.

Unfinished: durable trigger intent across process death, complete creator close/trim coordination, the separate intermittent synthetic replay-ledger anomaly, authenticated creator/options replay, explicit paper budgets and actual persistent deployment. The resolved Oct14 posted-fee reconciliation remains resolved. No sizing, strategy, risk configuration, account reset or execution-mode change is included.

## Research observation

Public-source refresh around 12:16–12:19 PDT on October 16 found no newly verified creator setup or calendar change warranting a separate signal. Instagram and Patreon retrieval remained inaccessible. WSB new/discussion/earnings were accessible through retrieved pages, while the DD-filter retrieval was marked crawled three weeks earlier; complete or fresh coverage is not claimed. Reddit content was not treated as order authorization. No new source alert, original posting timestamp, historical quote or economic actual is invented for this regression.
