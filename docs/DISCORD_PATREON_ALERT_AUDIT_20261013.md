# Discord, webhook relay, and Patreon alert audit

## Current status

The source paths have been exercised offline through production parsing and trade-routing code. Real creator delivery remains unverified. The current private configuration contains no Discord session token, watched channel IDs, trusted author/webhook IDs, notification webhook URL, Patreon token, OAuth refresh credentials, or Patreon campaign ID. The signed inbox is enabled in review mode; execution is disabled.

A normal incoming Discord webhook URL posts into a Discord channel. It does not grant access to read that channel. Creator alerts need a permitted Discord listener, an owner-authorized forwarding service, or an authenticated Patreon API integration. The existing optional Discord adapter remains disabled unless its explicit opt-in and source configuration are supplied. No Discord account login, external notification, or broker order was performed in this audit.

## Verified source behavior

| Area | Tested behavior |
| --- | --- |
| Discord text | Trusted channel plus creator or webhook ID; exact source identity; self messages ignored; stale, future, and invalid timestamps rejected. |
| Webhook-style Discord embeds | Title, description, and named fields enter the shared parser; conflicting symbols require review. |
| Discord images | Verified PNG/JPEG/WebP attachments and Discord CDN embed cards; bounded sizes/pixels/downloads; no redirects or arbitrary image origins; temporary files removed after completion. Multiple distinct cards require review. |
| Quotes and historical instructions | Quoted lines, multiline `>>>` quotes, fenced code excerpts, and struck-out instructions are excluded. Unclosed code blocks require review. |
| Duplicate messages | Durable message receipts survive restart. Twenty concurrent identical deliveries dispatch once. A duplicate source receipt is not evidence of a fill. |
| Corrections and deletion | Edits to dispatched content halt entries and require review; deleted alerts and uncached partial updates cannot automatically create, reverse, or close a position. |
| Async processing | Broker/OCR waits run outside the Discord gateway event loop. Cancellation preserves an image while a running callback still uses it and quarantines the event. |
| Patreon | HTML normalization, hidden/quoted content exclusion, complete bounded pagination, campaign identity selection, token refresh, stale-post rejection, durable initial seeding, duplicate/restart protection, edited posts, interrupted dispatch, and repeated API failures. |
| Signed incoming relay | HMAC covers the exact body and timestamp; stale/replayed IDs, conflicting ID payloads, malformed JSON, duplicate fields/headers, untrusted source/budget fields, rate limits, capacity limits, and interrupted dispatch are checked. Live mode remains prohibited on this inbox. |
| Cross-source copies | All six arrival orders of Patreon, Discord, and signed relay enter the actual shared parser/claims/router and produce one broker-boundary submission. |
| OCR source integration | Actual Tesseract OCR through both Discord and Patreon: CALL and PUT cards, clear and dark backgrounds; ticker, side, strike, expiry, target and stop preserved. These cards are synthetic fixtures. |
| Options instruction safety | Entry cards remain long-option entries. Close/short/cancel/hypothetical instructions do not silently become buys. Copying creator-directed option closes is still unsupported and requires position review. |
| Outbound notifications | Redirects disabled; exceptions and response bodies cannot expose webhook tokens; mass mentions disabled; failure returns false. Actual delivery is not verified. |

## Confirmed defects repaired

1. The existing listener used `discord.Intents`, which is absent from its intended `discord.py-self` library. The actual installed 2.1.0 package reproduced the mismatch. Client construction now matches that package; the constructor and listener wiring were exercised with external login blocked. Incorrect/conflicting Discord packages fail preflight.
2. Embed-only alerts previously supplied an empty string to the parser. Relevant embed text and fields are now parsed; attached/CDN trade cards feed the actual image parser.
3. Any author in a watched channel could previously reach execution. Trusted creator and webhook IDs are now mandatory. A spoofed display name is insufficient.
4. Discord previously had no durable message receipt, freshness gate, or correction quarantine. Receipts now cover duplicates, reconnect replay, interrupted dispatch, edits, deletion, and uncached corrections.
5. Broker waits previously ran synchronously on the Discord event loop. Dispatch now runs in a worker thread behind a serial claim/dispatch boundary.
6. Attachment download errors could leave temporary files or partial inputs; multiple cards were silently truncated to the first. Inputs are now bounded and verified, ambiguity requires review, and cleanup covers success, failure and cancellation.
7. Notification exceptions could print the URL/token and error body. Logging is now restricted to status or exception type, with explicit delivery success/failure and no mass mentions.

## Validation evidence

- Focused source matrix: 282 passed, including 100 Discord/source-specific regressions and eight actual OCR source cases.
- Final full suite: 2,539 passed, with two systemd Unix-socket notification tests skipped in this environment. This includes all additional OCR, quotation, correction and deletion cases.
- Actual optional Discord package 2.1.0 instantiated and ran the listener's construction boundary with login intercepted; no account was authenticated.
- Dependency check and Python compilation passed. Medium/high Bandit scan had zero findings; this does not certify all security properties.
- Tests block external I/O or replace the broker/source transport boundary. Synthetic fixture results establish mechanisms, not private account access, market performance, or guaranteed production delivery.

## Remaining gaps before actual source following

1. Authenticate the authorized Discord channel/creator or forwarding relay and verify live message content, embeds, cards, edits, deletion, and reconnect behavior with that real source.
2. Authenticate Patreon with access to the intended campaign's posts. Patreon requires `campaigns.posts` scope; a campaign URL or paid membership alone is not proof of API authorization.
3. Verify notification delivery to the configured destination. There is no durable notification outbox; returning success from a mocked transport does not establish delivery.
4. Verify deployed gateway disconnection, source health, and watchdog behavior on a persistent host. The offline callback/constructor tests do not certify a real Discord connection.
5. Review creator-directed option closing instructions and partial trims: the existing shared parser deliberately rejects these on the long-entry route; the bot's own verified-position exit monitors remain separate.
6. Dispatch receipts mean the handler ran, not that an order filled. Do not treat a `dispatched` inbox/message row as execution confirmation.

Existing paper trading blockers—zero entry budgets, pending broker cash-fee attribution and missing persistent host verification—remain in force. See `MARKET_OPEN_HARDENING_20261013.md`.

## Source references

- Discord webhook directions: https://github.com/discord/discord-api-docs/blob/main/developers/platform/webhooks.mdx
- Discord message fields and content access: https://github.com/discord/discord-api-docs/blob/main/developers/resources/message.mdx
- Library API differences (`Intents` removed): https://discordpy-self.readthedocs.io/en/latest/migrating_from_dpy.html
- Library client construction: https://discordpy-self.readthedocs.io/en/latest/quickstart.html
- Patreon OAuth scopes and campaign post endpoint: https://docs.patreon.com/
