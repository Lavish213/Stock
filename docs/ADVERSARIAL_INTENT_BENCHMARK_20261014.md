# Adversarial interpretation benchmark and grade

Scope: text interpretation and routing in the existing creator-alert pipeline.
No actual creator messages, account credentials, network/broker orders or live
deployment were used. The draft candidate retains earlier repairs in PR #7.

## Independent labels and measurement

The benchmark supplies the expected action and exact ticker/contract fields
before invoking the parser. Supported alerts rejected by the bot count as
incorrect. Unsupported or unsafe alerts dispatched by the bot also count as
incorrect. These are routing decisions, not observed real broker trades.

Acceptance requires less than 3% incorrect in **each** category, not merely a
weighted aggregate, and **zero unsafe dispatches**. A regression explicitly
checks that rejecting every alert cannot pass. Malformed identities, hidden
negations and ambiguous contracts are rejected, not silently corrected.

The corpora are synthetic and template-generated. Repeated surface variants
are not independent real-world samples; no real-creator error-rate estimate or
statistical confidence claim is justified by the counts below.

## Repair iterations

| Set | Evaluations | Before repair | After repair |
| --- | ---: | ---: | ---: |
| Development text set | 5,832 | 1,512 incorrect (25.9259%) | 0 (0%) |
| New challenge templates | 3,096 | 936 incorrect (30.2326%) | 0 (0%) |
| Randomized fields, seed 982451653 | 6,000 | 569 incorrect (9.4833%) | 0 (0%) |
| New randomized seed 32452843 | 6,000 | First evaluated after repairs | 0 (0%) |

The first three sets were used to discover and repair failures, so their final
scores are regression scores, **not held-out validation**. The final seed was
evaluated without a subsequent parser change. It changes field values and
compositions within the synthetic grammar; it is not an independent source
distribution. Final evaluation rounds total 20,928 case evaluations, not a
claim of 20,928 unique independently sampled creator alerts.

## Repaired defects

- Avoid/refrain/waiting/no-longer instructions could become BUY or SELL.
- Numeric delays, explicit time/price conditions, planning/considering, and
  further tested negations could execute immediately.
- Percent and fractional sells could become full-position sells.
- Conflicting strikes or labeled expirations could silently select one.
- The previous broad CALL+PUT conflict check rejected valid single-contract
  cards containing both unpaired UI toggle labels. Check the actual paired
  strike/side fields instead; contradictory explicit contracts still reject.
- Invisible Unicode format characters could split a negation while leaving
  BUY recognizable. Those inputs now require review.
- Plain-text ticker typos could be fuzzy-corrected to another tradable ticker.
  Text-only execution now requires a 100-score identity match. OCR extraction
  remains a distinct path and is not certified by this text benchmark.

## Verification and reruns

Use the pinned validation environment and keep execution dry:

```bash
TRADE_MODE=dry DISCORD_WEBHOOK_URL='' python -m pytest -q
TRADE_MODE=dry DISCORD_WEBHOOK_URL='' python -m lavish_core.validation.intent_benchmark --output validation/development.json
TRADE_MODE=dry DISCORD_WEBHOOK_URL='' python -m lavish_core.validation.intent_benchmark --challenge --output validation/challenge.json
TRADE_MODE=dry DISCORD_WEBHOOK_URL='' python -m lavish_core.validation.intent_benchmark --randomized --seed 32452843 --output validation/randomized.json
```

Each CLI command requires a new output path and reports misses and unsafe
dispatches separately. Regression tests also run these corpora under the
external-I/O-blocking validation harness.

Final local verification: **2,821 passed, 2 deployment-host checks skipped**;
zero medium/high Bandit findings, no broken dependencies, Python compilation
and whitespace checks passed. Measured branch-inclusive repository coverage
is 34.88%, including extensive legacy paths; this is not full-repository
verification. Local rerun evidence is in `private/adversarial-final.xml`,
`private/adversarial-coverage.json` and `private/adversarial-bandit.json` (not
published as account/deployment evidence).

## Brutal grade and remaining work

The **less-than-3% target is met for these defined synthetic routing tests**.
The overall bot remains **B, not an operational A**. Source interpretation is
substantially hardened but arbitrary English, images, edits/cancellations,
partial creator exits and broker ownership coordination remain outside this
metric. Actual creator source authentication, explicit real paper budgets,
account-bound fill/recovery observations, the original $0.05 cash difference,
notification/watchdog evidence and legacy coverage remain unresolved.

No mode, budget, strategy percentage, cash balance or entry halt was changed
to achieve passing results. No trading was activated. The PR remains draft.
