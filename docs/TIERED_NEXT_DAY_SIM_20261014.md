# Revised user policy — synthetic next-day continuation

Built against verified default commit1246e126fce265ab86bb362566acb96a1a455ac2
(CI38658716897 passed), including the latest candle-fault repairs. This replaces
the earlier proposed month policy in PR13; its archived results remain there.

The revised simulation allocates the available balance below the first$500
milestone, permanently reduces to50% after reaching$500, and permanently
reduces to10% after reaching$1,000. A single ordinary net loss does not halt
the month. The initial first-loss experiment and its results remain archived.

“Big loss” and the number of consecutive losses were unspecified. This run
labels **10% of the day-start balance** (one loss or cumulative daily net loss)
and **three consecutive net losing trades** as test assumptions. They are not
approved production settings. A pause blocks subsequent same-day entries;
the next modeled weekday can resume if no position is unresolved. Same-day
reset, weekend resume and backdated session changes are refused. Wins or net
breakeven reset the losing streak; otherwise it carries across sessions until
a pause. The pause creates a retained local review request. There is no model
training, production halt clearing, source activation or broker order.

The ordinary experiment still has at most one position per day; the same-day
repeated-loss gate is explicitly exercised in isolated stress tests. Settlement
and multiple real day trades with full cash are not certified. Fractional SPY
shares are simulated; this does not infer options returns or enable fractional
production execution.

## Fair paired comparison

The earlier generator advanced its random price stream only when trading.
That made later alerts/paths depend on a policy's halt. Version2 generates a
complete independent price path every day, even when entry is rejected. Both
policies now see identical alerts, executable prices and seeds. Old version1
headline numbers are not presented as a paired comparison with the new run.

Costs and uncalibrated synthetic prices are unchanged: two-bps full spread,
one-bps adverse slippage per side,$0.01 hypothetical fee per side, fractional
rounding and conservative cent accounting. Dates are32 modeled calendar days,
October1–November1,2026. These are invented prices and weekday sessions, not
historical quotes or a forward profitability forecast.

For predetermined seed20261014, the revised run ended **$98.60 after12 trades**.
It resumed after its modeled three-loss pause rather than ending after the
first loss. The200-seed revised cohort ended profitable in68 runs; median
balance$99.145, minimum$95.82, maximum$103.25. None reached$500 or$1,000.
There were172 review/pause events across the cohort. Those frequencies describe
this generator only. Losing more by continuing is a valid result; this report
does not search for a profitable seed or change thresholds to improve returns.

The matched version2 first-loss cohort ended profitable in55/200 runs, with
median$99.695, minimum$99.17 and maximum$102.45. It stopped earlier, so these
figures do not establish that either policy has a trading edge.

38 focused policy/parser checks passed, including threshold boundaries,
loss-streak resets, large-gap pauses, next-day resume, retained milestone
reductions and paired executable-price equality. A broader273-check parser,
gate/fill and broker-fault suite passed. Positive-entry tests remain present;
blanket rejection cannot pass. Changed-source security scan found zero
medium/high findings.

The paired cohort additionally matched361 common executions across all200
seeds, with identical entry/exit market prices. The comparison artifact retains
every seed's balances, trade counts and review counts, plus hashes of the full
generated trial files. The first revised32-day trace is retained separately.
Full trial details can be regenerated from the fixed seeds and code.

Evidence: docs/evidence/tiered-comparison-20261014.json and
docs/evidence/tiered-month-20261014.json. The CLI now defaults to the revised
policy; use `--policy first_loss` only for the explicit archived-policy comparison.

Reproduce in fresh directories:

```sh
python -m lavish_core.validation.small_account_month --policy tiered_next_day --output /tmp/tiered-unique --trials 200 --seed 20261014
python -m lavish_core.validation.small_account_month --policy first_loss --output /tmp/paired-original-unique --trials 200 --seed 20261014
python -m pytest validation_tests/test_tiered_next_day_sim.py validation_tests/test_small_account_month.py -q
```

## Strategy continuity and source checks

Read PR14's SPY_STRATEGY_STUDY_20261014.md and spy-strategy-results-20261014.json
at commit1ee026f25119af727dbe874e7e162aaed2eacefe. The selected blend failed
later-period superiority gates; tested intraday ORB/VWAP models lost money.
The2025–October2026 historical evaluation remains consumed. No retuning or
strategy promotion occurred. Fixed trend/cash and volatility-capped trend
remain prospective hypotheses. This random-alert experiment is not validation
of those strategies and cannot establish creator or option profitability.

Public creator access remained blocked. Rechecked Reddit new and official
BLS, DOL, Fed, BEA, Census and regional Fed surfaces without deriving an order.
PPI remains scheduled October15 at8:30a.m. EDT /5:30a.m. PDT; no September
actual value is yet published in the retrieved source. DOL latest release
remains October8. BEA lists October29 at8:30 without a verified timezone in
retrieved text. Speaker times remain unconverted where timezone is missing.
The actual Census2026 page is now located at:
https://www.census.gov/economic-indicators/calendar-listview-2026.html .
This supersedes relying on the default2027 calendar, but exact release timezone
still needs verification. No unknown original posting time or platform edit ID
was invented, and no new authenticated creator replay was claimed.

Production authentication, allocations, persistent host readiness and authentic
quote/fill/latency evidence remain unverified. The previously reconciled
five-cent cash concern is not reopened. All automatic resume here is confined
to a broker-free simulated account; no existing operational halt is disabled.

