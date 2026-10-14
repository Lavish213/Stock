import numpy as np
import pandas as pd
import pytest
from lavish_core.validation.spy_strategies import daily_weights,daily_returns,intraday_returns,validate,select_candidate


def daily(n=260):
    values=np.linspace(100,150,n)
    return pd.DataFrame({'o':values,'h':values+1,'l':values-1,'c':values,'v':100},
                        index=pd.date_range('2020-01-01',periods=n,freq='D',tz='UTC'))


def session():
    frame=pd.DataFrame({'o':100.,'h':101.,'l':99.,'c':100.,'v':100.,'vw':100.},
                       index=pd.date_range('2024-01-03 09:30',periods=26,freq='15min',tz='America/New_York'))
    frame.iloc[1]=[102.,103.,101.5,102.,100.,102.]
    frame.iloc[2]=[102.,103.,101.,102.,100.,102.]
    return frame


def test_closing_signal_fills_only_next_open_and_pays_turnover_cost():
    frame=daily(4)
    signal=pd.Series([0.,1.,0.,0.],index=frame.index)
    result=daily_returns(frame,signal,5.)
    assert result.exposure.tolist()==[0.,0.,1.]
    assert result['return'].iloc[2]==pytest.approx(frame.o.iloc[3]/frame.o.iloc[2]-1-.0005)


def test_future_price_changes_cannot_change_earlier_weights():
    frame=daily(400);changed=frame.copy()
    changed.iloc[300:,changed.columns.get_indexer(['o','h','l','c'])]*=2
    first,second=daily_weights(frame),daily_weights(changed)
    for name in first:pd.testing.assert_series_equal(first[name].iloc[:300],second[name].iloc[:300])


@pytest.mark.parametrize('mutation',['duplicate','nan','negative','bad_ohlc','reversed'])
def test_invalid_prices_refused(mutation):
    frame=daily()
    if mutation=='duplicate':frame=pd.concat([frame,frame.iloc[-1:]])
    if mutation=='nan':frame.iloc[0,0]=np.nan
    if mutation=='negative':frame.iloc[0,0]=-10
    if mutation=='bad_ohlc':frame.iloc[0,1]=90
    if mutation=='reversed':frame=frame.iloc[::-1]
    with pytest.raises(ValueError):validate(frame)


def test_intraday_closed_confirmation_next_bar_open_gap_stop():
    frame=session()
    frame.iloc[3]=[98.,100.,97.,99.,100.,99.]
    trials,excluded=intraday_returns(frame,5.)
    trade=trials['orb15'][0]['trade']
    assert trade['signal_bar']==1 and trade['entry_bar']==2
    assert trade['reason']=='gap_stop' and trade['exit']==98
    assert trials['orb15'][0]['return']==pytest.approx(98*.9995/(102*1.0005)-1)
    assert not excluded


def test_same_signal_bar_low_cannot_stop_trade_before_entry():
    frame=session()
    frame.iloc[1,frame.columns.get_loc('l')]=98.
    frame.iloc[2:25,frame.columns.get_loc('l')]=100.
    frame.iloc[2:25,frame.columns.get_loc('o')]=102.
    frame.iloc[2:25,frame.columns.get_loc('h')]=103.
    frame.iloc[2:25,frame.columns.get_loc('c')]=102.
    trials,_=intraday_returns(frame)
    assert trials['orb15'][0]['trade']['reason']=='15:45_exit'


def test_missing_session_bar_is_not_fabricated():
    rows,excluded=intraday_returns(session().iloc[:-1])
    assert all(not values for values in rows.values())
    assert len(excluded)==1


def test_after_hours_bars_do_not_make_early_close_a_full_session():
    rows,excluded=intraday_returns(session(),calendar={'2024-01-03':'13:00'})
    assert all(not values for values in rows.values())
    assert len(excluded)==1


def test_selection_refuses_negative_candidates_and_does_not_use_holdout():
    m={'return_pct':-1,'max_drawdown_pct':-2,'sharpe_zero_cash':4}
    assert select_candidate({'a':m})=='cash'
    m={**m,'return_pct':1}
    assert select_candidate({'a':m,'b':{**m,'sharpe_zero_cash':3}})=='a'


def test_good_backtest_cannot_authorize_orders_or_claim_option_profit():
    from lavish_core.trading.strategy_advisory import review_strategy
    report={'selection_locked_before_evaluation':'trend_200','promotion_gates':{'historical_pass':True,'forward_verified':False}}
    result=review_strategy(report,budget=100,price=700,instrument='call')
    assert result['action']=='RESEARCH_ONLY'
    assert result['execution_authorized'] is False
    assert 'Budget cannot buy one whole SPY share' in result['failed_or_unverified_gates']
    assert 'Underlying stock research does not establish option performance' in result['failed_or_unverified_gates']


def test_cost_stress_always_reduces_identical_intraday_trade_return():
    cheap,_=intraday_returns(session(),1.)
    expensive,_=intraday_returns(session(),25.)
    assert expensive['orb15'][0]['return']<cheap['orb15'][0]['return']


def test_paired_bootstrap_preserves_calendar_pairing_and_negative_edge():
    from lavish_core.validation.spy_strategies import paired_block_interval
    index=pd.date_range('2020-01-01',periods=100,tz='UTC')
    result=paired_block_interval(pd.Series(0.,index=index),pd.Series(.001,index=index),trials=100)
    assert result['paired_intervals']==100
    assert result['exploratory_95pct_high']<0


def test_identical_calendar_boundaries_deduped_conflicts_rejected():
    from lavish_core.validation.spy_history import merge_pages
    row={'t':'2020-01-01T00:00:00Z','c':100}
    rows,count=merge_pages([[row],[row.copy()]])
    assert len(rows)==1 and count==1
    with pytest.raises(ValueError):merge_pages([[row],[{**row,'c':101}]])
