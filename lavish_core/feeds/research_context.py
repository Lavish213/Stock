"""Conservative context tagging and append-only, point-in-time research storage.

Tags organize research; they never authorize an order or resolve an expiry.
Unknown publication times remain unknown, including relative screenshot times.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('An exact timestamp with timezone is required')
    return result.astimezone(timezone.utc)


def research_source(source: str) -> bool:
    return bool(re.search(r'reddit|wallstreetbets|(?:^|\W)wsb(?:$|\W)', source, re.I))


def classify(text: str) -> dict:
    """Return a review tag, never an execution decision."""
    normalized = ' '.join(text.lower().split())
    if re.search(r'\b(?:take|sell|trim)\s+(?:\d+(?:\.\d+)?\s*%|half|a quarter)(?!\w)|\btake\s+\d+(?:\.\d+)?\s*%\s+(?:of\s+)?profits|\bride the rest\b', normalized):
        kind = 'position_management'
    elif re.search(r'\b(?:at (?:the )?(?:first|second|third|\d+(?:st|nd|rd|th)) target|target (?:hit|reached)|(?:hit|reached) (?:the )?(?:target|second target)|was up|contracts were|current positions|i (?:bought|sold)|realized gain|realized loss|gain screenshot|loss report)\b', normalized):
        kind = 'progress_update'
    elif re.search(r'\b(?:buy|sell|exit|close)\s+\$?[a-z]{1,6}\s+(?:now|here)\b|\bbto\b', normalized) and not re.search(r'\b(?:if|when|once|tomorrow|break and hold|reclaim.*hold|only above|only below)\b', normalized):
        kind = 'instruction_candidate'
    elif re.search(r'\b(?:cpi|inflation|jobs report|employment situation|payrolls|ppi|fomc|fed|beige book|gdp|jobless claims)\b', normalized):
        kind = 'macro_context'
    elif re.search(r'\b(?:break and hold|reclaim.*hold|break.*above|buy.*(?:when|if|above|below)|watch(?:ing|list)?|set ?up)\b', normalized):
        kind = 'conditional_setup'
    elif re.search(r'\bwhales?\b|\b(?:unusual|institutional) (?:options? )?flow\b', normalized):
        kind = 'flow_context'
    elif re.search(r'\b(?:buy|buying|bto|sell|selling|stc|entry|exit|close|closing)\b', normalized):
        kind = 'instruction_candidate'
    else:
        kind = 'research_note'
    condition = re.search(r'break\s+and\s+hold\s+(above|below)\s*\$?(\d+(?:\.\d+)?)', normalized)
    return {'kind': kind, 'condition': {'direction': condition[1], 'level': float(condition[2])} if condition else None,
            'execution_authorized': False}


class ResearchJournal:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS research (version_id TEXT PRIMARY KEY, source TEXT NOT NULL, post_id TEXT, published_at TEXT, observed_at TEXT NOT NULL, source_url TEXT, text TEXT NOT NULL, tags TEXT NOT NULL)')

    def record(self, *, text: str, source: str, post_id: str | None = None,
               published_at: str | None = None, observed_at: str | None = None,
               source_url: str | None = None) -> str:
        observed = timestamp(observed_at or datetime.now(timezone.utc).isoformat())
        published = timestamp(published_at) if published_at else None
        if published and published > observed:
            raise ValueError('Post publication is after observation')
        # Edits append a version. Same-text retransmission does not duplicate it.
        identity = json.dumps([source, post_id, published_at, text], ensure_ascii=False)
        version = hashlib.sha256(identity.encode()).hexdigest()
        with sqlite3.connect(self.path) as conn:
            conn.execute('INSERT OR IGNORE INTO research VALUES (?,?,?,?,?,?,?,?)',
                         (version, source, post_id, published.isoformat() if published else None,
                          observed.isoformat(), source_url, text, json.dumps(classify(text))))
        return version

    def as_of(self, now: str) -> list[dict]:
        cutoff = timestamp(now)
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT * FROM research ORDER BY observed_at, version_id').fetchall()
        # Observed-at guards against later edits leaking into past decisions.
        return [dict(row) for row in rows if timestamp(row['observed_at']) <= cutoff
                and (not row['published_at'] or timestamp(row['published_at']) <= cutoff)]
