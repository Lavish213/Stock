import os
import requests
import time
import logging
from dotenv import load_dotenv
from lavish_core.patreon.patreon_refresh import refresh_patreon_token

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

PATREON_API_BASE = "https://www.patreon.com/api/oauth2/v2"
ACCESS_TOKEN = os.getenv("PATREON_ACCESS_TOKEN")
CAMPAIGN_ID = os.getenv("PATREON_CAMPAIGN_ID")  # optional, fetched if missing

def get_headers(token=None):
    return {"Authorization": f"Bearer {token or os.getenv("PATREON_ACCESS_TOKEN", ACCESS_TOKEN)}"}

def get_campaign_id():
    """Fetch the user's campaign ID automatically."""
    logging.info("🔍 Fetching campaign ID...")
    headers = get_headers()
    url = f"{PATREON_API_BASE}/identity?include=memberships.campaign"
    r = requests.get(url, headers=headers, timeout=20)
    
    if r.status_code == 401:
        logging.warning("⚠️ 401 while fetching campaign ID — refreshing token...")
        new_token = refresh_patreon_token()
        if not new_token:
            return None
        r = requests.get(url, headers=get_headers(new_token), timeout=20)

    if r.status_code != 200:
        logging.error(f"❌ Could not fetch campaign ID ({r.status_code}): {r.text}")
        return None

    data = r.json()
    includes = data.get("included", [])
    for inc in includes:
        if inc.get("type") == "campaign":
            cid = inc.get("id")
            logging.info(f"✅ Found campaign ID: {cid}")
            os.environ["PATREON_CAMPAIGN_ID"] = cid
            return cid
    logging.error("❌ No campaign ID found in response.")
    return None

def get_campaign_posts(campaign_id, token=None):
    """Fetch latest posts for your campaign."""
    url = f"{PATREON_API_BASE}/campaigns/{campaign_id}/posts?fields[post]=title,content,created_at&page[count]=5&sort=-created"
    headers = get_headers(token)
    r = requests.get(url, headers=headers, timeout=20)

    # Auto-refresh on 401
    if r.status_code == 401:
        logging.warning("⚠️ 401 Unauthorized — refreshing Patreon token...")
        new_token = refresh_patreon_token()
        if not new_token:
            return None
        r = requests.get(url, headers=get_headers(new_token), timeout=20)

    if r.status_code == 200:
        posts = r.json().get("data", [])
        if posts:
            logging.info(f"📬 Retrieved {len(posts)} posts successfully.")
            for post in posts:
                logging.info(f"📰 {post['attributes']['title']}")
        else:
            logging.info("📭 No new posts found.")
    else:
        logging.error(f"❌ Patreon API error {r.status_code}: {r.text}")

def patreon_listener_loop(interval=60):
    """Main listener loop that polls Patreon for updates."""
    logging.info("🎧 Patreon listener started. Waiting for post updates...")
    campaign_id = CAMPAIGN_ID or get_campaign_id()

    if not campaign_id:
        logging.error("❌ Could not initialize Patreon listener — no campaign ID found.")
        return

    while True:
        try:
            get_campaign_posts(campaign_id)
        except Exception as e:
            logging.error(f"⚠️ Listener exception: {e}")
        time.sleep(interval)

if __name__ == "__main__":
    patreon_listener_loop(interval=60)