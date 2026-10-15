# lavish_core/discord_ingest/listener.py
#
# Reads alert messages in near-real-time from a Discord server's channels
# (e.g. #stock-trades / #premium / #diamond) and feeds them into the same
# execution pipeline as the Patreon path (lavish_core.trading.trade_handler).
#
# =====================================================================
# READ THIS BEFORE SETTING DISCORD_USER_TOKEN
# =====================================================================
# A bot application needs an invitation and access from the server owner.
# An incoming webhook URL by itself cannot read channel messages. This
# optional existing adapter implements a user session, not bot OAuth.
# Prefer an owner-authorized relay or invited bot where one is available.
#
# Automating a Discord user account like this is against Discord's Terms
# of Service and can get that account warned or disabled - this isn't
# hypothetical, it's the same tradeoff every open-source project that does
# this (e.g. AdoNunes/DiscordAlertsTrader) documents explicitly. Nothing
# in this file runs unless you've made that call yourself:
#
#   DISCORD_USER_TOKEN=<your account's token>
#   DISCORD_INGEST_ACCEPT_TOS_RISK=1        (explicit opt-in, no default)
#   DISCORD_WATCH_CHANNEL_IDS=123,456,789   (channel IDs to watch)
#   DISCORD_WATCH_AUTHOR_IDS=123          (trusted creator user IDs)
#   DISCORD_WATCH_WEBHOOK_IDS=456         (trusted incoming webhook IDs)
#
# Requires the `discord.py-self` package (NOT `discord.py` - they occupy
# the same import name and cannot both be installed; discord.py-self is
# the community fork that supports logging in as a user account).
#   pip uninstall discord.py && pip install discord.py-self
#
# If you'd rather not take on that risk, don't set these - the bot will
# keep working off Patreon polling only, and you can manually paste alert
# text into a queue instead (a much smaller lift than the listener below,
# ask for it if you want that path instead).
# =====================================================================

from __future__ import annotations
import os, logging
from typing import Optional
from functools import partial

from lavish_core.logger_setup import get_logger
from lavish_core.trading.alert_handler import handle_alert_text as _handle_alert_text

log = get_logger("discord_ingest", log_dir="logs")

DISCORD_USER_TOKEN = os.getenv("DISCORD_USER_TOKEN")
ACCEPT_TOS_RISK = os.getenv("DISCORD_INGEST_ACCEPT_TOS_RISK", "0") == "1"
WATCH_CHANNEL_IDS = {
    int(c) for c in os.getenv("DISCORD_WATCH_CHANNEL_IDS", "").split(",") if c.strip().isdigit()
}
WATCH_AUTHOR_IDS = {int(c) for c in os.getenv('DISCORD_WATCH_AUTHOR_IDS', '').split(',') if c.strip().isdigit()}
WATCH_WEBHOOK_IDS = {int(c) for c in os.getenv('DISCORD_WATCH_WEBHOOK_IDS', '').split(',') if c.strip().isdigit()}
MAX_ALERT_AGE_SECONDS = int(os.getenv('DISCORD_MAX_ALERT_AGE_SECONDS', '120'))
SIGNAL_SOURCE_NAME = os.getenv("DISCORD_SIGNAL_SOURCE_NAME", "discord")

# Kept as a module-level alias so existing callers/tests using
# listener.handle_alert_text(...) keep working; parsing/execution logic
# itself now lives in lavish_core.trading.alert_handler, shared with Patreon.
handle_alert_text = partial(_handle_alert_text, source=SIGNAL_SOURCE_NAME)


def run() -> None:
    if not DISCORD_USER_TOKEN or not ACCEPT_TOS_RISK:
        log.warning(
            "Discord listener not starting: requires both DISCORD_USER_TOKEN and "
            "DISCORD_INGEST_ACCEPT_TOS_RISK=1 to be set. See the module docstring "
            "in lavish_core/discord_ingest/listener.py before enabling this."
        )
        return
    if not WATCH_CHANNEL_IDS:
        log.warning("DISCORD_WATCH_CHANNEL_IDS is empty - nothing to listen to. Not starting.")
        return
    if not (WATCH_AUTHOR_IDS or WATCH_WEBHOOK_IDS):
        raise ValueError('Configure trusted Discord creator or webhook IDs before listening')

    try:
        import discord
    except ImportError:
        log.error(
            "discord.py-self is not installed. This must NOT be installed alongside "
            "discord.py (same import name, they conflict). Run: "
            "pip uninstall discord.py && pip install discord.py-self"
        )
        return

    if hasattr(discord, 'Intents'):
        raise RuntimeError('Discord bot package conflicts with the configured user-session listener; install discord.py-self only')
    # discord.py-self deliberately removes discord.Intents. The old bot-API
    # constructor crashed before connecting when the documented package was
    # installed. Never log in during validation; tests inject this boundary.
    client = discord.Client()
    from lavish_core.db.hybrid_store import DEFAULT_DB
    from lavish_core.discord_ingest.message_processor import DiscordProcessor
    processor = DiscordProcessor(DEFAULT_DB, channels=WATCH_CHANNEL_IDS, authors=WATCH_AUTHOR_IDS,
                                 webhooks=WATCH_WEBHOOK_IDS, callback=_handle_alert_text,
                                 source=SIGNAL_SOURCE_NAME, max_age=MAX_ALERT_AGE_SECONDS)

    @client.event
    async def on_ready():
        processor.own_user_id = client.user.id
        log.info("Discord listener connected as %s, watching channels: %s",
                  client.user, sorted(WATCH_CHANNEL_IDS))

    async def _process(message) -> None:
        try:
            result = await processor.process(message)
            if result == 'review_required':
                log.warning('Discord message requires reconciliation; inspect durable receipt journal')
        except Exception as exc:
            log.error('Discord message rejected: %s', type(exc).__name__)

    @client.event
    async def on_message(message):
        await _process(message)

    @client.event
    async def on_message_edit(before, after):
        # Corrections to dispatched alerts are quarantined for review; an edit
        # must never silently create another entry or reverse an existing one.
        await _process(after)

    @client.event
    async def on_raw_message_edit(payload):
        # Uncached MESSAGE_UPDATE payloads can omit author/content/attachments.
        # Never reconstruct a tradable alert from a partial correction.
        if payload.cached_message is None:
            await processor.retract(payload.channel_id,payload.message_id,'uncached_message_edit')

    @client.event
    async def on_raw_message_delete(payload):
        await processor.retract(payload.channel_id,payload.message_id)

    log.info("Starting Discord listener (channels=%s)...", sorted(WATCH_CHANNEL_IDS))
    client.run(DISCORD_USER_TOKEN)


if __name__ == "__main__":
    run()
