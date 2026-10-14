"""Bounded HTML normalization and durable, at-most-once Patreon dispatch."""
from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser

from lavish_core.db.hybrid_store import _positions_lock


class PostHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.images = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        style = re.sub(r"\s+", "", attrs.get("style", "").lower())
        hidden = (tag in {"script", "style", "template", "noscript", "blockquote", "del"}
                  or "hidden" in attrs or attrs.get("aria-hidden") == "true"
                  or "display:none" in style or "visibility:hidden" in style)
        suppressed = hidden or any(item[1] for item in self.stack)
        if tag not in {"img", "br", "hr", "input", "meta", "link", "wbr"}:
            self.stack.append((tag, suppressed))
        if not suppressed:
            if tag in {"p", "div", "li", "br", "hr", "tr"}:
                self.parts.append("\n")
            if tag == "img" and attrs.get("src"):
                self.images.append(attrs["src"])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {"img", "br", "hr", "input", "meta", "link", "wbr"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in {"img", "br", "hr", "input", "meta", "link", "wbr"}:
            return
        if self.stack:
            if self.stack[-1][0] != tag:
                raise ValueError("Malformed HTML requires review")
            self.stack.pop()
        else:
            raise ValueError("Unmatched HTML closing tag")
        if tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not any(item[1] for item in self.stack):
            self.parts.append(data)


def normalize_html(content):
    if not isinstance(content, str) or len(content) > 120000:
        raise ValueError("Invalid or oversized post content")
    parser = PostHTML()
    parser.feed(content)
    parser.close()
    if parser.stack:
        raise ValueError("Unclosed HTML requires review")
    text = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
    # Deduplicate identical attachments, preserving order.
    images = list(dict.fromkeys(parser.images))
    if len(text) > 12000 or len(images) > 4:
        raise ValueError("Post requires review: oversized text or too many attachments")
    return text, images


def fingerprint(post):
    attrs = post["attributes"]
    relevant = {key: attrs.get(key) for key in ("title", "content", "published_at")}
    return hashlib.sha256(json.dumps(relevant, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class PostJournal:
    def __init__(self, store):
        self.store = store
        self.legacy_seen = store.get_all_ingest_seen("patreon")
        with _positions_lock:
            store.execute("CREATE TABLE IF NOT EXISTS patreon_receipts(post_id VARCHAR PRIMARY KEY,digest VARCHAR NOT NULL,status VARCHAR NOT NULL,reason VARCHAR)")
            store.execute("CREATE TABLE IF NOT EXISTS patreon_ingest_state(name VARCHAR PRIMARY KEY,value VARCHAR NOT NULL)")
            interrupted = store.fetchall("SELECT post_id FROM patreon_receipts WHERE status='processing'")
            if interrupted:
                store.set_kill_switch(True, "Patreon dispatch interrupted; review broker outcome")
                store.execute("UPDATE patreon_receipts SET status='review_required',reason='interrupted_dispatch' WHERE status='processing'")

    def seeded(self):
        with _positions_lock:
            return bool(self.store.fetchall("SELECT value FROM patreon_ingest_state WHERE name='seeded'")) or bool(self.store.get_all_ingest_seen("patreon"))

    def finish_seed(self):
        with _positions_lock:
            self.store.execute("INSERT INTO patreon_ingest_state VALUES ('seeded','true') ON CONFLICT(name) DO NOTHING")

    def claim(self, post, status, reason=None):
        identity, digest = post["id"], fingerprint(post)
        with _positions_lock:
            rows = self.store.fetchall("SELECT digest,status FROM patreon_receipts WHERE post_id=?", (identity,))
            if rows:
                if rows[0][0] != digest:
                    if rows[0][1] in {"processing", "dispatched"}:
                        self.store.set_kill_switch(True, "Previously dispatched Patreon post edited; review correction")
                    self.store.execute("UPDATE patreon_receipts SET status='review_required',reason='post_edited' WHERE post_id=?", (identity,))
                return False
            # Preserve old durable cursors during upgrade; never replay them.
            if identity in self.legacy_seen:
                status, reason = "seeded", "legacy_cursor"
            if self.store.fetchall("SELECT count(*) FROM patreon_receipts")[0][0] >= 50000:
                raise ValueError("Patreon journal capacity requires review")
            self.store.execute("INSERT INTO patreon_receipts VALUES (?,?,?,?)", (identity, digest, status, reason))
            return status == "processing"

    def complete(self, identity, *, failed=False):
        with _positions_lock:
            if failed:
                self.store.set_kill_switch(True, "Patreon dispatch failed; review broker outcome before retry")
            self.store.execute("UPDATE patreon_receipts SET status=?,reason=? WHERE post_id=?", ("review_required" if failed else "dispatched", "dispatch_error" if failed else None, identity))
