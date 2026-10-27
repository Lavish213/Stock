# Creator screenshot trigger study

All10 supplied images were visually inspected. The corpus records source filename, SHA256, visible date text, manually transcribed caption, visible contract facts, missing fields and observation timestamp. It does not claim automated OCR extraction, original post IDs, exact original posting times or authentic executable quotes. Separate panels, comments and later montages are not merged into one original alert.

## What the images mean

| Image | Visible setup/message | Bot interpretation |
|---|---|---|
|IMG_2884.jpeg|NVDA220 CALL, September4,2026; reclaim218–220, hold above with buyers; targets225/230|Conditional setup. No immediate entry. Hold duration, premium and stop are not visible; stretch line cropped. Later228.43 chart is outcome context.|
|IMG_2885.jpeg|SNDK1600 CALL8/14; risky48-hour swing; later caption claims2.50->70|Original comment and later winner recap are distinct. Expiry year, stop and original timestamp absent. Do not purchase from the recap or infer28x historical execution.|
|IMG_2886.png|GOOGL305 CALL, March13; premium panels2.63->7.55|Progress montage, not a fresh entry. Year and stop unreadable; target text uncertain. Displayed daily41.90% is not entry-to-exit return.|
|IMG_2888.png|OPEN warrant-distribution information; separate "Buy before the close today" comment|Corporate-action/common-share context. Exact time, original comment identity and authoritative terms unverified. Adjacent option board below is another post, not warrant contract metadata.|
|IMG_2889.png|OPEN7 CALL February20,2026;1.43->3.15; "Take80% profits and ride the rest"|Position management requiring a matching holding. Wording does not explicitly resolve80% of profits versus80% of contract quantity. No rebuy from the Buy tab.|
|IMG_2890.png|OPEN7 CALL February20,2026;1.43; whale commentary|Flow context; no automatic entry instruction. Breakeven8.39 differs from strike+displayed1.43, possibly quote-basis differences; retain both, do not overwrite.|
|IMG_2891.png|AMD285 CALL November28;2.90; whale300k claim|Flow context; explicit expiration year, stop and trigger absent. "Inside trade" is an unverified caption claim, not privileged information or authorization.|
|IMG_2892.png|MSTR347.5 CALL October17;4.00; "Could print next week"|Speculative watch context. Explicit expiration year and entry instruction absent. Daily-44.21% is not copied-trade P&L.|
|IMG_2893.jpeg|INTC swing/news commentary; October17 selected; strike hidden|Incomplete contract. Preserve original commentary separately from later partnership claim. Never invent the strike from the unrelated price panel.|
|IMG_2895.png|HOOD170 CALL October31;1.68; invitation/question and "We up"|Invitation/progress requires review; explicit expiration year, original time and stop absent.|

Visible dates with a year can establish a calendar date, not an exact post timestamp. A year on a later outer post is not silently inserted into an inner contract selection. Missing original values remain null. These are historic screenshots, not fresh market alerts. News and corporate-action statements are recorded as screenshot claims and were not independently verified in this task.

## Changes and reproduced defect

Production context tags now distinguish `flow_context` and `position_management`; both entry-router checks retain these for review rather than dispatching a new entry. Explicit complete BTO instructions still have a positive-entry regression, including a message that mentions unusual flow. Reclaim-and-hold phrasing is classified as conditional context. These changes do not implement automatic exit sizing or promote research conditions to trades.

A reproduced numeric parser defect required at least two digits before a decimal in target/stop extraction. A valid single-digit level such as stop5 or target8 could become missing/conflicting. Accept one-digit and sub-dollar prices consistently while retaining existing ambiguity checks. Tests cover5,8 and0.5/0.8, alongside larger targets/stops and the existing instruction-integrity regressions.

## Fake alerts actually sent through the parser

`lavish_core.validation.screenshot_trigger_study` creates8 explicitly synthetic complete call alerts for NVDA,OPEN,AMD,MSTR,HOOD,INTC,SNDK andGOOGL. Missing dates/strikes/targets/stops are completed only in the synthetic copies, never in source records. The fake events use fresh explicit November20,2026 expiration and timestamped synthetic identity; source screenshots retain their original visible dates.

The configured exact recognition catalog supports5/8 of these tickers. OPEN,INTC andSNDK require catalog configuration; they are added only inside a scoped offline fixture, giving8/8 correct ticker/side/expiry entries. Production trade whitelists are not expanded. This test configuration difference is recorded per generated alert and is not presented as production readiness.

Ten actual caption excerpts produced zero entry dispatches. This is desirable for these incomplete/conditional/contextual excerpts, not a claim that all creator messages should reject. Eight positive synthetic instructions demonstrate that the harness is not succeeding by blanket rejection. Pixel-to-OCR parsing was not certified by this manually transcribed dataset.

## Conditional and management replay

Synthetic NVDA upper-zone level220 cases:

-220/220: touch alone remains waiting.
-221/219: failed hold remains waiting.
-221/222: condition confirmed for review after two available closed bars.

Two bars is a fixture assumption, not a creator-specified duration. Volume confirmation is not certified; production strong-volume gap from the blueprint audit remains separate. Confirmation does not automatically dispatch production execution.

A separate exchange fixture buys5 synthetic OPEN contracts at0.15, sells4 at0.30, sells remaining1 at0.20, and charges0.65 per contract per side. Duplicate entry ID returns the same order. Final balance158.50 from100, flat, net58.50. Every quote is invented to exercise scaling, not to model profitability. This is intentionally different from the actual screenshot's1.43 premium, which requires at least143 before fees and is unaffordable with100. No production80/20 monitor is claimed or enabled. Exact80/20 needs a multiple of5 contracts; actual instruction interpretation and small-position fallback remain unresolved.

Reproduce with a new output file:

```bash
python -m lavish_core.validation.screenshot_trigger_study --output /tmp/new-screenshot-trigger-study.json
python -m pytest validation_tests/test_screenshot_trigger_study.py validation_tests/test_research_context.py validation_tests/test_alert_instruction_integrity.py validation_tests/test_backtest_and_ocr.py -q
```

174 focused tests passed, followed by2791/2791 passing tests across the complete validation suite in92.36seconds. Changed-source Bandit reported zero medium/high findings; whitespace checks passed. Source records and detailed results are under `docs/evidence/screenshot-trigger-*-20261015.json`. All delivery is local with production parser dispatch intercepted and external network blocked. No Discord/Patreon messages, broker orders, live activation, allocation changes or automatic learning occurred. Authentication, original chronology, real fills and profitability remain unverified.

## Expanded follow-up: explicit confirmation constraints

A new reproducer found that complete option details plus each of these four instructions could still dispatch an entry: "Wait for confirmation", "This is not an automatic entry", "These setups are not automatic entries", and "Entry requires market confirmation". The original richer screenshot was blocked by its reclaim/hold/entry-trigger language, masking this shorter-phrase defect. This was parser dispatch in an isolated harness, not proof a broker order would pass later safety gates.

A literal confirmation-constraint recognizer now classifies those phrases and related waiting/needs/only-confirmed wording as conditional context before direct BTO classification. The existing router's pre-parse and post-image checks consequently retain them for review. A caption with a complete BTO cannot override a confirmation requirement extracted from its synthetic image text. It does not infer that a later bare price update removes a requirement.

The synthetic phrase matrix contains144 cases across8 tickers:32 positive entry phrasings and112 negative mutations. All32 positive cases dispatch with the expected ticker/side and all112 negative cases produce no new entry dispatch. The selected matrix's error counts are0/32 positive and0/112 negative; these are not observed real-world rates. The same scoped offline recognition-catalog extension described above is used; production whitelist is unchanged. Actual-source sample count remains10 screenshots, not154 sources.

A separate durable exit test starts with5 option contracts, partially fills2 of a requested4-contract trim, then reconstructs ExitState after restart. It adopts the existing close rather than submitting another order, confirms the remaining2 trim contracts, preserves1 runner, then closes the runner. Five total sell contracts and3.25 total closing fees are recorded once. This tests existing journal/recovery mechanics with a manually specified trim plan; automatic creation of that plan from a source phrase is still not wired into the production monitor.

162 focused parser/context/recovery tests passed after this extension. The full-suite result for this follow-up is recorded in the PR validation update. No historical source fields, model returns or risk settings were altered to improve the result.
