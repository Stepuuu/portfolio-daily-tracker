"""The file's own balances against the ledger after the import: 一致 or 差额 with causes."""
from decimal import Decimal

import pytest

from core.importers import session
from tests.importer_support import ACCOUNT, book, csv, fixture, ledger_at, open_account, patch, run


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    ledger = ledger_at(tmp_path)
    open_account(ledger)
    return ledger


@pytest.mark.parametrize('name', ['ths_trades_gbk.xls', 'tdx_trades_gbk.txt', 'eastmoney_trades_html.xls',
                                  'hk_connect_bom.csv'])
def test_clean_exports_reconcile(ledger, name):
    created, view, _rows = run(fixture(name), name)
    result = view['reconciliation']
    assert result['available'] and result['consistent'], result
    assert all(item['status'] == '一致' for item in result['cash'] + result['positions'])
    assert result['causes'] == [] and '一致' in result['summary']
    ledger.confirm(session.propose(created['id'])['proposal']['id'])
    after = session.view(session.load(created['id']))['reconciliation']
    assert after['consistent'] and after['cash'] == result['cash'], 'the same after confirmation'


def test_sub_cent_tail_of_an_average_price_still_agrees(ledger):
    _created, view, _rows = run(fixture('ths_trades_gbk.xls'), 'jgd.xls')
    [cash] = view['reconciliation']['cash']
    assert cash['file'] == '418475.8' and abs(Decimal(cash['difference'])) < Decimal('0.0001')
    assert '418,475.80' in view['reconciliation']['summary']
    shares = {p['ticker']: (p['file'], p['ledger']) for p in view['reconciliation']['positions']}
    assert shares == {'SHA:600036': ('1300', '1300'), 'SHA:600519': ('50', '50'), 'SHE:000001': ('1000', '1000')}


def test_difference_lists_the_rows_that_explain_it(ledger):
    created, view, _rows = run(fixture('eastmoney_statement_bom.csv'), 'dzd.csv')
    result = view['reconciliation']
    [cash] = result['cash']
    assert cash['status'] == '差额' and cash['difference'] == '16004.5' and cash['label'] == '差 CNY 16,004.50'
    assert any('需处理 3 行没有入账' in c for c in result['causes'])
    assert any('不支持 1 行' in c for c in result['causes'])
    assert any('正好等于' in c for c in result['causes'])
    ipo = next(p for p in result['positions'] if p['ticker'] == 'SHA:603999')
    assert (ipo['status'], ipo['label']) == ('差额', '差 -500 股')
    assert any('SHA:603999 有 1 行没有入账' in c for c in result['causes'])
    view, _rows = patch(created['id'], rows=[{'row': 10, 'kind': 'buy', 'price': '10.00', 'fee': '0'},
                                             {'row': 9, 'kind': 'skip'}])
    result = view['reconciliation']
    assert result['cash'][0]['difference'] == '11004.5', 'the IPO now moves cash in the ledger too'
    assert next(p for p in result['positions'] if p['ticker'] == 'SHA:603999')['status'] == '一致'


def test_later_ledger_events_do_not_disturb_the_comparison(ledger):
    book(ledger, [{'kind': 'deposit', 'date': '2026-11-20', 'account': ACCOUNT, 'currency': 'CNY', 'amount': '1'}])
    _created, view, _rows = run(fixture('tdx_trades_gbk.txt'), 'tdx.txt')
    result = view['reconciliation']
    assert result['consistent'], 'balances are compared as of the file’s last date'


def test_without_balance_columns_or_opening_there_is_no_reconciliation(ledger):
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '发生金额']
    _c, view, _rows = run(csv(header, ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00',
                                       '-168043.68']), 'a.csv')
    assert view['reconciliation'] == {'available': False, 'reason': '文件没有资金余额或股份余额列，无法对账。'}
    _c, view, _rows = run(fixture('ths_trades_gbk.xls'), 'b.xls', account='另一个账户')
    assert view['reconciliation']['available'] is False and '期初' in view['reconciliation']['reason']


def test_wrong_opening_cash_shows_a_difference(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    ledger = ledger_at(tmp_path)
    open_account(ledger, cash_balances={'CNY': '499988.00'})
    _c, view, _rows = run(fixture('tdx_trades_gbk.txt'), 'tdx.txt')
    [cash] = view['reconciliation']['cash']
    assert cash['difference'] == '-12' and cash['label'] == '差 CNY -12.00'
    assert view['reconciliation']['causes'][-1].startswith('也可能是期初现金')


def test_rows_interleaved_with_confirmed_events_are_checked_against_the_full_ledger(ledger):
    book(ledger, [{'kind': 'sell', 'date': '2026-11-04', 'account': ACCOUNT, 'ticker': 'SHE:000001', 'currency': 'CNY',
                   'quantity': '1500', 'price': '11'}])
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '发生金额', '资金余额']
    created, view, rows = run(csv(header,
                                  ['2026-11-03', '000001', '平安银行', '证券买入', 1000, '11.00', '11000.00', '5.00', '-11005.00',
                                   '488995.00'],
                                  ['2026-11-10', '000001', '平安银行', '证券卖出', 1700, '11.50', '19550.00', '5.00',
                                   '19545.00', '525040.00']), 'mixed.csv')
    assert rows[2]['status'] == 'new'
    assert rows[3]['status'] == 'review' and '只持有' not in rows[3]['messages'][0] and '更晚' in rows[3]['messages'][0]
    [cash] = view['reconciliation']['cash']
    assert view['reconciliation']['as_of'] == '2026-11-10'
    assert cash['ledger'] == '505495', 'as of the file’s last date, including the confirmed sale in between'
    ledger.confirm(session.propose(created['id'])['proposal']['id'])
    assert ledger.state()['accounts'][ACCOUNT]['positions']['SHE:000001']['quantity'] == '1500'


def test_reconciliation_stays_as_of_the_files_last_date_after_confirmation(ledger):
    # Review finding 15: after confirming, a later deposit of 1,000 showed up as a difference.
    book(ledger, [{'kind': 'deposit', 'date': '2026-11-20', 'account': ACCOUNT, 'currency': 'CNY', 'amount': '1000'}])
    created, view, _rows = run(fixture('tdx_trades_gbk.txt'), 'tdx.txt')
    before = view['reconciliation']
    assert before['consistent'] and before['cash'][0]['ledger'] == '475170.36' and before['as_of'] == '2026-11-10'
    ledger.confirm(session.propose(created['id'])['proposal']['id'])
    after = session.view(session.load(created['id']))
    assert after['counts']['imported'] == 3
    result = after['reconciliation']
    assert result['as_of'] == '2026-11-10' and result['cash'] == before['cash'] and result['consistent']
    assert result['positions'] == before['positions']
