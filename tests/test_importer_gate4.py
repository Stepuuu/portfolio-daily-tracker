"""Release gate (round 4) of the broker importer: each section is one blocker of reviews cr_g4a / cr_g4as.

Every scenario confirms through the ledger where the report's claim is about booked cash, and compares the cash with
the broker's own figure.
"""
from datetime import datetime, timezone
from decimal import Decimal
import sqlite3
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import imports as api
from backend.api import ledger as ledger_api
from core.importers import session
from core.ledger import encode, normalize
from tests.importer_support import ACCOUNT, csv, ledger_at, open_account, patch, run, xlsx_bytes


def _ledger(tmp_path, monkeypatch, **cash):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    ledger = ledger_at(tmp_path)
    open_account(ledger, cash_balances=cash or {'CNY': '500000.00', 'HKD': '100000.00', 'USD': '10000.00'})
    return ledger


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    return _ledger(tmp_path, monkeypatch)


@pytest.fixture
def client(ledger):
    app = FastAPI()
    app.include_router(api.router, prefix='/api/imports')
    app.include_router(ledger_api.router, prefix='/api/ledger')
    return TestClient(app)


def cash(ledger, unit='CNY'):
    return Decimal(ledger.state()['accounts'][ACCOUNT]['cash_balances'].get(unit, '0'))


def book_all(ledger, created):
    proposal = session.propose(created['id'])['proposal']
    ledger.confirm(proposal['id'])
    return proposal


# -- 1. a newest-first single-day export without times (transactions.py:1536, reconcile.py:248) ---------------

TDX = ['成交日期', '业务名称', '证券代码', '证券名称', '成交价格', '成交数量', '成交金额', '清算金额', '剩余金额', '佣金']
TDX_BUY = ['20261103', '证券买入', '600036', '招商银行', '32.00', '300', '9600.00', '-9605.00', '6385.00', '5.00']
TDX_SELL = ['20261103', '证券卖出', '000001', '平安银行', '11.00', '1000', '11000.00', '10990.00', '15990.00', '10.00']


@pytest.mark.parametrize('balances', [True, False], ids=['with 剩余金额', 'without balances'])
def test_a_newest_first_day_without_times_books_the_sale_before_the_buy(tmp_path, monkeypatch, balances):
    ledger = _ledger(tmp_path, monkeypatch, CNY='5000.00')
    header, buy, sell = (TDX, TDX_BUY, TDX_SELL) if balances else (TDX[:8] + TDX[9:], TDX_BUY[:8] + TDX_BUY[9:],
                                                                   TDX_SELL[:8] + TDX_SELL[9:])
    created, view, rows = run(csv(header, buy, sell), 'tdx.txt')   # newest first: the buy was the day's last trade
    assert [rows[2]['status'], rows[3]['status']] == ['new', 'new'], [rows[2]['messages'], rows[3]['messages']]
    if balances:
        line = view['reconciliation']['cash'][0]
        assert (line['file'], line['ledger'], line['status'], line['row']) == ('6385', '6385', '一致', 2), line
    book_all(ledger, created)
    assert cash(ledger) == Decimal('6385')


FUNDS_BALANCE = ['发生日期', '业务名称', '发生金额', '资金余额']


def test_reconciliation_is_never_consistent_while_rows_that_move_cash_are_left_out(ledger):
    _c, view, rows = run(csv(FUNDS_BALANCE, ['2026-11-03', '银行转证券', '100.00', '500100.00'],
                             ['2026-11-04', '其他业务', '-50.00', '500050.00'],
                             ['2026-11-05', '其他业务', '50.00', '500100.00']), 'f.csv')
    assert [rows[n]['status'] for n in (2, 3, 4)] == ['new', 'review', 'review']
    recon = view['reconciliation']
    line = recon['cash'][0]
    assert line['difference'] == '0' and line['status'] != '一致' and '2 行' in line['label'], line
    assert recon['consistent'] is False and any('需处理 2 行' in cause for cause in recon['causes']), recon
    assert '一致' not in recon['summary'].split('，')[-1], recon['summary']


# -- 2. a NASDAQ row that declares USD is not ambiguous (transactions.py:524) ---------------------------------

US_HEADER = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '发生金额', '币种',
             '交易市场']
US_BUY = ['2026-11-03', 'AAPL', 'Apple', '买入', '1', '$190.00', '$190.00', '$1.00', '-$191.00', 'USD', 'NASDAQ']


def test_a_nasdaq_row_that_declares_usd_books_without_a_choice(ledger):
    created, _view, rows = run(csv(US_HEADER, US_BUY), 'us.csv')
    assert rows[2]['status'] == 'new', (rows[2]['messages'], rows[2]['needs'])
    assert [(e['ticker'], e['currency'], e['price'], e['fee']) for e in rows[2]['events']] == \
        [('NASDAQ:AAPL', 'USD', '190', '1')]
    book_all(ledger, created)
    assert cash(ledger, 'USD') == Decimal('9809')


def test_choosing_the_offered_currency_resolves_a_dollar_row(ledger):
    no_currency = [c for i, c in enumerate(US_BUY) if US_HEADER[i] != '币种']
    created, _view, rows = run(csv([h for h in US_HEADER if h != '币种'], no_currency), 'us.csv')
    assert rows[2]['status'] == 'review' and 'currency' in rows[2]['needs'], rows[2]['messages']
    _view, rows = patch(created['id'], rows=[{'row': 2, 'currency': 'USD'}])
    assert rows[2]['status'] == 'new', rows[2]['messages']
    book_all(ledger, created)
    assert cash(ledger, 'USD') == Decimal('9809')


def test_a_holdings_cost_in_dollars_of_a_us_listing_is_usd_and_an_entered_cost_settles_any_doubt(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    ledger_at(tmp_path)
    header = ['证券代码', '证券名称', '股票余额', '成本价', '交易市场']
    created, _view, rows = run(csv(header, ['AAPL', 'Apple', '10', '$150.00', 'NASDAQ'],
                                   ['00700', '腾讯控股', '100', '¥300.00', '香港']), 'k.csv', account='新账户')
    assert rows[2]['status'] == 'new', rows[2]['messages']
    assert rows[3]['status'] == 'review' and rows[3]['needs'] == ['price'], rows[3]['messages']
    _view, rows = patch(created['id'], rows=[{'row': 3, 'price': '320'}])
    assert rows[3]['status'] == 'new', rows[3]['messages']


# -- 3. a dividend with gross, withheld and net amounts (transactions.py:1203) --------------------------------

DIVIDEND_HEADER = ['发生日期', '业务名称', '证券代码', '证券名称', '成交金额', '手续费', '发生金额', '币种', '交易市场']


@pytest.mark.parametrize('cells, ticker, unit, net', [
    (['2026-11-03', '红利入账', '00700', '腾讯控股', 'HK$100.00', 'HK$1.00', 'HK$99.00', '港币', '香港'], 'HKG:00700',
     'HKD', '99'),
    (['2026-11-03', '股息入账', 'AAPL', 'Apple', '$100.00', '$10.00', '$90.00', '美元', 'NASDAQ'], 'NASDAQ:AAPL', 'USD',
     '90'),
], ids=['HK', 'US'])
def test_a_dividend_whose_gross_less_deductions_is_the_net_books_the_net(ledger, cells, ticker, unit, net):
    before = cash(ledger, unit)
    created, _view, rows = run(csv(DIVIDEND_HEADER, cells), 'd.csv')
    assert rows[2]['status'] == 'new', (rows[2]['messages'], rows[2]['needs'])
    assert [(e['kind'], e['ticker'], e['currency'], e['amount']) for e in rows[2]['events']] == \
        [('dividend', ticker, unit, net)]
    book_all(ledger, created)
    assert cash(ledger, unit) == before + Decimal(net)


def test_a_dividend_whose_amounts_disagree_is_settled_by_the_amount_the_page_asks_for(ledger):
    cells = ['2026-11-03', '红利入账', '00700', '腾讯控股', 'HK$100.00', 'HK$1.00', 'HK$98.00', '港币', '香港']
    created, _view, rows = run(csv(DIVIDEND_HEADER, cells), 'd.csv')
    assert rows[2]['status'] == 'review' and rows[2]['needs'] == ['amount'], rows[2]['messages']
    _view, rows = patch(created['id'], rows=[{'row': 2, 'amount': '98'}])
    assert rows[2]['status'] == 'new', (rows[2]['messages'], rows[2]['needs'])
    assert rows[2]['events'][0]['amount'] == '98'


# -- 4. a native Excel number is never read with the decimal-comma convention of text cells (values.py:126) ---

TRADE = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '发生金额']


def test_a_native_workbook_price_keeps_its_value_beside_a_decimal_comma_text_fee(ledger):
    rows_in = [TRADE, ['2026-11-03', '510300', '沪深300ETF', '证券买入', '100', '3.912', '391.2', '5,00', '-396.2']]
    data = xlsx_bytes(rows_in, numeric=lambda r, c, v: r > 0 and c in (4, 5, 6, 8))
    created, _view, rows = run(data, 'b.xlsx')
    assert rows[2]['status'] == 'new', rows[2]['messages']
    assert [(e['price'], e['fee']) for e in rows[2]['events']] == [('3.912', '5')]
    book_all(ledger, created)
    assert cash(ledger) == Decimal('499603.80')


def test_a_dot_only_text_number_in_a_decimal_comma_file_needs_review(ledger):
    # Without 成交金额 nothing would catch 3.912 read as 3,912 per share.
    header = [h for h in TRADE if h != '成交金额']
    lines = [header, ['2026-11-03', '510300', '沪深300ETF', '证券买入', '100', '3.912', '5,00', '-396,20']]
    _c, _view, rows = run(('\n'.join(';'.join(line) for line in lines) + '\n').encode(), 'b.csv')
    assert rows[2]['status'] == 'review' and 'price' in rows[2]['needs'], (rows[2]['messages'], rows[2]['events'])


# -- 5. two exports that overlap on the morning fill (dedupe.py:244) -------------------------------------------

FILLS = ['成交日期', '成交时间', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '发生金额', '资金余额',
         '成交编号']
MORNING = ['2026-11-03', '09:31:00', '600036', '招商银行', '证券买入', '100', '32.10', '3210.00', '5.00', '-3215.00',
           '496785.00', '0001']
AFTERNOON = ['2026-11-03', '13:05:00', '600036', '招商银行', '证券买入', '100', '32.15', '3215.00', '5.00', '-3220.00',
             '493565.00', '0002']


def _without(header, row, *columns):
    return [cell for name, cell in zip(header, row) if name not in columns]


@pytest.mark.parametrize('variant', ['ids and times', 'newest first', 'no times, newest first',
                                     'the later export without 成交编号, newest first, no times',
                                     'the later export without 成交编号, times or balances, newest first'])
def test_an_export_overlapping_the_morning_fill_adds_only_the_afternoon_fill(ledger, variant):
    drop = {'ids and times': (), 'newest first': (), 'no times, newest first': ('成交时间',),
            'the later export without 成交编号, newest first, no times': ('成交时间', '成交编号'),
            'the later export without 成交编号, times or balances, newest first': ('成交时间', '成交编号', '资金余额')}[variant]
    morning = csv(FILLS, MORNING) if variant != 'no times, newest first' else \
        csv(_without(FILLS, FILLS, '成交时间'), _without(FILLS, MORNING, '成交时间'))
    created, _view, _rows = run(morning, 'morning.csv')
    book_all(ledger, created)
    header, a, b = (_without(FILLS, row, *drop) for row in (FILLS, MORNING, AFTERNOON))
    later = csv(header, a, b) if variant == 'ids and times' else csv(header, b, a)
    created, view, rows = run(later, 'afternoon.csv')
    statuses = {row['cells'][header.index('成交价格')]: (row['status'], row['include']) for row in rows.values()}
    assert statuses['32.15'] == ('new', True), (statuses, [r['messages'] for r in rows.values()])
    assert statuses['32.10'][1] is False, statuses
    book_all(ledger, created)
    assert cash(ledger) == Decimal('493565')


# -- 6. 港股通: the CNY settlement is the truth, an HKD fee is converted (transactions.py:497) ---------------

CONNECT = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '佣金', '发生金额', '结算汇率', '币种',
           '交易市场']


@pytest.mark.parametrize('header, fee', [(CONNECT, 'HK$96.00'),
                                         ([*CONNECT[:7], '佣金(港币)', *CONNECT[8:]], '96.00'),
                                         (CONNECT, '96.00')], ids=['HK$ in the cell', 'HKD in the header', 'unlabelled'])
def test_a_connect_buy_with_an_hkd_fee_moves_exactly_the_cny_settlement(ledger, header, fee):
    row = ['2026-11-05', '00700', '腾讯控股', '港股通买入', '200', '380.00', '76000.00', fee, '-70008.32', '0.9200', '人民币',
           '沪港通']
    created, _view, rows = run(csv(header, row), 'c.csv')
    assert rows[2]['status'] == 'new', (rows[2]['messages'], rows[2]['needs'])
    assert rows[2]['fee'] == '88.32' and rows[2]['fee_source'] == 'derived', (rows[2]['fee'], rows[2]['notes'])
    fx, buy = rows[2]['events']
    assert (fx['amount'], fx['received_amount'], buy['fee']) == ('70008.32', '76096', '96'), rows[2]['events']
    before = cash(ledger, 'HKD')
    book_all(ledger, created)
    assert cash(ledger) == Decimal('500000') - Decimal('70008.32') and cash(ledger, 'HKD') == before


def test_choosing_cny_for_a_connect_row_never_books_the_hkd_fee_as_yuan(ledger):
    row = ['2026-11-05', '00700', '腾讯控股', '港股通买入', '200', '380.00', '76000.00', 'HK$96.00', '-70008.32', '0.9200', '',
           '沪港通']
    created, _view, rows = run(csv(CONNECT, row), 'c.csv')
    _view, rows = patch(created['id'], rows=[{'row': 2, 'currency': 'CNY'}])
    assert rows[2]['status'] == 'new' and rows[2]['fee'] == '88.32', (rows[2]['messages'], rows[2]['fee'])
    book_all(ledger, created)
    assert cash(ledger) == Decimal('500000') - Decimal('70008.32')


# -- 8. a preview made by the CSV import of 3.2.1 (source broker-csv) is withdrawn after the upgrade ----------

CSV_CONTENT = f'external_id,date,kind,account,currency,amount\nx1,2026-11-03,deposit,{ACCOUNT},CNY,10\n'


def _as_in_321(ledger):
    """A ledger written by 3.2.1: no binding table, a pending preview of its CSV import."""
    events = [normalize({'external_id': 'x1', 'date': '2026-11-03', 'kind': 'deposit', 'account': ACCOUNT,
                         'currency': 'CNY', 'amount': '10', 'source': 'broker-csv'})]
    payload = {'events': events, 'before': {}, 'after': {}, 'duplicates': 0, 'warnings': []}
    proposal_id = uuid.uuid4().hex
    db = sqlite3.connect(str(ledger.path))
    with db:
        db.execute('DROP INDEX IF EXISTS proposal_bindings_one_pending')
        db.execute('DROP TABLE IF EXISTS proposal_bindings')
        revision = db.execute('SELECT revision FROM ledger_meta WHERE id=1').fetchone()[0]
        db.execute('INSERT INTO proposals VALUES (?,?,?,?,NULL)',
                   (proposal_id, revision, encode(payload), datetime.now(timezone.utc).isoformat()))
    db.close()
    return proposal_id


def test_a_321_csv_import_preview_is_withdrawn_and_can_be_imported_again(client, ledger):
    old = _as_in_321(ledger)
    for _attempt in range(2):
        refused = client.post(f'/api/ledger/proposals/{old}/confirm')
        assert refused.status_code == 409 and '请重新导入' in refused.json()['detail'], refused.json()
    assert cash(ledger) == Decimal('500000') and ledger.proposal(old) is None
    body = client.post('/api/imports/parse', files={'file': ('old.csv', CSV_CONTENT.encode(), 'text/csv')},
                       data={'account': ACCOUNT}).json()
    assert [row['status'] for row in body['rows']] == ['new'], body['rows']
    proposal = client.post(f"/api/imports/{body['id']}/propose").json()['proposal']
    assert client.post(f"/api/ledger/proposals/{proposal['id']}/confirm").status_code == 200
    assert cash(ledger) == Decimal('500010')


def test_a_csv_preview_made_after_the_upgrade_still_confirms(client, ledger):
    made = client.post('/api/ledger/csv-preview', json={'content': CSV_CONTENT, 'source': 'broker-csv'}).json()
    assert client.post(f"/api/ledger/proposals/{made['id']}/confirm").status_code == 200
    assert cash(ledger) == Decimal('500010')


# -- 9. the 累计净投入 suggestion of a holdings opening counts everything the opening books ------------------

ESTIMATE = '按现金＋持仓成本＋基金估值估算，只是参考：请改成实际累计转入的本金'   # as the setup wizard says it


def test_the_contribution_suggestion_counts_cash_positions_at_cost_and_the_fund(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    ledger_at(tmp_path)
    created, _view, _rows = run(csv(['证券代码', '证券名称', '股票余额', '成本价'], ['600519', '贵州茅台', '10', '1500']), 'k.csv',
                                account='新账户')
    view, _rows = patch(created['id'], opening={'date': '2026-11-14', 'cash_balances': {'CNY': '10000', 'HKD': '0'},
                                                'fund_cny': '40000'})
    assert view['holdings']['suggested_contributed_cny'] == {'value': '65000', 'label': ESTIMATE}
