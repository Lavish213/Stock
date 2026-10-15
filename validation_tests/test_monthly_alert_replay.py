from copy import deepcopy
from datetime import datetime, timedelta
import pytest

from lavish_core.validation.monthly_alert_replay import (
    ET, prepare, make_alerts, fractional_comparison, production_replay,
)


def session_fixture():
    opening = datetime(2026, 9, 1, 9, 30, tzinfo=ET)
    bars = [dict(t=(opening+timedelta(minutes=i)).isoformat(),
                 o=750.,h=750.1,l=749.9,c=750.,v=1000) for i in range(390)]
    calendar = [dict(date="2026-09-01",open="09:30",close="16:00")]
    return bars, calendar


def test_missing_minute_prevents_month_claim():
    bars, calendar = session_fixture()
    with pytest.raises(ValueError, match="Missing regular-session minute"):
        prepare(bars[:10]+bars[11:],calendar,"2026-09")


def test_alerts_do_not_read_future_prices():
    bars, calendar = session_fixture()
    first = make_alerts(prepare(bars,calendar,"2026-09"))[0]
    later = deepcopy(bars)
    for bar in later[15:]:
        bar.update(o=900.,h=901.,l=899.,c=900.)
    changed = make_alerts(prepare(later,calendar,"2026-09"))[0]
    assert first["text"] == changed["text"]
    assert first["posted"] == changed["posted"]


def test_real_http_gate_and_sizing_never_force_one_share(tmp_path):
    bars, calendar = session_fixture()
    # First alert's next closed bar breaks out. Remaining price path stays
    # below any later breakout: later alerts time out safely.
    for bar in bars[15:]:
        bar.update(o=750.2,h=750.3,l=750.15,c=750.2)
    sessions = prepare(bars,calendar,"2026-09")
    result = production_replay(sessions,tmp_path/"month.duckdb")
    assert result["alerts"] == result["duplicate_deliveries"] == 3
    assert result["outcomes"]["whole_share_budget_skip"] == 1
    assert result["trades"] == result["fills"] == result["final_positions"] == 0
    assert result["final_cash"] == 1000


def test_fractional_ambiguous_bar_uses_stop_and_respects_cap():
    bars, calendar = session_fixture()
    sessions = prepare(bars,calendar,"2026-09")
    alerts = make_alerts(sessions)[:1]
    # Entry at 09:46 open, after confirmation at 09:46. Both levels touched.
    sessions[0]["bars"][16].update(h=760.,l=740.)
    receipt = dict(gate=dict(confirmed=True,decision_at="2026-09-01T09:46:00-04:00"))
    result = fractional_comparison(alerts,[receipt],5)
    trade = result["ledger"][0]
    assert trade["entry_at"].endswith("09:46:00-04:00")
    assert trade["reason"] == "stop" and trade["ambiguous_bar_stop_first"]
    assert trade["qty"]*trade["entry_price"] <= 100
    assert trade["net_pnl"] < 0
    assert result["final_cash"] == pytest.approx(1000+trade["net_pnl"])


def test_fractional_cannot_enter_before_decision_and_does_not_overlap():
    bars, calendar = session_fixture()
    sessions = prepare(bars,calendar,"2026-09")
    alerts = make_alerts(sessions)
    receipts = [dict(gate=dict(confirmed=True,decision_at=(a["posted"]+timedelta(seconds=30)).isoformat())) for a in alerts]
    result = fractional_comparison(alerts,receipts,5)
    assert result["trades"] == 1
    assert result["skips"]["existing_position"] == 2
    assert result["ledger"][0]["entry_at"].endswith("09:46:00-04:00")
    assert result["ledger"][0]["reason"] == "session_close"


def test_costs_worsen_identical_flat_price_result():
    bars, calendar = session_fixture()
    sessions = prepare(bars,calendar,"2026-09")
    alerts = make_alerts(sessions)[:1]
    receipts = [dict(gate=dict(confirmed=True,decision_at=alerts[0]["posted"].isoformat()))]
    cheap = fractional_comparison(alerts,receipts,1)
    expensive = fractional_comparison(alerts,receipts,25)
    assert expensive["final_cash"] < cheap["final_cash"] < 1000


def test_opening_target_gap_precedes_later_stop_touch():
    bars, calendar = session_fixture()
    sessions = prepare(bars,calendar,"2026-09")
    alerts = make_alerts(sessions)[:1]
    sessions[0]["bars"][17].update(o=760.,h=761.,l=740.,c=750.)
    receipts = [dict(gate=dict(confirmed=True,decision_at="2026-09-01T09:46:00-04:00"))]
    trade = fractional_comparison(alerts,receipts,5)["ledger"][0]
    assert trade["reason"] == "target"
    assert not trade["ambiguous_bar_stop_first"]
    assert trade["exit_price"] == pytest.approx(alerts[0]["target"]*.9995)
