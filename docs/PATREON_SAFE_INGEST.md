# Patreon ingestion safety

The supervised bot uses `patreon_trigger.py`, then the same alert parser and
broker safeguards as the signed inbox. The legacy normalizer is not an order
source. No Patreon credentials are currently configured in the private paper
deployment, so actual publisher access and market-hours delivery are unverified.

Configure an explicit numeric `PATREON_CAMPAIGN_ID`, a token authorized for that
campaign, and OAuth refresh credentials in the private environment file. The
official posts endpoint requires `campaigns.posts` scope; paid membership alone
does not establish successful API access. Reference: https://docs.patreon.com/.
Automatic campaign discovery refuses multiple campaigns.

## Delivery and replay

- A complete snapshot is fetched before processing, following only same-host,
  same-campaign HTTPS pagination links. Redirects never receive OAuth credentials.
  Pages are limited to 50 posts, 20 pages and 2 MiB per response. Larger feeds
  require configuration review; truncation never silently triggers a partial set.
- The first complete snapshot seeds history without trades. An empty initial
  snapshot is also durably seeded. Existing cursors remain non-replayable.
- Only timezone-aware, nonfuture `published_at` values within
  `PATREON_MAX_ALERT_AGE_SECONDS` are eligible. Creation time is not publication.
  Drafts, invalid timestamps and stale posts are ignored and journaled.
- Fresh posts are considered in publication order. Receipts record a content
  fingerprint and status before dispatch. `dispatched` means handed to the shared
  parser, not an order or a confirmed fill.
- An exception or interrupted dispatch becomes `review_required` and halts
  entries. It is never retried automatically: an order may already have reached
  the broker. Edits to previously dispatched posts also halt for correction
  review. Review the broker journal before manually replacing an alert.
- Poll failures back off to at most 120 seconds. Three consecutive failures halt
  entries. Setup failures also halt after three attempts. Recovery does not clear
  an operator halt. `patreon_ingest_state` stores the last successful poll and
  consecutive failure count; thread heartbeat alone does not prove source health.

## Reading posts

HTML entities, paragraphs, inline emphasis and single-quoted image attributes
are normalized. Scripts, styles, comments, quoted/deleted content and explicitly
hidden elements cannot supply instructions. Malformed or oversized HTML requires
review. Multiple symbols or distinct image cards require review; unavailable
attachments cannot be silently ignored. A single verified screenshot is passed
through the existing OCR and text/image conflict checks.

Conditional/watch-only instructions, incomplete option contracts, ambiguous
directions and conflicting cards remain subject to the shared parser's rejection
rules. Options are never silently downgraded to stock trades. No deferred trigger
engine is introduced: a conditional price instruction requires review.
ISO option expiries are supported without altering the stated date. OCR and
caption instructions both pass the actionable gate. Options close/short alerts
are rejected by the entry path; automatic handling of publisher-directed option
exits still requires a separate verified-position implementation. Existing
bracket exits continue through their own lifecycle.

Live orders remain subject to the separate live approval and risk gates. This
ingestion change supplies no approval, position budget or strategy validation.

## Validation, 2026-10-13

The full offline suite passed: 1,696 tests passed and two socket-restricted cases
were skipped. This includes 69 new ingestion/intent cases, the existing options
simulation matrix, and Unicode fuzz checks. All 326 Python files compiled and
dependency consistency checks passed. Bandit reported zero high, zero medium
and 83 low findings. Tests used synthetic credentials and did not submit broker
orders. These results do not verify authenticated Patreon delivery, actual fills,
or strategy profitability.
