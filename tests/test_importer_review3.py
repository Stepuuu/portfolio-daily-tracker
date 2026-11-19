"""Review round 3 of the broker importer (analysis level).

Each section reproduces one finding of the third review (``(A)`` / ``(B)`` with the file and line it
names) and then checks the class behind it, so the fix cannot be a special case of the example.
"""
import io

import pytest

from core.importers import ImportFailure, codes, session
from tests.importer_support import ACCOUNT, csv, ledger_at, open_account, patch, run, xls_bytes


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    ledger = ledger_at(tmp_path)
    open_account(ledger)
    return ledger


def held(ledger, ticker):
    return ledger.state()['accounts'][ACCOUNT]['positions'].get(ticker, {}).get('quantity')


def confirm(ledger, session_id):
    return ledger.confirm(session.propose(session_id)['proposal']['id'])


def not_bookable(view, row):
    """The row cannot reach a proposal: it is not an included new row, or the import has a blocker."""
    return not (row['include'] and row['status'] == 'new') or bool(view['blockers'])


def workbook(build) -> bytes:
    import openpyxl
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = '对账单'
    build(sheet)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


TEMPLATE = ['external_id', 'date', 'kind', 'account', 'ticker', 'currency', 'quantity', 'price', 'fee', 'amount']


# -- (A) session.py:406,410; dedupe.py:119: duplicate detection sees split events and needs no 成交编号 ----------

SPLIT = ['发生日期', '业务名称', '证券代码', '证券名称', '成交数量', '发生金额', '成交编号']
BONUS = ['2026-11-03', '红股入账', '600036', '招商银行', '1000', '0.00', '1']


def test_a_split_imported_once_is_recognised_with_or_without_its_trade_id(ledger):
    created, _view, rows = run(csv(SPLIT, BONUS), 'split.csv')
    assert rows[2]['status'] == 'new' and rows[2]['events'][0]['ratio'] == '2'
    confirm(ledger, created['id'])
    assert held(ledger, 'SHA:600036') == '2000'
    _c, _view, rows = run(csv(SPLIT, BONUS), 'again.csv')
    assert rows[2]['status'] == 'imported'
    # An overlapping export without 成交编号: confirming it again would leave 3000 shares.
    _c, view, rows = run(csv(SPLIT[:-1], BONUS[:-1]), 'without_id.csv')
    assert rows[2]['status'] in ('imported', 'possible_duplicate') and not rows[2]['include'], rows[2]
    assert view['counts']['included_events'] == 0
    # The same 成交编号 with another quantity is not the booked split.
    _c, view, rows = run(csv(SPLIT, [*BONUS[:4], '2000', '0.00', '1']), 'changed.csv')
    assert rows[2]['status'] == 'changed' and not rows[2]['include'], rows[2]
    assert '数量' in rows[2]['messages'][0]
    assert held(ledger, 'SHA:600036') == '2000'


FILLS = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '发生金额', '成交编号']


def test_a_fill_reimported_without_its_trade_id_is_a_probable_duplicate_at_any_price(ledger):
    created, _v, _rows = run(csv(FILLS, ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00',
                                         '5.00', '-168005.00', 'T1']), 'first.csv')
    confirm(ledger, created['id'])
    # An overlapping export without 成交编号 that rounds the average price differently.
    _c, view, rows = run(csv(FILLS[:-1], ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.50', '168050.00',
                                          '5.00', '-168055.00']), 'overlap.csv')
    assert rows[2]['status'] == 'possible_duplicate' and not rows[2]['include'], rows[2]
    assert view['counts']['included_events'] == 0


# -- (A) accounts.py:119,176: a row without an account identity never passes a chosen identity --------------

def test_a_row_without_identity_never_passes_a_chosen_identity(ledger):
    header = ['发生日期', '业务名称', '证券代码', '证券名称', '发生金额', '股东代码']
    created, _v, rows = run(csv(header, ['2026-11-03', '红利入账', '600036', '招商银行', '100.00', 'A000000001'],
                                ['2026-11-04', '银行转证券', '', '', '200.00', '']), 'holder.csv')
    _view, rows = patch(created['id'], account_filter=['A000000001'])
    assert rows[2]['status'] == 'new'
    assert rows[3]['status'] == 'review' and 'account' in rows[3]['needs'], rows[3]
    _view, rows = patch(created['id'], account_filter=['A000000001', ''])
    assert rows[3]['status'] == 'new', 'accepted once rows without an identity are chosen as well'


# -- (A) mapping.py:388,352,391: direction and quantity units -------------------------------------------------

def test_a_blank_action_cannot_hide_an_explicit_one_in_another_column(ledger):
    header = ['成交日期', '证券代码', '证券名称', '业务名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '发生金额']
    created, view, rows = run(csv(header, ['2026-11-03', '600036', '招商银行', '', '卖出', 100, '10.00', '1000.00', '5.00',
                                           '-1005.00']), 'direction.csv')
    assert not_bookable(view, rows[2]), (rows[2], view['blockers'])
    with pytest.raises(ImportFailure):
        session.propose(created['id'])


def test_quantity_columns_in_different_units_are_compared_in_shares(ledger):
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量(股)', '成交数量(手)', '成交价格', '成交金额', '手续费']
    created, view, rows = run(csv(header, ['2026-11-03', '600036', '招商银行', '证券买入', 1, 1, '10.00', '10.00', '5.00']),
                              'units.csv')
    assert [c['field'] for c in view['column_conflicts']] == ['quantity'] and view['blockers'], view['column_conflicts']
    _c, view, rows = run(csv(header, ['2026-11-03', '600036', '招商银行', '证券买入', 100, 1, '10.00', '1000.00', '5.00']),
                         'units_agree.csv')
    assert view['column_conflicts'] == [] and rows[2]['status'] == 'new' and rows[2]['quantity'] == '100', rows[2]


# -- (A) codes.py:250,255,216; transactions.py:1083,1455: market evidence and the held spelling of a listing --

@pytest.mark.parametrize('code, market, remembered', [
    ('600036', 'NASDAQ', {}), ('600036', 'NYSE', {}), ('ABC.N', '上海', {}), ('ABC.O', '深圳', {}),
    ('ABC', '上海A股', {'US:ABC': 'NASDAQ:ABC'}), ('700', 'NYSE', {}), ('NYSE:ABC', '上海', {})])
def test_a_market_column_that_contradicts_the_code_needs_review(code, market, remembered):
    result = codes.resolve(code, hint=codes.market_hint(market=market), aliases=remembered)
    assert result.status == 'review', (code, market, result)


def test_a_trade_on_a_us_market_with_an_a_share_code_needs_review(ledger):
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '交易市场']
    _c, _view, rows = run(csv(header, ['2026-11-03', '600036', '招商银行', '证券买入', 100, '10.00', '1000.00', '5.00',
                                       'NASDAQ']), 'm.csv')
    assert rows[2]['status'] == 'review' and rows[2]['events'] == [], rows[2]


HK_OPENING = {'cash_balances': {'CNY': '500000.00', 'HKD': '100000.00'},
              'positions': [{'ticker': 'HKG:00700', 'name': '腾讯控股', 'quantity': '100', 'cost_price': '300'}]}


def test_template_rows_and_ticker_edits_reuse_the_held_spelling_of_a_listing(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    ledger = ledger_at(tmp_path)
    open_account(ledger, **HK_OPENING)
    template = ['external_id', 'date', 'kind', 'account', 'ticker', 'currency', 'quantity', 'price', 'fee']
    _c, _v, rows = run(csv(template, ['t-1', '2026-11-03', 'buy', ACCOUNT, 'HKG:700', 'HKD', 100, 300, 5]), 't.csv')
    assert rows[2]['status'] == 'new' and rows[2]['events'][0]['ticker'] == 'HKG:00700', rows[2]
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '币种']
    created, _v, rows = run(csv(header, ['2026-11-04', '799999', '示例', '买入', 100, '300.00', '30000.00', '5.00', '港币']),
                            'e.csv')
    assert rows[2]['status'] == 'review'
    _view, rows = patch(created['id'], rows=[{'row': 2, 'ticker': 'HKG:700'}])
    assert rows[2]['status'] == 'new' and rows[2]['events'][0]['ticker'] == 'HKG:00700', rows[2]


# -- (A) transactions.py:863,1062,1225,1338: every sign, rate and cash relation is verified --------------------

def test_a_sell_with_a_negative_turnover_and_no_cash_amount_needs_review(ledger):
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费']
    _c, _v, rows = run(csv(header, ['2026-11-03', '600036', '招商银行', '证券卖出', 100, '10.00', '-1000.00', '5.00']),
                       's.csv')
    assert rows[2]['status'] == 'review' and '成交金额' in rows[2]['messages'][0], rows[2]


def test_an_explicitly_invalid_settlement_rate_needs_review(ledger):
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '佣金', '发生金额', '结算汇率', '币种',
              '交易市场']
    _c, _v, rows = run(csv(header, ['2026-11-05', '00700', '腾讯控股', '港股通买入', 100, '10.00', '1000.00', '5.00',
                                    '-905.00', '-2', '人民币', '沪港通']), 'c.csv')
    assert rows[2]['status'] == 'review' and '汇率' in rows[2]['messages'][0], rows[2]


def test_a_split_with_an_unexplained_cash_movement_needs_review(ledger):
    header = ['发生日期', '业务名称', '证券代码', '证券名称', '成交数量', '发生金额']
    _c, _v, rows = run(csv(header, ['2026-11-05', '红股入账', '600036', '招商银行', 300, '100.00']), 'b.csv')
    assert rows[2]['status'] == 'review' and rows[2]['events'] == [], rows[2]


def test_repo_legs_whose_stated_direction_contradicts_their_cash_are_not_paired(ledger):
    header = ['发生日期', '业务名称', '证券代码', '证券名称', '发生金额']
    _c, view, rows = run(csv(header, ['2026-11-03', '到期购回', '204001', 'GC001', '-10000.00'],
                             ['2026-11-04', '逆回购借出', '204001', 'GC001', '10001.00']), 'r.csv')
    assert [rows[2]['status'], rows[3]['status']] == ['review', 'review'], (rows[2], rows[3])
    assert view['counts']['included_events'] == 0


# -- (A) mapping.py:161; transactions.py:1474; core/ledger.py:206: currency evidence and an explicit fee --------

@pytest.mark.parametrize('column', ['成交金额(NZD)', '发生金额(新西兰元)', '发生金额(CHF)'])
def test_a_currency_named_in_a_header_is_never_dropped(ledger, column):
    header = ['发生日期', '业务名称', '证券代码', '证券名称', column]
    _c, _v, rows = run(csv(header, ['2026-11-03', '红利入账', '600036', '招商银行', '100']), 'nzd.csv')
    assert rows[2]['status'] == 'review' and rows[2]['events'] == [], rows[2]


@pytest.mark.parametrize('cells, words', [
    (['t-1', '2026-11-03', 'deposit', ACCOUNT, '', 'CNY', '', '', '', 'US$100'], 'USD'),
    (['t-1', '2026-11-03', 'deposit', ACCOUNT, '', 'CNY', '', '', '', 'HK$100'], 'HKD'),
    (['t-1', '2026-11-03', 'deposit', ACCOUNT, '', '', '', '', '', '100'], '币种'),
    (['t-1', '2026-11-03', 'buy', ACCOUNT, 'SHA:600519', 'CNY', '100', '10', '', ''], '费用'),
])
def test_template_currency_and_fee_are_stated_not_assumed(ledger, cells, words):
    _c, _view, rows = run(csv(TEMPLATE, cells), 't.csv')
    assert rows[2]['status'] == 'review' and rows[2]['events'] == [] and words in ''.join(rows[2]['messages']), rows[2]


# -- (A) sniff.py:360,367: merged cells and formulas without a cached value -----------------------------------

def test_a_merged_cell_applies_to_every_row_it_spans(ledger):
    def build(sheet):
        sheet.append(['发生日期', '业务名称', '发生金额', '币种'])
        sheet.append(['2026-11-03', '利息归本', '10', 'USD'])
        sheet.append(['2026-11-04', '利息归本', '10', None])
        sheet.merge_cells('D2:D3')
    _c, _v, rows = run(workbook(build), 'merged.xlsx')
    for number in (2, 3):
        events = [(e['kind'], e['currency'], e['amount']) for e in rows[number]['events']]
        assert events == [('interest', 'USD', '10')], rows[number]


def test_a_formula_without_a_cached_value_is_never_read_as_empty(ledger):
    def build(sheet):
        sheet.append(['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '佣金', '过户费'])
        sheet.append(['2026-11-03', '600036', '招商银行', '证券买入', 100, 10, 1000, '=2+3', 1])
    _c, _v, rows = run(workbook(build), 'formula.xlsx')
    assert rows[2]['status'] == 'review' and rows[2]['events'] == [] and '佣金' in ''.join(rows[2]['messages']), rows[2]


# -- (A) values.py:64,77,94: numbers are read exactly or need review --------------------------------------------

def test_a_workbook_number_is_read_exactly(ledger):
    def build(sheet):
        sheet.append(['发生日期', '业务名称', '发生金额'])
        sheet.append(['2026-11-03', '利息归本', 123456789012345.5])
    for data, name in ((workbook(build), 'exact.xlsx'),
                       (xls_bytes([['发生日期', '业务名称', '发生金额'], ['2026-11-03', '利息归本', 123456789012345.5]]),
                        'exact.xls')):
        _c, _v, rows = run(data, name)
        assert rows[2]['status'] == 'new' and rows[2]['events'][0]['amount'] == '123456789012345.5', (name, rows[2])


def test_a_decimal_comma_in_a_semicolon_file_is_never_a_thousands_separator(ledger):
    _c, _v, rows = run('发生日期;业务名称;发生金额\n2026-11-03;利息归本;12,50\n'.encode(), 'semicolon.csv')
    row = rows[2]
    assert row['status'] == 'review' or row['events'][0]['amount'] == '12.5', row


def test_precision_is_checked_before_normalisation(ledger):
    _c, _v, rows = run(csv(TEMPLATE, ['t-1', '2026-11-03', 'deposit', ACCOUNT, '', 'CNY', '', '', '',
                                      '100.00000000000000000000000000000001']), 't.csv')
    assert rows[2]['status'] == 'review' and '小数位数' in rows[2]['messages'][0], rows[2]


# -- (A) reconcile.py:223,234: the chosen account's own balance ----------------------------------------------

def test_reconciliation_uses_the_chosen_accounts_own_balances(ledger):
    header = ['发生日期', '业务名称', '发生金额', '资金余额', '资金账号']
    created, _v, _rows = run(csv(header, ['2026-11-03', '利息归本', '10.00', '500010.00', '111'],
                                 ['2026-11-04', '利息归本', '5.00', '900.00', '222']), 'two.csv')
    view, rows = patch(created['id'], account_filter=['111'])
    assert rows[3]['status'] == 'review'
    [item] = view['reconciliation']['cash']
    assert (item['file'], item['ledger'], item['status']) == ('500010', '500010', '一致'), item


# -- (A) codes.py:81: B shares are one unsupported case whatever the spelling -----------------------------------

@pytest.mark.parametrize('code, unit, market', [
    ('900901', '美元', '上海'), ('900901.SH', '美元', '上海'), ('SHA:900901', '美元', '上海'),
    ('200012', '港币', '深圳'), ('200012.SZ', '港币', '深圳'), ('SHE:200012', '港币', '深圳'), ('201872', '港币', '')])
def test_b_shares_are_unsupported_whatever_the_spelling(ledger, code, unit, market):
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '币种', '交易市场']
    _c, _v, rows = run(csv(header, ['2026-11-03', code, '示例B股', '买入', 100, '1.00', '100.00', '1.00', unit, market]),
                       'b.csv')
    assert rows[2]['status'] == 'unsupported' and 'B 股' in rows[2]['messages'][0], rows[2]


# -- (B) transactions.py:1336: a corrected repo cash cell takes part in pairing ---------------------------------

def test_a_corrected_repo_cash_cell_takes_part_in_pairing(ledger):
    header = ['发生日期', '业务名称', '证券代码', '证券名称', '发生金额']
    created, _v, rows = run(csv(header, ['2026-11-03', '融券回购', '204001', 'GC001', '一万'],
                                ['2026-11-04', '融券购回', '204001', 'GC001', '10001.00']), 'r.csv')
    assert rows[2]['status'] == 'review'
    _view, rows = patch(created['id'], rows=[{'row': 2, 'amount': '10000'}])
    assert rows[2]['status'] == 'merged', rows[2]
    assert [(e['kind'], e['amount']) for e in rows[3]['events']] == [('interest', '1')], rows[3]
