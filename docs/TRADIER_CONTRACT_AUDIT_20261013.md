# Tradier option identity hardening — October 13, 2026

## Confirmed defect and fix

The alternate Tradier options adapter shared the zero-tolerance defect previously repaired for Alpaca: a request for a specific strike could select a different strike with the default tolerance of zero. Returned OCC symbols were trusted without verifying ticker, expiry, call/put or strike, and a missing symbol was fabricated. Malformed chain rows could also raise instead of returning no contract.

Thirteen new failing cases reproduced these defects before the patch. The resolver now validates finite positive strikes and nonnegative tolerances, requires an exact matching returned OCC symbol, checks supplied underlying/expiry metadata, and filters malformed rows. Zero tolerance requires the requested strike; nearest-strike selection requires explicit tolerance. Valid singleton chain responses and valid contracts mixed with malformed rows remain supported. An existing nearest-strike test now explicitly specifies its intended $1 tolerance.

## Verification

- Validation suite: 2,366 passed. Two additional singleton/mixed-chain cases were added afterward and passed in the complete 16-case new regression module.
- Existing repository tests: 261 passed, 2 skipped. The skipped systemd socket checks require the deployment host.
- Focused contract identity, broker-fault and cash-reconciliation set: 226 passed before the two additional cases.
- Bandit: zero medium/high findings; dependency check passed; diff whitespace check passed.
- Offline validation coverage includes parser/ingestion, sizing, partial fills, ambiguous/duplicate orders, cash reconciliation, exit recovery/restart, concurrency and security. No actual broker orders were placed.

## Repository status and remaining readiness gaps

At inspection, the primary repair branch remained at `4c93998251072d8f4aa5de793eaf333258efd8c2`. Draft PR #1 (`0828f625`) and stacked draft PR #2 (`a961dc18`) both had successful GitHub CI. This change builds on PR #2; those pending fixes were preserved rather than duplicated.

The available configuration remains paper mode, with both default entry budgets zero and signed-feed execution false. Patreon credentials/campaign and trusted Discord creator authentication are absent. The earlier $0.05 cash difference and persistent host/watchdog readiness remain unverified. No credentials, account reset, live-mode changes or halt bypasses were used. These tests establish mocked contract handling, not real exchange execution, source delivery latency or profitability.

Local evidence retained: `private/tradier-contract-before.log`, `private/tradier-contract-full.log`, `private/tradier-contract-full.xml`, `private/tradier-contract-new.log`, `private/tradier-contract-focused.log`, `private/tradier-existing-tests.log` and `private/tradier-contract-bandit.json`.
