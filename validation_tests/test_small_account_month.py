from datetime import datetime
from decimal import Decimal,ROUND_DOWN
import pytest
from lavish_core.validation.small_account_month import Account,offline,parser,run_month


def event(identity='entry'):
    return dict(id=identity,created_at='2026-10-01T10:00:00-07:00',text='BUY SPY now',synthetic=True)


SIGNAL={'symbol':'SPY','action':'BUY'}


def test_all_available_fractional_and_cost_reserve():
    a=Account();assert a.enter(event(),SIGNAL,770,market_open=True)=='entered'
    assert 0<a.position['qty']<1 and a.position['allocation']==100
    assert a.cash>=a.fee
    a.close(770,'2026-10-01T11:00:00-07:00','day_close')
    assert a.cash<100 and a.halted and not a.relearn['automatic_resume']


def test_threshold_latches_ten_percent_after_crossing():
    a=Account(cash=999)
    a.enter(event(),SIGNAL,100,market_open=True)
    a.close(101,'2026-10-01T11:00:00-07:00','target')
    assert a.ten_percent and a.cash>=1000
    balance=a.cash
    assert a.enter(event('next'),SIGNAL,100,market_open=True)=='entered'
    assert a.position['allocation']==(balance*Decimal('.1')).quantize(Decimal('.01'),rounding=ROUND_DOWN)


def test_halt_cannot_be_bypassed_by_new_day_or_threshold():
    a=Account(cash=1000);a.enter(event(),SIGNAL,770,market_open=True)
    a.close(760,'2026-10-01T11:00:00-07:00','stop')
    assert a.halted and a.ten_percent and a.cash<1000
    assert a.enter(event('new-day'),SIGNAL,770,market_open=True)=='halted'


def test_retry_does_not_double_spend_and_conflict_rejected():
    a=Account();a.enter(event(),SIGNAL,770,market_open=True);cash=a.cash
    assert a.enter(event(),SIGNAL,770,market_open=True)=='duplicate'
    assert a.cash==cash
    with pytest.raises(ValueError):a.enter(dict(event(),text='changed'),SIGNAL,770,market_open=True)


@pytest.mark.parametrize('signal,open_session',[({'symbol':'AAPL','action':'BUY'},True),(SIGNAL,False),(None,True),({'symbol':'SPY','action':'SELL'},True)])
def test_no_unapproved_entry(signal,open_session):
    a=Account();a.enter(event(),signal,770,market_open=open_session)
    assert a.cash==100 and a.position is None


def test_production_parser_positive_and_negative():
    with offline():
        signal=parser('BUY SPY now target 775 stop 760')
        assert signal['symbol']=='SPY'
        assert Account().enter(event(),signal,770,market_open=True)=='entered'
        assert parser('SPY is at the second target') is None
        assert parser('SPY setup break and hold above770') is None


@pytest.mark.parametrize('seed',range(12))
def test_month_halts_after_first_net_loss_and_remains_flat(seed):
    with offline():r=run_month(seed)
    assert r['alert_days']==32 and r['duplicate_attempts']==32 and r['cash_error']==0
    assert r['final_position']==0 and r['real_broker_orders']==0
    assert r['trades'], 'Blanket rejection is not a successful simulation'
    assert r['records'][0]['date']=='2026-10-01' and r['records'][-1]['date']=='2026-11-01'
    losing=[i for i,t in enumerate(r['trades']) if t['net_pnl']<0]
    if losing:assert losing==[len(r['trades'])-1] and r['relearn_request']
    for t in r['trades']:
        assert datetime.fromisoformat(t['entry_at']).date()==datetime.fromisoformat(t['exit_at']).date()
        assert datetime.fromisoformat(t['exit_at']).hour<=13
    assert not any(row['status']=='entered' for i,row in enumerate(r['records']) if any(x['halted'] for x in r['records'][:i]))

