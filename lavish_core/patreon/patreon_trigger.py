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
from lavish_core.db.hybrid_store import HybridStore, DEFAULT_DB, _positions_lock
from lavish_core.patreon.ingest_safety import PostJournal, normalize_html
from urllib.parse import urlsplit, urljoin
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
    global CAMPAIGN, ACCESS
    if CAMPAIGN:
        if not re.fullmatch(r"[0-9]+", CAMPAIGN):
            raise ValueError("Invalid Patreon campaign identity")
        return CAMPAIGN
    url = f"{API}/identity?include=memberships.campaign"
    r = requests.get(url, headers=_headers(), timeout=20, allow_redirects=False)
    if r.status_code == 401:
        new_tok = refresh_patreon_token()
        if not new_tok:
            raise RuntimeError("Unable to refresh Patreon token")
        ACCESS = new_tok
        r = requests.get(url, headers=_headers(), timeout=20, allow_redirects=False)
    r.raise_for_status()
    data = r.json()
    campaigns = {inc.get("id") for inc in data.get("included", []) if inc.get("type") == "campaign"}
    if len(campaigns) != 1:
        raise RuntimeError("Configure an explicit Patreon campaign ID; identity is absent or ambiguous")
    CAMPAIGN = campaigns.pop()
    if not isinstance(CAMPAIGN, str) or not re.fullmatch(r"[0-9]+", CAMPAIGN):
        raise ValueError("Invalid Patreon campaign identity")
    os.environ["PATREON_CAMPAIGN_ID"] = CAMPAIGN
    return CAMPAIGN

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
    for url in normalize_html(html)[1]:
        try:
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
    content, image_urls = normalize_html(attrs.get("content") or "")
    if not isinstance(title, str) or len(title) > 1000:
        raise ValueError("Invalid post title")
    body = f"{title}\n{content}"
    from lavish_core.feeds.research_context import ResearchJournal, classify
    if os.getenv('RESEARCH_CONTEXT_DB'):
        ResearchJournal(os.environ['RESEARCH_CONTEXT_DB']).record(
            text=body, source='patreon', post_id=str(post.get('id') or ''),
            published_at=attrs.get('published_at'))
    if classify(body)['kind'] in ('macro_context', 'progress_update', 'conditional_setup'):
        log.info('Patreon context retained for review; no order dispatch')
        return
    from lavish_core.vision.extract_signal import KNOWN_TICKERS
    symbols = {word.upper() for word in re.findall(r"\b[A-Za-z]{1,8}\b", body)
               if word.upper() in KNOWN_TICKERS}
    if len(symbols) > 1:
        raise ValueError("Multiple symbols require review; do not combine separate trades")
    if not symbols and not image_urls:
        raise ValueError("No exact supported symbol; do not infer a ticker from prose")
    if len(image_urls) > 1:
        raise ValueError("Multiple trade cards require review")
    if image_urls and not VISION_AUTO:
        raise ValueError("Post attachment requires OCR")
    pid = post.get("id")

    # Download any attached trade-card screenshot *before* parsing, so it
    # feeds the same text+image parser Discord alerts go through - a
    # strike/expiry/stop stated only in the image (not the caption) used
    # to be silently dropped since nothing OCR'd it.
    image_path = None
    if VISION_AUTO:
        imgs = _download_images_from_post(post)
        if len(imgs) != len(image_urls):
            raise ValueError("Post attachment unavailable; do not infer missing contract fields")
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
        post_id=str(pid) if pid is not None else None,
        published_at=attrs.get('published_at'),
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
            if restart_count >= 3:
                from lavish_core.utils.control_file import write_control
                write_control(DEFAULT_DB, True, "Patreon setup unhealthy; verify campaign and credentials")
            log.error("patreon poll_loop: setup/fatal error (restart #%d, retrying in %ds): %s",
                      restart_count, POLL_LOOP_RESTART_DELAY_SECONDS, type(e).__name__)
            post_discord(f"🚨 Patreon poller crashed ({type(e).__name__}) - restarting automatically "
                          f"(restart #{restart_count}). New alerts may be missed until it recovers; "
                          f"check if this keeps happening.")
            from lavish_core.utils.worker_health import WORKER_HEALTH
            WORKER_HEALTH.beat("patreon-poller")
            time.sleep(POLL_LOOP_RESTART_DELAY_SECONDS)


def _published_age(post):
    stamp = post["attributes"].get("published_at")
    if not isinstance(stamp, str):
        return None
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except ValueError:
        return None


def _fetch_posts(cid):
    """Collect a complete bounded snapshot before allowing any dispatch.

    Never forward an OAuth token to arbitrary pagination links or redirects.
    A truncated/failed snapshot cannot seed the cursor or trigger trades.
    """
    global ACCESS
    path = f"/api/oauth2/v2/campaigns/{cid}/posts"
    url = f"{API}/campaigns/{cid}/posts?fields[post]=title,content,published_at&page[count]=50"
    visited, posts = set(), {}
    for _ in range(20):
        if url in visited:
            raise ValueError("Pagination cycle")
        visited.add(url)
        response = requests.get(url, headers=_headers(), timeout=25, allow_redirects=False, stream=True)
        if response.status_code == 401:
            response.close()
            new_token = refresh_patreon_token()
            if new_token:
                ACCESS = new_token
                response = requests.get(url, headers=_headers(), timeout=25, allow_redirects=False, stream=True)
        if response.status_code != 200:
            # No response bodies, URLs or OAuth tokens in logs/errors.
            response.close()
            raise RuntimeError(f"Patreon HTTP status {response.status_code}")
        body = bytearray()
        try:
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body) > 2 * 1024 * 1024:
                    raise ValueError("Oversized Patreon response")
            payload = json.loads(body.decode("utf-8"))
        finally:
            response.close()
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or len(payload["data"]) > 50:
            raise ValueError("Invalid post page")
        for post in payload["data"]:
            if (not isinstance(post, dict) or post.get("type") != "post"
                    or not isinstance(post.get("id"), str) or not re.fullmatch(r"[0-9]+", post["id"])
                    or not isinstance(post.get("attributes"), dict)):
                raise ValueError("Invalid post resource")
            if post["id"] in posts and posts[post["id"]] != post:
                raise ValueError("Post changed during pagination")
            posts[post["id"]] = post
        links = payload.get("links") or {}
        if not isinstance(links, dict):
            raise ValueError("Invalid pagination")
        next_url = links.get("next")
        if not next_url:
            return list(posts.values())
        if not isinstance(next_url, str):
            raise ValueError("Invalid pagination link")
        url = urljoin(url, next_url)
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.netloc != "www.patreon.com"
                or parts.path != path or parts.fragment):
            raise ValueError("Unsafe pagination destination")
    raise ValueError("Patreon snapshot exceeds 1000-post limit; review configuration")


def _process_snapshot(posts, journal):
    seed = not journal.seeded()
    # Fresh alerts are dispatched in publication order, independent of API sorting.
    def publication_order(post):
        age = _published_age(post)
        return -age if age is not None else float("-inf")
    ordered = sorted(posts, key=publication_order)
    for post in ordered:
        age = _published_age(post)
        state, reason = "processing", None
        if seed:
            state, reason = "seeded", "initial_snapshot"
        elif age is None or not 0 <= age <= MAX_ALERT_AGE_SECONDS:
            state, reason = "ignored", "invalid_or_stale_publication"
        if not journal.claim(post, state, reason):
            continue
        try:
            _handle_post(post)
        except Exception:
            journal.complete(post["id"], failed=True)
            log.error("Patreon post dispatch requires review (post=%s)", post["id"])
            break
        journal.complete(post["id"])
    if seed:
        journal.finish_seed()


def _poll_loop_inner():
    cid = _ensure_campaign_id()
    store = HybridStore(duckdb_path=str(DEFAULT_DB))
    journal = PostJournal(store)
    failures = 0
    while True:
        try:
            posts = _fetch_posts(cid)
            _process_snapshot(posts, journal)
            failures = 0
            with _positions_lock:
                store.execute("INSERT INTO patreon_ingest_state VALUES ('last_success',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (datetime.now(timezone.utc).isoformat(),))
                store.execute("INSERT INTO patreon_ingest_state VALUES ('consecutive_failures','0') ON CONFLICT(name) DO UPDATE SET value=excluded.value")
        except Exception as exc:
            failures += 1
            log.warning("Patreon poll failed (%s, attempt=%d)", type(exc).__name__, failures)
            with _positions_lock:
                store.execute("INSERT INTO patreon_ingest_state VALUES ('consecutive_failures',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (str(failures),))
            if failures >= 3:
                store.set_kill_switch(True, "Patreon source unhealthy; verify authentication and post access")
        from lavish_core.utils.worker_health import WORKER_HEALTH
        WORKER_HEALTH.beat("patreon-poller")
        time.sleep(min(120, max(1, POLL_SECONDS) * (2 ** min(failures, 3))))

def main():
    poll_loop()

if __name__ == "__main__":
    main()
