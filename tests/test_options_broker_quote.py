# Covers latest_option_quote()'s None-vs-0.0 distinction - previously
# untested. A confirmed $0.00 bid (a real, deep-OTM/near-expiry option -
# common right before expiration) is meaningful market information, not
# the same as missing/unreachable data, and used to be collapsed into the
# same thing (float(q.get("bp", 0) or 0) defaults a missing field to 0.0
# too). options_exit_monitor.py's exit retry loop relies on telling these
# apart - see test_options_exit_monitor.py's worthless-option tests.
from __future__ import annotations

from unittest.mock import MagicMock, patch

import lavish_core.trade.options_broker as ob


def _fake_response(status_code=200, quotes=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = {"quotes": quotes or {}}
    return r


def test_confirmed_zero_bid_is_returned_as_a_real_zero():
    with patch.object(ob, "_check_keys"), \
         patch.object(ob.SESSION, "get", return_value=_fake_response(
             quotes={"AAPL260101C00200000": {"bp": 0.0, "ap": 0.01}})):
        q = ob.latest_option_quote("AAPL260101C00200000")

    assert q == {"bid": 0.0, "ask": 0.01}


def test_missing_bid_field_returns_none_not_a_fake_zero():
    with patch.object(ob, "_check_keys"), \
         patch.object(ob.SESSION, "get", return_value=_fake_response(
             quotes={"AAPL260101C00200000": {"ap": 4.1}})):  # no "bp" key at all
        q = ob.latest_option_quote("AAPL260101C00200000")

    assert q is None


def test_normal_quote_round_trips_correctly():
    with patch.object(ob, "_check_keys"), \
         patch.object(ob.SESSION, "get", return_value=_fake_response(
             quotes={"AAPL260101C00200000": {"bp": 3.9, "ap": 4.1}})):
        q = ob.latest_option_quote("AAPL260101C00200000")

    assert q == {"bid": 3.9, "ask": 4.1}


def test_no_quote_for_symbol_returns_none():
    with patch.object(ob, "_check_keys"), \
         patch.object(ob.SESSION, "get", return_value=_fake_response(quotes={})):
        q = ob.latest_option_quote("AAPL260101C00200000")

    assert q is None


def test_non_200_status_returns_none():
    with patch.object(ob, "_check_keys"), \
         patch.object(ob.SESSION, "get", return_value=_fake_response(status_code=500)):
        q = ob.latest_option_quote("AAPL260101C00200000")

    assert q is None
