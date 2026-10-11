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

5. **Confirm the local Python environment matches what was tested
   remotely:**
   ```bash
   pip install -r requirements.txt  # only if you need the full test extras beyond requirements-deploy.txt
   pytest tests/
   ```
   Should be 261 passed (same count as the remote session that built
   this). A different count means something about this local
   environment's dependency versions diverges - investigate before
   trusting the next step's results.

6. **The real first test - actually touches the broker and Discord:**
   ```bash
   python3 simulate_alerts.py --live
   ```
   Runs curated sample alerts through the REAL pipeline: real quotes,
   the candle-confirmation gate (`lavish_core/trade/candle_gatekeeper.py`)
   watching real 1-minute bars before letting any entry through, real
   bracket orders, real paper fills. Watch them land in the Alpaca paper
   dashboard, and watch the configured Discord channel - every
   circuit-breaker trip / gatekeeper rejection / exit-monitor event
   should start showing up there for the first time.

   Expect this to take a while per alert if the gatekeeper is enabled
   (`GATEKEEPER_ENABLED=true` by default) - it can wait up to
   `GATEKEEPER_MAX_WAIT_SECONDS` (900s/15min default) per entry for a
   real candle to confirm the move before trading. Lower that env var
   temporarily if you want faster iteration while testing.

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
