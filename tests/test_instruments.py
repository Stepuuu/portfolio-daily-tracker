"""Tickers are resolved from the exchange prefix, never from a bare code."""
import pytest

from core import ledger
from core.market import instruments
from core.market.instruments import InstrumentError, eastmoney_secid, parse, yahoo_symbol


def test_shenzhen_000001_is_the_bank_not_the_shanghai_index():
    # Regression: the older providers mapped the bare code 000001 to the
    # Shanghai Composite, so a SHE:000001 holding was priced as an index.
    bank, index = parse('SHE:000001'), parse('IDX:000001')
    assert (bank.kind, bank.eastmoney_secid, bank.yahoo_symbol) == ('equity', '0.000001', '000001.SZ')
    assert (index.kind, index.eastmoney_secid, index.yahoo_symbol) == ('index', '1.000001', '000001.SS')
    assert bank.eastmoney_secids != index.eastmoney_secids


@pytest.mark.parametrize('ticker', ['000001', '600519', 'AAPL', ':000001', 'SHE:', 'SHE 000001', '', None, 1])
def test_a_ticker_without_an_exchange_prefix_is_rejected(ticker):
    with pytest.raises(InstrumentError):
        parse(ticker)


def test_unknown_exchanges_and_unlisted_indices_are_rejected_not_guessed():
    for ticker in ['LSE:VOD', 'IDX:999999', 'IDX:AAPL']:
        with pytest.raises(InstrumentError):
            parse(ticker)


@pytest.mark.parametrize('ticker,currency,market,secids,yahoo', [
    ('SHA:600519', 'CNY', 'CN', ('1.600519',), '600519.SS'),
    ('SHA:510300', 'CNY', 'CN', ('1.510300',), '510300.SS'),
    ('SHE:300750', 'CNY', 'CN', ('0.300750',), '300750.SZ'),
    ('SHE:600519', 'CNY', 'CN', ('0.600519',), '600519.SZ'),      # the prefix decides, not the leading digit
    ('HKG:700', 'HKD', 'HK', ('116.00700',), '0700.HK'),
    ('HKG:00700', 'HKD', 'HK', ('116.00700',), '0700.HK'),
    ('HKG:09988', 'HKD', 'HK', ('116.09988',), '9988.HK'),
    ('NASDAQ:AAPL', 'USD', 'US', ('105.AAPL',), 'AAPL'),
    ('NYSE:BRK.B', 'USD', 'US', ('106.BRK_B', '107.BRK_B'), 'BRK-B'),
    ('NYSE:SPY', 'USD', 'US', ('106.SPY', '107.SPY'), 'SPY'),
    ('IDX:399001', 'CNY', 'CN', ('0.399001',), '399001.SZ'),
    ('IDX:HSI', 'HKD', 'HK', ('100.HSI',), '^HSI'),
])
def test_symbols_for_each_source(ticker, currency, market, secids, yahoo):
    instrument = parse(ticker)
    assert (instrument.currency, instrument.market) == (currency, market)
    assert instrument.eastmoney_secids == secids
    assert eastmoney_secid(ticker) == secids[0]
    assert yahoo_symbol(ticker) == yahoo


def test_parsing_normalises_case_and_whitespace():
    assert parse(' she:000001 ').ticker == 'SHE:000001'
    assert parse(parse('NASDAQ:AAPL')) == parse('nasdaq:aapl')


@pytest.mark.parametrize('ticker', ['SHA:DEMO', 'SHE:1234', 'HKG:TENCENT', 'HKG:123456'])
def test_codes_that_cannot_exist_on_the_exchange_have_no_source_symbol(ticker):
    # The ledger accepts them, so they parse; a source must then report them
    # as unsupported instead of sending a guessed symbol.
    instrument = parse(ticker)
    assert instrument.eastmoney_secids == ()
    assert instrument.eastmoney_secid is None
    assert instrument.yahoo_symbol is None


@pytest.mark.parametrize('ticker', ['SHA:000001', 'SHA:000300', 'SHA:000905', 'SHE:399001', 'SHE:399006',
                                    'SHE:395001'])
def test_an_equity_ticker_in_an_index_range_is_never_mapped_to_the_index(ticker):
    # Regression: SHA:000001 was sent to both providers as 1.000001 / 000001.SS,
    # which is the Shanghai Composite, and 100 "shares" were valued at 3595.22.
    instrument = parse(ticker)
    assert instrument.kind == 'equity'
    assert instrument.eastmoney_secids == () and instrument.eastmoney_secid is None
    assert instrument.yahoo_symbol is None
    assert '指数' in instrument.symbol_problem and '核对' in instrument.symbol_problem


def test_only_the_idx_prefix_reaches_an_index_and_neighbouring_codes_still_map():
    assert parse('IDX:000001').symbol_problem is None and parse('IDX:000001').eastmoney_secid == '1.000001'
    assert 'IDX:000001' in parse('SHA:000001').symbol_problem       # the hint names the index ticker
    for ticker, secid in [('SHE:000001', '0.000001'), ('SHA:600000', '1.600000'), ('SHA:010107', '1.010107'),
                          ('SHE:300750', '0.300750'), ('SHE:159915', '0.159915')]:
        assert parse(ticker).symbol_problem is None and parse(ticker).eastmoney_secid == secid


def test_hong_kong_codes_with_or_without_leading_zeros_are_one_instrument_for_quoting():
    spellings = [parse(text) for text in ('HKG:700', 'HKG:0700', 'HKG:00700', ' hkg:00700 ')]
    assert {instrument.quote_key for instrument in spellings} == {'HKG:00700'}
    assert {instrument.eastmoney_secids for instrument in spellings} == {('116.00700',)}
    assert {instrument.yahoo_symbol for instrument in spellings} == {'0700.HK'}
    # What the ledger stores is not rewritten.
    assert [instrument.ticker for instrument in spellings] == ['HKG:700', 'HKG:0700', 'HKG:00700', 'HKG:00700']
    assert parse('HKG:1').quote_key == parse('HKG:00001').quote_key == 'HKG:00001'
    assert parse('SHE:000001').quote_key == 'SHE:000001' and parse('NYSE:BRK.B').quote_key == 'NYSE:BRK.B'


@pytest.mark.parametrize('ticker', ['HKG:0', 'HKG:00000'])
def test_a_hong_kong_code_of_zero_is_not_a_listing(ticker):
    instrument = parse(ticker)
    assert instrument.eastmoney_secids == () and instrument.yahoo_symbol is None


def test_exchanges_and_currencies_match_the_ledger():
    assert {exchange: currency for exchange, (currency, _) in instruments.EXCHANGES.items()} == ledger.MARKETS
    for exchange in ledger.MARKETS:
        assert parse(ledger.instrument(f'{exchange}:TEST1')).exchange == exchange


@pytest.mark.parametrize('ticker,currency,secid,yahoo', [
    ('SHA:900901', 'USD', '1.900901', '900901.SS'),      # Shanghai B shares trade in US dollars
    ('SHE:200596', 'HKD', '0.200596', '200596.SZ'),      # Shenzhen B shares in Hong Kong dollars
    ('SHE:201872', 'HKD', '0.201872', '201872.SZ'),
])
def test_b_shares_carry_their_trading_currency_and_mainland_calendar(ticker, currency, secid, yahoo):
    # Probed on 2026-11-17: Eastmoney prices 1.900901 at 0.498 and Yahoo reports
    # 900901.SS in USD and 200596.SZ in HKD. The ledger books every SHA/SHE
    # holding in CNY, which is why ``ledger_currency_problem`` exists.
    instrument = parse(ticker)
    assert (instrument.currency, instrument.market, instrument.kind) == (currency, 'CN', 'equity')
    assert instrument.eastmoney_secids == (secid,) and instrument.yahoo_symbol == yahoo
    assert instrument.symbol_problem is None
    assert ('B 股' in instrument.ledger_currency_problem and currency in instrument.ledger_currency_problem
            and 'CNY' in instrument.ledger_currency_problem)


@pytest.mark.parametrize('ticker', ['SHA:600000', 'SHE:000596', 'SHE:300750', 'SHA:510300', 'HKG:700', 'NASDAQ:AAPL',
                                    'SHE:900901', 'SHA:200596', 'IDX:000001'])
def test_only_b_shares_differ_from_the_ledger_currency(ticker):
    instrument = parse(ticker)
    assert instrument.ledger_currency_problem is None
    if instrument.kind == 'equity':
        assert instrument.currency == ledger.MARKETS[instrument.exchange]


def test_b_share_ranges_agree_with_the_importer_rule():
    from core.importers import codes
    ranges = {prefix for prefixes in instruments.B_SHARE_PREFIXES.values() for prefix in prefixes}
    assert ranges == set(codes.B_SHARE_PREFIXES)
    assert codes.a_share('900901').status == codes.a_share('200596').status == 'unsupported'
