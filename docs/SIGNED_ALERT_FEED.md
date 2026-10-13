# Private signed alert feed

The receiver accepts authenticated text alerts, journals them durably and can dispatch them through `handle_alert_text()` and the existing trading safeguards. It is a receiver, not a strategy or a connection to someone else's paid alerts. A real publisher still needs to send events. No external publisher or public endpoint is configured by this change.

## Current configuration

The private local environment has a generated feed secret, `ALERT_FEED_ENABLED=true`, `ALERT_FEED_EXECUTE=false`, `TRADE_MODE=paper`, a loopback bind and port 8877. New entries remain halted and default entry budgets are zero. The secret and broker keys are excluded from Git and never printed. Review mode accepts events but does not dispatch them for trading. Existing review records never become executable when the mode changes.

On a persistent host, start the supervised bot using the documented service. The private feed can be the sole incoming source; Patreon and Discord credentials are optional when a valid private feed is enabled. Source, reconciliation and account workers run in the same process and share the bot's journal. Do not start a second process against an active bot database.

For a standalone **review-only** receiver:

```bash
python -m lavish_core.feeds.alert_feed --env-file /private/alpaca.env --db /private/review.duckdb
```

Standalone execution mode is prohibited because it lacks the complete bot's recovery supervision. Scratch listeners are temporary and cannot provide unattended overnight service.

## Send an alert

Put alert text into a local UTF-8 file. Use the signer with a private environment file containing the shared feed secret:

```bash
python -m lavish_core.feeds.send_alert --env-file /private/alpaca.env --text-file alert.txt --save-event event.json
```

For a retry within the freshness window, resend the same saved event:

```bash
python -m lavish_core.feeds.send_alert --env-file /private/alpaca.env --event-file event.json
```

Do not create a fresh ID/timestamp to blindly retry an uncertain outcome. The original event has exactly `id`, `created_at` (timezone-aware ISO timestamp) and `text`. Images, arbitrary URLs, source impersonation, broker fields and budget overrides are rejected. Upstream relays should retain the original source identity/time. Alert text still passes the existing parser, whitelist, confidence checks, market gate, risk checks, entry halt, durable reservations and execution reconciliation.

POST to `/v1/alerts` with `Content-Type: application/json`, `X-Alert-Timestamp` (Unix seconds), and `X-Alert-Signature` (hex HMAC-SHA256 of `timestamp + "\n" + exact_body_bytes`). Signatures are compared in constant time. Requests and alerts expire after 120 seconds; future timestamps are rejected, so synchronize publisher/server clocks. The sender accepts plain HTTP only for loopback, requires HTTPS for remote endpoints and refuses redirects.

The server binds loopback only. For a remote publisher, use an operator-configured HTTPS reverse proxy on the persistent host, retain signature headers, limit the body to 32 KiB and protect the receiver secret. Do not expose the raw port publicly or place secrets in URLs, Git or access logs. An outgoing Discord webhook is not this receiver and does not supply incoming alerts.

## Receipts and recovery

- `202 review`: stored for review; no trading dispatch.
- `202 queued`: durable queue acceptance; not a fill or execution confirmation.
- `200 duplicate`: same identity/content already recorded; no second dispatch. Whitespace/field ordering alone does not create another event.
- `409 id_conflict`: identity reused with changed text or creation time; review the original outcome.
- `401`: signature/timestamp invalid; nothing queued.
- `422`: malformed, stale, future-dated or unsupported payload.
- `429`: bounded request rate; retry the original fresh event appropriately.
- `503`: live mode prohibited, journal unavailable or capacity reached.

Dispatched means the parser/trading pipeline was invoked; it does not establish broker execution. Broker outcomes remain in the normal orders/fills journal. An in-flight dispatch at process death is quarantined and halts new entries on recovery. Dispatch exceptions require review and are not blindly replayed. Queued alerts that age out are expired without dispatch. Capacity is bounded; operators must review/archive the journal without deleting records needed to resolve uncertain outcomes.

To enable actual paper dispatch, configure the intended budgets/risk settings, verify the publisher, review broker/ledger state, set `ALERT_FEED_EXECUTE=true`, restart the supervised bot and release the entry halt only after reviewing its cause. This feed refuses live execution even if another component has live approval. No notifications or broker orders were sent while building/testing it.
