# Isolated NautilusTrader research preparation

This is an optional engine-compatibility lab, not a production replacement.
No imports from the bot, no live clients, no credentials, no risk-setting changes.
The fixed tape, instructions, capital, fees and contract are synthetic. Smoke
success does not establish creator replay fidelity, realistic fills, return
expectancy, persistent deployment or readiness for automatic paper/live trading.

## Version decision

Checked 2026-10-26: plain PyPI resolution offers stable `1.231.0` for CPython
3.12. Current upstream `/docs/latest/` targets the incompatible 2.x prerelease
API and Python 3.13–3.14. This lab is written and executed against the installed
1.231.0 API/source, not blindly copied from the latest quickstart. Keep it in a
separate environment. Do not add these requirements to the bot's deploy file.
The lock records the tested Linux x86_64 environment; other platforms need
separate wheel/dependency validation. Dependency attestation verification and
distribution/license review remain required before any production adoption.

```bash
python3.12 -m venv /absolute/path/to/nautilus-lab-venv
/absolute/path/to/nautilus-lab-venv/bin/python -m pip install -r research/nautilus/requirements-lock.txt
/absolute/path/to/nautilus-lab-venv/bin/python -m pip check
/absolute/path/to/nautilus-lab-venv/bin/python research/nautilus/batch.py --output /absolute/path/to/new-evidence-directory
```

Use a fresh output directory: receipts are never silently overwritten. Exit
status is nonzero unless all nine outcomes pass. Each receipt is hashed in the
summary. The nine cases vary whole-contract quantity 1/2/3 and simulated latency
0/100ms/1.5s on ONE invented quote tape, not independent strategy trials.

Assertions: two fills; correct BUY then SELL; correct whole quantity; flat final
inventory; no fill before submission plus configured latency; cash independently
recomputed with the 100-share multiplier and $0.65/contract/side assumed fee.
Positive entries are mandatory. The simulated account has $1,000; these numbers
are lab inputs, not the user's approved allocation or fees.

## What this can help with

| Capability | Benefit | Still required |
|---|---|---|
| Event-driven matching | Independent fill/order-state behavior | Compare identical intents/events with production replay, not a separate invented strategy |
| Quote ticks and books | Spread, liquidity and intrabar-order research | Licensed point-in-time options quotes; bars cannot establish real spreads or queue position |
| Fee and latency models | Cost/delay sensitivity | Calibrate to observed broker receipts and source-to-order timing |
| Partial liquidity | Partial-fill/cancel experiments | Explicit size-aware model, tiny book sizes and positive controls; this smoke uses deep liquidity |
| Options instruments | Multiplier/identity accounting | Adjusted deliverables, symbol mapping, exercise/assignment, expiry and settlement validation |
| Data catalog / Databento | Potential normalized historical tape | Entitlement and cost approval; no data purchase or API request made here |

The official integration list reviewed does not list Alpaca/Tradier. Do not
promise seamless execution portability. Databento is a data adapter, not a broker.
An Interactive Brokers adapter is not permission to change the user's broker.

## Bridge contract before differential replay

Keep creator interpretation/risk decisions in the existing production path.
Feed the same resulting intents into a separate engine replay. Normalize:

1. Source: post/comment ID, version hash, exact publication time or null, first
   observation time, authorization/source class. Never backdate a later edit.
2. Instrument: OCC identity, underlying, right, exact strike/expiry, multiplier,
   deliverable, price/quantity increments, currency, exchange session/timezone.
3. Market event: source record ID, event time and availability/receipt time in
   UTC nanoseconds; bid/ask and sizes; preserve gaps. Drive replay by what was
   available then, not by a vendor timestamp learned later.
4. Intent: immutable ID, source version, position lifecycle, side, whole quantity,
   decision/submit/cancel time, risk disposition, confirmation/invalidation state.
5. Outcome: accepted/rejected/unknown, partial fills, fees, remaining quantity,
   cash/reservations and open positions. Compare each event, not only final P&L.

Reject missing/contradictory identities and timestamps; do not infer option
premiums from underlying candles. Source authentication, budgets and deployment
gates remain enforced by the existing bot. Neither engine may mint approvals.

## Acceptance gates before extending beyond smoke

- Reproduce identical deterministic traces across repeated fixed-input runs
  (ignoring run observation timestamps); explain every ordering difference.
- Run production-vs-Nautilus comparisons for long CALL and PUT entries/exits,
  spread/delay/cost stress, partial/cancel races and insufficient cash. Zero
  unexplained cash/inventory/intent differences; do not demand identical fills
  from intentionally different fill models.
- Prevent trades from incomplete/late creator instructions; prove valid entries
  still pass. Confirm five-minute bars by close AND availability, not a touch.
- Test retirement/replacement and creator trim/full-close against monitor exits
  with durable per-position ownership. These remain open production features.
- Test expiry/session boundaries and adjusted contracts. OPRA date-only expiry
  metadata must not be mistaken for the exchange's actual settlement cutoff.
- Obtain authorized authentic quotes and separately approved paper limits before
  forward execution. No live activation, broker change or automatic allocation.

## Known observed issue

The stable engine emits a pandas `Timestamp.utcnow` deprecation warning under
the recorded pandas version. Both smoke runs complete; this is an upstream
compatibility warning, not a reason to suppress failures or change bot pandas.

## Primary sources

- https://nautilustrader.io/docs/latest/getting_started/installation/
- https://nautilustrader.io/docs/latest/concepts/backtesting/data-and-venues/
- https://nautilustrader.io/docs/latest/concepts/behavioral_models/
- https://nautilustrader.io/docs/latest/integrations/
- https://nautilustrader.io/docs/latest/integrations/databento/

These describe current upstream capabilities; only the smoke behavior actually
executed against 1.231.0 is verified locally. No claims of 2.x API parity.
