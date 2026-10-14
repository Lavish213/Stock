# Stock Alley screenshot study and offline replay

Ten user-supplied images were visually read and separately transcribed with
Tesseract. These are screenshots of posts and recaps, not authenticated
original alert deliveries or a complete historical market dataset.

## Visible information

| Image | Evidence | Missing or ambiguous |
| --- | --- | --- |
| IMG_2861.jpeg | Newsletter membership advertisement | No trade instruction |
| IMG_2860.jpeg | NVDA 235 call, October 2; break and hold above 230; targets 233/236/240, stretch 242–245; invalidate below 226.50; recap screenshot 237.06 | Original publication time/year and option quotes; screenshot supports neither exact fills nor reaching 240 |
| IMG_2859.png / IMG_2858.png | SPY price recaps around 774.4 and 776.18 | Original entry, contract and exits absent |
| IMG_2857.jpeg | MRVL call exp October 16, 2026; reclaim and hold 280; targets 290/300/315/330; recap 292.89 | Strike obscured; stop absent; cannot infer an executable contract |
| IMG_2856.jpeg | MRVL 300 recap and “take profits” | Calls 300 the stretch target despite card listing 330; no linked contract, exit quantity or explicit plan revision |
| IMG_2855.jpeg | SPY crossed 781; caption claims option premium 0.27 to 1.07; partially visible AMD card | SPY contract/entry time absent; AMD strike/expiry cropped, no usable complete setup |
| IMG_2854.jpeg / IMG_2853.jpeg | SNDK recaps; claimed premium 2.36 to 10.74 | Original contract, timestamps, quotes and exit instruction absent |
| IMG_2852(1).jpeg | GOOGL 350 call, October 9; break and hold above 346; second-target recap | Year, original timestamps, target levels, stop and premium absent |

No historical bars or options bid/ask sequence was inferred from a drawn
chart. Screenshots of gains do not establish an unbiased win rate or prove
that the bot could obtain those fills. Underlying price gains cannot be
translated into call-premium gains without actual contract quotes.

## Confirmed repair

Normalized cards with complete future contract identities reproduced **12
unsafe immediate dispatches across 16 cases** on the previous router. It
recognized “when/if” but missed “entry trigger,” “break and hold,” and
“reclaim and hold.” The remaining four MRVL cases already required ticker
review. Now all 16 require review instead of submitting immediately. Valid
unconditional entries retain their original target/stop fields.

This repair does not implement conditional entry execution. Doing that needs
durable setup identity, a defined hold/retest rule, expiration, price freshness,
restart state, risk checks at activation and coordination with creator updates.
The ordinary candle gate cannot be assumed to mean “hold above 230.”

## Offline simulation outcomes

- Ten manually transcribed cards and all ten actual OCR texts: zero immediate
  dispatches. Membership, recaps and incomplete instructions cannot create
  guessed trades.
- 36 conditional reference paths: no-break and false-break remain waiting;
  two consecutive closes above the trigger enter; each target is tested as an
  alternative full exit; gap stops use the observed lower price.
- Two confirming closes are an explicit experimental assumption, not a rule
  stated by the creator. MRVL stop 270 is a stress-test assumption because
  its real invalidation is unavailable. No scale-out proportions are invented.
- Two production options-adapter/journal simulations use synthetic contracts
  and screenshot-claimed premium endpoints. Simulated budget 250 and fee
  0.65/contract/side: SPY buys nine at 0.27 and sells at 1.07, modeled net
  708.30; SNDK buys one at 2.36 and sells at 10.74, modeled net 836.70.
  Both finish flat with matching cash/ledger. These amounts are arithmetic
  scenarios, **not verified historical P&L or executable returns**. No real
  options contract identity is attributed to either recap.

Reproduce:

```bash
TRADE_MODE=dry DISCORD_WEBHOOK_URL='' python -m lavish_core.validation.creator_screenshot_replay --directory NEW_OUTPUT_DIRECTORY
TRADE_MODE=dry DISCORD_WEBHOOK_URL='' python -m pytest -q validation_tests/test_creator_screenshot_conditions.py
```

The simulation blocks external requests and patches the production broker
adapter onto an offline exchange. All allocations are isolated test inputs.
No broker order, production budget/mode/entry halt or creator authentication
was changed. Full historical replay remains blocked by missing original alerts,
timestamps, contract identities, historical bid/ask bars and linked updates.

Verification: 256 focused regressions passed; complete repository suite
2,850 passed with two deployment-host skips. Bandit found no medium/high
issues. Dependency and whitespace checks passed.
