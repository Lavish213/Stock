"""Screenshot-derived conditional setups never become immediate orders."""
from unittest.mock import Mock
import pytest
from lavish_core.trading import alert_handler as ah

@pytest.mark.parametrize('ticker,strike,condition', [
    ('NVDA',235,'Entry trigger: Break $230 and hold above it.'),
    ('NVDA',235,'Break $230 and hold above it.'),
    ('GOOGL',350,'Break and hold above $346.'),
    ('MRVL',280,'Reclaim and hold $280.'),
])
@pytest.mark.parametrize('surface', ['original','lower','upper','newlines'])
def test_conditional_card_requires_review(ticker,strike,condition,surface,monkeypatch):
    from datetime import date,timedelta
    expiry=(date.today()+timedelta(days=14)).isoformat()
    text=f'BTO {ticker} {strike} CALL exp {expiry}. {condition}'
    text={'original':text,'lower':text.lower(),'upper':text.upper(),'newlines':text.replace(' ','\n')}[surface]
    execute=Mock()
    monkeypatch.setattr(ah,'execute_trade_from_post',execute)
    ah.handle_alert_text(text)
    execute.assert_not_called()

def test_unconditional_entry_is_preserved(monkeypatch):
    execute=Mock()
    monkeypatch.setattr(ah,'execute_trade_from_post',execute)
    ah.handle_alert_text('BUY NVDA now target 233 stop 226.50')
    assert execute.call_count==1
    assert execute.call_args.args[0]['target_hint']==233
    assert execute.call_args.args[0]['stop_hint']==226.5
