# SPY month replay: $1,000 account, 10% allocation

**Period: September 1–30, 2026 (21 trading sessions). Completed historical replay, not a live month or a forecast.**

## Result

| Scenario | Starting balance | Ending balance | Net result | Trades |
|---|---:|---:|---:|---:|
| Current whole-share bot | $1,000.00 | $1,000.00 | $0.00 | 0 |
| Hypothetical fractional shares, 5 bps cost per side | $1,000.00 | $996.43 | $-3.57 (-0.357%) | 26 |

The current bot cannot spend approximately $100 on a whole SPY share when the share costs more than $700. It did not violate the budget to force a purchase. Of 63 new fake alerts, 31 passed the actual candle gate but were skipped by whole-share sizing. The other 32 failed the gate: 31 invalidations and one reference range below the quality floor. There were 63 duplicate deliveries, all recognized without another gate invocation. No orders, fills, or open positions resulted.

## Fixed fake-creator plan

- Three BUY SPY alerts per session: 09:45, 10:45 and 13:45 Eastern.
- Each target is 0.5% above the last already-closed minute; each stop is 0.3% below it.
- Run the production one-minute candle gate with its 15-minute wait, 15-second polling and 1 bp minimum reference range.
- Start with $1,000; cap each allocation at 10% of account equity. Only one hypothetical fractional position can be open, so concurrent allocation never exceeds that cap.
- Reinvest gains/losses through the next 10% budget; do not tune times, levels, or direction after seeing returns.

The source data is actual Alpaca SIP one-minute SPY history, split adjusted: **8,190 complete regular-session candles**. Calendar validation requires every scheduled minute. Synthetic alerts use only closed prices known at their posting times. Data SHA-256: `8f8deef3389f3984c2ccd1ad42a4a50cac3eab9207173a43a866d1dd86d6823c`. Raw authenticated download data and credentials are excluded from the repository.

## Fractional research comparison

This comparison uses independent research execution logic. The current bot does not implement the fractional sizing/bracket path modeled here. It is included to show the effect of the requested budget if fractional shares were available in the bot. It is not a bot paper-trading return.

At the baseline assumed cost, there were **7 winning trades and 19 losing trades**. Estimated spread/slippage cost was **$2.59**; ending positions were zero. 18 alerts were skipped while another position was open, and 19 additional alerts failed their gates.

| Assumed total spread/slippage cost per side | Ending balance | Net result |
|---|---:|---:|
| 1 bps | $998.50 | $-1.50 |
| 5 bps | $996.43 | $-3.57 |
| 10 bps | $993.85 | $-6.15 |
| 25 bps | $986.15 | $-13.85 |

Entry uses the next minute open at or after the decision and pays adverse costs. Quantity is floored to eight decimals and entry spend remains at or below the 10% cap including modeled entry costs. Exit uses a known opening gap first, then target/stop touches. Stop wins when both levels touch inside an otherwise ambiguous minute. All remaining positions close at the session close; unfilled opportunity alerts are not carried overnight. The model checks intratrade low marks as well as realized equity. Baseline modeled drawdown was 0.357%. Costs are stress assumptions, not measured commissions or executable broker spreads.

## Verification and limits

Seven targeted regressions check missing candles, alert lookahead, actual HTTP/gate/sizing integration, duplicate receipts, allocation caps, next-open timing, overlapping positions, ambiguous bar ordering, opening-gap chronology and cost sensitivity. Full repository and CI results are recorded in the associated PR.

No authentic option premiums or chains were used, so this report does not imply a SPY CALL/PUT return. Cheap options cannot be substituted for stock shares without testing their premiums, spreads, decay and whole-contract sizing. The fractional comparison does not enforce broker account eligibility, PDT or settled-cash restrictions. Inline thread scheduling and blocked order boundaries do not establish deployment, latency, crash recovery or actual fills/exits.

**Assessment:** the current stock path respected the $100 cap but could not participate. The fixed fake-creator strategy lost money in the hypothetical comparison even at the lowest modeled cost. This does not establish a profitable strategy or improve the overall operational grade to A.

Reproduce using `python -m lavish_core.validation.monthly_alert_replay --data <private-1min-history.json> --calendar <calendar.json> --journal <new-replay.duckdb> --output <report.json>`. Detailed alert receipts and trade ledger: `SPY_MONTH_REPLAY_202609.json`.
