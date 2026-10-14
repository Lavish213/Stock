"""Canonical settings entry point (legacy mapping access also supported)."""
import os
from .config import Settings, get_settings, set_runtime_overrides

FINNHUB_API = os.getenv('FINNHUB_API_KEY', '')
ALPHA_API = os.getenv('ALPHA_VANTAGE_API_KEY', '')
FRED_API = os.getenv('FRED_API_KEY', '')
