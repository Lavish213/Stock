# Stock bot: intensive evidence rubric

**Assessment date: October 16, 2026 (America/Los_Angeles). Overall development judgment: C, provisional. Unattended trading: blocked. Profitability: not established.**

The C is an editorial judgment about current development maturity, not a calculated score. This document creates a 96-check, 12-domain assessment with 100 total weighted points. The checklist is complete; the item-by-item certification is not. Every item starts unassessed until linked evidence supports its exact scope. A synthetic test pass cannot certify an authentic feed, broker outcome or return expectation.

Reviewed repair branch: PR #28, remote `0a2ebbd2d0eade5b5da2f1b6cc39ba5e4d533a09`; matching local working tree at `1998c33ece4f2d97b18ad9b368e16292f7ba87a9`. This evaluates the repair stack, not proof that those repairs are merged, deployed or present on the default branch. No execution settings are changed by this document.

## How grading works

| Score | Evidence standard |
|---|---|
| 0 | Failed or absent; separately label observed failure versus missing evidence |
| 1 | Design documented or isolated unit evidence |
| 2 | Reproducible production-path offline integration, including valid positives and relevant faults |
| 3 | Representative authentic-source replay and/or authenticated paper observations applicable to the criterion, reviewed against complete traces |
| 4 | Repeated prospective or actual-host evidence meeting predefined limits, with drift monitoring and reviewed recovery where applicable |

For a completed assessment, domain points = weight × sum(eight check scores) / 32. Sum domain points to obtain the weighted development-evidence score out of 100. Publish assessed-item coverage and evidence origin alongside it. An incomplete assessment may show a conservative lower bound with unassessed items contributing zero, clearly labeled incomplete; it cannot claim those missing observations were tested failures. Do not drop difficult checks or renormalize away inaccessible sources. Scope changes require a new rubric version and rationale.

Scores track evidence maturity, not expected returns. Report execution readiness and economic evidence separately. A high weighted score cannot override any mandatory gate below. Thresholds in this document are proposed project rules rather than guarantees or industry certifications. Numerical strategy thresholds, risk budgets and operating deadlines must be fixed before evaluating new data; they are not silently chosen to make a run pass.

## Mandatory gates

| Gate | Requirement | Current state | Evidence gap |
|---|---|---|---|
| R1 | Authentic authorized source and complete chronology | blocked | No independently verified complete creator feed/replay; inaccessible Instagram/member posts remain outside coverage. |
| R2 | Faithful instructions including held-position exits | blocked | Unsupported ambiguous instructions are guarded; complete production close/trim coordination remains unfinished. |
| R3 | Account-bound risk and feasible capital | unverified | Controls exist; intended paper budgets and observed account feasibility are not certified. |
| R4 | Exactly-once lifecycle and cash/inventory integrity | blocked | Intermittent synthetic replay duplicate-order/extra-fill anomaly remains unexplained; not the resolved Oct14 broker fee concern. |
| R5 | Durable exit recovery on actual host | blocked | PR28 improves in-process lookup during missing/zero quote; durable trigger intent and actual host recovery remain incomplete. |
| R6 | Actual monitoring, escalation and secure deployment | unverified | Persistent deployment, actual notification observations and security attestations remain incomplete. |
| P1 | Authentic executable replay with complete outcomes | blocked | No complete authenticated creator/options dataset; unresolved trades prohibit profitability comparison. |
| P2 | Frozen strategy passes prospective economic criteria | not_established | SPY selected blend failed later-period superiority; tested ORB/VWAP lost. No options profitability inference. |

R1–R6 govern unattended execution evidence. P1–P2 govern any claim of demonstrated profitability. Passing either group does not automatically authorize orders or allocation changes. No composite letter grade cancels these requirements.

## Current strengths and weaknesses

| Area | Grounded assessment | What improves it |
|---|---|---|
| Offline execution mechanics | Substantial fault and positive-entry coverage; PR28 contains focused reconciliation repairs | Fresh revision-bound integrated results, then authentic lifecycle evidence |
| Alert understanding | Tests cover negation, conflicting prices, contracts and conditional triggers | Independently labeled complete source samples and measured false rejection/false execution |
| Exit coordination | In-process stop/target latching and quote-outage lookup improved | Durable pre-submit trigger intent, creator close/trim coordination, crash-boundary tests |
| Accounting | Conservation checks exist, but an intermittent replay defect is unresolved | Reproduce duplicate rows/extra fill under controlled DB/process isolation and prove root cause |
| Authentic inputs | Feed mechanics exist; complete authorized creator chronology and authentic option coverage remain unverified | Authorized exports, receipt logs, version history and actual point-in-time quotes |
| Strategy | No demonstrated superior strategy or options edge | Frozen rules, authentic costs, complete portfolio replay and genuinely subsequent evaluation |
| Deployment | Runbooks and supervision controls exist; actual intended host observations are incomplete | Reviewed service/notification/restart receipts tied to the deployed revision |

Existing evidence reviewed: `docs/OPTIONS_QUOTE_RECOVERY_20261016.md`, `docs/OPTIONS_EXIT_TRIGGER_LATCH_20261015.md`, `docs/OPTIONS_EDGE_LAB_20261015.md`, `docs/OPTIONS_RISK_COMPARISON_20261015.md`, `docs/SPY_STRATEGY_STUDY_20261014.md`, and `docs/REAL_MONEY_READINESS.md`, plus the listed test files and exit-state code. Prior test counts overlap; they must not be summed as independent observations. No fresh full-suite result is claimed by creating this rubric.

The six-scenario edge lab contains 648 synthetic candidate trials: 270 closed, 234 untriggered, 54 invalidated/already at target and 90 unresolved. Closed is not synonymous with profitable. Independent per-alert $100 budgets do not constitute a shared $1,000 account. The Oct14 fee reconciliation remains resolved; the separate local synthetic ledger anomaly remains open. Successful annual reruns do not erase its original failing traces.

## Detailed requirements

Every check requires: evidence origin; exact code/config/data hashes; sample interval; numerator and denominator; raw trace; failure severity; worst case; reviewer and limitations. The test-file links identify where to investigate, not a claim that every row already has a passing test. Proposed scenarios are not executed results.

### A. Source authenticity and chronology — 10 points

Research references: S1, S2. Existing test/document discovery: `test_signed_alert_feed.py,test_patreon_safe_ingest.py,test_discord_creator_ingestion.py`.

| ID | Check | Required result or measurement |
|---|---|---|
| A01 | Publisher identity | Reject forged sender/signature and unauthorized source; retain successful authorized-delivery controls. |
| A02 | Original versions | Persist post ID, version ID, raw bytes/hash, publication, receipt and observation times; absent original times remain unknown. |
| A03 | Edit lifecycle | Replay initial setup, later edit and deletion in availability order; never apply a later target to an earlier decision. |
| A04 | Delivery deduplication | Repeated transport retries and reordered JSON produce one semantic event across concurrent workers and restart. |
| A05 | Complete sampling | Inventory every accessible alert in the evaluation window, including deleted/failed/untraded cases; quantify inaccessible coverage. |
| A06 | Clock integrity | Reject impossible future receipt/availability; exercise UTC, Pacific daylight-saving transitions and clock skew. |
| A07 | Source interruptions | Authentication expiry, rate limits and stale polling produce visible source-health failures and bounded backoff. |
| A08 | Authority boundaries | Social commentary, Reddit, newsletter marketing and quoted messages cannot silently become execution authorization. |

### B. Alert understanding and instruction fidelity — 10 points

Research references: S1. Existing test/document discovery: `test_alert_instruction_integrity.py,test_creator_screenshot_conditions.py,test_labeled_intent_benchmark.py`.

| ID | Check | Required result or measurement |
|---|---|---|
| B01 | Intent taxonomy | Label setup, conditional entry, immediate entry, hold, trim, close, update, recap and no-trade separately; publish confusion matrix. |
| B02 | Contract identity | Match ticker, call/put, strike, expiry and multiplier exactly; ambiguity must not silently select another contract. |
| B03 | Price units | Separate underlying trigger/stop/target from option premium; validate decimal, percent, range and OCR formatting. |
| B04 | Negation and conditionals | No call, not yet, only if, did not hold and hypothetical examples cannot execute immediately. |
| B05 | Cross-modal conflicts | Caption/image or within-message disagreement requires review with conflicting fields retained. |
| B06 | Context binding | Associate reply/update with the correct original setup and held position; never inherit another ticker's stop. |
| B07 | Close and trim semantics | Apply full/partial close only to matched holdings; ambiguous unsupported trims are reviewed, never treated as new buys. |
| B08 | Useful acceptance | Retain valid CALL/PUT and full-exit positives alongside adversarial cases; report false rejection as well as false execution. |

### C. Trigger and market context — 9 points

Research references: S1, S9, S10. Existing test/document discovery: `test_screenshot_trigger_study.py,test_checklist_candle_faults.py,test_research_context.py`.

| ID | Check | Required result or measurement |
|---|---|---|
| C01 | Break and hold | Specify candle interval, completed-bar rule and hold duration before evaluation; a wick alone does not satisfy a close-based rule. |
| C02 | Reclaim and retest | Explicit state transitions distinguish touch, reclaim, retest and invalidation; cover bullish and bearish mirrors. |
| C03 | False breakout | A brief breach followed by reversal invalidates or waits according to frozen rules; no retroactive favorable entry. |
| C04 | Candle completeness | Missing, duplicate, zero-volume, out-of-order and still-open candles cannot create unearned confirmation. |
| C05 | Session boundaries | Verify exchange sessions, holidays and early closes; reject late entries according to configured policy. |
| C06 | Event awareness | Use primary release calendars and separately stored actual/forecast/vintage; convert only verified timezones to Pacific. |
| C07 | Setup expiration | Late alerts, target already reached, canceled setup and stale levels have explicit expiration outcomes. |
| C08 | Reason trace | Every entered or skipped alert records source/version, bars used, state transition, rule revision and reason code. |

### D. Option data and economic exposure — 9 points

Research references: S2, S5, S6. Existing test/document discovery: `test_contract_identity.py,test_options_edge_lab.py,test_tradier_contract_identity.py`.

| ID | Check | Required result or measurement |
|---|---|---|
| D01 | Authentic quotes | Authenticate point-in-time quote provenance and entitlement; indicative/derived quotes are not relabeled actual OPRA. |
| D02 | Availability clock | Quote event and availability timestamps must precede the decision; enforce and report freshness bounds. |
| D03 | Executable market | Check bid, ask, spread, size and timestamp; test zero bid, locked/crossed markets and missing depth explicitly. |
| D04 | Chain selection | Freeze expiry/strike selection and tie-breakers; preserve explicit creator contract without hindsight substitution. |
| D05 | Volatility exposure | Report IV/vega sensitivity and scenarios where correct stock direction still loses option value. |
| D06 | Decay and convexity | Stress time decay and near-expiry gamma over elapsed market/calendar time; theoretical prices stay labeled synthetic. |
| D07 | Corporate actions | Verify adjusted deliverables, splits, symbol changes and contract multipliers from authoritative metadata. |
| D08 | Expiration and exercise | Verify broker-specific exercise deadlines, auto-exercise handling, settlement and funding against actual account records. |

### E. Sizing and portfolio feasibility — 9 points

Research references: S3, S5. Existing test/document discovery: `test_risk_storage_reporting.py,test_small_account_month.py,test_readiness_and_options_simulation.py`.

| ID | Check | Required result or measurement |
|---|---|---|
| E01 | Explicit risk plan | Bind positive operator-approved premium, gross exposure and loss limits to intended account; no inferred budget increase. |
| E02 | Whole contracts | Floor quantity after premium multiplier and fees; one contract cannot become a fractional runner. |
| E03 | Concurrent reservations | Reserve capital transactionally across simultaneous alerts; open orders consume available budget. |
| E04 | Aggregate exposure | Enforce per-symbol, correlated exposure and portfolio caps, including pending orders and partial holdings. |
| E05 | Loss accounting | Define realized/unrealized and day-boundary treatment; fees and adverse fills enter loss controls. |
| E06 | Gap and ruin stress | Report depletion and drawdown across adverse gaps and losing streaks; stops do not guarantee capped fill loss. |
| E07 | Exit independence | Entry budget zero or entry halt cannot prevent a valid close; verified holdings cap sell quantity. |
| E08 | Shared capital | Use one chronological account for monthly/year results, with overlapping trades, cash constraints and no hidden replenishment. |

### F. Broker order lifecycle — 10 points

Research references: S1, S3. Existing test/document discovery: `test_broker_fault_matrix.py,test_gate_and_fill_lifecycle.py,test_option_entry_identity.py`.

| ID | Check | Required result or measurement |
|---|---|---|
| F01 | Pre-submit intent | Persist account/contract/side/quantity/client ID and strategy intent before network submission. |
| F02 | Lost acknowledgment | A timeout after acceptance triggers identity lookup; never assume rejection and submit a duplicate. |
| F03 | Partial fills | Apply cumulative quantity, average price and fee changes incrementally once; position equals confirmed net fills. |
| F04 | Cancel race | Cancellation request is not cancellation confirmation; handle fill-during-cancel and replacement without oversell. |
| F05 | Response identity | Reject wrong account/symbol/side/client ID/order ID or inconsistent quantity; retain evidence and halt affected entries. |
| F06 | Transport faults | Test 429, 5xx, disconnect, malformed payload and duplicate/out-of-order streaming events with bounded retry. |
| F07 | Outstanding orders | Track accepted, pending, canceled, expired, rejected and unknown states; no order vanishes from reconciliation. |
| F08 | Positive lifecycle | Demonstrate valid entry, partial fill, cancel remainder and full close through production code with confirmed accounting. |

### G. Exit protection and restart recovery — 10 points

Research references: S1, S3. Existing test/document discovery: `test_options_exit_latched_trigger.py,test_protection_recovery.py,test_equity_restart_recovery.py`.

| ID | Check | Required result or measurement |
|---|---|---|
| G01 | Latched exits | Once stop/target/creator close is requested, later underlying recovery cannot erase it. |
| G02 | Quote-outage reconciliation | Resolve already submitted orders during missing/zero quotes; no fresh replacement without a usable quote. |
| G03 | Durable trigger intent | Persist stop/target/close reason and lifecycle before submit; recover after death even before broker acknowledgment. |
| G04 | Restart adoption | Restore orders, remaining holdings and exit ownership exactly once; test kill points before/after every durable write. |
| G05 | Competing exits | Stop, target, creator close/trim and session deadline coordinate one bounded remaining quantity. |
| G06 | Unresolved exits | Missing liquidity/unknown outcomes remain explicit unresolved exposure; no invented flat status or booked favorable price. |
| G07 | Host/network recovery | Actual deployment restart, lost connection, stuck worker and broker recovery restore protection with measured detection times. |
| G08 | Independent halt | Entry halt works across processes while existing exits remain supervised; resume requires reconciled cause. |

### H. Cash, ledger and reporting integrity — 8 points

Research references: S1, S3. Existing test/document discovery: `test_cash_activity_reconciliation.py,test_reconciliation_identity.py,test_additional_monitoring_and_concurrency.py`.

| ID | Check | Required result or measurement |
|---|---|---|
| H01 | Cash conservation | Opening cash plus signed posted cash flows equals ending broker cash within currency rounding; classify every adjustment. |
| H02 | Inventory conservation | Per-contract bought less sold plus documented actions equals broker holdings; no orphaned positions. |
| H03 | Unique executions | Order/client/execution identifiers and replayed snapshots cannot duplicate rows, fills or fees across restart/concurrency. |
| H04 | Fee reconciliation | Use authentic posted fees for observed trading; synthetic fee assumptions remain separate and explicit. |
| H05 | Atomic storage | Crash during reservation/order/fill/position updates preserves invariants; verify actual database concurrency and durability modes. |
| H06 | Truthful P&L | Mark open positions at declared realizable convention; show realized, unrealized, fees and unresolved exposure separately. |
| H07 | Failed-run preservation | Retain failing seeds, databases, first drift time and traces; successful reruns do not erase original failures. |
| H08 | Account boundaries | Separate synthetic ledgers, paper/live accounts and strategy research; never aggregate unrelated budgets as one portfolio. |

### I. Simulation validity and reproducibility — 8 points

Research references: S2, S4, S7, S8. Existing test/document discovery: `test_options_risk_comparison.py,test_production_alert_replay.py,test_options_edge_lab.py`.

| ID | Check | Required result or measurement |
|---|---|---|
| I01 | Production path | Identify which parser/gate/sizer/broker/exit components are real and which are mocked; alternate lab logic is not production coverage. |
| I02 | Virtual clocks | Advance parser, candles, risk, calendar and persistence consistently; positive entries must remain possible throughout the full simulated year. |
| I03 | Deterministic comparisons | Hash input/code/config and key exogenous draws independently of trading decisions; paired policies see identical market paths. |
| I04 | Execution realism | Model bid/ask, latency, size, fees and failed/partial fills; record omissions such as queue position and market impact. |
| I05 | No look-ahead | Contract selection, context, edits and economics use only information available then; revised data cannot leak backward. |
| I06 | Unresolved accounting | Count every eligible alert and unresolved trade; unresolved P&L is null and blocks whole-run profitability comparisons. |
| I07 | Scenario diversity | Cover trend, chop, gap, IV crush, stale data, event shock, outages and affordability with positive controls. |
| I08 | Honest scope | Separate authentic observations, curated screenshots, synthetic mutations and broker-paper simulations; never report 648 trials as 648 real trades. |

### J. Profitability and statistical evidence — 9 points

Research references: S4, S7, S8. Existing test/document discovery: `test_spy_strategies.py,test_options_edge_lab.py; docs/SPY_STRATEGY_STUDY_20261014.md`.

| ID | Check | Required result or measurement |
|---|---|---|
| J01 | Net expectancy | Estimate net portfolio return and per-trade expectancy using complete authentic outcomes after executable costs. |
| J02 | Benchmark comparison | Compare fixed policy against cash and an appropriate exposure-matched simple baseline; show incremental return and risk. |
| J03 | Chronological validation | Freeze development/selection/evaluation boundaries; consumed 2025–October 2026 data cannot become fresh holdout again. |
| J04 | Multiple testing | Log all attempted variants and abandoned searches; adjust claimed significance for selection and correlated trials. |
| J05 | Uncertainty | Use session-clustered or justified block methods, report effective sample size and intervals; insufficient precision stays inconclusive. |
| J06 | Regime stability | Report months, losses, drawdown, recovery duration and event/regime dependence; disclose profit concentration in best days/trades. |
| J07 | Cost sensitivity | Re-evaluate fixed policy under wider spreads, extra latency, missed fills and empirically grounded fee stress without retuning. |
| J08 | Prospective confirmation | Measure a frozen candidate on genuinely subsequent complete data under the intended capital constraints; no income promise from one year. |

### K. Operations, security and observability — 4 points

Research references: S1, S3. Existing test/document discovery: `test_source_auth_and_notifications.py,test_manual_scripts_and_deployment.py,test_broker_transport_security.py`.

| ID | Check | Required result or measurement |
|---|---|---|
| K01 | Persistent service | Verify process manager, startup path, dependencies and real host availability; scratch runtime is not durable deployment. |
| K02 | Worker health | Measure loop progress, source freshness and reconciliation success separately from thread liveness. |
| K03 | Actionable notifications | Actual delivery/acknowledgment tests cover unknown orders, stale feed, lost protection and cash drift with escalation deadlines. |
| K04 | Credential controls | Verify private storage, least privilege, rotated exposed credentials and paper/live endpoint separation without logging secrets. |
| K05 | Audit trail | Correlate source, decision, order, fill, risk snapshot and incident IDs with synchronized timestamps. |
| K06 | Operator view | Dashboard exposes stale-data age, pending orders, remaining holdings, halts, cash drift and evidence limitations. |
| K07 | Change control | Bind evidence and approvals to exact code/config/account; changed execution logic invalidates prior operational certification. |
| K08 | Recovery practice | Demonstrate backup restore, deployment rollback and incident runbook on intended host without resetting accounts to hide drift. |

### L. Research discipline and improvement loop — 4 points

Research references: S1, S7, S8. Existing test/document discovery: `docs/OPTIONS_EDGE_LAB_20261015.md; docs/REAL_MONEY_READINESS.md`.

| ID | Check | Required result or measurement |
|---|---|---|
| L01 | Failure taxonomy | Classify each failure as source, interpretation, trigger, quote, sizing, order, recovery, accounting or strategy. |
| L02 | Causal attribution | Separate signal quality, execution drag and sizing effects using paired fixed-data comparisons; no best-seed promotion. |
| L03 | Preregistered hypotheses | State economic rationale, candidate bounds and rejection criteria before seeing evaluation outcomes. |
| L04 | Independent labels | Review actual-source labels independently of bot output; retain disagreements and ambiguous cases without forced ground truth. |
| L05 | Mutation quality | Mutations target a named invariant; group correlated cases and include useful positives instead of inflating test counts. |
| L06 | Repair proof | Preserve before-fix reproduction, minimal patch, after-fix regression and related integration coverage. |
| L07 | Readiness reviews | Review every mandatory gate, unresolved incident and evidence age before changing execution status. |
| L08 | Bounded learning | Research can propose candidates but cannot auto-enable orders, leverage, rule changes or larger allocations. |

## Metrics that prevent misleading grades

* Unsafe execution rate = unauthorized/wrong/invalid dispatched instructions divided by independently labeled non-executable instructions. Count wrong contract/side/account, duplicate entry and oversell individually; any observed critical violation blocks readiness until repaired and regression-verified.
* Valid-entry recall = correctly accepted eligible instructions divided by all independently labeled eligible instructions. Report valid-call, valid-put and valid-exit recall separately. An all-rejection bot has zero recall and cannot pass by being inactive.
* Field accuracy = exact matches / labeled fields for contract, trigger, stop, target and action, with missing and ambiguous fields visible. Do not average away a catastrophic contract mismatch.
* Trigger error = premature entries and missed valid confirmations, each divided by its own labeled opportunities. Freeze what hold/retest means before labeling.
* Delivery coverage = preserved alerts / authorized source inventory. Report source access gaps separately; unknown total inventory means completeness is unknown.
* Execution drag = modeled/observed fill difference from the decision-time executable quote, plus fees, reported by spread, delay, size, expiry and market regime. A paper fill is still simulated execution.
* Reconciliation = cash residual, per-contract inventory residual, duplicate IDs, repeated fees and unresolved-order counts. Any unexplained drift blocks an otherwise profitable run. Use ledger currency precision and explicit posted adjustments, not an arbitrary large tolerance.
* Recovery = time without confirmed exit ownership, unknown-order duration, detection/reconciliation latency and restored invariants at each crash boundary. Project deadlines must be configured before the experiment.
* Economics = complete-account net P&L, daily marked equity, monthly returns, losing months, drawdown, time to recovery, capital utilization and worst outcomes. Report both realized and open risk; terminal cash alone is not drawdown.
* Statistical strength = confidence intervals, dependence/effective sample size, sample coverage, search breadth and prospective period. With zero errors in n independent cases, an approximate one-sided 95% upper error bound is 3/n; correlated mutations are not independent cases. No universal trade count or one profitable year guarantees an edge.

For each metric publish actual-source, screenshot-derived, synthetic and authenticated broker-paper panels separately. Use independent labels and disclose disagreements. Quote authenticity and complete sampling are prerequisites to economic inference. A declared source string or successful socket connection is not provenance verification.

## Intensive variation plan

Freeze seed lists and run pairwise factor coverage plus explicit critical combinations, rather than claiming to exhaust an infinite Cartesian space. Record unique base alerts separately from mutations and parameter trials.

| Factor | Planned variations |
|---|---|
| Instructions | CALL/PUT; long equity; setup/entry/hold/close/trim/update/recap; negated and quoted instructions |
| Input quality | Text, image/OCR, decimals, ranges, missing fields, conflicting caption, multi-ticker, unusual Unicode |
| Time | Receipt delays 0/5/15/30/60/120 seconds; late receipt; edits; session edges; DST; virtual month/year transitions |
| Trigger | Valid hold, wick-only, retest, immediate reversal, target already reached, stale setup, canceled alert |
| Options | 1–7/8–21/22–45 days; exact binding; wide spreads; zero bid; scarce depth; adjusted contract; unaffordable premium |
| Market path | Trend/chop/gap; favorable stock with IV crush; event shock; sideways decay; volatility expansion |
| Broker | Accept-then-timeout, not-found lookup, lookup outage, repeated partial, fill-during-cancel, reject, unknown, malformed identity |
| Persistence | Kill before/after intent write, POST, fill booking and callback; restart; competing exit worker; duplicate delivery |
| Portfolio | Shared $1,000 research account where specified; baseline user policy; separately labeled allocation stress; simultaneous correlated alerts |
| Operations | Source stall, dead worker, disconnected broker, full disk, database contention, notification failure, recovery supervisor |

Critical combinations include a latched stop plus lost acknowledgment plus quote outage; partial fill plus cancellation race plus restart; duplicate source update plus concurrent entry; and session deadline plus missing liquidity. Include matching valid controls in every factor family. These are planned tests until an execution receipt is attached.

## Profitability study protocol

1. Collect authorized complete original alerts and point-in-time underlying/option quotes, with availability timestamps and a coverage manifest. Screenshots can seed interpretation cases but cannot supply invented timestamps or fills.
2. Freeze one economic hypothesis, code, contract selection, entry/exit definitions, cost/latency model, risk limits and data splits. Keep the consumed 2025–October 2026 SPY evaluation marked consumed.
3. Verify shared-capital accounting before comparing any return. Preserve unresolved positions and cash drift. Test affordability on actual available contracts, not universally cheap invented premiums.
4. Use development data for mechanics and selection; record every candidate. Use appropriate dependence-aware inference and multiple-testing correction before claiming a selected edge. Failed candidates remain in the record.
5. Evaluate the frozen candidate on genuinely subsequent observations. Predefine economically meaningful improvement over baseline, acceptable drawdown/loss limits, and precision requirements; insufficient data yields inconclusive, not pass.
6. Report sensitivity to cost/delay/missed trades, best-trade removal, event exposure, losing months and concentration. If net improvement disappears under plausible costs, reject or continue research without promotion.
7. Apply only supported engineering lessons automatically. Any execution or allocation promotion remains a separate authorized decision after the readiness evidence requirements are satisfied.

## Prioritized repair and evidence backlog

| Priority | Work item | Completion evidence |
|---|---|---|
| P0 | Isolate replay-ledger duplicate/extra-fill anomaly | Saved failing seed/DB; first divergence; root-cause explanation; before/after concurrent and sequential tests; cash/inventory invariants |
| P0 | Durable exit trigger intent and restart ownership | Crash matrix around intent/submit/fill; one exit owner; no duplicate/oversold quantity; preserved stop reason |
| P0 | Bind creator close/trim to actual held positions | Parser-to-orchestrator integration for exact IDs, partials, late/duplicate edits and conflicting messages; valid positives |
| P1 | Complete authorized source corpus and provenance | Export manifest, original/version IDs, publication/receipt clocks, inaccessible-source inventory and reviewed labels |
| P1 | Authenticate quotes and portfolio replay | Point-in-time data receipts, spread/depth/latency handling, shared-capital conservation and explicit unresolved outcomes |
| P1 | Observe intended host and escalation | Revision/account-bound startup, process death, source/broker outage, notification and recovery receipts |
| P2 | Prospective economic evaluation | Frozen protocol, subsequent complete data, uncertainty, baseline/cost comparison and documented accept/reject decision |

This backlog is a diagnostic order, not a claim that all implementation work is complete. The present request produces the rubric and initial evidence map. It does not certify all 96 checks or enable trading.

## Primary-source research register

All entries checked October 16, 2026. Source guidance informs the requirements; the weights, scoring anchors and project gates are our proposed assessment design. No external source endorses this bot or guarantees the scoring thresholds.

| ID | Source | Use and reading scope |
|---|---|---|
| S1 | [FINRA Regulatory Notice 15-09](https://www.finra.org/rules-guidance/notices/15-09) | March 26, 2015 guidance: testing, validation, change records and controls. Used as engineering guidance; this rubric is not a legal applicability determination. |
| S2 | [Alpaca historical option data](https://docs.alpaca.markets/us/docs/historical-option-data) | Historical coverage begins February 2024; subscribed OPRA BBO differs from derived indicative data. |
| S3 | [Alpaca order lifecycle](https://docs.alpaca.markets/us/docs/orders-at-alpaca) | Client IDs, status lookup and order state handling inform reconciliation checks. |
| S4 | [Alpaca paper trading](https://docs.alpaca.markets/us/docs/paper-trading) | Paper fills omit several real execution effects; displayed liquidity is not enforced in the same way as an exchange. Add independent stress assumptions. |
| S5 | [OCC options disclosure landing page](https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document) | Current disclosure availability and settlement-version information; full contract/broker-specific exercise review remains required. Landing page checked, not a complete ODD review. |
| S6 | [OIC implied volatility](https://www.optionseducation.org/videolibrary/implied-volatility-increase-or-decrease-advanced) | Options require volatility exposure analysis alongside directional analysis. Video page checked; no claim to have watched the full video. |
| S7 | [Bailey and López de Prado: Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) | Multiple testing and non-normality can inflate apparent performance; disclose search history and use appropriate corrections. |
| S8 | [Bailey et al.: Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) | Selecting a historical winner can produce poor out-of-sample performance; evaluate the selection process as well as the winning curve. |
| S9 | [BLS 2026 release schedule](https://www.bls.gov/schedule/2026/home.htm) | Primary calendar endpoint for release verification; exact event/timezone must be rechecked before use. |
| S10 | [BEA release schedule](https://www.bea.gov/news/schedule) | Primary GDP/PCE calendar endpoint; preserve observation/version and verified timezone rather than assume it. |
