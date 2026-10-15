"""Exact fault sequences from the user's pipeline checklist, without network I/O."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from lavish_core.trade import candle_gatekeeper as gate


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(value=0.0)
    monkeypatch.setattr(gate, "time", SimpleNamespace(
        monotonic=lambda: state.value, time=lambda: state.value,
        sleep=lambda seconds: setattr(state, "value", state.value + 1)))
    monkeypatch.setattr(gate, "ENABLED", True)
    monkeypatch.setattr(gate, "MAX_CONSECUTIVE_NO_DATA", 4)
    return state


def bars():
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=4)
    def bar(offset, close, high=103, low=97):
        return dict(t=(base + timedelta(minutes=offset)).isoformat(),
                    o=100, h=high, l=low, c=close, v=0)
    return bar(0, 100, 101, 99), bar(1, 102), bar(2, 98)


@pytest.mark.parametrize("outage", [None, TimeoutError("halt/data transport"), []])
def test_checklist_4_7_outages_recover_and_reset_streak(clock, monkeypatch, outage):
    ref, breakout, _ = bars()
    sequence = iter([outage] * 3 + [[ref]] + [outage] * 3 + [[ref, breakout]])
    def fetch(*args, **kwargs):
        result = next(sequence)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(gate, "get_bars", fetch)
    result = gate.confirm_entry("SPY", "bullish", 30)
    assert result["confirmed"]
    assert result["reason"] == "wick_breakout_volume_unavailable"


def test_checklist_7_persistent_transport_failure_is_bounded(clock, monkeypatch):
    calls = []
    def fetch(*args, **kwargs):
        calls.append(1)
        raise ConnectionError("halt")
    monkeypatch.setattr(gate, "get_bars", fetch)
    assert gate.confirm_entry("SPY", "bullish", 30)["reason"] == "no_data"
    assert len(calls) == 4


@pytest.mark.parametrize("width", [0, 0.000001])
def test_checklist_6_flat_reference_cannot_confirm(clock, monkeypatch, width):
    ref, breakout, _ = bars()
    ref.update(h=100 + width, l=100)
    sequence = iter([[ref], [ref, breakout]])
    monkeypatch.setattr(gate, "get_bars", lambda *a, **k: next(sequence))
    result = gate.confirm_entry("SPY", "bullish", 30)
    assert not result["confirmed"]
    assert result["reason"] == "reference_range_too_small"


def test_checklist_8_fetch_crossing_deadline_cannot_confirm(clock, monkeypatch):
    ref, breakout, _ = bars()
    calls = []
    def fetch(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return [ref]
        clock.value = 10
        return [ref, breakout]
    monkeypatch.setattr(gate, "get_bars", fetch)
    result = gate.confirm_entry("SPY", "bullish", 10)
    assert not result["confirmed"]
    assert result["reason"] == "timeout"


@pytest.mark.parametrize("reverse", [False, True])
def test_checklist_5_15_first_closed_bar_wins(clock, monkeypatch, reverse):
    ref, breakout, invalidation = bars()
    if reverse:
        breakout["t"], invalidation["t"] = invalidation["t"], breakout["t"]
    sequence = iter([[ref], [ref] + ([invalidation, breakout] if reverse else [breakout, invalidation])])
    monkeypatch.setattr(gate, "get_bars", lambda *a, **k: next(sequence))
    result = gate.confirm_entry("SPY", "bullish", 10)
    assert result["confirmed"] is (not reverse)
    assert result["price"] == (98 if reverse else 102)


def test_checklist_10_timestamp_offsets_normalized_across_provider_switch(clock, monkeypatch):
    ref, breakout, _ = bars()
    alternate_ref = dict(ref, t=datetime.fromisoformat(ref["t"]).astimezone(
        timezone(timedelta(hours=-4))).isoformat())
    sequence = iter([[ref], [alternate_ref, breakout]])
    monkeypatch.setattr(gate, "get_bars", lambda *a, **k: next(sequence))
    assert gate.confirm_entry("SPY", "bullish", 10)["confirmed"]


def test_deadline_does_not_depend_on_wall_clock(clock, monkeypatch):
    def wrong_clock():
        raise AssertionError("Wall clock must not drive wait deadlines")
    monkeypatch.setattr(gate.time, "time", wrong_clock)
    monkeypatch.setattr(gate, "get_bars", lambda *a, **k: [])
    assert gate.confirm_entry("SPY", "bullish", 2)["reason"] == "timeout"


@pytest.mark.parametrize("stamp", [None, 123, "not-a-timestamp"])
def test_malformed_timestamp_fails_closed(clock, monkeypatch, stamp):
    ref, _, _ = bars()
    ref["t"] = stamp
    monkeypatch.setattr(gate, "get_bars", lambda *a, **k: [ref])
    assert gate.confirm_entry("SPY", "bullish", 10)["reason"] == "invalid_bars"
