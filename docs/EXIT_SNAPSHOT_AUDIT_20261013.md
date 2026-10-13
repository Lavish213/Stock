# Exit snapshot validation repair — October 13, 2026

Parent: `4c93998251072d8f4aa5de793eaf333258efd8c2`.

## Reproduced defect

An exit response marked `filled` with a cumulative quantity smaller than the requested close reached `_record_snapshot()` before completeness validation. A two-share close claiming one filled share reduced the durable position from two to one, then reported `fill_unverified`. A later inconsistent terminal response after an already confirmed partial fill could similarly book another unverified sell. Some invalid snapshots also failed to halt unrelated new entries.

## Repair

- Validate the terminal cumulative quantity against the durable order request before booking any new fill delta.
- Quarantine invalid quantities/prices and persist the entry halt.
- Retain previously confirmed partial fills and the unresolved closing order; never resubmit solely because validation failed.
- Preserve the existing entry/exit rules, broker endpoint guards, and paper/live readiness settings.

## Evidence

Four regressions failed before the patch: zero, incomplete, excessive terminal quantities, and an inconsistent terminal response after a confirmed partial fill. The final test receipt is `exit-snapshot-verified.xml`; security scan receipt is `exit-snapshot-bandit.json` in the private validation workspace. No broker connection or order submission was used.

Final suite: 2,543 passed, 2 environment-specific systemd socket skips. Focused recovery, broker-fault, cash-accounting and paper-cycle suite: 287 passed. Dependency check passed; medium/high Bandit findings: zero.

The repair is proposed on `codex/exit-snapshot-validation-20261013`, targeting `codex/trading-safety-repairs-20261012`.

The existing $0.05 cash discrepancy, real Patreon authentication, explicit paper budgets and persistent-host readiness remain unverified. This repair does not clear their halts or authorize trading.
