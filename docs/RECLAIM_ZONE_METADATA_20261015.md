# Reclaim-zone research fidelity

This repair is stacked on verified PR20 commit774158413f4819c13156986f332b3aec21db66a7. Default branch69f50f5 and both open repair PRs19/20 were checked first. It changes research metadata only, not trading mode, allocation, confirmation timing, broker execution or strategy selection.

## Reproduced problems

1. The manually transcribed NVDA screenshot says to reclaim218–220 and hold above the area with buyers stepping in. The existing classifier correctly retained it as conditional, but stored `condition:null`. The zone was lost to downstream research consumers.
2. A synthetic message containing both “break and hold above346” and “break and hold below340” retained only the first condition. It did not authorize an order, but silently picking one branch is unsuitable research evidence.

The new normalizer preserves explicit reclaim-zone boundaries and their upper threshold. A zone218–220 with “hold above this area” produces direction above, level220, zone_low218 and zone_high220. It retains the literal request for buyers stepping in, while hold_duration and volume_policy stay null. Neither an arbitrary two-candle window nor an arbitrary15–30-minute hold is inferred from the screenshot. Review is still required; execution_authorized stays false.

Reversed, equal or nonpositive ranges are invalid. An explicit hold threshold different from the range's high, an unresolved resistance label, or competing conditions require review. Identical repeated conditions can deduplicate. Existing single-level GOOGL break-and-hold metadata remains compatible. Contract strike and later spot prices do not become trigger levels.

## Verification scope

- One previously supplied actual-source manual excerpt, identified by its original screenshot filename and SHA256. Its original post ID/publication timestamp remain unknown; no new creator coverage is claimed.
- Four synthetic before/after examples in `docs/evidence/reclaim-zone-metadata-20261015.json`, including a preserved GOOGL level, ambiguous opposing triggers, a reversed range and a contradictory hold threshold.
- 27 focused new test cases cover four separator spellings, BTO-prefixed conditional text, valid single levels, invalid conditions, deduplication, a positive explicit-entry control and append-only journal behavior across a simulated restart/later edit.
- 86 focused tests across this new file plus existing context/screenshot suites passed. Tests use local mocks and network blocking. The full validation result is recorded in the PR.
- Changed-source Bandit found no medium/high findings. This is a diagnostic code-quality result, not operational certification.

The journal stores the richer tags only when a new record is written. Existing stored history is not retroactively rewritten, missing source timestamps are not invented, and a later edited zone does not appear in an earlier as-of query. Old records without the new metadata remain old records, not silently reinterpreted evidence.

Reproduce:

```bash
python -m pytest validation_tests/test_reclaim_zone_metadata.py validation_tests/test_research_context.py validation_tests/test_screenshot_trigger_study.py -q
```

## Public-source refresh

Primary calendars were rechecked at BLS, DOL, BEA, Census, the Federal Reserve Board and San Francisco Fed. BLS still schedules September PPI for October15,2026 at08:30 Eastern/05:30 Pacific daylight time; the retrieved homepage still carries August actuals, not September actuals. No new actual release or changed calendar item was established. Census and BEA display scheduled times, but no additional timezone conversion was asserted without source verification. Unverified secondary forecasts and speaker times are not promoted into actual results or execution schedules.

Sources: https://www.bls.gov/ppi/ ; https://www.dol.gov/ui/data.pdf ; https://www.bea.gov/news/schedule/full ; https://www.census.gov/economic-indicators/calendar-listview-2026.html ; https://www.federalreserve.gov/newsevents/2026-october.htm ; https://www.frbsf.org/news-and-media/events/ .

Public WSB new, daily discussion1x5nazb, DD/earnings-related post1x5h8uq and weekly earnings1x1qqq0 were accessible, but yielded no authenticated creator instruction. Relative comment times were not invented as original timestamps. They remain research only, including claims or price targets in a DD post. Instagram and Patreon fetches remained inaccessible; no sign-in or paywall bypass was attempted.

The prior SPY historical evaluation remains consumed and unchanged. No strategy was retuned/promoted, no fee discrepancy reopened, no broker order submitted, and no automatic allocation or learning change made. Production conditional promotion, 80/20 monitor wiring, source authentication, persistent deployment and authentic creator quote/fill replay remain unverified.
