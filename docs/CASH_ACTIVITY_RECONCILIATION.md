# Cash activity reconciliation

The October 13 paper cycle filled one option at $1.49 and closed at $1.48.
The multiplier is 100, so fill-based P&L is -$1.00. Broker cash changed by
-$1.05. Order and fill records contain no fee field, and the current account
reports zero accrued fees, zero pending regulatory fees and zero intraday
adjustments. The fee activity endpoint and complete activity window have no
matching fee. The remaining -$0.05 is therefore **pending broker evidence**.

Alpaca documents intraday fee accrual and later fee posting, and activity date
filters refer to creation date rather than settlement date. This can explain
delayed records but does not prove that this particular difference is a fee.
Sources: https://docs.alpaca.markets/us/docs/regulatory-fees and
https://docs.alpaca.markets/us/reference/getaccountactivitiesbyactivitytype-1.

## Repairs

- Cash arithmetic uses finite, bounded Decimal values, preserving the exact
  -$0.05 diagnostic without binary floating-point noise.
- A bounded, cursor-paginated activity read refuses malformed, cycling and
  truncated snapshots. It does not follow arbitrary links or redirects.
- Only executed, matching-day fee debits are eligible. Foreign accounts,
  unrelated fills, unrelated fees, other cash movements and uncertain fee
  attribution require review.
- Verified fee activities are journaled by immutable broker identity. Replays
  cannot duplicate the debit or silently replace changed evidence. Fees already
  recorded on fills are not blindly counted again as cash activities.
- Reporting shows fill-based P&L and posted account-level fees separately;
  total net P&L includes verified cash fees. Fee attribution to individual trades
  or sources is not guessed.
- The paper cycle distinguishes completed execution from pending cash
  reconciliation. Pending evidence is not a passing readiness receipt.
- Completed isolated paper journals are explicitly checkpointed before closing.
  The original journal and recovery log were preserved; a copy of the complete
  database was independently compared with both broker fills and the flat
  account. The recovered copy supports a successful read-only recheck.
- CI's offline network guard permits local Unix sockets while blocking external
  network calls. Watchdog test listeners have bounded receives and daemon
  threads, so a failed notification cannot hang the CI process.

## Recheck without another trade

```bash
python -m lavish_core.validation.cash_audit --env-file .env \
  --receipt validation/repair/paper-open-cycle-0713.json \
  --db validation/repair/paper-open-cycle-recovered.duckdb \
  --output validation/repair/new-cash-review.json
```

Use a new output filename. This submits zero orders, verifies the same paper
account, resolves both existing broker order identities, requires a flat account
and no open orders, and compares posted activities with actual cash. It never
clears an entry halt automatically. The original execution receipt remains
immutable; a recheck is separate evidence.

## Validation

2,428 local tests passed; two restricted Unix-socket cases skipped. All 330
Python files compiled. Bandit found zero high, zero medium and 83 low findings.
The new tests cover delayed fee evidence, duplicates, conflicts, malformed
numbers, unrelated activities, fee double counting, reporting and reopening a
completed paper journal from a fresh process. No additional broker orders were
submitted during reconciliation.

## Alert feed

The signed local receiver and Patreon reader exist and are tested, but actual
publisher access is unconfigured. Connecting a paid feed requires the intended
creator's campaign and authorized read access or an authenticated publisher
sending signed events. Brokerage access does not establish alert-source access.
Automatic paper budgets remain zero and entries halted. No persistent service
has been deployed. Publisher-directed options exits and conditional triggers
remain manual review cases.
