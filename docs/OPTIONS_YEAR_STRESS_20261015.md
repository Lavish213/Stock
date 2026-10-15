# One-year options mechanics stress — October 15, 2026

This is synthetic research, not a historical SPY backtest, profitability proof,
institutional certification or production activation. No orders were placed.

## Reproduce

`python -m lavish_core.validation.options_creator_year --trials 20 --output NEW.json`

20 independent generated years, each 365 calendar days (October 1, 2026 through
September 30, 2027), paired across two initial balances and four delays: 160
account paths, 58,400 alert deliveries, sharing 7,300 generated daily paths.
These are not 160 independent market years. Weekends reject; holidays and early
closes are NOT modeled. Daily spot resets independently. Prices are uncalibrated
European toy calls, with a $0.04 spread, $0.01 fill slippage and $0.65 per contract
per side. The model chooses deeply out-of-money zero-day calls and has no proven
signal edge. It does not represent the full range of creator options strategies.

| Start | Delay | Median finish | Worst | Best | Profitable / 20 | Trades |
|---|---:|---:|---:|---:|---:|---:|
| $100 | 0s | $6.40 | $2.80 | $14.80 | 0 | 90 |
| $100 | 60s | $6.80 | $1.50 | $14.60 | 0 | 87 |
| $100 | 180s | $8.15 | $1.80 | $14.20 | 0 | 86 |
| $100 | 360s | $100.00 | $100.00 | $100.00 | 0 | 0 |
| $1,000 | 0s | $553.85 | $341.50 | $656.40 | 0 | 505 |
| $1,000 | 60s | $539.23 | $307.80 | $778.10 | 0 | 508 |
| $1,000 | 180s | $563.00 | $326.20 | $779.50 | 0 | 491 |
| $1,000 | 360s | $1,000.00 | $1,000.00 | $1,000.00 | 0 | 0 |

No-delay vs delayed cohorts share inputs: apparent improvement at 180s is not a
validated timing advantage. Six-minute alerts exceed the five-minute freshness
limit, explaining unchanged balances. Cash reconciliation errors: zero.

Budget milestones persist: below first $500 use available balance, after $500
50%, after $1,000 10%. A $1,000 start therefore uses 10%. Day-loss 10% and three
consecutive losses remain assumed simulation pause thresholds. No changes to
actual broker allocation or risk settings. Stops are triggers, not guaranteed
fills; zero bids do not pretend to fill. No auto-learning or retuning occurred.

## Verification and remaining gaps

231 existing focused tests passed: options monthly experiment, screenshot intent,
broker fault matrix and protection recovery. These overlap prior suite counts;
they are separate from the annual toy account paths. Production parser is reused,
but account/exit execution is a synthetic adapter. Partial fills and restarts are
tested separately, not injected into every annual trade. No complete real feed,
production 80/20 exit, live latency, authentic option chain, authentic creator
replay, or persistent deployment was certified. Drawdown records closed balances,
not intratrade equity. The consumed historical holdout was not reused or retuned.

## Current primary-source observations

Observed after October 15, 2026 05:36 PDT, not available to earlier replay:

- BLS PPI release, published October 15 08:30 ET / 05:30 PDT: September final
  demand +1.3% monthly and +6.3% yearly; excluding foods, energy and trade services
  +0.5% monthly. August monthly final demand revised to +0.5%.
  https://www.bls.gov/news.release/ppi.nr0.htm
- DOL October 15 08:30 Eastern / 05:30 PDT: initial claims for week ending
  October 10 199,000; previous week revised to 198,000. These are actual releases,
  not forecasts. https://www.dol.gov/ui/data.pdf
- Alpaca options documentation and FINRA options risk material were reviewed.
  Actual broker capability and contract eligibility must be checked rather than
  assuming equity-style bracket support. Long option premium can expire worthless;
  synthetic stops and scaling do not eliminate risk.
  https://docs.alpaca.markets/us/docs/options-trading
  https://www.finra.org/investors/investing/investment-products/options

Next engineering priorities: authenticated point-in-time source/quote capture,
exchange-session calendar, realistic American exercise handling, continuous spot
paths, within-trade faults and latency, conditional production entry wiring and
whole-contract trim ownership. No success or profit grade is justified yet.
