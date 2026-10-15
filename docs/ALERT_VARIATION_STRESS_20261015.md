# Alert variation stress — October 15, 2026

Added 242 offline tests through the production text alert handler with execution captured at its dispatch boundary. These are parser/dispatch tests, not broker execution or profitability simulations.

* 54 positive cases: six tickers, three explicit entry verbs, three whitespace layouts. Assert exact ticker, call side, strike (including fractional MSTR strike), and expiry.
* 180 negative synthetic cases: the same six contracts crossed with ten confirmation, cancellation, negation, flow, recap and management constraints and three whitespace layouts.
* Eight manually transcribed excerpts/themes from the newly supplied screenshots, tested without inventing missing contract details. These are not eight authenticated original alerts or OCR accuracy measurements.

All 242 passed. Network connections and notifications were blocked. No trading rules, whitelist, allocations, broker settings or production execution code changed.

## Follow-up repair

With a complete AMD contract appended, the screenshot's retrospective “went from $6.50 a contract to $25.75 today” caption reproduced a fresh BUY dispatch. The context classifier now recognizes literal price-to-price recaps and “no valid call/put/entry/trade” reports before instruction classification. Added 48 complete-contract mutations including explicit BTO prefixes. The combined alert, context, screenshot and production replay subset passed 366 tests after the fix; the previous unchanged-production full suite passed 3,094 tests before this repair. Do not confuse those verification scopes.

User benchmark: approximately $178.60 net per month on a $1,000 simulated starting account (17.86%). This is a requested comparison target, not demonstrated expectancy or an allocation change. Future monthly evaluations must include target-hit frequency, losses, drawdowns and capital feasibility, without tuning invented prices to force the target.

## What the images do and do not establish

HOOD displays a 170 call, selected October 31, premium 1.68, and breakeven 171.60. These are distinct fields: the premium and displayed breakeven do not agree exactly, so do not silently recompute one as authenticated truth. A displayed buy tab is not an entry instruction. September 24, 2025 is visible on the post, but an exact original timestamp is unavailable.

INTC shows an October 17 selection and 35.91 underlying share price; the strike is not visible. Do not substitute the underlying price for strike. MSTR shows a 347.5 call and 4.00 premium with October 17 selected; “could print next week” is speculative context, not a complete execution policy.

AMD displays a retrospective low/high and a 600 call expiring 9/25. A daily low of 6.50 and high of 25.75 do not prove an achievable entry/exit pair. NFLX “reclaiming my level” lacks the numeric level. The MU failed-breakout excerpt explicitly describes no valid call; it must not become a new entry.

The macro screenshot uses relative “tomorrow” without a verified original publication timestamp. Preserve that uncertainty. Membership pricing and the provider's 21st-of-month service cutoff do not authorize trades, liquidation or modifications to the user's risk policy.

## Limits

No authentic point-in-time option quotes, historical fills, creator source authentication or autonomous readiness are established. Creator-directed closes, coordinated 80/20 exits and conditional setup promotion remain separate implementation gaps. Test pass rates are not profitability estimates.
