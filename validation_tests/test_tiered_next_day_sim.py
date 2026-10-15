from decimal import Decimal
import pytest
from lavish_core.validation.small_account_month import TieredAccount,Account,offline,run_month


def event(identity='entry'):
    return dict(id=identity,created_at='2026-10-01T10:00:00-07:00',text='BUY SPY now',synthetic=True)


SIGNAL={'symbol':'SPY','action':'BUY'}


@pytest.mark.parametrize('cash,fraction',[('100',1),('499.99',1),('500',.5),('999.99',.5),('1000',.1)])
def test_requested_tier_boundaries(cash,fraction):
    a=TieredAccount(cash=cash);a.begin_day('2026-10-01')
    assert a.fraction()==Decimal(str(fraction))
    assert a.enter(event(),SIGNAL,100,market_open=True)=='entered'
    assert a.position['allocation']<=Decimal(cash)*Decimal(str(fraction))


def test_ordinary_loss_continues_and_three_losses_pause():
    a=TieredAccount();a.begin_day('2026-10-01')
    for i in range(3):
        a.enter(event(str(i)),SIGNAL,100,market_open=True)
        a.close(99.5,'2026-10-01T11:00:00-07:00','stop')
        assert a.halted==(i==2)
    assert a.relearn['trigger']=='consecutive_net_losses'
    assert a.enter(event('blocked'),SIGNAL,100,market_open=True)=='halted'
    a.begin_day('2026-10-01')
    assert a.halted, 'Same-day begin cannot bypass pause'
    a.begin_day('2026-10-02')
    assert not a.halted and a.loss_streak==0
    assert a.enter(event('next-session'),SIGNAL,100,market_open=True)=='entered'


def test_big_loss_pauses_until_next_weekday_and_preserves_review():
    a=TieredAccount();a.begin_day('2026-10-02')
    a.enter(event(),SIGNAL,100,market_open=True);a.close(80,'2026-10-02T11:00:00-07:00','gap_stop')
    assert a.halted and a.relearn['trigger']=='big_loss'
    with pytest.raises(ValueError):a.begin_day('2026-10-03')
    with pytest.raises(ValueError):a.begin_day('2026-10-01')
    a.begin_day('2026-10-05')
    assert not a.halted and len(a.review_requests)==1


def test_winning_trade_resets_loss_streak():
    a=TieredAccount();a.begin_day('2026-10-01')
    for i,price in enumerate([99.5,101,99.5]):
        a.enter(event(str(i)),SIGNAL,100,market_open=True)
        a.close(price,'2026-10-01T11:00:00-07:00','test')
    assert a.loss_streak==1 and not a.halted


def test_milestone_reductions_remain_after_crossing():
    a=TieredAccount(cash=499);a.begin_day('2026-10-01')
    a.enter(event(),SIGNAL,100,market_open=True);a.close(101,'2026-10-01T11:00:00-07:00','target')
    assert a.half_reached and a.fraction()==Decimal('.5')
    a.enter(event('loss'),SIGNAL,100,market_open=True);a.close(90,'2026-10-01T12:00:00-07:00','stop')
    assert a.cash<500 and a.fraction()==Decimal('.5')


@pytest.mark.parametrize('seed',range(8))
def test_month_uses_identical_alerts_for_competing_rules(seed):
    with offline():
        original=run_month(seed,account_factory=Account)
        revised=run_month(seed,account_factory=TieredAccount)
    assert [r['alert'] for r in original['records']]==[r['alert'] for r in revised['records']]
    assert revised['trades'] and revised['final_position']==0 and revised['cash_error']==0
    assert revised['real_broker_orders']==0
    # Every overlapping trade has identical executable market prices.
    old={t['entry_id']:t for t in original['trades']}
    for t in revised['trades']:
        if t['entry_id'] in old:
            assert t['entry_price']==old[t['entry_id']]['entry_price']
            assert t['exit_price']==old[t['entry_id']]['exit_price']

