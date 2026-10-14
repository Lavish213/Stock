# Local setup runbook - for an AI coding agent (Codex, etc.) with real
# filesystem + network access on the user's own machine.

Written for an agent with no prior context on this project. If you're
reading this, the user wants this repo fully set up and test-driven
locally - something a remote/cloud assistant (no real network access)
could prepare but not actually execute.

## What this project is

A bot that watches a Patreon/Discord creator's trade alerts (text or
screenshots), parses them (ticker, side, strike, expiry, target, stop),
and auto-executes matching trades on a broker (Alpaca or Tradier, paper
or live). It copies stated calls; it does not generate its own entries
from technical analysis. Real entry point: `run_ingest.py`. Full
architecture notes: `README.md`. Production deployment (systemd service,
VPS): `DEPLOY.md` - this doc only covers interactive local testing.

## Security rule - read before touching `.env`

**Never ask the user to paste real secrets (Alpaca/Tradier keys, Patreon
tokens, Discord tokens) into this chat, and never print/log/echo any
value already in `.env` once it exists.** This is this project's own
established rule (see `DEPLOY.md`'s opening section and `env.example`'s
header comment), not something specific to this doc.

The user should already have a `.env` file prepared from an earlier
session (downloaded as an attachment) with real `ALPACA_API_KEY`,
`ALPACA_SECRET_KEY`, and `DISCORD_WEBHOOK_URL` values already filled in,
everything else at its documented default from `env.example`. Ask them
to move/copy that file to this repo's root yourself if you have
filesystem access to wherever it downloaded to - don't ask them to
retype any value. If no such file exists, copy `env.example` to `.env`
and have the user fill in real values directly in their own editor, not
by telling you the values to type.

`.env` is already covered by `.gitignore` (`.env*`) - verify with
`git check-ignore -v .env` before proceeding if you're unsure.

## Setup steps

1. **Clone the right branch:**
   ```bash
   git clone -b claude/lavish-bot-audit-4m7e1o https://github.com/Lavish213/Stock.git
   cd Stock
   ```

2. **Install dependencies** - use `requirements-deploy.txt`, not
   `requirements.txt` (the latter pulls in a large amount of unrelated
   ML/scraping code - torch, transformers, langchain, easyocr, etc. -
   that the live bot never touches):
   ```bash
   python3 -m venv venv && source venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements-deploy.txt
   sudo apt install -y tesseract-ocr   # the real OCR binary, not just the pip wrapper
   ```

3. **Place `.env`** at the repo root per the security rule above.

4. **Preflight check:**
   ```bash
   python3 system_boot.py
   ```
   Expect `[ok]` on Alpaca keys, imports, and the tesseract binary. A
   `[FAIL]` on Patreon credentials is expected until those are added too
   (see "What's still missing" below) - don't treat that one as broken.

5. **Verify the checked-out candidate, including repair regressions:**
   ```bash
   pip install -r requirements-validation.txt
   TRADE_MODE=dry DISCORD_WEBHOOK_URL='' python -m pytest -q
   ```
   Run the entire repository suite, including `validation_tests/`. Test
   counts depend on the commit; use its audit report and CI evidence rather
   than treating a fixed count as a version check. The draft repair PRs are
   not yet in the default branch. Verify the selected repair commit before
   deployment. Do not substitute the unrelated experimental dependencies
   in `requirements.txt` for the pinned validation environment.

6. **Repeat the offline signed-alert test before broker testing:**
   ```bash
   TRADE_MODE=dry DISCORD_WEBHOOK_URL='' python -m pytest -q validation_tests/test_synthetic_alert_following.py
   ```
   This exercises the production receiver, inbox, parser, sizing/risk,
   broker adapters and exits with synthetic prices and fills. The gated
   cases also run the real candle logic with synthetic closed bars and
   deterministic worker scheduling. No broker orders are sent.

   `simulate_alerts.py --live` is intentionally refused in the repaired
   candidate; the old instructions incorrectly recommended that launcher.
   Use the documented paper preflight/probe/cycle tools for separately
   authorized account-bound observations. Offline passes do not verify
   creator access, notification delivery, cash reconciliation, or deployed
   recovery. Keep automatic entries halted while those remain unresolved.

## What's still missing

No Patreon credentials yet (`PATREON_ACCESS_TOKEN` or the three
`PATREON_CLIENT_ID`/`PATREON_CLIENT_SECRET`/`PATREON_REFRESH_TOKEN`
values) - until those are added, `run_ingest.py` (the always-on bot that
actually watches for new alerts and reacts automatically) has no real
alert source to react to. Steps 4-6 above work without it; the
always-on loop does not yet.

Discord self-bot ingestion (`DISCORD_USER_TOKEN`) was deliberately
**not** set up - it requires automating a personal Discord account,
which is against Discord's ToS and risks that account being banned. The
project runs Patreon-only by design; `DISCORD_WEBHOOK_URL` (already set)
is unrelated - that's only the bot's own OUTGOING notifications, not a
way to read anyone's messages.

## If something fails

Read the actual error - `system_boot.py` and `simulate_alerts.py` both
log specifically, not generically. Common first-run issues: `tesseract`
not on PATH (step 2), `.env` not actually at the repo root (step 3), or
a stale `WHITELIST_TICKERS` missing a ticker the user expects to see
traded (silently skips that alert - check `env.example`'s comment on
this var).
