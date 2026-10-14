# Exit worker supervision — October 13, 2026

## Confirmed defect

Option, native-bracket and active-equity async entrypoints started daemon threads directly. A monitor raising before its exit callback, returning without invoking that callback, or failing inside the callback could leave the symbol in the shared watched registry. Periodic reconciliation then skipped that position because it appeared protected. A process-level worker watchdog did not establish that an individual exit thread was still running.

Fifteen regression cases failed before the patch across all three monitor entrypoints. These cover exceptions, invalid returns, abnormal outcomes, callback exceptions and successful termination cleanup.

## Repair

A shared exit-worker supervisor wraps all three entrypoints. It persists an entry halt for uncertain or failed outcomes, guarantees watched-marker cleanup when the thread terminates, and reports an outcome at most once. The callback is marked as reported before invocation: a callback that books a fill and then raises is never replayed. Failure reporting contains only the exception type, not a potentially sensitive broker response body. A successful confirmed exit does not create a halt.

The supervisor also handles thread-start failures. If journal access itself fails, it logs that the halt could not be persisted and still clears the watched marker; disk/storage recovery remains an operational requirement.

## Evidence

- Before repair: 15 failed cases and unhandled-thread warnings.
- Focused supervision, active exit, equity restart and protection recovery tests: 38 passed.
- Complete repository suite: 2,747 passed, 2 deployment-host checks skipped.
- Bandit across `lavish_core` and `ml`: zero medium/high findings. Dependency and whitespace checks passed.
- Local evidence: `private/exit-supervision-before.log`, `private/exit-supervision-focused.log`, `private/exit-supervision-full.log`, `private/exit-supervision-full.xml`, `private/exit-supervision-bandit.json`.

This repair builds on draft PR #5, whose two GitHub workflows were confirmed successful. It does not change source parsing, strategy parameters, paper budgets, trade mode or account balances. No actual broker orders were placed. Authorized creator-feed authentication, explicit paper budgets, the original $0.05 cash difference and persistent host readiness remain unverified.
