"""Bounded creator-message ingestion with durable at-most-once dispatch.

This processes messages already delivered by an authorized Discord session.
It does not connect, fetch channel history, or authenticate a relay by itself.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from lavish_core.db.hybrid_store import HybridStore, _positions_lock

MAX_IMAGE_BYTES = 10 * 1024 * 1024


def visible_text(text):
    if not isinstance(text, str) or len(text) > 12000:
        raise ValueError('Invalid Discord text')
    if any(ord(ch) < 32 and ch not in '\t\r\n' for ch in text):
        raise ValueError('Invalid Discord control character')
    fenced_parts = text.split('```')
    if len(fenced_parts) % 2 == 0:
        raise ValueError('Unclosed quoted code block')
    text = ''.join(fenced_parts[::2])
    parts = []
    for line in text.splitlines():
        if line.lstrip().startswith('>>>'):
            break  # Discord's multiline quote includes every following line.
        if not line.lstrip().startswith('>'):
            parts.append(line)
    return re.sub(r'~~.*?~~', '', '\n'.join(parts), flags=re.S).strip()


def message_text(message):
    parts = [visible_text(message.content or '')]
    embeds = list(getattr(message, 'embeds', []))
    if len(embeds) > 10:
        raise ValueError('Too many embeds')
    for embed in embeds:
        body = embed.to_dict()
        if not isinstance(body, dict):
            raise ValueError('Invalid embed')
        for key in ('title', 'description'):
            if body.get(key):
                parts.append(visible_text(body[key]))
        fields = body.get('fields', [])
        if not isinstance(fields, list) or len(fields) > 25:
            raise ValueError('Invalid embed fields')
        for field in fields:
            if not isinstance(field, dict):
                raise ValueError('Invalid embed field')
            parts.extend(visible_text(field.get(key, '')) for key in ('name', 'value'))
    text = '\n'.join(part for part in parts if part)
    if len(text) > 12000:
        raise ValueError('Oversized combined alert')
    from lavish_core.vision.extract_signal import KNOWN_TICKERS
    symbols = {word.upper() for word in re.findall(r'\b[A-Za-z]{1,8}\b', text)
               if word.upper() in KNOWN_TICKERS}
    if len(symbols) > 1:
        raise ValueError('Multiple symbols require separate alerts')
    return text


def stable_image_url(url):
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError('Invalid image URL')
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


def embed_images(message):
    urls = []
    for embed in getattr(message, 'embeds', []):
        body = embed.to_dict()
        for key in ('image', 'thumbnail'):
            if body.get(key):
                if not isinstance(body[key], dict) or not isinstance(body[key].get('url'), str):
                    raise ValueError('Invalid embedded image')
                if body[key]['url'] not in urls:
                    urls.append(body[key]['url'])
    return urls


def fetch_embed_image(url):
    """Only Discord CDN images, bounded streaming, no credentials/redirects."""
    import requests
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname not in ('cdn.discordapp.com', 'media.discordapp.net')
            or parts.username or parts.password or parts.port not in (None, 443) or parts.fragment):
        raise ValueError('Untrusted embedded image origin')
    response = requests.get(url, timeout=15, allow_redirects=False, stream=True)
    try:
        if response.status_code != 200:
            raise ValueError('Embedded image unavailable')
        data = bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError('Embedded image too large')
        return bytes(data)
    finally:
        response.close()


class DiscordProcessor:
    def __init__(self, db_path, *, channels, authors, webhooks, callback,
                 own_user_id=None, source='discord', max_age=120, clock=None):
        if not channels or not (authors or webhooks):
            raise ValueError('Configure watched channels and trusted creator or webhook IDs')
        if isinstance(max_age, bool) or not isinstance(max_age, int) or not 1 <= max_age <= 900:
            raise ValueError('Discord alert age must be 1 to 900 seconds')
        self.db_path = db_path
        self.channels, self.authors, self.webhooks = set(channels), set(authors), set(webhooks)
        self.callback, self.own_user_id, self.source = callback, own_user_id, source
        self.max_age, self.clock = max_age, clock or (lambda: datetime.now(timezone.utc))
        self.lock = asyncio.Lock()
        with _positions_lock:
            store = HybridStore(db_path)
            try:
                store.execute('CREATE TABLE IF NOT EXISTS discord_receipts(message_id VARCHAR PRIMARY KEY,digest VARCHAR NOT NULL,status VARCHAR NOT NULL,reason VARCHAR)')
                if store.fetchall("SELECT message_id FROM discord_receipts WHERE status='processing'"):
                    store.set_kill_switch(True, 'Discord dispatch interrupted; review broker outcome')
                    store.execute("UPDATE discord_receipts SET status='review_required',reason='interrupted_dispatch' WHERE status='processing'")
            finally:
                store.con.close()

    def _claim(self, identity, digest, status):
        with _positions_lock:
            store = HybridStore(self.db_path)
            try:
                rows = store.fetchall('SELECT digest,status FROM discord_receipts WHERE message_id=?', (identity,))
                if rows:
                    if rows[0][0] != digest:
                        if rows[0][1] in ('processing', 'dispatched'):
                            store.set_kill_switch(True, 'Previously dispatched Discord message edited; review correction')
                        store.execute("UPDATE discord_receipts SET status='review_required',reason='message_edited' WHERE message_id=?", (identity,))
                        return 'review_required'
                    return 'duplicate'
                if store.fetchall('SELECT count(*) FROM discord_receipts')[0][0] >= 50000:
                    store.set_kill_switch(True, 'Discord receipt capacity requires review')
                    raise ValueError('Discord receipt capacity requires review')
                store.execute('INSERT INTO discord_receipts VALUES (?,?,?,?)', (identity, digest, status, None))
                return status
            finally:
                store.con.close()

    def _complete(self, identity, failed=False):
        with _positions_lock:
            store = HybridStore(self.db_path)
            try:
                state = 'review_required' if failed else 'dispatched'
                store.execute('UPDATE discord_receipts SET status=?,reason=? WHERE message_id=?',
                              (state, 'dispatch_error' if failed else None, identity))
                if failed:
                    store.set_kill_switch(True, 'Discord dispatch failed; review before retry')
            finally:
                store.con.close()

    async def retract(self, channel_id, message_id, reason='message_deleted'):
        """Deleted/uncached edited instructions cannot silently alter exposure."""
        if channel_id not in self.channels:
            return 'ignored'
        identity = f'{channel_id}:{message_id}'
        async with self.lock:
            def record():
                with _positions_lock:
                    store = HybridStore(self.db_path)
                    try:
                        rows = store.fetchall('SELECT status FROM discord_receipts WHERE message_id=?', (identity,))
                        if not rows or rows[0][0] not in ('processing', 'dispatched'):
                            return 'ignored'
                        store.execute("UPDATE discord_receipts SET status='review_required',reason=? WHERE message_id=?", (reason,identity))
                        store.set_kill_switch(True, 'Dispatched Discord instruction removed or corrected; review broker exposure')
                        return 'review_required'
                    finally:
                        store.con.close()
            return await asyncio.to_thread(record)

    async def process(self, message):
        channel, author = message.channel.id, message.author.id
        webhook = getattr(message, 'webhook_id', None)
        if channel not in self.channels or author == self.own_user_id:
            return 'ignored'
        if (webhook is not None and webhook not in self.webhooks) or (webhook is None and author not in self.authors):
            return 'untrusted_source'
        async with self.lock:
            created = message.created_at
            if not isinstance(created, datetime) or created.tzinfo is None:
                return 'invalid_timestamp'
            attachments = list(message.attachments)
            embeds = []
            for embed in getattr(message, 'embeds', []):
                body = embed.to_dict()
                relevant = {key: body.get(key) for key in ('title', 'description', 'fields')}
                for key in ('image', 'thumbnail'):
                    if body.get(key) and isinstance(body[key], dict):
                        relevant[key] = stable_image_url(body[key].get('url'))
                    elif body.get(key):
                        relevant[key] = body[key]
                embeds.append(relevant)
            raw = {'channel': channel, 'author': author, 'webhook': webhook,
                   'created': created.isoformat(), 'content': message.content,
                   'embeds': embeds,
                   'attachments': [(a.id, a.filename, a.size, a.content_type) for a in attachments]}
            # Signed CDN URL query parameters are deliberately not identity fields.
            encoded = json.dumps(raw, sort_keys=True, ensure_ascii=False).encode('utf-8')
            if len(encoded) > 150000:
                return 'oversized_message'
            identity = f'{channel}:{message.id}'
            age = (self.clock() - created).total_seconds()
            state = await asyncio.to_thread(self._claim, identity, hashlib.sha256(encoded).hexdigest(),
                                            'processing' if 0 <= age <= self.max_age else 'expired')
            if state != 'processing':
                return state
            image_path = None
            dispatch_task = None
            try:
                text = message_text(message)
                remote_urls = []
                for url in embed_images(message):
                    if url.startswith('attachment://'):
                        if not any(url == 'attachment://' + a.filename for a in attachments):
                            raise ValueError('Embedded attachment unavailable')
                    elif not any(getattr(a, 'url', None) and stable_image_url(a.url) == stable_image_url(url) for a in attachments):
                        if not any(stable_image_url(other) == stable_image_url(url) for other in remote_urls):
                            remote_urls.append(url)
                if len(attachments) + len(remote_urls) > 1:
                    raise ValueError('Multiple attachments require review')
                data = None
                if attachments:
                    attachment = attachments[0]
                    if attachment.content_type not in ('image/png', 'image/jpeg', 'image/webp'):
                        raise ValueError('Unsupported trade card')
                    if isinstance(attachment.size, bool) or not isinstance(attachment.size, int) or not 0 < attachment.size <= MAX_IMAGE_BYTES:
                        raise ValueError('Oversized or invalid trade card')
                    data = await asyncio.wait_for(attachment.read(), timeout=10)
                    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_IMAGE_BYTES or len(data) != attachment.size:
                        raise ValueError('Incomplete or oversized trade card')
                elif remote_urls:
                    data = await asyncio.to_thread(fetch_embed_image, remote_urls[0])
                if data is not None:
                    from PIL import Image
                    with Image.open(io.BytesIO(data)) as image:
                        if image.format not in ('PNG', 'JPEG', 'WEBP') or image.width * image.height > 20_000_000:
                            raise ValueError('Unsupported image dimensions')
                        image.verify()
                    fd, image_path = tempfile.mkstemp(suffix='.png')
                    with os.fdopen(fd, 'wb') as output:
                        output.write(data)
                if text or image_path:
                    # Parsing/OCR and broker waits never block the Discord gateway loop.
                    dispatch_task = asyncio.create_task(asyncio.to_thread(
                        self.callback, text=text, image_path=image_path,
                        source=self.source, note='discord:' + identity))
                    await asyncio.shield(dispatch_task)
                await asyncio.to_thread(self._complete, identity)
                return 'dispatched'
            except asyncio.CancelledError:
                # A running broker/OCR thread cannot be cancelled. Retain its
                # image until completion and quarantine the interrupted event.
                if dispatch_task is not None:
                    try:
                        await dispatch_task
                    except Exception:
                        pass
                await asyncio.to_thread(self._complete, identity, True)
                raise
            except Exception:
                await asyncio.to_thread(self._complete, identity, True)
                return 'review_required'
            finally:
                if image_path:
                    Path(image_path).unlink(missing_ok=True)
