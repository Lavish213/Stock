# Quote-based options profitability research

This research tool compares explicit hypotheses; it has not found or established a profitable strategy. It has no broker imports, execution hooks, account access or production configuration writes. It is separate from the production-path fault replay and does not repair its intermittent ledger defect.

## Frozen first comparison

108 candidates: two sampled entry gates (hold, retest then hold), six alert receipt delays (0, 5, 15, 30, 60, 120 seconds), three expiry buckets (1–7, 8–21, 22–45 remaining 24-hour periods), three exit policies (underlying target, +45% premium, 80% scale plus runner). These are research choices, not creator instructions. When the creator specifies a contract, identity is binding: a different expiry cannot silently substitute. Otherwise choose the nearest strike among direction-matching, quoted contracts in the bucket, with symbol as the fixed tie-breaker. There is no optimization over delta, IV, volume or volatility regimes yet.

The entry gate requires two sampled minutes beyond the structural level with gaps no longer than 60 seconds. Retest requires an earlier observed move above/below the level followed by a return to its opposite side. This is not proof of continuous holding, a volume confirmation, or the creator's unspecified definition of hold. Exact targets and stops come from explicit alert fields. A received creator close cancels a pending entry; after entry it requests a delayed exit. Creator trim text parsing and live position orchestration remain separate work.

Quotes must have distinct contract/event identities, event timestamps and availability timestamps with timezones. No observation may be available before its event. Prices and sizes are validated; future-unavailable and stale observations are excluded. Original post/version IDs, publication and receipt times are preserved. Every eligible source alert must be included, including failures; promotional screenshots alone are not a representative sample. Regime tags are reporting metadata, not trade filters and not a claim that the tag was known in advance.

## Accounting and limits

Each alert trial has an independent $100 premium-and-entry-fee budget by default. This is not a shared $1,000 portfolio, a monthly return, the user's tiered allocation policy, or compounding. Budget can be supplied explicitly for offline sensitivity work. Production risk rules are unchanged.

Entry uses a quote available at confirmation plus one second; quotes/ticks older than five seconds are refused. Buy at ask plus $0.01 adverse slippage, sell at bid minus $0.01 (floor zero); charge $0.65 per contract per side. These are research cost assumptions, not authenticated broker fees. Entries are whole contracts capped by displayed ask size and budget; relative bid/ask width must be no more than 10% of ask. Exits require sufficient displayed bid size for the requested leg. The model does not simulate queue position, competing orders, routing, partial-exit fills or guaranteed liquidity.

All policies include structural invalidation, a 30% premium drawdown trigger, received creator close and a session-exit request five minutes before supplied close. Triggers do not guarantee stop prices. A later executable quote is required after order latency. Missing exits remain unresolved with `net: null`; affected candidate comparisons are ineligible. Closed-trade subtotals remain explicitly labeled and must not conceal unresolved trades. One contract is sold whole; it cannot retain a fractional runner. The supplied session boundaries need independent calendar verification; the tool does not authenticate exchange schedules or source provenance. `declared_opra` is a declaration, never automatic authentication.

## Completed mechanical experiment

The saved synthetic input contains six invented scenarios: trending confirmation, chop, favorable stock move with falling option premium, adverse gap, missing exit quotes, and a late alert. Across 108 candidates it produces 648 independent trials: 270 closed, 234 without confirmed triggers, 54 invalidated/already at target before entry, and 90 unresolved exits. These counts exercise decision and accounting behavior, not prediction accuracy or a market win rate. Outcome frequencies and quotes were invented. Do not rank the candidates by these profits.

25 isolated tests pass, including positive CALL/PUT entries, each expiry bucket, whole-contract cost conservation, false-break rejection, future-quote exclusion, exact-contract identity, delayed adverse exits, insufficient depth, close-before-entry, creator close latency, integer scale-outs and invalid input. The old virtual environment was unavailable in this session; tests ran with a temporary pytest dependency and `--noconftest`. A local socket blocker remains active in this test file. This does not replace full repository CI or production-path validation.

Reproduce the saved comparison:

```bash
python -m lavish_core.validation.options_edge_lab \
  --input docs/evidence/options-edge-lab-synthetic-input-20261015.json \
  --output /tmp/new-options-edge-report.json --budget 100
```

Output creation refuses to overwrite an existing file. Input and compressed output hashes are retained in `docs/evidence/options-edge-lab-summary-20261015.json`. Full per-alert results are in the adjacent `.json.gz` artifact.

## Evidence needed to find an edge

Obtain an authorized, complete alert export with original versions and receipt times, a verified session calendar and authentic point-in-time option BBO/depth covering all candidate contracts and exits. Preserve gaps; never manufacture an option price from an underlying chart. Alpaca's official documentation states that historical options start in February 2024, indicative quotes are derived rather than actual OPRA BBO, and OPRA requires a subscription: https://docs.alpaca.markets/us/docs/historical-option-data (checked October 15, 2026). OIC explains why favorable underlying direction may coexist with an option loss from volatility changes: https://www.optionseducation.org/videolibrary/implied-volatility-and-post-earning-volatility-risk.

Before real evaluation, freeze this code revision, data manifest, cost assumptions, candidate definitions and chronological split. Use development data to diagnose mechanics and select at most one candidate. Evaluate that frozen candidate on prospectively collected data once; compare net expectancy, losing months, drawdown, concentration, coverage and unresolved-trade rates with the unchanged baseline. Stress costs/latency and use uncertainty estimates clustered by session, with multiple-comparison correction if claiming significance across 108 candidates. No universal sample count guarantees an edge. The previously consumed 2025–October 2026 SPY holdout is excluded from fresh validation claims. This tool currently reports independent trial expectancy and regime subtotals; portfolio drawdown, monthly capital feasibility and statistical inference require the complete authentic dataset and a separate verified portfolio replay.

No winner, profitability grade, monthly earning estimate, live promotion or allocation change is justified by this synthetic run.

## Event-clock hardening follow-up

Two added regressions failed against the initial PR revision: a structural stop breached between option quotes was forgotten after the underlying recovered, and a creator close received between quotes waited until the next quote to start its latency clock. The research loop now merges underlying ticks, creator receipts, session deadlines and option quotes chronologically. A stop remains requested even if price subsequently recovers; filling still requires a later fresh option quote and sufficient depth. A separate regression verifies that the session-exit request starts before the final quote, rather than at it.

Multiple versions of the same source post in one independent-trial batch are now rejected. They need an explicit edit lifecycle replay, otherwise revisions could inflate the number of entries. Original version metadata remains in the input and per-trial output. This guard does not certify that an externally supplied version is the original; provenance still needs independent verification.

The expanded focused file contains 29 tests. The broader focused run, using the repository's shared offline fixtures and recovered installed dependencies, passed 387 tests in 6.10 seconds. Bandit identified no issues in the research module. The unchanged six-scenario input was rerun; totals remain 270 closed, 234 untriggered, 54 invalidated/already at target, and 90 unresolved. New hashed traces are in `options-edge-lab-event-clock-results-20261015.json.gz` with the adjacent summary; initial traces remain preserved. These corrections affect research timing, not production execution or trading rules. Production close/trim orchestration and the intermittent production-path replay ledger issue remain open.

The complete offline validation directory passed **3,183 tests in 126.11 seconds**, including the 29 edge-lab regressions. This is mechanical validation, not profitability validation.
