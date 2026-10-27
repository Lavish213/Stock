# Postfix confirmation prerequisites — October 27, 2026

Base: PR #38, `a49828e0fdd2feb7d24578c88f7eee424f98a388`.
Default branch was independently checked at `69f50f5aaa644b1fec96244576b4e70a61a58063`;
creator-research branch remained `d134977adfd51a9ec3260313d69549ca8b8c763c`.
This repair is stacked on the unmerged repair candidate, not the default.

## Reproduction and repair

Four synthetic complete-contract mutations used explicit prerequisites with
the duration following the hold: "Entry requires a hold for 5 minutes",
"Entry requires holding for five minutes", "Only enter after price holds
over 342.50 for 15 minutes", and "Wait for price to hold over 342.50 for
one hour". All four failed combined classification/no-dispatch assertions:
three dispatched immediately; the only-enter case was already blocked by
another guard but incorrectly tagged as an instruction candidate.

Recognize those literal postfix-duration prerequisites before instruction
classification. Require explicit entry prerequisite language; an ordinary
"Plan to hold for five minutes" remains distinct. Do not infer whether a
hold has occurred, invent a candle policy, or automatically promote a setup.

## Evidence and honest scope

- Before: 3/4 unsafe immediate dispatches and 4/4 combined assertion failures
  on this deliberately targeted synthetic set, not a representative error rate.
- After: all seven new checks pass: four prerequisite cases, one parsed-image
  instruction case and two unconditional-entry positive controls.
- Combined focused confirmation/context tests: 73 passed.
- Changed-source Bandit medium/high findings: zero; `git diff --check` passed.
- Full suite and new-branch CI are pending at initial publication; prior PR
  #38 CI success does not certify this change.

No new authenticated creator samples or point-in-time fill evidence were
added. These cases are synthetic mutations, not historical creator posts or
profitability evidence. Existing SPY study conclusions are unchanged; no
retuning of the consumed evaluation period occurred. Conditional promotion,
creator trims/closes, position-epoch ownership, Nautilus differential parity,
persistent authenticated delivery, approved paper limits and intended-host
recovery/notification evidence remain incomplete. No broker orders, live
activation, deployment, account reset, halt bypass or risk/allocation changes.
