# Synthetic equity and call alert-following test

## Result

105 generated scenarios passed. There were 72 complete round trips and 144 simulated fills. Every final position was flat. Real broker orders: zero. The full repository suite passed 2,613 tests with two environment-specific systemd socket skips.

| Instrument | Scenarios | Completed round trips | Simulated fills | Median local dispatch |
| --- | ---: | ---: | ---: | ---: |
| Equity buys | 28 | 18 | 36 | 148.515 ms |
| Long option calls | 77 | 54 | 108 | 141.531 ms |

Maximum measured local dispatch was 184.674 ms for equities and 264.443 ms for calls. These timings cover the local signed-handler/inbox/parser/entry path with a simulated broker. They do not measure network latency, a deployed dispatcher's scheduling delay, or actual exchange fills.

## What the feed actually exercises

Synthetic HMAC-signed HTTP requests enter the production HTTP handler, durable inbox, shared alert parser, sizing/risk logic, Alpaca HTTP adapters, fill journal and exit monitors. Broker HTTP responses and the price clock are modeled; other requests are blocked. HTTP request framing is exercised with an in-memory connection, not a deployed TCP service.

- Tickers: AAPL, NVDA and SPY.
- Budgets: $99, $100, $250 and $500. At a $100 equity price or $1 call premium, $99 produces no entry; the other budgets produce 1, 2 and 5 shares/contracts respectively.
- Call expiry dates: 3, 7 and 21 days ahead. The expected OCC symbol is checked against the requested ticker, expiry, CALL side and strike.
- Both target and stop exits. No exit occurs before the modeled threshold; exits occur on the first eligible observed trigger tick, within one configured poll interval.
- Duplicate delivery produces one entry and one exit.
- Future, stale, closed-market and expired-contract scenarios produce no entry.
- Final fills, holdings and cash are compared independently between the broker model and durable ledger.
- The model has zero fees. Existing fee and cash-reconciliation tests remain separate; this run does not clear the outstanding real-paper cash discrepancy.

Example equity alert: `BUY AAPL now target 105 stop 95`.

Example call alert: `BTO AAPL 100 CALL exp 10/20/2026 target 105 stop 95`.

## Timing trace

The equity model observes prices at 0, 15 and 30 virtual seconds. The target path is $100 → $102 → $106; the stop path is $100 → $98 → $94. At the final tick, the modeled native bracket closes and the production equity monitor records it.

The call model observes the same underlying levels at 0, 5 and 10 virtual seconds. Call premiums follow $1.00 → $1.02 → $1.20 for a target, or $1.00 → $0.98 → $0.80 for a stop. The production option exit monitor submits a bounded sell-to-close order at the observed trigger.

Candle confirmation is disabled only inside this baseline simulator. It remains covered by the separate gatekeeper suite. This run does not guarantee immediate production entry when that gate is enabled, certify broker latency/liquidity, or establish profitability.

## Defect found and fixed

Nine adversarial contract cases failed before the repair. `find_contract()` trusted returned symbols and treated zero strike tolerance as an unlimited tolerance. A mismatched ticker, OCC expiry, CALL/PUT side or strike could therefore be accepted despite requested identity fields.

The resolver now rejects inconsistent metadata/OCC symbols, nonfinite/missing/zero strikes, and any strike deviation beyond the explicit tolerance. Zero tolerance requires the exact requested strike. A valid exact contract and a valid explicitly allowed tolerance still resolve.

Focused signed-feed and contract checks: 68 passed before the additional two closed-market cases; both added cases passed in the full suite. All four earlier exit-snapshot regressions are included in the full suite. Medium/high security scan findings: zero; dependency checks passed.

## Run it again

From the repository, use a new output directory:

```bash
python -m lavish_core.validation.alert_following --directory /tmp/alert-following-new-run
```

The command writes a `results.json` trace and isolated per-scenario journals. It refuses an existing output directory. It uses synthetic credentials and transport responses; no real credentials or broker access are required.

Regression tests: `validation_tests/test_synthetic_alert_following.py` and `validation_tests/test_contract_identity.py`.

Actual Discord/Patreon authentication, deployed host behavior, real fills and real cash-fee attribution remain outside this synthetic result.
