# Aggressive SPY allocation comparison

September 2026; same 63 fake alerts and historical candle decisions, same 5 bps modeled cost per side. Starting equity $1,000. Each simulation allows one fractional stock position and compounds the chosen percentage of current equity. No leverage, options, changed entry rules, live configuration or broker orders. This is an independent research comparison, not the current whole-share bot.

| Entry allocation | Starting entry budget | Ending balance | Net result | Trades |
|---|---:|---:|---:|---:|
| 10% | $100 | $996.43 | $-3.57 | 26 |
| 25% | $250 | $991.09 | $-8.91 | 26 |
| 50% | $500 | $982.24 | $-17.76 | 26 |
| 100% | $1,000 | $964.74 | $-35.26 | 26 |

The same alerts lose money at every allocation. Larger stakes increase the loss rather than fixing entry quality. The 100% case uses the entire account allocation, not leverage. These stock returns do not establish CALL/PUT option returns. Options require their own historical premiums, bid/ask spreads, contract selection and costs.

The $1000 balances here are hypothetical; the current whole-share bot is unchanged. Broker account/PDT/settled-cash restrictions are not modeled. Original month report: `SPY_MONTH_REPLAY_202609.md`. Full aggressive ledgers: `SPY_AGGRESSIVE_ALLOCATION_202609.json`.
