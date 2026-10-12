"""Shared fail-closed numeric and broker endpoint validation."""
import math
from urllib.parse import urlsplit, urlunsplit

def finite_number(value, *, positive=False, nonnegative=False):
    if value is None or isinstance(value, bool):
        raise ValueError("Expected a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Expected a finite number") from exc
    if not math.isfinite(number) or (positive and number <= 0) or (nonnegative and number < 0):
        raise ValueError("Number outside permitted range")
    return number

def valid_positive(value):
    try:
        finite_number(value, positive=True)
        return True
    except ValueError:
        return False

def normalize_base_url(value, version="v2"):
    parts = urlsplit(value.strip())
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Broker endpoint must be an uncredentialed HTTPS URL")
    path = parts.path.rstrip("/")
    if path.endswith("/" + version):
        path = path[:-(len(version)+1)]
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))

def check_trading_endpoint(mode, url, provider="alpaca"):
    if mode not in ("dry", "paper", "live"):
        raise ValueError("Unknown trading mode")
    normalized = normalize_base_url(url)
    parts = urlsplit(normalized)
    hosts = {"alpaca": {"paper": "paper-api.alpaca.markets", "live": "api.alpaca.markets"},
             "tradier": {"paper": "sandbox.tradier.com", "live": "api.tradier.com"}}
    if provider not in hosts or parts.port not in (None,443):
        raise ValueError("Unapproved broker endpoint")
    allowed = set(hosts[provider].values()) if mode == "dry" else {hosts[provider][mode]}
    if parts.hostname not in allowed:
        raise ValueError("Trading mode and broker endpoint disagree")
    return normalized


def fresh_timestamp(value,*,max_age_seconds=60,now=None):
    from datetime import datetime,timezone
    try:
        if not isinstance(value,str):return False
        stamp=datetime.fromisoformat(value.replace("Z","+00:00"))
        if stamp.tzinfo is None:return False
        age=((now or datetime.now(timezone.utc))-stamp).total_seconds()
        return 0<=age<=max_age_seconds
    except (TypeError,ValueError,OverflowError):return False
