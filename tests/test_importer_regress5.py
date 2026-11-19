"""Round 5 of the broker importer: what round 4 changed beyond the scenario it was written for (cr_v5a, cr_v5as).

Each section pins the figure 40868ac gave for the regression scenario, next to the round-4 scenario that must still
pass ("both ways"). Booked cash is read back from the ledger.
"""
from decimal import Decimal

import pytest

from core.importers import session
from tests.importer_support import ACCOUNT, OPENING, book, csv, ledger_at, open_account, patch, run


def _ledger(tmp_path, monkeypatch, **cash):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    ledger = ledger_at(tmp_path)
    open_account(ledger, cash_balances=cash or {'CNY': '500000.00', 'HKD': '100000.00'})
    return ledger


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    return _ledger(tmp_path, monkeypatch)


def account(ledger):
    return ledger.state()['accounts'][ACCOUNT]


def cash(ledger, unit='CNY'):
    return Decimal(account(ledger)['cash_balances'].get(unit, '0'))


def book_all(ledger, created):
    proposal = session.propose(created['id'])['proposal']
    ledger.confirm(proposal['id'])
    return proposal


# -- 1. same-day template rows keep the file's order (transactions.py:1646, v5a B3) ------------------------------

TEMPLATE = ['external_id', 'date', 'kind', 'account', 'currency', 'amount', 'fee']


def test_same_day_template_fund_rows_keep_their_order(ledger):
    created, _view, rows = run(csv(TEMPLATE, ['f-1', '2026-11-03', 'fund_value', ACCOUNT, 'CNY', '0', ''],
                                   ['f-2', '2026-11-03', 'fund_buy', ACCOUNT, 'CNY', '400', '0'],
                                   ['f-3', '2026-11-03', 'fund_sell', ACCOUNT, 'CNY', '100', '0']), 'fund.csv')
    assert [rows[n]['status'] for n in (2, 3, 4)] == ['new'] * 3, [rows[n]['messages'] for n in (2, 3, 4)]
    proposal = book_all(ledger, created)
    assert [e['kind'] for e in proposal['events']] == ['fund_value', 'fund_buy', 'fund_sell']
    assert Decimal(account(ledger)['fund_cny']) == Decimal('300')     # 40868ac: 300 (HEAD of round 4: 0)
    assert cash(ledger) == Decimal('499700')


# -- 2. the balance chain that decides the direction is the selected account's (transactions.py:1599, v5as B4) ------

TDX = ['成交日期', '业务名称', '证券代码', '证券名称', '成交价格', '成交数量', '成交金额', '清算金额', '剩余金额', '佣金', '资金账号']
SELL = ['20261103', '证券卖出', '000001', '平安银行', '11.00', '1000', '11000.00', '10990.00', '15990.00', '10.00', '111']
BUY = ['20261103', '证券买入', '600036', '招商银行', '32.00', '300', '9600.00', '-9605.00', '6385.00', '5.00', '111']


def _other(balance):
    return ['20261103', '证券买入', '600036', '招商银行', '9.95', '100', '995.00', '-1000.00', balance, '5.00', '222']


def test_another_accounts_balances_never_reverse_a_chronological_day(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch, CNY='5000.00')
    # 111 in file order: the sale, then the buy it pays for. 222's balances read newest first.
    created, _view, _rows = run(csv(TDX, SELL, BUY, _other('8000.00'), _other('9000.00'), _other('10000.00')), 'tdx.txt')
    view, rows = patch(created['id'], account_filter=['111'])
    assert [rows[2]['status'], rows[3]['status']] == ['new', 'new'], [rows[2]['messages'], rows[3]['messages']]
    line = view['reconciliation']['cash'][0]
    assert (line['file'], line['ledger'], line['status']) == ('6385', '6385', '一致'), line   # 40868ac: 6385 一致
    book_all(ledger, created)
    assert cash(ledger) == Decimal('6385')


def test_a_newest_first_day_of_one_account_among_two_still_books_the_sale_first(tmp_path, monkeypatch):
    # The round-4 scenario, with another account's rows in the file: 111's own balances say newest first.
    ledger = _ledger(tmp_path, monkeypatch, CNY='5000.00')
    created, _view, _rows = run(csv(TDX, BUY, SELL, _other('8000.00')), 'tdx.txt')
    view, rows = patch(created['id'], account_filter=['111'])
    assert [rows[2]['status'], rows[3]['status']] == ['new', 'new'], [rows[2]['messages'], rows[3]['messages']]
    line = view['reconciliation']['cash'][0]
    assert (line['file'], line['ledger'], line['status']) == ('6385', '6385', '一致'), line
    book_all(ledger, created)
    assert cash(ledger) == Decimal('6385')


# -- 3. a dot-only number the row's own arithmetic settles (values.py:129, v5as B5) --------------------------------

TRADE = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '发生金额']


def _semicolons(*lines):
    return ('\n'.join(';'.join(line) for line in lines) + '\n').encode()


def test_a_dot_only_quantity_that_quantity_times_price_settles_is_read_as_grouped(ledger):
    data = _semicolons(TRADE, ['2026-11-03', '600036', '招商银行', '证券买入', '1.000', '10,00', '10.000,00', '5,00',
                               '-10.005,00'])
    created, _view, rows = run(data, 'b.csv')
    assert rows[2]['status'] == 'new', (rows[2]['messages'], rows[2]['needs'])
    assert [(e['quantity'], e['price'], e['fee']) for e in rows[2]['events']] == [('1000', '10', '5')]
    book_all(ledger, created)
    assert cash(ledger) == Decimal('489995')            # 40868ac: 1,000 shares, ¥489,995
    assert account(ledger)['positions']['SHA:600036']['quantity'] == '2000'


def test_a_dot_only_price_that_the_turnover_contradicts_still_needs_review(ledger):
    # Read as grouped, 3.912 is ¥3,912 a share: 100 × 3,912 is not the 391,20 turnover, so nothing settles it.
    data = _semicolons(TRADE, ['2026-11-03', '510300', '沪深300ETF', '证券买入', '100', '3.912', '391,20', '5,00', '-396,20'])
    _c, _view, rows = run(data, 'b.csv')
    assert rows[2]['status'] == 'review' and 'price' in rows[2]['needs'], (rows[2]['messages'], rows[2]['events'])


# -- 4. 港股通 with the fee stated in two currencies: the fee the page asks for settles it (transactions.py:1157) ------

CONNECT = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '佣金', '过户费', '发生金额', '结算汇率',
           '币种', '交易市场']
MIXED = ['2026-11-05', '00700', '腾讯控股', '港股通买入', '200', '380.00', '76000.00', 'HK$90.00', '¥5.52', '-70008.32',
         '0.9200', '人民币', '沪港通']


@pytest.mark.parametrize('edit', [{'fee': '88.32'}, {'currency': 'CNY', 'fee': '88.32'}],
                         ids=['the fee', 'CNY and the fee (as 40868ac asked)'])
def test_entering_the_fee_resolves_a_connect_row_whose_fees_name_two_currencies(ledger, edit):
    created, _view, rows = run(csv(CONNECT, MIXED), 'c.csv')
    assert rows[2]['status'] == 'review', rows[2]['messages']
    _view, rows = patch(created['id'], rows=[{'row': 2, **edit}])
    assert rows[2]['status'] == 'new', (rows[2]['messages'], rows[2]['needs'])
    before = cash(ledger, 'HKD')
    book_all(ledger, created)
    assert cash(ledger) == Decimal('429991.68') and cash(ledger, 'HKD') == before     # 40868ac: ¥429,991.68


def test_a_connect_row_whose_fees_name_two_currencies_asks_for_the_fee(ledger):
    _c, _view, rows = run(csv(CONNECT, MIXED), 'c.csv')
    assert rows[2]['status'] == 'review' and rows[2]['needs'] == ['fee'], (rows[2]['messages'], rows[2]['needs'])


# -- 5. a cash-only row left out never un-verifies share counts (reconcile.py:278, v5a B5, v5as B7) ----------------

DIVIDEND = ['发生日期', '业务名称', '证券代码', '证券名称', '成交数量', '成交金额', '发生金额', '资金余额', '股份余额']


@pytest.mark.parametrize('variant', ['unchecked', 'awaiting the amount'])
def test_a_dividend_left_out_leaves_the_shares_verified(ledger, variant):
    gross = '10.00' if variant == 'unchecked' else '12.00'      # 12 against 10 posted: the row asks for the amount
    created, view, rows = run(csv(DIVIDEND, ['2026-11-05', '红利入账', '600036', '招商银行', '1000', gross, '10.00',
                                             '500010.00', '1000']), 'd.csv')
    if variant == 'unchecked':
        assert rows[2]['status'] == 'new', rows[2]['messages']
        view, rows = patch(created['id'], rows=[{'row': 2, 'include': False}])
    else:
        assert rows[2]['status'] == 'review' and 'amount' in rows[2]['needs'], (rows[2]['messages'], rows[2]['needs'])
    shares = {line['ticker']: line for line in view['reconciliation']['positions']}['SHA:600036']
    assert (shares['file'], shares['ledger'], shares['status']) == ('1000', '1000', '一致'), shares   # 40868ac: 一致
    line = view['reconciliation']['cash'][0]
    assert (line['file'], line['ledger'], line['status']) == ('500010', '500000', '差额'), line


SHARES = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '发生金额', '股份余额']


def test_trades_left_out_still_leave_matching_shares_unverified(ledger):
    # The round-4 rule for the rows it was meant for: unchecked trades move shares, so 1000 = 1000 is not checked.
    created, _view, _rows = run(csv(SHARES, ['2026-11-05', '600036', '招商银行', '证券买入', '100', '32.00', '3200.00', '5.00',
                                             '-3205.00', '1100'],
                                    ['2026-11-06', '600036', '招商银行', '证券卖出', '100', '32.00', '3200.00', '5.00',
                                     '3195.00', '1000']), 't.csv')
    view, _rows = patch(created['id'], rows=[{'row': 2, 'include': False}, {'row': 3, 'include': False}])
    shares = {line['ticker']: line for line in view['reconciliation']['positions']}['SHA:600036']
    assert (shares['file'], shares['ledger'], shares['status']) == ('1000', '1000', '未核对'), shares


# -- 6. an afternoon-only export after the morning fill was booked (dedupe.py:244, v5a g4as-2.3) -------------------

FILLS = ['成交日期', '成交时间', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '发生金额', '资金余额',
         '成交编号']
MORNING = ['2026-11-03', '09:31:00', '600036', '招商银行', '证券买入', '100', '32.10', '3210.00', '5.00', '-3215.00',
           '496785.00', '0001']
AFTERNOON = ['2026-11-03', '13:05:00', '600036', '招商银行', '证券买入', '100', '32.15', '3215.00', '5.00', '-3220.00',
             '493565.00', '0002']


def test_a_fill_with_its_own_trade_number_is_new_beside_a_booked_fill_with_another(ledger):
    created, _view, _rows = run(csv(FILLS, MORNING), 'morning.csv')
    book_all(ledger, created)
    created, _view, rows = run(csv(FILLS, AFTERNOON), 'afternoon.csv')
    assert (rows[2]['status'], rows[2]['include']) == ('new', True), (rows[2]['messages'], rows[2]['duplicate_of'])
    book_all(ledger, created)
    assert cash(ledger) == Decimal('493565')


def test_an_afternoon_export_without_trade_numbers_still_asks_about_the_booked_fill(ledger):
    created, _view, _rows = run(csv(FILLS, MORNING), 'morning.csv')
    book_all(ledger, created)
    created, _view, rows = run(csv(FILLS[:-1], AFTERNOON[:-1]), 'afternoon.csv')
    assert (rows[2]['status'], rows[2]['include']) == ('possible_duplicate', False), rows[2]['messages']


# -- 7. dedupe and reconciliation compare listings, not spellings (ledger.py:703, v5a B6, v5as B3) -------------------

HK = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '发生金额', '币种', '股份余额']
HK_BUY = ['2026-11-05', '00700', '腾讯控股', '买入', '200', '380.00', '76000.00', '50.00', '-76050.00', '港币', '200']


def _two_spellings(ledger):
    """This account once held Tencent as HKG:700 (sold out); another account holds it as HKG:00700."""
    book(ledger, [{**OPENING, 'account': '另一账户', 'cash_balances': {'CNY': '1000.00'},
                   'positions': [{'ticker': 'HKG:00700', 'name': '腾讯控股', 'quantity': '100', 'cost_price': '300'}]}])
    book(ledger, [{'kind': 'buy', 'date': '2026-11-02', 'account': ACCOUNT, 'source': 'manual', 'ticker': 'HKG:700',
                   'currency': 'HKD', 'quantity': '100', 'price': '300', 'fee': '0'}])
    book(ledger, [{'kind': 'sell', 'date': '2026-11-03', 'account': ACCOUNT, 'source': 'manual', 'ticker': 'HKG:700',
                   'currency': 'HKD', 'quantity': '100', 'price': '300', 'fee': '0'}])


def test_reuploading_a_booked_hk_fill_is_imported_and_its_shares_agree(ledger):
    _two_spellings(ledger)
    created, view, rows = run(csv(HK, HK_BUY), 'hk.csv')
    assert rows[2]['status'] == 'new', rows[2]['messages']
    book_all(ledger, created)
    after = cash(ledger, 'HKD')
    assert after == Decimal('23950')
    _created, view, rows = run(csv(HK, HK_BUY), 'hk-again.csv')
    assert rows[2]['status'] == 'imported', (rows[2]['messages'], rows[2].get('existing'))    # 40868ac: 已导入
    shares = view['reconciliation']['positions'][0]
    assert (shares['file'], shares['ledger'], shares['status']) == ('200', '200', '一致'), shares   # 40868ac: 200/200
    assert cash(ledger, 'HKD') == after


def test_a_fill_booked_under_the_held_spelling_is_imported_and_counted_with_it(ledger):
    # The round-4 rule: a listing the account holds as HKG:700 is booked as HKG:700, whatever the file's spelling.
    _two_spellings(ledger)
    book(ledger, [{'kind': 'buy', 'date': '2026-11-04', 'account': ACCOUNT, 'source': 'manual', 'ticker': 'HKG:700',
                   'currency': 'HKD', 'quantity': '100', 'price': '100', 'fee': '0'}])
    held = HK_BUY[:-1] + ['300']
    created, view, rows = run(csv(HK, held), 'hk.csv')
    assert rows[2]['status'] == 'new', rows[2]['messages']
    assert view['reconciliation']['positions'][0]['status'] == '一致', view['reconciliation']['positions']
    book_all(ledger, created)
    assert account(ledger)['positions']['HKG:700']['quantity'] == '300'
    _created, view, rows = run(csv(HK, held), 'hk-again.csv')
    assert rows[2]['status'] == 'imported', (rows[2]['messages'], rows[2].get('existing'))
    shares = view['reconciliation']['positions'][0]
    assert (shares['file'], shares['ledger'], shares['status']) == ('300', '300', '一致'), shares
