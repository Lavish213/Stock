# Research replay availability repair — October 14, 2026

Base: verified default commit `741e61ef35ae47da13e32a0d0c69798a36ea007d`.
Default CI run 38631605807 passed. PR10's final two CI runs also remain passed;
its isolated lab is still distinct from this default-branch research replay.

## Confirmed failures and fix

Synthetic GOOGL condition: two consecutive closes above 346. The first and
third bars were visible before the replay cutoff, but the intervening bar did
not arrive until later. The old loop skipped that unknown bar without resetting
its confirmation streak and incorrectly confirmed using the nonconsecutive
observations. This happened whether the hidden middle close was 345 or 347.

The old loop also allowed a bar that ended before setup expiry but arrived
after expiry to establish confirmation. Both issues affect the research-only
`condition_confirmed_review` result. This function places zero broker orders.

Unavailable or otherwise ineligible bars now reset the observable streak and
its availability timestamp. Data arriving after setup expiry cannot confirm.
The existing inclusive deadline convention is retained: arrival exactly at
expiry can establish historical confirmation; this is not authorization to
open a position at that deadline.

Four added regression cases preserve a positive late-arrival case, verify both
hidden-middle close values, enforce the expiry boundary and retain exact
deadline semantics. Two closed bars remain an explicit simulation assumption;
the creator's missing hold policy is not inferred.

Verification: 262 focused parser/context, signed-feed, gate/fill and input checks
passed after the change. A separate 457-check source ingestion, broker-fault,
cash and restart/recovery suite passed before the fix. These suites overlap and
must not be added as unique test cases. The changed-source Bandit scan had zero
medium/high findings. Four new regression cases passed; their error rate is
0/4 after repair, not an estimate of real-world source accuracy.

## Newly observed research

Observed at `2026-10-14T21:18:00.352785+00:00` (2:18 p.m. PDT October 14).
The official October Beige Book was accessible. It describes limited overall
growth, little employment change and persistent price pressure. Its information
was collected on or before October 5. This is published qualitative context,
not a new numerical GDP measurement, a forecast guarantee or an order.

The exact original publication clock and platform version IDs were not
exposed. They remain unknown. Local immutable content hashes, original document
or Reddit post IDs and observation timestamps were retained. These observations
were excluded from a pre-observation replay cutoff. No historical option fill
or creator profit was inferred.

Primary report:
https://www.federalreserve.gov/monetarypolicy/beigebook202610-summary.htm

Rechecked public Reddit new, October14 discussion `1x5nazb`, CLF DD `1x5h8uq`
and weekly earnings `1x1qqq0`. These are research-only sources. They cannot
authorize orders even when they contain otherwise complete contracts.
Instagram and the creator's public Patreon identity page remained inaccessible;
no claim of complete creator coverage or a new authenticated setup is made.

## Calendar boundaries retained

- September PPI remains scheduled for October15, 2026, 8:30 a.m. Eastern
  (EDT, UTC−4), equivalent to 5:30 a.m. Pacific (PDT, UTC−7). BLS explicitly
  states the timezone. Its current latest figures are for August; a September
  actual value must not be invented before release.
- Latest retrieved DOL claims release is still October8. It is not a new
  October15 actual.
- The retrieved Census list is a 2027 calendar. It is not accepted as proof
  of the 2026 retail schedule.
- BEA lists October29 at 8:30 for upcoming GDP/income-outlays releases, but
  retrieved schedule text did not verify its timezone; no asserted conversion.
- Board speaker clock times were retrieved without a verified timezone.
  Regional Fed event coverage remains bounded; no guessed Pacific times.

Sources: https://www.bls.gov/ppi/ ; https://www.dol.gov/ui/data.pdf ;
https://www.census.gov/economic-indicators/calendar-listview.html ;
https://bea.gov/news/schedule/full ;
https://www.federalreserve.gov/newsevents/2026-october.htm ;
https://www.frbsf.org/news-and-media/events/ .

No broker orders were placed, no mode/account reset occurred, and no halt was
cleared. Actual creator authentication, explicit paper allocations, persistent
host observations and authentic point-in-time creator replay remain unverified.
The previously resolved five-cent cash concern is not reopened by this repair.
