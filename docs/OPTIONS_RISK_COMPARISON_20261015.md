# Aggressive options research and replay-clock correction

The user's benchmark is approximately $178.60 net in a month on $1,000. It is a comparison target, not expected income or an execution requirement.

## Confirmed evaluation defect

The production-path replay advanced execution, market calendar, ledger, risk and monitor clocks but left `vision.extract_signal.date` and `datetime` on the computer's real date. Its 45-day expiry validation rejected later virtual-year contracts. Earlier annual results and scenario-order monthly results are superseded as performance evaluations. They still exercised individual execution mechanisms, but do not establish annual or monthly earning ability.

The replay now patches the parser's clock alongside the other production components. A regression requires positive entries after December 15 in a 100-day run; a whole-year all-rejection run cannot pass. Production expiry safeguards remain unchanged.

## Paired experiment

Run `python -m lavish_core.validation.options_risk_comparison --output NEW_DIRECTORY`.

Twelve 365-day runs start with $1,000: premium allocations of 10%, 25%, 50% and 100%, each under baseline, weak follow-through and gap-loss profiles. This allocation is premium committed, not guaranteed maximum account loss at a stop. Daily quote draws are keyed to seed plus calendar index, independent of whether the bot enters. This prevents different policies from receiving different random outcomes simply because a prior entry was blocked.

Baseline invented exit quotes are 0.12/0.14/0.16 against a 0.10 entry. Weak follow-through uses 0.10/0.11/0.12. Gap-loss changes the premium-stop quote from 0.04 to 0.01. All other production risk gates remain enabled. No halt is cleared to obtain a profit. Report monthly net, losing months, benchmark hits, account depletion, session-end cash drawdown, halt state, cash and inventory reconciliation. Cash drawdown excludes intraday excursions.

These scenarios have no empirically calibrated frequencies and cannot establish a strategy edge, probability of ruin, confidence interval or expected return. Cheap 0.10 contracts do not represent the affordability or liquidity of an authentic option chain. There is one synthetic year per profile, not twelve independent years of market evidence. Do not promote the best allocation to production.

## Completed run and blockers

All twelve corrected annual runs completed (4,380 daily alert records). Ten reconciled throughout. Exclude the 10% gap-loss and 25% baseline runs: they accumulated cash/inventory discrepancies. The first 10% gap-loss discrepancy appears March 5, 2027, with 159.75 cash error and an orphaned local position for SPY270303C00770000. Its saved ledger contains duplicate local order rows for one ID and an extra buy fill. Root cause is not established; this is a local replay/ledger integrity blocker, not evidence of real broker activity. Do not erase the evidence, reset an account or report these runs as profitable.

Weak-follow-through outcomes from $1,000: 10% allocation ends 523.50; 25% ends 170.00; 50% ends 52.80; 100% ends 10.90. All reconcile. Their session-end cash drawdowns are 47.93%, 83.21%, 94.7437% and 98.91%, respectively. All have zero months reaching +178.60 net. Positive baseline outcomes are strongly driven by the deliberately favorable invented event mix; they cannot select an allocation or validate monthly profitability.

The corrected code passed 29 focused replay tests and 3,154 full offline validation tests. Reporting was then hardened to flag reconciliation failures explicitly and distinguish disabled halt tuples from active halts; targeted reporting tests cover both. Full results and SHA-256-linked compressed traces are in `docs/evidence/options-risk-comparison-20261015.json` and `options-risk-comparison-traces-20261015.json.gz`.

## What I would build toward

Use a focused, explicit creator-trigger strategy in liquid contracts, with contract identity, underlying confirmation and option quote confirmation separated. Preserve original alert versions and receipt times. Compare fills at executable bid/ask with latency, whole-contract sizing, fees, liquidity and adverse gaps. Record actual reason codes for skips. Complete held-position creator close/trim coordination and restart recovery before autonomous operation. An 80/20 runner requires an integer-feasible position; one contract cannot be split.

For strategy research, compare a preregistered moderate allocation hypothesis against the existing policy and more aggressive stress policies. Require authentic point-in-time option quotes, forward paper observations and untouched evaluation data before choosing risk or claiming monthly profitability. The consumed SPY holdout is not fresh data. Adaptive learning may propose offline candidates, but cannot silently rewrite trading rules or raise allocation.

Primary references checked October 15, 2026:

* https://docs.alpaca.markets/us/docs/historical-option-data — historical options since February 2024; indicative quotes are derivatives, not actual OPRA BBO. OPRA access requires subscription.
* https://www.optionseducation.org/news — option time decay, volatility changes and spread costs can overwhelm a favorable underlying move.

No broker orders, account resets, live-mode activation or production allocation changes are part of this experiment.

## Sequential annual rerun follow-up

On October 15, both previously failing configurations were rerun sequentially in new databases with the same seed, days, starting capital, allocation and stress profile. The 10% gap-loss run and 25% baseline run each had zero cash-error sessions and zero inventory-mismatch sessions, with the same final synthetic broker balances as their original failed runs. This establishes intermittent behavior, not a root cause or a verified fix. The original failed runs remain excluded and their evidence is preserved. Do not substitute the successful reruns to hide the failures.

The focused fill lifecycle, monitoring/concurrency and cash reconciliation suite passed 101 tests in 4.62 seconds. It ran briefly during the second replay, so this is not a fully isolated host experiment and cannot prove parallel execution caused the original discrepancy. No production change is justified by these observations alone. The unresolved issue concerns local synthetic replay integrity; it does not reopen the already resolved October 14 broker cash concern.

The two full rerun traces (730 daily alert records), summary and trace hash are retained in `docs/evidence/options-ledger-rerun-20261015.json` and `options-ledger-rerun-traces-20261015.json.gz`. No market-profitability claim, strategy promotion, order submission or allocation change follows from these reruns.
