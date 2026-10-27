"""Synthetic mutations of visible screenshot themes; no historical fills."""
import pytest
from lavish_core.validation.options_creator_month import offline, parse

CONTRACTS = [('SPY', 780), ('NVDA', 235), ('AMD', 600), ('HOOD', 170), ('MSTR', 347.5), ('GOOGL', 350)]
CONSTRAINTS = [
    'Wait for confirmation.',
    'Waiting for market confirmation.',
    'This is not an automatic entry.',
    'Entry requires market confirmation.',
    'Reclaim and hold above the zone before entry.',
    'Do not buy this contract.',
    'Cancel this setup.',
    'Take 80% profits and ride the rest.',
    'This trade is at the second target.',
    'Whales are still in and still buying.',
]

@pytest.mark.parametrize('ticker,strike', CONTRACTS)
@pytest.mark.parametrize('prefix', ['BTO ', 'BUY ', 'buying '])
@pytest.mark.parametrize('separator', [' ', '\n', '  '])
def test_positive_contract_identity(ticker, strike, prefix, separator):
    text = f'{prefix}{ticker} {strike} CALL exp 2026-11-20 target {strike+5} stop {strike-2}'
    with offline():
        signal = parse(text.replace(' ', separator))
    assert signal
    assert (signal['ticker'], signal['side'], signal['expiry'], float(signal['strike'])) == (ticker, 'CALL', '2026-11-20', strike)

@pytest.mark.parametrize('ticker,strike', CONTRACTS)
@pytest.mark.parametrize('constraint', CONSTRAINTS)
@pytest.mark.parametrize('separator', [' ', '\n', '  '])
def test_context_cannot_become_entry(ticker, strike, constraint, separator):
    text = f'{ticker} {strike} CALL exp 2026-11-20 target {strike+5} stop {strike-2}. {constraint}'
    with offline():
        assert parse(text.replace(' ', separator)) is None

@pytest.mark.parametrize('text', [
    'Who has the heart to take this call with me?',
    'Cheap but risky swing trade available. Whales are loading up on Intel.',
    'MSTR could print next week.',
    'PPI and Retail Sales reports will be released tomorrow. If you held the SPY trade overnight.',
    'Our AMD banger went from $6.50 a contract to $25.75 today.',
    'Netflix reclaiming my level perfectly.',
    'MU briefly pushed above $1,110 today but immediately reversed, so there was no valid call.',
    'All trades stop on the 21st of each month. $250 per month membership.',
])
def test_visible_caption_excerpts_without_invented_contract(text):
    with offline():
        assert parse(text) is None

@pytest.mark.parametrize('ticker,strike', CONTRACTS)
@pytest.mark.parametrize('prefix', ['', 'BTO '])
@pytest.mark.parametrize('suffix', [
    'Our banger went from $6.50 a contract to $25.75 today.',
    'Contracts went from $0.27 to $1.07.',
    'There was no valid call because it immediately reversed.',
    'There was no valid entry because the level failed.',
])
def test_complete_contract_cannot_convert_recap_to_fresh_buy(ticker, strike, prefix, suffix):
    with offline():
        assert parse(f'{prefix}{ticker} {strike} CALL exp 2026-11-20. {suffix}') is None
