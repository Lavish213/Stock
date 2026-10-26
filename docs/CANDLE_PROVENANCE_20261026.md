# Candle and feed provenance prerequisites

Research preparation, not execution authorization. Stacked on PR #37. The
default branch remains `69f50f5aaa644b1fec96244576b4e70a61a58063` when checked.

`aggregate_closed_minutes` accepts explicitly timestamped one-minute OHLC and
emits only complete UTC-aligned windows available at the requested cutoff.
Missing minutes are never filled. Duplicate, unordered, misaligned, nonfinite
or inconsistent candles fail validation. Availability is the latest constituent
receipt. It does not infer creator policy, trading sessions, adjustment basis,
source authenticity or revision history. It is not wired to order execution.

A reproduced integration defect dropped signed-feed event identity before
the actual research journal (`post_id` became null). Dispatch now retains it
under source `private-alert-feed`. This is a transport event ID, NOT a creator
post ID. Sender `created_at` is deliberately not passed as `published_at`.
The new end-to-end journal regression failed before repair and passes after.

Focused offline set: 336 passed, including signed ingestion, context, candle,
broker-fault, cash, restart, durable exit intent and inventory controls. These
overlap; they are not independent market samples. Full-suite and CI outcomes
must be checked separately on the published head.

Source refresh: BLS October calendar, BEA schedule, DOL claims PDF, Census
2026 calendar, Federal Reserve October calendar and SF Fed AI event page
showed no material change to previously reported events. Public creator search
did not establish a new authenticated GOOGL/JPM/CPI setup. No comprehensive
Instagram/Patreon coverage is claimed.

Reddit refreshed as research only: new listing; earnings `1xdjnsl`, discussion
`1xfc7de`, DD `1xaucr7`. Original exact publication times and edit/version IDs
were unavailable and remain unknown. Relative labels and cached crawl dates
must not be converted into original posting times or used for historical fills.
No new authentic point-in-time option quotes/fills were obtained.

Conditional promotion, creator trim/close orchestration, position-epoch
ownership and production-to-Nautilus differential parity remain open.
Authenticated persistent delivery, explicit applicable paper limits, intended
host recovery and prospective quote/fill evidence remain unverified. No
strategy retuning, account reset, halt bypass, orders, deployment or risk
changes. The Oct14 SPY study and resolved posted-fee reconciliation stand.
