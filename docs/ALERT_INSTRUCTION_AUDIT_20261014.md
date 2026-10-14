# Alert instruction integrity and integrated gate verification

Reviewed October 13, 2026 Pacific time (October 14 UTC). Candidate retains all
repairs through PR #6, commit `89d5752`; it does not represent a deployed bot.

## Repairs

| Confirmed failure | Candidate behavior |
| --- | --- |
| Equity trims and quantity-limited sells became unsized full liquidation | Reject unsupported partial instructions; clear full exits remain supported |
| SHORT could sell an existing long, and a cover could become a new long | Reject short/cover text and remove structured SHORT-to-SELL coercion |
| Multi-ticker alerts selected one symbol and executed it | Preserve extraction but flag multiple exact known symbols for review; repeated mentions of one ticker remain valid |
| Caption/image target, stop and entry prices disagreed without rejection | Check price fields alongside instrument identity; preserve conflicts from either input |
| Negated or future/conditional instructions executed immediately | Reject tested negations and deferred instructions; no scheduler is inferred |
| Repeated contradictory targets/stops or both CALL and PUT chose one value | Reject explicit contradictory fields |
| Explicit zero, negative, nonfinite or unsupported numeric levels disappeared | Mark those fields invalid instead of falling back to default exits |
| Equity source levels were replaced by defaults; wrong-direction option levels reached submission | Check source levels against the fresh reference/underlying quote and CALL/PUT direction before submission; invalid or already-crossed levels reject entry |
| Setup runbook recommended a disabled forced-live script and only the old 261-test subset | Document the pinned full-suite validation and offline signed-alert checks |

The parser is deterministic and conservative. Mixed commentary, comparisons,
multiple instruments, ambiguous quantities, and supported-looking words inside
longer prose can now require review. This is not arbitrary-language trading
support. Creator-directed partial exits, option closes, and corrections are
not implemented by guessing their contract, quantity or ownership.

## Integrated verification

Twelve added scenarios exercise the signed HTTP handler, durable inbox, parser,
real candle-confirmation logic, gated entry callback, sizing, broker HTTP
adapters, fills and target/stop exits together. They cover equities and calls
with candle confirmation, invalidation and timeout. Accepted entries must wait
for a confirming candle; failed gates submit nothing. Duplicate delivery uses
the original creation timestamp even when gate polling advances the clock.

Market bars, broker prices and fills are synthetic, fees are zero, the clock is
virtual, and gate worker scheduling is inline for determinism. Existing tests
separately exercise actual worker scheduling and post-wait safety checks. No
real broker order, creator-source connection, or production latency claim is
made by these tests.

Final local verification:

- Entire suite: **2,817 passed, 2 deployment-host checks skipped**.
- Focused instruction/gate/fill suite: 175 passed.
- New instruction integrity module: 59 cases; integrated candle cases: 12.
- Medium/high Bandit findings across `lavish_core` and `ml`: **zero**.
- Measured coverage including branches: repository 34.25%; alert handler
  97.04%; trade handler 81.41%; extraction 80.64%. This does not certify every
  legacy file or all possible input interpretations.

Reproduce with the pinned `requirements-validation.txt` environment:

```bash
TRADE_MODE=dry DISCORD_WEBHOOK_URL='' python -m pytest -q
python -m bandit -r lavish_core ml -ll
python -m pip check
git diff --check
```

## Remaining A-grade blockers

1. Confirm authorized creator delivery and its exact message/quantity/price
   conventions using actual source access. Outgoing Discord notifications are
   not an incoming creator feed.
2. Establish explicit actual paper budgets and instrument ownership. The $250
   fixture is a simulated cap, not approval of a real account allocation.
3. Reconcile the previously reported $0.05 cash difference against its original
   broker activities and cycle journal; this session did not retrieve or alter
   that account or manufacture a fee.
4. Observe account-bound paper partial fills, rejects, gaps, cancellations and
   restart reconciliation on the selected broker.
5. Verify deployed notification delivery, watchdog/crash recovery, clock and
   storage behavior; host checks remain skipped here.
6. Specify and implement source-directed partial closes, option closes and
   edits/cancellations with durable identity and coordinated existing exits
   before claiming that every creator instruction is followed.
7. Expand coverage or explicitly scope/retire the extensive legacy paths before
   grading the whole repository as verified.

All fixes remain a review candidate. Trading mode, real account state, entry
halts, strategy percentages and configured budgets were not reset or bypassed.
An overall A or readiness for autonomous trading is not established.
