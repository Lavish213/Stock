# Market-open preparation — October 13, 2026

The authenticated Alpaca paper clock confirmed today's open at 06:30 Pacific
(09:30 Eastern), closing at 13:00 Pacific. The 05:41 Pacific preflight verified
an eligible paper account, options level 3, zero positions, zero open orders and
a clean local/broker state audit. It submitted no orders.

## Opening rehearsal

`validation_tests/test_opening_market_stress.py` drives real alert parsing and
entry handlers through a synthetic broker boundary. The opening matrix covers
calls and puts, underlying jumps from -20% through +30%, option spreads from
0% through 80%, closed/open market states, and $0/$99/$100/$200 budgets. It
checks that only permitted entries reach submission, quantities respect the
budget and rejected orders never start a filled-position monitor.

One hundred reproducible 60-tick paths exercise diffusion, abrupt reversals,
wide spreads, zero/partial liquidity, resting limits and replacement exits.
Every tick checks cash conservation with fees and nonnegative holdings. Each
path also runs a round trip through the actual option adapter, durable ExitState
and reporting; these fabricated returns are not evidence of profitability.

Alpaca/IEX returned 30 recent daily bars each for SPY and QQQ, ending October 12.
Observed daily log-return standard deviations were approximately 0.63% and
0.94%, respectively; the largest absolute opening gaps were 1.22% and 1.64%.
These historical statistics provide context. The synthetic path parameters
are intentionally severe stress conditions, not a fitted forecast of today's
intraday or option volatility. No volatility surface, order-book queue model,
or actual opening fills have been certified.

## Repairs

- Nonfinite, negative, boolean and crossed option quotes are rejected at the
  data adapter. A verified zero bid remains available for exit diagnostics.
- A midpoint alone cannot authorize an options entry without bid and ask.
- Entry requires a valid alert-time underlying quote and a valid current quote.
  The current-quote check also applies after candle confirmation.
- Paper/live candle confirmation cannot use unverified fallback candles.
- The entire test suite blocks unmocked external I/O. Legacy fill/budget/race
  fixtures now supply explicit deterministic quotes; affordable-budget tests
  verify that the order is actually submitted with the correct quantity.

The signed receiver's real local HTTP smoke test returned health 200, initial
acceptance 202 in review mode, and duplicate 200. No execution was enabled.

## Run before enabling entries

Run the read-only preflight with the private environment and actual journal:

```bash
python -m lavish_core.validation.paper_market_preflight \
  --env-file .env --db data/brain.db --output validation/open-preflight.json
```

Use a new output path for each receipt. Run the supervised bot with
`LAVISH_VENV_PATH=/absolute/path/to/venv ./start_bot.sh` on the intended persistent
host. Validate publisher identity, post freshness, restart recovery and receipt
delivery there. Never run two processes against the active journal.

Automatic entries remain halted: the publisher is unverified, explicit paper
entry budgets are zero, and no persistent production host is established. The
private signed feed remains review-only; actual Patreon access is unconfigured.
Publisher-directed option exits and conditional price triggers still need
manual review. Simulation does not enable live approval or clear these gates.

## Final verification

The full offline suite passed: **2,395 passed, two socket-restricted skips**,
including 699 opening stress cases. All 328 Python files compiled; dependency
consistency checks passed. Bandit reported zero high, zero medium and 83 low
findings. The supervised bot started its broker workers and private feed in
paper review mode and answered its health check. It was deliberately stopped
after this smoke test; no service is left running and no orders were submitted.
