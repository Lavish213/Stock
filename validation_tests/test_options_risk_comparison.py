import pytest
from lavish_core.validation.production_alert_replay import replay
from lavish_core.validation.options_risk_comparison import summarize


@pytest.mark.parametrize('fraction',[0,-.1,1.01,float('nan'),float('inf'),True])
def test_invalid_policy_never_starts_a_replay(tmp_path,fraction):
    target=tmp_path/'invalid'
    with pytest.raises(ValueError):replay(target,days=1,fixed_fraction=fraction)
    assert not target.exists()


def test_month_boundaries_losses_and_drawdown_are_not_hidden():
    sessions=[dict(date=day,broker_cash=value,entry_orders=1,halt=False,cash_error=0,position_match=True)
              for day,value in [('2026-10-01',1200),('2026-10-31',1100),('2026-11-01',900),('2026-11-30',950)]]
    result=summarize(dict(initial=1000,final_cash=950,simulation_fixed_fraction=.25,
                          stress_profile='baseline',sessions=sessions))
    assert result['monthly']['2026-10']['net']==100
    assert result['monthly']['2026-11']['start']==1100
    assert result['monthly']['2026-11']['net']==-150
    assert result['net']==-50 and result['losing_months']==1
    assert result['closed_session_cash_drawdown_pct']==25
    assert result['months_at_178_60']==0
    assert result['halt_sessions']==0
    sessions[0]['halt']=[False,'']
    sessions[1]['halt']=[True,'review']
    assert summarize(dict(initial=1000,final_cash=950,simulation_fixed_fraction=.25,
                          stress_profile='baseline',sessions=sessions))['halt_sessions']==1


@pytest.mark.parametrize('fraction',[.1,.25,.5,1.])
def test_explicit_simulation_fraction_respects_cash_and_fees(tmp_path,fraction):
    result=replay(tmp_path/str(fraction),days=1,initial=1000,fixed_fraction=fraction)
    session=result['sessions'][0]
    assert session['budget']==1000*fraction
    assert session['entry_orders']==1  # Retain a positive execution control.
    assert session['cash_error']==0 and session['position_match']
    assert result['real_orders']==0


def test_parser_uses_virtual_date_beyond_real_date_expiry_horizon(tmp_path):
    result=replay(tmp_path/'clock',days=100,initial=1000,fixed_fraction=.1)
    # An unpatched parser rejects every dated contract beyond its real-clock
    # 45-day horizon, silently turning the remainder of a year into no trades.
    late=[s for s in result['sessions'] if s['date']>='2026-12-15']
    assert sum(s['entry_orders'] for s in late)>0
