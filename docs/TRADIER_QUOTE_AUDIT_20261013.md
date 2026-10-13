# Tradier option quote hardening — October 13, 2026

## Confirmed defect

The Tradier option quote adapter converted broker bid/ask fields with `float()` but did not require finite, correctly signed, non-crossed values. Five unsafe cases reproduced before the fix: negative bid, bid above ask, NaN bid, infinite ask and boolean bid. Those values could contaminate option entry sizing or an exit limit calculation.

The adapter now uses the shared finite-number validator, requires a nonnegative bid and positive ask, and rejects an ask below the bid. A legitimate zero bid is preserved because it is meaningful for an illiquid or nearly worthless option. A missing quote field continues to fail closed.

## Verification

- New Tradier identity and quote regression module: 23 cases pass.
- Focused contract, broker-fault and cash-reconciliation set: 240 passed.
- Existing repository suite: 261 passed, 2 skipped. The skips require deployment-host systemd socket support.
- Bandit: zero medium/high findings. Dependency and whitespace checks pass.
- No broker endpoint was contacted and no order was placed.

Draft PR #3 was confirmed by two successful GitHub CI runs before this patch. This change is stacked on PR #3 so the prior exit-snapshot and Alpaca/Tradier contract-identity repairs are preserved.

## Readiness status

Automatic entries remain blocked. The available configuration has zero equity and option budgets, signed-feed execution disabled, and no authenticated Patreon campaign or trusted Discord creator identity. The earlier $0.05 cash difference and persistent deployment host/watchdog readiness remain unresolved. Offline success is not evidence of profitability or deployed execution timing.

Local evidence retained in `private/tradier-quote-before.log`, `private/tradier-quote-focused.log`, `private/tradier-quote-validation.log`, `private/tradier-quote-validation.xml`, `private/tradier-quote-existing.log` and `private/tradier-quote-bandit.json`.
