# lavish_core/patreon/trigger.py
from __future__ import annotations
import os, time, json, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests

from lavish_core.logger_setup import get_logger
from lavish_core.trading.alert_handler import handle_alert_text
from lavish_core.patreon.patreon_refresh import refresh_patreon_token
from lavish_core.db.hybrid_store import HybridStore, DEFAULT_DB
from lavish_core.utils.alerts import post_discord

ROOT = Path(__file__).resolve().parents[1]
VISION_RAW = ROOT / "vision" / "raw"
LOG_DIR = ROOT / "patreon" / "logs"
VISION_RAW.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

log = get_logger("patreon.trigger", log_dir=str(LOG_DIR))

API = "https://www.patreon.com/api/oauth2/v2"
ACCESS   = os.getenv("PATREON_ACCESS_TOKEN", "")
CAMPAIGN = os.getenv("PATREON_CAMPAIGN_ID", "")
POLL_SECONDS = int(os.getenv("PATREON_POLL_SECONDS", "20"))
VISION_AUTO  = os.getenv("VISION_AUTO", "true").strip().lower() in ("1","true","yes","on")

# A post older than this when first seen is stale enough that acting on it
# isn't "copying a fresh alert" anymore (e.g. the bot was down and this is
# catching up on a backlog) - skip trading it, but still mark it seen so
# it isn't retried forever. Generous default since Patreon's own feed can
# lag; tightened per-deployment if latency is confirmed better than this.
MAX_ALERT_AGE_SECONDS = int(os.getenv("PATREON_MAX_ALERT_AGE_SECONDS", "900"))

_INGEST_SOURCE = "patreon"

def _headers(tok: Optional[str]=None) -> Dict[str, str]:
    return {"Authorization": f"Bearer {tok or ACCESS}"}

def _ensure_campaign_id() -> str:
    global CAMPAIGN
    if CAMPAIGN:
        return CAMPAIGN
    url = f"{API}/identity?include=memberships.campaign"
    r = requests.get(url, headers=_headers(), timeout=20)
    if r.status_code == 401:
        new_tok = refresh_patreon_token()
        if not new_tok:
            raise RuntimeError("Unable to refresh Patreon token")
        r = requests.get(url, headers=_headers(new_tok), timeout=20)
    r.raise_for_status()
    data = r.json()
    for inc in data.get("included", []):
        if inc.get("type") == "campaign":
            CAMPAIGN = inc.get("id")
            os.environ["PATREON_CAMPAIGN_ID"] = CAMPAIGN
            log.info(f"Patreon campaign id resolved: {CAMPAIGN}")
            return CAMPAIGN
    raise RuntimeError("No Patreon campaign found on identity")

def _safe_image_url(url):
    import socket, ipaddress
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None,443):
        return False
    if not any(host == root or host.endswith("."+root) for root in ("patreon.com","patreonusercontent.com")):
        return False
    addresses = socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
    return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)


def _download_images_from_post(post: Dict[str, Any]) -> List[Path]:
    import io
    from PIL import Image
    saved = []
    html = (post.get("attributes") or {}).get("content") or ""
    for match in re.finditer(r'src="([^"]+)"', html):
        try:
            url = match.group(1)
            if not _safe_image_url(url):
                continue
            response = requests.get(url, timeout=20, allow_redirects=False, stream=True)
            with response:
                if response.status_code != 200:
                    continue
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > 10*1024*1024:
                        raise ValueError("Image too large")
            with Image.open(io.BytesIO(data)) as img:
                if img.width*img.height > 20_000_000 or img.format not in ("PNG","JPEG","WEBP"):
                    raise ValueError("Unsupported image")
                img.verify()
            safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(post.get("id","unknown")))[:80]
            path = VISION_RAW / f"pat_{safe_id}_{len(saved)+1}.jpg"
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(data)
            saved.append(path)
        except Exception:
            log.warning("Image download rejected or failed")
    return saved

def _post_age_seconds(post: Dict[str, Any]) -> Optional[float]:
    created_at = (post.get("attributes") or {}).get("created_at")
    if not created_at:
        return None
    try:
        dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return None


def _handle_post(post: Dict[str, Any]) -> None:
    attrs = post.get("attributes", {})
    title = attrs.get("title", "") or ""
    content = (attrs.get("content") or "")
    body = f"{title}\n{content}"
    pid = post.get("id")

    # Download any attached trade-card screenshot *before* parsing, so it
    # feeds the same text+image parser Discord alerts go through - a
    # strike/expiry/stop stated only in the image (not the caption) used
    # to be silently dropped since nothing OCR'd it.
    image_path = None
    if VISION_AUTO:
        imgs = _download_images_from_post(post)
        if imgs:
            image_path = str(imgs[0])
            log.info("Saved %d Patreon image(s) → %s", len(imgs), VISION_RAW)

    patron_dollars = os.getenv("PATRON_DOLLARS")
    handle_alert_text(
        text=body,
        image_path=image_path,
        note=f"patreon:{pid}",
        source="patreon",
        amount_usd=float(patron_dollars) if patron_dollars else None,
    )

# A restart here means the WHOLE setup (campaign id resolution, cursor
# load, etc.) failed, not a single poll inside the loop - the inner loop
# below already retries individual poll failures forever on its own, so
# only a setup-time failure should ever reach this.
POLL_LOOP_RESTART_DELAY_SECONDS = int(os.getenv("PATREON_RESTART_DELAY_SECONDS", "30"))


def poll_loop():
    """
    Thin restart wrapper around _poll_loop_inner(). Real bug found and
    fixed: _ensure_campaign_id() used to run BEFORE the inner loop's own
    try/except even existed, so any failure there (a transient Patreon
    blip at the exact moment the bot started, a token refresh failing
    once) killed this entire background thread permanently and silently -
    the rest of the process (including the watchdog ping thread) kept
    looking perfectly healthy with nobody watching Patreon ever again.
    This must never let the thread die; log, alert, back off, and retry
    the whole setup instead.
    """
    restart_count = 0
    while True:
        try:
            _poll_loop_inner()
            return  # _poll_loop_inner() only returns on its own deliberate exit, if ever
        except Exception as e:
            restart_count += 1
            log.error("patreon poll_loop: setup/fatal error (restart #%d, retrying in %ds): %s",
                      restart_count, POLL_LOOP_RESTART_DELAY_SECONDS, e)
            post_discord(f"🚨 Patreon poller crashed ({e}) - restarting automatically "
                          f"(restart #{restart_count}). New alerts may be missed until it recovers; "
                          f"check if this keeps happening.")
            time.sleep(POLL_LOOP_RESTART_DELAY_SECONDS)


def _poll_loop_inner():
    global ACCESS
    cid = _ensure_campaign_id()
    log.info(f"📬 Patreon listening (campaign={cid}) — poll={POLL_SECONDS}s vision_auto={VISION_AUTO}")

    # Durable cursor (HybridStore) instead of an in-memory set - a restart
    # used to reset "seen" to empty, so the next poll's most recent posts
    # all looked brand new and got traded as if they just happened.
    store = HybridStore(duckdb_path=str(DEFAULT_DB))
    seen: set[str] = store.get_all_ingest_seen(_INGEST_SOURCE)
    first_run_seed = not seen
    if first_run_seed:
        log.warning(
            "Patreon ingest cursor is empty (first run, or a fresh DB) - the first batch of posts "
            "fetched will be marked seen WITHOUT trading them, so this doesn't replay her entire "
            "recent history as if every post just happened."
        )

    base = f"{API}/campaigns/{cid}/posts?fields[post]=title,content,created_at,post_type&page[count]=10&sort=-created"

    # A periodic "still alive" line so someone tailing logs can tell "quietly
    # healthy, no new posts" apart from "silently stopped polling" without
    # having to infer it from the absence of any log line at all.
    heartbeat_every = max(1, int(900 // max(1, POLL_SECONDS)))  # ~every 15 min
    loop_count = 0

    while True:
        loop_count += 1
        if loop_count % heartbeat_every == 0:
            log.info("Patreon poller heartbeat: alive, %d post(s) seen total.", len(seen))
        try:
            r = requests.get(base, headers=_headers(), timeout=25)
            if r.status_code == 401:
                log.warning("401 from Patreon — refreshing token…")
                new_tok = refresh_patreon_token()
                if new_tok:
                    ACCESS = new_tok
                    r = requests.get(base, headers=_headers(), timeout=25)
            if r.status_code != 200:
                log.warning("Patreon poll error %s: %s", r.status_code, r.text[:200])
            else:
                data = r.json().get("data", [])
                for post in data:
                    pid = post.get("id")
                    if not pid or pid in seen:
                        continue
                    seen.add(pid)
                    store.mark_ingest_seen(_INGEST_SOURCE, pid)

                    if first_run_seed:
                        continue  # cursor-seeding batch - mark seen, don't trade

                    age = _post_age_seconds(post)
                    if age is not None and age > MAX_ALERT_AGE_SECONDS:
                        log.warning("Skipping stale Patreon post %s (%.0fs old, > %ds max) - not trading it.",
                                     pid, age, MAX_ALERT_AGE_SECONDS)
                        continue

                    _handle_post(post)
                first_run_seed = False  # only the very first successful poll is the seed batch
        except Exception as e:
            log.error("patreon_poll_error: %s", e)
        time.sleep(POLL_SECONDS)

def main():
    poll_loop()

if __name__ == "__main__":
    main()