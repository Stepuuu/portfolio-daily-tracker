"""Bare codes → ledger tickers: the prefix table, ambiguous short codes, HK form, US exchange."""
import pytest

from core.importers import aliases, codes
from core.ledger import LedgerError


@pytest.mark.parametrize('code, ticker, kind', [
    ('600519', 'SHA:600519', 'stock'), ('688981', 'SHA:688981', 'stock'), ('510300', 'SHA:510300', 'etf'),
    ('501018', 'SHA:501018', 'fund'), ('563300', 'SHA:563300', 'etf'), ('588000', 'SHA:588000', 'etf'),
    ('113050', 'SHA:113050', 'bond'), ('000001', 'SHE:000001', 'stock'), ('300750', 'SHE:300750', 'stock'),
    ('159915', 'SHE:159915', 'etf'), ('161725', 'SHE:161725', 'fund'), ('180101', 'SHE:180101', 'fund'),
    ('123100', 'SHE:123100', 'bond'),
])
def test_a_share_prefix_table(code, ticker, kind):
    result = codes.resolve(code)
    assert (result.status, result.ticker, result.kind, result.currency) == ('ok', ticker, kind, 'CNY')


@pytest.mark.parametrize('code, status, words', [
    ('204001', 'repo', None), ('131810', 'repo', None),
    ('832000', 'unsupported', '北交所'), ('430047', 'unsupported', '北交所'), ('920001', 'unsupported', '北交所'),
    ('900901', 'unsupported', 'B 股'), ('200002', 'unsupported', 'B 股'),
    ('787001', 'review', '申购'), ('732999', 'review', '申购'), ('990001', 'review', '无法从代码'),
])
def test_special_codes(code, status, words):
    result = codes.resolve(code)
    assert result.status == status and result.ticker is None
    assert words is None or words in result.message


def test_qualified_and_suffixed_forms():
    assert codes.resolve('SHA:600519').ticker == 'SHA:600519'
    assert codes.resolve('600519.SH').ticker == 'SHA:600519'
    assert codes.resolve('600519.SS').ticker == 'SHA:600519'
    assert codes.resolve('SZ000001').ticker == 'SHE:000001'
    assert codes.resolve('00700.HK').ticker == 'HKG:00700'
    assert codes.resolve('BJ832000').status == 'unsupported'
    assert codes.resolve('AAPL.O').ticker == 'NASDAQ:AAPL'


def test_hong_kong_form_reuses_the_ledger_form():
    hint = codes.market_hint(action='港股通买入')
    assert codes.resolve('00700', hint=hint).ticker == 'HKG:00700'
    assert codes.resolve('700', hint=hint).ticker == 'HKG:00700'
    assert codes.resolve('00700', hint=hint, known=['HKG:0700']).ticker == 'HKG:0700'
    assert codes.resolve('HKG:00700', known=['HKG:0700']).ticker == 'HKG:0700'
    assert codes.resolve('700').ticker == 'HKG:00700', 'no A-share evidence: short codes are HK'
    assert codes.resolve('700').note


def test_short_codes_in_an_a_share_file_are_not_guessed():
    stripped = codes.resolve('1', numeric_cell=True)
    assert stripped.status == 'review' and stripped.choices == ['SHE:000001', 'HKG:00001']
    assert codes.resolve('1', file_has_a_codes=True).status == 'review'
    padded = codes.resolve('1', hint=codes.market_hint(market='深圳A股'))
    assert padded.ticker == 'SHE:000001' and '000001' in padded.note
    assert codes.resolve('1', hint=codes.market_hint(shareholder='0000000001')).ticker == 'SHE:000001'
    assert codes.resolve('1', short_code_market='A', numeric_cell=True).ticker == 'SHE:000001'
    assert codes.resolve('1', short_code_market='HKG', numeric_cell=True).ticker == 'HKG:00001'


def test_market_hints():
    assert codes.market_hint(market='深圳A股') == 'SHE'
    assert codes.market_hint(market='SHENZHEN') == 'SHE', '"SHENZHEN" contains "SH"'
    assert codes.market_hint(market='上海A股') == 'SHA'
    assert codes.market_hint(market='沪港通') == 'HKG'
    assert codes.market_hint(currency='港币') == 'HKG'
    assert codes.market_hint(market='北京') == 'BSE'
    assert codes.market_hint(shareholder='A000000001') == 'SHA'
    assert codes.market_hint() is None


def test_exchange_conflict_needs_review():
    result = codes.resolve('600519', hint='SHE')
    assert result.status == 'review' and '上交所' in result.message and result.choices == ['SHA:600519']
    assert codes.resolve('600519', hint='HKG').status == 'review'


def test_us_symbols_need_an_exchange_once():
    first = codes.resolve('EXAMPLE')
    assert first.status == 'review' and first.choices == ['NASDAQ:EXAMPLE', 'NYSE:EXAMPLE']
    assert codes.resolve('EXAMPLE', known=['NYSE:EXAMPLE']).ticker == 'NYSE:EXAMPLE'
    assert codes.resolve('EXAMPLE', aliases={'US:EXAMPLE': 'NASDAQ:EXAMPLE'}).ticker == 'NASDAQ:EXAMPLE'
    assert codes.resolve('').status == 'review'
    assert codes.resolve('贵州茅台').status == 'review'


def test_chosen_tickers_are_checked():
    assert codes.check_ticker('sha:600519') == 'SHA:600519'
    assert codes.check_ticker('HKG:00700') == 'HKG:00700'
    for bad in ('SHA:787001', 'SHE:600519', 'SHA:60051', 'BSE:832000', 'NASDAQ'):
        with pytest.raises(LedgerError):
            codes.check_ticker(bad)


def test_aliases_are_remembered_in_the_desk_store(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    assert aliases.lookup() == {}
    aliases.remember('US:EXAMPLE', 'NYSE:EXAMPLE', 'Example Corp')
    aliases.remember('US:EXAMPLE', 'NASDAQ:EXAMPLE')
    assert aliases.lookup(['US:EXAMPLE', 'US:OTHER']) == {'US:EXAMPLE': 'NASDAQ:EXAMPLE'}
    assert (tmp_path / 'desk.sqlite3').is_file()


def test_an_explicit_exchange_in_the_file_wins_over_remembered_tickers():
    # Review finding 9: ABC.N (NYSE) became NASDAQ:ABC because the ledger already had NASDAQ:ABC.
    conflict = codes.resolve('ABC.N', known=['NASDAQ:ABC'])
    assert conflict.status == 'review' and conflict.ticker is None
    assert 'NYSE' in conflict.message and 'NASDAQ:ABC' in conflict.message and conflict.choices[0] == 'NYSE:ABC'
    assert codes.resolve('ABC.N', aliases={'US:ABC': 'NASDAQ:ABC'}).status == 'review'
    assert codes.resolve('ABC.N').ticker == 'NYSE:ABC' and codes.resolve('ABC.O', known=['NASDAQ:ABC']).ticker == 'NASDAQ:ABC'
    assert codes.resolve('NYSE:ABC', known=['NASDAQ:ABC']).status == 'review'
    nyse = codes.market_hint(market='纽约证券交易所')
    assert nyse == 'NYSE' and codes.market_hint(market='NASDAQ') == 'NASDAQ'
    assert codes.resolve('ABC', hint=nyse).ticker == 'NYSE:ABC'
    assert codes.resolve('ABC', hint=nyse, known=['NASDAQ:ABC']).status == 'review'
    assert codes.resolve('ABC.O', hint=nyse).status == 'review', 'suffix and market column disagree'


def test_an_explicit_exchange_conflict_reaches_the_row(tmp_path, monkeypatch):
    from tests.importer_support import OPENING, csv, ledger_at, open_account, run
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    ledger = ledger_at(tmp_path)
    open_account(ledger, cash_balances={'CNY': '500000', 'USD': '10000'},
                 positions=OPENING['positions'] + [{'ticker': 'NASDAQ:ABC', 'quantity': '10', 'cost_price': '5'}])
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '币种']
    _c, _v, rows = run(csv(header, ['2026-11-03', 'ABC.N', 'ABC Corp', '买入', 10, '5.00', '50.00', '1.00', '美元']),
                       'exchange.csv')
    assert rows[2]['status'] == 'review' and rows[2]['choices']['ticker'][0] == 'NYSE:ABC'


def test_qualified_and_suffixed_codes_are_validated_like_bare_ones():
    # Review round 2, finding 6: SHA:000001 was accepted as a Shanghai stock; 000001.SZ with market 上海 passed.
    shanghai, shenzhen = codes.market_hint(market='上海A股'), codes.market_hint(market='深圳A股')
    connect = codes.market_hint(action='港股通买入')
    for code, hint in (('SHA:000001', None), ('SHE:600519', None), ('SHA:60051', None), ('SHA:6005190', None),
                       ('000001.SZ', shanghai), ('SZ000001', shanghai), ('SHE:000001', shanghai),
                       ('600519.SH', shenzhen), ('SHA:600519', shenzhen), ('00700.HK', shanghai),
                       ('HKG:00700', shanghai), ('600519.SH', connect), ('SHA:600519', connect),
                       ('HKG:ABC', None), ('600519.HK', None), ('NASDAQ:600519', None), ('NYSE:ABC', shanghai)):
        result = codes.resolve(code, hint=hint)
        assert result.status == 'review' and result.ticker is None and result.message, (code, hint, result)
    assert codes.resolve('SHA:600519', hint=shanghai).ticker == 'SHA:600519'
    assert codes.resolve('000001.SZ', hint=shenzhen).ticker == 'SHE:000001'
    assert codes.resolve('SHE:000001').ticker == 'SHE:000001' and codes.resolve('HKG:700').ticker == 'HKG:00700'
    assert codes.resolve('00700.HK', hint=connect).ticker == 'HKG:00700'
    assert codes.resolve('NYSE:ABC').ticker == 'NYSE:ABC'
    assert codes.resolve('SHA:204001').status == 'repo'


def test_a_qualified_code_of_the_wrong_exchange_reaches_the_row(tmp_path, monkeypatch):
    from tests.importer_support import csv, ledger_at, open_account, run
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    open_account(ledger_at(tmp_path))
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '交易市场']
    _c, view, rows = run(csv(header, ['2026-11-03', 'SHA:000001', '平安银行', '证券买入', 100, '10.00', '1000.00', '5.00', ''],
                             ['2026-11-03', '000001.SZ', '平安银行', '证券买入', 100, '10.00', '1000.00', '5.00', '上海'],
                             ['2026-11-03', '000001.SZ', '平安银行', '证券买入', 100, '10.00', '1000.00', '5.00', '深圳']),
                         'qualified.csv')
    assert [rows[n]['status'] for n in (2, 3, 4)] == ['review', 'review', 'new']
    assert 'ticker' in rows[2]['needs'] and rows[4]['events'][0]['ticker'] == 'SHE:000001'
