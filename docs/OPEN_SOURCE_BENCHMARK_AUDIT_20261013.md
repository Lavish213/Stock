# Stock bot engineering benchmark and repair audit

Reviewed October 13, 2026. Scope: the deployed `run_ingest.py` alert pipeline, parser/router, Alpaca and Tradier adapters, durable order/position journal, entry/exit supervision, source authentication, deployment launchers, and offline verification. Existing draft repairs are retained in this candidate. No strategy parameters, budgets, broker account balances, live-mode settings or entry halts were reset to obtain passing results.

## Assessment

**Engineering grade: B, substantially improved. Automated paper readiness: blocked. An overall A is not yet supported by the evidence.** This is a reviewer judgment, not a certification or profitability rating. An A requires demonstrated authorized source delivery, account-bound paper execution/recovery, reconciled cash, functioning deployed notification/watchdog recovery, and adequate coverage of the runtime being graded. Offline tests alone cannot satisfy those conditions.

The entire repository's measured statement coverage remains about 32%, because it contains extensive legacy/dashboard/research paths. Core source/execution modules have much stronger coverage, but that is not equivalent to full-repository verification. Unsupported creator-directed option close/trim instructions remain quarantined for review. Broad automatic interpretation of those messages was not added without a precise identity/quantity contract.

## Established project comparison

The reviewed projects have different scopes; crypto frameworks provide execution-engine lessons, not validation of US equities/options support.

| Reference | Observed strength | Application to this bot |
|---|---|---|
| [NautilusTrader reconciliation](https://nautechsystems.github.io/nautilus_docs/latest/concepts/execution/reconciliation/) | Explicit order/position reports, retained execution state, startup and continuous reconciliation | Validate identity and terminal completeness before ledger mutation; ambiguous outcomes halt entries |
| [QuantConnect LEAN](https://github.com/QuantConnect/Lean) and [order events](https://www.quantconnect.com/docs/v2/writing-algorithms/trading-and-orders/order-events) | Multi-asset execution events and explicit partial-fill examples | Preserve actual partial executions and cancellation races rather than requested quantities |
| [VeighNa/vn.py](https://github.com/vnpy/vnpy) and [Chinese risk documentation](https://www.vnpy.com/docs/cn/elite/strategy/elite_riskmanager.html) | Event-driven execution and pre-order risk controls | Keep risk checks before transport and distinguish a worker being alive from a confirmed order outcome |
| [RQAlpha](https://github.com/ricequant/rqalpha) | Extensible multi-security research/trading framework | Keep parsing, risk, execution and journal responsibilities distinct |
| [Qlib](https://github.com/microsoft/qlib) | Research workflows and explicit trust boundaries for artifact loading | Research/model artifacts are not proof of broker readiness; do not import untrusted downloaded models |
| [Backtrader order lifecycle](https://www.backtrader.com/docu/order/) | Repeated partial-fill notifications and cancellation uncertainty | Cancellation acceptance does not establish a terminal canceled order |
| [Freqtrade backtesting](https://www.freqtrade.io/en/stable/backtesting/) | Documented simulation assumptions and retained reproducibility artifacts | Synthetic fills and virtual timing must remain labeled and cannot certify real fill quality |
| [Hummingbot](https://github.com/hummingbot/hummingbot) | Connector-based bot deployment framework | Apply consistent adapter contracts across broker implementations |

Community discovery included the [VeighNa forum discussion of detailed order management](https://www.vnpy.com/forum/topic/3133-vn-pyshe-qu-jing-xuan-21-tickji-de-wei-tuo-jing-xi-hua-guan-li), [Hacker News discussion of open-source backtesting](https://news.ycombinator.com/item?id=24121164), and [first-hand Reddit paper-to-live failures](https://www.reddit.com/r/algotrading/comments/1vmdce4/paper_2_live_what_mistakes_did_your_trading_bot/). Community reports were used to generate adversarial cases; primary project documentation and local reproduction support the repairs. No unfamiliar forum scripts, packages or model files were installed.

## Confirmed repairs in this batch

| Failure | Resulting behavior |
|---|---|
| Pending reconciliation booked contradictory terminal fills before rejecting them | Identity, quantity, price and fee validation occurs before exposure changes; invalid snapshots retain reservations and persist a halt |
| Recovered order identity could change between polls | A validated broker ID is bound into durable metadata; subsequent conflicting IDs are rejected |
| Equity and option entries trusted conflicting initial/polled identity | Supplied ticker/contract, side, client ID, broker ID and requested quantity must agree; sparse polls retain previously established fields |
| “Filled” entries could contain fewer units than requested | Terminal completeness is required before booking or starting monitors |
| Equity partial-entry cancellation acceptance released funds | Funds remain reserved until a terminal broker outcome is reconciled |
| Canceled entries could contain real fills which were discarded | Confirmed partial quantity and supplied execution fees are retained, even with canceled/expired/rejected status |
| Recovery omitted cumulative execution fees | Only verified incremental fees are applied, including later fee increases with unchanged cumulative quantity |
| Native bracket exits trusted wrong identity or incomplete terminal quantity | Contradictory child reports preserve exposure and halt entries; supplied fees are recorded |
| A rejected later alert could replace original option exit levels during restart | Recovery selects confirmed entry evidence rather than an unexecuted rejected alert |
| Failed monitor launch left a permanent “watched” marker | Failed launches clear the marker and persist an entry halt so recovery can retry |
| Equity restart protection was missing | Recover original bracket IDs or recorded active exit levels when ownership/quantity is verified; otherwise halt for review without guessing |
| Credential-bearing requests could follow redirects or use unapproved hosts | Both broker sessions constrain HTTPS origins and disable redirects for all methods |
| Tradier silently selected the first account from a multi-account token | Multiple accounts require explicit `TRADIER_ACCOUNT_ID` |
| Tradier lookup could choose the first of duplicate tags | Duplicate matching tags are ambiguous and fail closed |
| Tradier lookup relied on defaults that omit tags and return only 25 orders | Request `includeTags=true`, use bounded pagination, and reject incomplete history |
| Tradier `pending`/`open`/ack states were not consistently visible to durable recovery | Normalize these states; preserve supplied identity, including `option_symbol` for options |
| Legacy `infra/startup.sh` forced `--mode live` through another entrypoint | Route through the supervised canonical launcher and preflight |

Tradier's [official account-order reference](https://docs.tradier.com/reference/brokerage-api-accounts-get-account-orders) documents the tag and pagination defaults and limits its listing to the current market session. An absent result from that bounded session is not proof that an older uncertain order never executed; Tradier uncertain submissions continue to require reconciliation rather than automatic resubmission.

## Verification and evidence

- Before repair: 82 adversarial cases failed across recovery identity, broker transport, protection recovery, equity entry and option entry modules.
- Added 96 regression cases in total, including equity restart recovery and adapter-contract checks.
- Final complete repository run: **2,732 passed, 2 skipped**. The skipped Unix socket/systemd checks require the deployment environment.
- Additional focused checks exercised real parser/router/store paths, simulated equity/call round trips, cash accounting, restart, partial fills, duplicates and broker faults.
- Bandit across `lavish_core` and `ml`: zero medium/high findings. Dependency check, compilation and whitespace checks passed.
- No real broker orders were placed. A read-only activity refresh still returned only the two original fills, with no fee activity attributable to them.
- Evidence is retained in `private/open-source-audit-final.xml`, `private/open-source-audit-final.log`, `private/open-source-audit-final-coverage.json`, `private/open-source-audit-final-bandit.json`, and the five `*-before.log` reproduction logs.

Existing fixture expectations were corrected where they encoded unsafe behavior: selecting the first of several accounts, unlimited default strike deviation, canceling already terminal orders, or claiming a full fill smaller than the requested quantity. Sparse poll fixtures remain supported without discarding explicit identity contradictions.

## Remaining work required for A

| Requirement | Current evidence / completion condition |
|---|---|
| Authorized source | No verified creator delivery. Provide authenticated Patreon campaign access or a trusted signed bridge/Discord source, then observe real alerts and corrections. An outgoing Discord webhook cannot read a creator's channel |
| Explicit paper budgets | Available defaults are zero; equity and options budgets must be explicitly selected before automatic entry |
| Cash reconciliation | Earlier $0.05 difference remains unverified. Refresh showed no posted fee record. Require attributable broker evidence plus the original cycle journal; do not manufacture a fee |
| Persistent host | Verify deployed restart, watchdog, notifications, clock, storage permissions and secret handling with recorded observations |
| Broker execution quality | Record account-bound paper fills, partials, rejects and gap exits. Simulator dispatch latency and midpoint fills do not measure real execution quality |
| Equity native partials / complex ownership | Contradictory terminal partials and uncertain aggregated ownership fail closed. Demonstrate recovery against real bracket/position lifecycle reports before expanding automatic ownership inference |
| Source-directed option closes | Keep ambiguous closes/trims in review. Require an explicit contract, held quantity and reduce-only semantics before implementing automatic source-directed exits |
| Full-repository coverage | Raise coverage or explicitly retire unused legacy paths after ownership/scope review. Current core-path coverage does not justify declaring every file verified |
| Alternative containers | `infra/Dockerfile`, compose and system-requirements files are empty legacy placeholders. The supported deployment is the documented systemd service; no container deployment was claimed or started |

The grade is not increased by changing strategy rules, enabling trading, resetting the account, bypassing halts or treating synthetic profits as real evidence.
