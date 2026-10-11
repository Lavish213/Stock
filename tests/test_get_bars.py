# Covers get_bars() on both broker backends - candle_gatekeeper.py's only
# data dependency, so a broken/misnormalized bar shape there would quietly
# break every entry (either stuck confirming forever, or - worse - the
# wrong price triggering a breakout). Alpaca's shape is mocked at the
# SESSION level; Tradier's the same way, matching the existing per-broker
# test file conventions (test_options_broker_quote.py, test_broker_tradier.py).
from __future__ import annotations

from unittest.mock import MagicMock, patch

import lavish_core.trade.broker_alpaca as alpaca
import lavish_core.trade.broker_tradier as tradier


def _resp(status_code=200, body=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = body or {}
    r.text = str(body)
    return r


# --- Alpaca ---

def test_alpaca_get_bars_normalizes_fields():
    with patch.object(alpaca, "_check_keys"), \
         patch.object(alpaca.SESSION, "get", return_value=_resp(body={
             "bars": [
                 {"t": "2026-01-02T14:30:00Z", "o": 100.0, "h": 101.0, "l": 99.5, "c": 100.5, "v": 1000},
                 {"t": "2026-01-02T14:31:00Z", "o": 100.5, "h": 102.0, "l": 100.4, "c": 101.5, "v": 1500},
             ]
         })):
        bars = alpaca.get_bars("AAPL", limit=2)

    assert len(bars) == 2
    assert bars[0] == {"t": "2026-01-02T14:30:00Z", "o": 100.0, "h": 101.0, "l": 99.5, "c": 100.5, "v": 1000.0}


def test_alpaca_get_bars_falls_back_to_yfinance_on_failure():
    fake_bar = {"t": "2026-01-02T14:30:00", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 100.0}
    with patch.object(alpaca, "_check_keys"), \
         patch.object(alpaca.SESSION, "get", return_value=_resp(status_code=500)), \
         patch.object(alpaca, "_yfinance_fallback_bars", return_value=[fake_bar]) as mock_fallback:
        bars = alpaca.get_bars("AAPL", limit=1)

    assert bars == [fake_bar]
    mock_fallback.assert_called_once_with("AAPL", 1)


def test_alpaca_get_bars_returns_empty_list_when_everything_fails():
    with patch.object(alpaca, "_check_keys"), \
         patch.object(alpaca.SESSION, "get", side_effect=RuntimeError("down")), \
         patch.object(alpaca, "_yfinance_fallback_bars", return_value=[]):
        assert alpaca.get_bars("AAPL") == []


def test_alpaca_get_bars_handles_null_bars():
    with patch.object(alpaca, "_check_keys"), \
         patch.object(alpaca.SESSION, "get", return_value=_resp(body={"bars": None})), \
         patch.object(alpaca, "_yfinance_fallback_bars", return_value=[]):
        assert alpaca.get_bars("AAPL") == []


# --- Tradier ---

def test_tradier_get_bars_normalizes_fields():
    with patch.object(tradier, "API_TOKEN", "test"), \
         patch.object(tradier.SESSION, "get", return_value=_resp(body={
             "series": {"data": [
                 {"time": "2026-01-02T14:30:00", "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1000},
                 {"time": "2026-01-02T14:31:00", "open": 100.5, "high": 102.0, "low": 100.4, "close": 101.5, "volume": 1500},
             ]}
         })):
        bars = tradier.get_bars("AAPL", limit=2)

    assert len(bars) == 2
    assert bars[1] == {"t": "2026-01-02T14:31:00", "o": 100.5, "h": 102.0, "l": 100.4, "c": 101.5, "v": 1500.0}


def test_tradier_get_bars_respects_limit_by_taking_the_most_recent():
    data = [
        {"time": f"t{i}", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        for i in range(10)
    ]
    with patch.object(tradier, "API_TOKEN", "test"), \
         patch.object(tradier.SESSION, "get", return_value=_resp(body={"series": {"data": data}})):
        bars = tradier.get_bars("AAPL", limit=3)

    assert len(bars) == 3
    assert bars[-1]["t"] == "t9"


def test_tradier_get_bars_handles_null_series():
    with patch.object(tradier, "API_TOKEN", "test"), \
         patch.object(tradier.SESSION, "get", return_value=_resp(body={"series": "null"})):
        assert tradier.get_bars("AAPL") == []


def test_tradier_get_bars_returns_empty_on_non_200():
    with patch.object(tradier, "API_TOKEN", "test"), \
         patch.object(tradier.SESSION, "get", return_value=_resp(status_code=500)):
        assert tradier.get_bars("AAPL") == []


def test_tradier_get_bars_returns_empty_on_exception():
    with patch.object(tradier, "API_TOKEN", "test"), \
         patch.object(tradier.SESSION, "get", side_effect=RuntimeError("down")):
        assert tradier.get_bars("AAPL") == []
