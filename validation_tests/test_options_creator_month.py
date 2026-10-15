from datetime import date
from decimal import Decimal
import pytest
from lavish_core.validation.options_creator_month import CallAccount,offline,parse,quote,run

AT='2026-10-01T07:30:00-07:00'
END='2026-10-01T08:30:00-07:00'
SIGNAL=dict(ticker='SPY',side='CALL',action='BUY',strike=780,expiry='2026-10-01',target_hint=775,stop_hint=765)

def event(i='one'):return dict(id=i,created_at=AT,text='synthetic',synthetic=True)
def account(initial=100):
    a=CallAccount(initial);a.begin(date(2026,10,1));return a


def test_whole_contract_multiplier_fees_and_duplicate():
    a=account();assert a.enter(event(),SIGNAL,'.48','.50',AT)=='entered'
    assert a.position['qty']==1 and a.position['cost']==Decimal('51.65')
    assert a.balance==Decimal('48.35')
    assert a.enter(event(),SIGNAL,'.48','.50',AT)=='duplicate'
    r=a.close('1.07',END,'creator_exit',strike=780,expiry='2026-10-01')
    assert r['net_pnl']==53.7 and a.balance==Decimal('153.70') and a.position is None


def test_premium_one_dollar_is_unaffordable_at_100_after_costs():
    a=account();assert a.enter(event(),SIGNAL,'.98','1',AT)=='unaffordable_contract'
    assert a.balance==100


@pytest.mark.parametrize('balance,fraction',[(100,'1'),(499.99,'1'),(500,'.5'),(999.99,'.5'),(1000,'.1')])
def test_tiers(balance,fraction):assert account(balance).fraction()==Decimal(fraction)


def test_reduced_tier_persists_and_no_overallocation():
    a=account(500);assert a.enter(event(),SIGNAL,'.98','1',AT)=='entered'
    assert a.position['qty']==2 and a.position['allocation']==250
    a.close('.30',END,'stop');assert a.balance<500 and a.fraction()==Decimal('.5')
    assert a.paused;a.begin('2026-10-02');assert not a.paused and a.reviews


def test_ordinary_losses_continue_but_three_pause():
    a=account()
    for i in range(3):
        assert a.enter(event(str(i)),SIGNAL,'.48','.50',AT)=='entered'
        a.close('.50',END,'stop')
        assert a.paused==(i==2)
    a.begin('2026-10-01');assert a.paused
    a.begin('2026-10-02');assert not a.paused


def test_zero_bid_is_not_fake_exit_and_expiration_is_full_premium_loss():
    a=account();a.enter(event(),SIGNAL,'.48','.50',AT)
    assert a.close(0,END,'stop')=='unfilled_zero_bid'
    with pytest.raises(ValueError):a.close(0,END,'expired_worthless')
    r=a.close(0,'2026-10-01T13:00:00-07:00','expired_worthless')
    assert r['net_pnl']==-51.65 and a.balance==Decimal('48.35') and a.paused


def test_wrong_exit_contract_cannot_close_holding():
    a=account();a.enter(event(),SIGNAL,'.48','.50',AT)
    assert a.close('.60',END,'creator_exit',strike=781)=='wrong_contract'
    assert a.close('.60',END,'creator_exit',expiry='2026-10-02')=='wrong_contract'
    assert a.position


@pytest.mark.parametrize('changes,status',[(dict(ticker='QQQ'),'not_spy_call'),(dict(side='PUT'),'not_spy_call'),(dict(expiry='2026-09-30'),'invalid_contract')])
def test_wrong_ticker_side_date(changes,status):
    assert account().enter(event(),dict(SIGNAL,**changes),'.48','.50',AT)==status


def test_late_invalidated_entry_and_nonfinite_quotes():
    assert account().enter(event(),SIGNAL,'.48','.50',AT,776)=='invalidated_before_entry'
    assert account().enter(event(),SIGNAL,'.48','.50','2026-10-01T07:36:00-07:00')=='stale_or_future'
    with pytest.raises(ValueError):account().enter(event(),SIGNAL,'nan','.50',AT)


def test_toy_time_decay_and_bid_ask_cost():
    early=quote(770,785,330);late=quote(770,785,30)
    assert late[1]<early[1] and early[0]<early[1]
    assert quote(770,785,0)[0]==0


def test_real_parser_positive_and_missing_contract_recap_rejected():
    with offline():
        s=parse('BTO SPY 780 CALL exp 2026-10-01 target 775 stop 765')
        assert s['ticker']=='SPY' and s['side']=='CALL'
        assert account().enter(event(),s,'.48','.50',AT)=='entered'
        assert parse('SPY is at the second target') is None
        assert parse('BTO SPY 780 CALL') is None


@pytest.mark.parametrize('delay',[0,60,180])
def test_month_positive_entry_cash_flat_timing_and_creator_exits(delay):
    with offline():r=run(20261015,delay)
    assert r['trades'] and r['cash_error']==0 and r['real_orders']==0
    assert len(r['records'])==32
    for t in r['trades']:
        assert isinstance(t['contracts'],int) and t['contracts']>0
        assert t['entry_at'][:10]==t['exit_at'][:10]
        assert t['expiry']==t['entry_at'][:10]
    assert any(t['reason'] in ('target','stop','creator_exit') for t in r['trades'])


def test_crossing_both_milestones_and_winning_resets_streak():
    a=account(499);a.enter(event(),SIGNAL,'.48','.50',AT)
    a.close('.60',END,'target')
    assert a.half and a.fraction()==Decimal('.5')
    a.enter(event('two'),SIGNAL,'.48','.50',AT);a.close('2',END,'creator_exit')
    assert a.tenth and a.fraction()==Decimal('.1') and a.loss_streak==0


def test_retry_conflict_and_no_weekend_or_backdated_resume():
    a=account();a.enter(event(),SIGNAL,'.48','.50',AT)
    with pytest.raises(ValueError):a.enter(dict(event(),text='changed'),SIGNAL,'.48','.50',AT)
    with pytest.raises(ValueError):a.begin('2026-10-03')
    with pytest.raises(ValueError):a.begin('2026-09-30')
    with pytest.raises(ValueError):a.begin('2026-10-02')


def test_creator_caption_premiums_as_synthetic_positive_case():
    # User screenshot claims .27 ->1.07; contract/timestamps absent.
    # These test quotes are invented, not historical creator fills.
    a=account();a.enter(event(),SIGNAL,'.25','.27',AT)
    assert a.position['qty']==3
    r=a.close('1.07',END,'creator_exit')
    assert r['net_pnl']==230.1 and a.balance==Decimal('330.10')


def test_same_cheap_contract_can_lose_nearly_whole_account():
    a=account();a.enter(event(),SIGNAL,'.25','.27',AT)
    r=a.close('.01',END,'stop')
    assert r['net_pnl']==-84.9 and a.balance==Decimal('15.10') and a.paused
