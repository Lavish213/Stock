# Pipeline checklist audit — 2026-10-15

This is an honest disposition of all 100 proposed scenarios, not a claim that all 100 have passed. New tests use synthetic closed candles and mocked data transport. They send no orders. Existing broad suites are useful regression evidence but do not prove every exact race listed here.

## Repairs and evidence

Before repair, the initial ten new cases produced **6 failures and 4 passes**. Repairs cover bounded null/transport outages, degenerate reference rejection, and deadline enforcement with a monotonic clock. Earlier breakout/invalidation and timestamp normalization already behaved correctly. The default 1 bp minimum reference range is a conservative quality policy, not a tuned or profit-validated threshold. Config changes require restart.

`validation_tests/test_checklist_candle_faults.py` contains 14 cases, including malformed timestamps and wall-clock independence. **60 focused checks passed** across the new fault cases, existing candle tests, and gate/fill lifecycle tests. Complete repository results are recorded in the PR after execution.

A transport retry does not establish that the exchange is trading: market-open checks still apply, and this audit does not add an exchange-halt oracle. A broker fetch must retain its own bounded network timeout; the gate cannot interrupt a hung synchronous call.

## All 100 scenarios

| # | Scenario | Status / remaining evidence |
|---|---|---|
| 1 | Gap with no pending alert | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 2 | Pre-bell alert | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 3 | Zero-volume bars | New deterministic coverage — Zero-volume breakout confirms with explicit volume-unavailable reason. |
| 4 | Null response and recovery | New deterministic coverage — Three null/empty/error polls, recovery, then three more outages: streak resets. |
| 5 | Breakout then reversal between polls | New deterministic coverage — All closed bars processed; earlier breakout wins. |
| 6 | Near-flat reference | New deterministic coverage — Flat and near-flat reference rejected; 1 bp default quality floor. |
| 7 | Halt and transport outage | New deterministic coverage — Transient transport exception recovers; four consecutive failures stop safely. Real LULD feed behavior remains unverified. |
| 8 | Deadline during fetch | New deterministic coverage — Fetch returning at deadline cannot confirm; monotonic deadline. |
| 9 | CALL and PUT reference isolation | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 10 | Provider timestamp switch | New deterministic coverage — Equivalent UTC/offset timestamps normalized; live provider comparison remains unverified. |
| 11 | Confirmation immediately before close | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 12 | Unlisted strike and expiry | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 13 | Opposite-side dedup | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 14 | Broker clock skew during expiry exit | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 15 | First event wins whipsaw | New deterministic coverage — Earlier invalidation or breakout wins; no later overwrite. |
| 16 | Cross-source atomic claim | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 17 | BUYSPY token boundary | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 18 | Watermarked OCR strike | External / additional experiment — Requires original watermark fixture and production OCR result. |
| 19 | Mixed known and unknown tickers | Policy correction — Do not silently pick a known ticker from an ambiguous multi-instrument order; require unambiguous instrument evidence. |
| 20 | Hedge and target scoring | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 21 | Discord edit and delete | External / additional experiment — Requires actual Discord event integration evidence. |
| 22 | Image-only Patreon signal | External / additional experiment — Requires timestamped real image-only source payload. |
| 23 | Next Friday expiry | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 24 | Same-source duplicate | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 25 | Whitelist hot reload | Policy correction — Gate/config settings are read at startup; hot reload must not be assumed. |
| 26 | Confidence floor equality | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 27 | Leveraged SPY phrasing | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 28 | Unicode ticker lookalike | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 29 | Kill switch cleared before gate resolves | Existing partial coverage — Post-gate kill/market recheck already tested; clear-before-resolution policy is not separately established. |
| 30 | Very long alert | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 31 | Empty successful order response | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 32 | Huge fill slippage | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 33 | POST timeout with server-side success | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 34 | Multiple partial fill fragments | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 35 | Cancel versus fill race | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 36 | Exit poll HTTP 429 | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 37 | Client ID collision | Policy correction — Ambiguous client IDs should fail closed; deterministic selection could hide a duplicate. |
| 38 | Broker and host clock drift | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 39 | Exposure rejection audit | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 40 | Zero options quantity | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 41 | Provider hot swap | Policy correction — Changing providers in flight is unsupported; require explicit restart/reconciliation. |
| 42 | Both bracket legs filled | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 43 | Truncated Tradier tag | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 44 | Response body connection reset | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 45 | Account restriction diagnosis | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 46 | Simultaneous stop-outs | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 47 | Loss breaker preserves exits | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 48 | PDT entry versus exit race | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 49 | Unknown correlation ticker | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 50 | Failed account read blocks entry | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 51 | Reduce-only float epsilon | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 52 | Five pending gates and kill switch | Existing partial coverage — Existing threaded gate test proves one pending gate rechecks kill; exact five-worker race remains open. |
| 53 | Both price gates disabled | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 54 | Duplicate correlation groups | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 55 | External manual close reconciliation | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 56 | PDT holiday weekend | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 57 | Loss reset versus new loss | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 58 | Zero bid with positive ask | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 59 | Position scale hot change | Policy correction — Hot reload is unsupported; existing position lifecycle must retain original sizing. |
| 60 | Exact exposure limit | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 61 | Crash after exit submission | External / additional experiment — Requires process-kill and recovered broker state evidence. |
| 62 | Two ingest processes | External / additional experiment — Requires two actual ingest processes and storage backend verification. |
| 63 | Locked DuckDB write | External / additional experiment — Requires real concurrent DB lock experiment. |
| 64 | SIGTERM during gate | External / additional experiment — Requires shutdown lifecycle experiment. |
| 65 | Redis failure fallback | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 66 | Duplicate exit monitor claim | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 67 | Crash after gate before order | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 68 | Disk full logging | External / additional experiment — Requires filesystem/log-handler fault injection. |
| 69 | Notification hang during error | External / additional experiment — Requires notification hang and thread timeout evidence. |
| 70 | Error handler store creation failure | External / additional experiment — Requires nested error fault injection. |
| 71 | Early close clock | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 72 | Holiday alert | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 73 | DST handling | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 74 | Last-minute entry | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 75 | Weekend actionable timestamp | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 76 | Thin premarket data | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 77 | Wait exceeds time to close | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 78 | Wrong host clock diagnosis | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 79 | Expiry-close entry thrashing | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 80 | Expiry year boundary | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 81 | Broker sizing parity | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 82 | Native and active exit parity | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 83 | Tradier total cost basis | External / additional experiment — Requires Tradier sandbox position; no authenticated observation in this audit. |
| 84 | Tradier cancel endpoint | External / additional experiment — Requires actual Tradier sandbox cancellation. |
| 85 | Provider swap with open position | Policy correction — Changing providers with open positions is unsupported; require migration/reconciliation. |
| 86 | Duplicate adjusted strike | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 87 | Real cross-provider bar outcomes | External / additional experiment — Requires same symbol/window real data from both brokers. |
| 88 | Breaker and unrelated active exit | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 89 | Reversal while first gate pending | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 90 | Whole simulated trading day | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 91 | Cropped OCR duplicate | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 92 | Gate on and off shadow week | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 93 | Pending gate exposure snapshot | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 94 | Flatten with pending entries | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 95 | Three real crash points | External / additional experiment — Requires three actual process-crash experiments. |
| 96 | Creator sell and monitor race | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 97 | Mixed equity and options state | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 98 | Malformed env | Open exact scenario — not independently established by this audit; implement a targeted regression or cite matching prior evidence before marking passed. |
| 99 | Real network end-to-end latency | External / additional experiment — Requires timestamped real-network lifecycle measurement. |
| 100 | Full month SPY paper soak | External / additional experiment — Requires elapsed month of prospective SPY paper operation; cannot be replaced by synthetic replay. |

## Grade and next priorities

No A grade or less-than-3% production error claim is supported by these synthetic cases. Current session assessment remains **B overall / incomplete operational validation**. Next engineering priorities: atomic entry-versus-flatten coordination, exit ownership across all callers, calendar-aware last-minute and expiry entry blocks, multi-process claims/crash recovery, and exact broker ambiguity/partial-fill races. Real Tradier parity, source OCR fidelity, network latency, and a prospective month require separate evidence. Win rate and trading profitability are different metrics from deterministic test failures.
