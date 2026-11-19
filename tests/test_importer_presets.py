"""Guard against over-correction: ordinary, clean exports of every built-in preset and the template
import as new rows with the right values, no blocker, and confirm."""
import pytest

from core.importers import session
from tests.importer_support import ACCOUNT, csv, fixture, ledger_at, open_account, patch, run

NEW = '新账户'


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    ledger = ledger_at(tmp_path)
    open_account(ledger)
    return ledger


def summary(event):
    keys = ('kind', 'ticker', 'currency', 'quantity', 'price', 'fee', 'amount')
    return tuple(event.get(key) for key in keys)


# name, data, preset id, {row: [(kind, ticker, currency, quantity, price, fee, amount) or None to skip the price]},
# rows that are not ordinary trades and are expected otherwise ({row: status})
TRANSACTIONS = [
    ('ths_trades_gbk.xls', lambda: fixture('ths_trades_gbk.xls'), 'ths_trades', {
        3: [('buy', 'SHA:600519', 'CNY', '100', '1680', '43.68', None)],
        4: [('sell', 'SHE:000001', 'CNY', '1000', '11.2', '10.71', None)],
        5: [('buy', 'SHA:600036', 'CNY', '300', None, '5.1', None)],
        7: [('interest', None, 'CNY', None, None, None, '4.89')],
        10: [('sell', 'SHA:600519', 'CNY', '50', '1700', '64.6', None)]},
     {6: 'merged', 8: 'skipped', 9: 'skipped', 11: 'skipped'}),
    ('tdx_trades_gbk.txt', lambda: fixture('tdx_trades_gbk.txt'), 'tdx_trades', {
        3: [('sell', 'SHE:000001', 'CNY', '500', '11.5', '8.15', None)],
        4: [('buy', 'SHA:510300', 'CNY', '5000', '3.912', '5.97', None)],
        5: [('buy', 'SHE:000001', 'CNY', '1000', '11', '5.52', None)]}, {}),
    ('eastmoney_trades_html.xls', lambda: fixture('eastmoney_trades_html.xls'), 'generic', {
        3: [('buy', 'SHE:000001', 'CNY', '1000', '11', '5.11', None)],
        4: [('sell', 'SHE:000001', 'CNY', '500', '11.4', '7.91', None)]}, {}),
    ('eastmoney_trades.csv', lambda: csv(['成交日期', '业务名称', '证券代码', '证券名称', '成交数量', '成交价格', '成交金额', '手续费',
                                          '发生金额', '资金余额'],
                                         ['2026-11-03', '证券买入', '600036', '招商银行', 100, '30.00', '3000.00', '5.00',
                                          '-3005.00', '496995.00'],
                                         ['2026-11-04', '证券卖出', '600036', '招商银行', 100, '31.00', '3100.00', '6.55',
                                          '3093.45', '500088.45']),
     'eastmoney_trades', {
        2: [('buy', 'SHA:600036', 'CNY', '100', '30', '5', None)],
        3: [('sell', 'SHA:600036', 'CNY', '100', '31', '6.55', None)]}, {}),
    ('hk_connect_bom.csv', lambda: fixture('hk_connect_bom.csv'), 'generic', {
        2: [('fx', None, 'CNY', None, None, None, '70008.35'), ('buy', 'HKG:00700', 'HKD', '200', '380', None, None)],
        3: [('sell', 'HKG:00700', 'HKD', '100', '400', None, None), ('fx', None, 'HKD', None, None, None, None)]}, {}),
    ('statement.csv', lambda: csv(['发生日期', '业务名称', '发生金额', '资金余额'], ['2026-11-02', '银行转证券', '1000.00', '501000.00'],
                                  ['2026-11-03', '利息归本', '1.23', '501001.23'],
                                  ['2026-11-04', '证券转银行', '-500.00', '500501.23']), 'statement_cash', {
        2: [('deposit', None, 'CNY', None, None, None, '1000')],
        3: [('interest', None, 'CNY', None, None, None, '1.23')],
        4: [('withdrawal', None, 'CNY', None, None, None, '500')]}, {}),
    ('eastmoney_statement_bom.csv', lambda: fixture('eastmoney_statement_bom.csv'), 'eastmoney_trades', {
        2: [('deposit', None, 'CNY', None, None, None, '50000')],
        3: [('buy', 'SHA:600036', 'CNY', '500', '32.1', '5.16', None)],
        4: [('dividend', 'SHA:600036', 'CNY', None, None, None, '1936')],
        5: [('fee', 'SHA:600036', 'CNY', None, None, None, '96.8')],
        6: [('split', 'SHA:600036', None, None, None, None, None)],
        7: [('interest', None, 'CNY', None, None, None, '12.34')],
        8: [('withdrawal', None, 'CNY', None, None, None, '20000')]},
     # deliberately not ordinary: 中签缴款, 新股入帐, 北交所, 基金申购, 资金调整
     {9: 'review', 10: 'review', 11: 'unsupported', 12: 'review', 13: 'review'}),
    ('template.csv', lambda: csv(['external_id', 'date', 'kind', 'account', 'ticker', 'currency', 'quantity', 'price', 'fee',
                                  'amount', 'fx_rate'],
                                 ['t-1', '2026-11-03', 'buy', ACCOUNT, 'SHA:600519', 'CNY', '100', '1680', '5', '', ''],
                                 ['t-2', '2026-11-04', 'sell', ACCOUNT, 'SHE:000001', 'CNY', '100', '11', '5', '', ''],
                                 ['t-3', '2026-11-04', 'deposit', ACCOUNT, '', 'CNY', '', '', '', '1000', ''],
                                 ['t-4', '2026-11-05', 'dividend', ACCOUNT, 'SHA:600036', 'CNY', '', '', '', '50', ''],
                                 ['t-5', '2026-11-05', 'fee', ACCOUNT, '', 'CNY', '', '', '', '1', ''],
                                 ['t-6', '2026-11-06', 'withdrawal', ACCOUNT, '', 'CNY', '', '', '', '10', ''],
                                 ['t-7', '2026-11-06', 'interest', ACCOUNT, '', 'CNY', '', '', '', '0.5', ''],
                                 ['t-8', '2026-11-06', 'deposit', ACCOUNT, '', 'USD', '', '', '', '100', '7.1']),
     'template', {
        2: [('buy', 'SHA:600519', 'CNY', '100', '1680', '5', None)],
        3: [('sell', 'SHE:000001', 'CNY', '100', '11', '5', None)],
        4: [('deposit', None, 'CNY', None, None, None, '1000')],
        5: [('dividend', 'SHA:600036', 'CNY', None, None, None, '50')],
        6: [('fee', None, 'CNY', None, None, None, '1')],
        7: [('withdrawal', None, 'CNY', None, None, None, '10')],
        8: [('interest', None, 'CNY', None, None, None, '0.5')],
        9: [('deposit', None, 'USD', None, None, None, '100')]}, {}),
]


@pytest.mark.parametrize('name, data, preset, expected, others', TRANSACTIONS, ids=[case[0] for case in TRANSACTIONS])
def test_clean_exports_import_as_new_rows_and_confirm(ledger, name, data, preset, expected, others):
    created, view, rows = run(data(), name)
    assert view['preset']['id'] == preset and view['blockers'] == [] and view['column_conflicts'] == []
    for number, events in expected.items():
        row = rows[number]
        assert row['status'] == 'new' and row['include'] and not row['needs'], (number, row['messages'])
        assert len(row['events']) == len(events), (number, row['events'])
        for event, wanted in zip(row['events'], events):
            got = summary(event)
            assert tuple(g if w is not None else None for g, w in zip(got, wanted)) == wanted, (number, got)
    for number, status in others.items():
        assert rows[number]['status'] == status, (number, rows[number]['messages'])
    assert {number for number, row in rows.items() if row['status'] == 'new'} == set(expected)
    if view['reconciliation']['available'] and not set(others.values()) & {'review', 'unsupported'}:
        assert view['reconciliation']['consistent'], view['reconciliation']
    proposal = session.propose(created['id'])['proposal']
    assert len(proposal['events']) == sum(len(events) for events in expected.values())
    receipt = ledger.confirm(proposal['id'])
    assert receipt['applied'] == len(proposal['events'])


HOLDINGS = [
    ('ths_holdings.xls', ['证券代码', '证券名称', '股票余额', '可用余额', '成本价', '市价', '市值', '浮动盈亏', '交易市场', '股东帐户'],
     [['600519', '贵州茅台', '100', '100', '1650.000', '1680.00', '168000.00', '3000.00', '上海A股', 'A000000001'],
      ['000001', '平安银行', '2000', '1500', '10.500', '11.20', '22400.00', '1400.00', '深圳A股', '0000000001']],
     'ths_holdings'),
    ('tdx_holdings.txt', ['证券代码', '证券名称', '证券数量', '可用数量', '成本价', '当前价', '最新市值'],
     [['600519', '贵州茅台', '100', '100', '1650.000', '1680.00', '168000.00'],
      ['000001', '平安银行', '2000', '1500', '10.500', '11.20', '22400.00']], 'tdx_holdings'),
]


@pytest.mark.parametrize('name, header, cells, preset', HOLDINGS, ids=[case[0] for case in HOLDINGS])
def test_clean_holdings_open_an_account(ledger, name, header, cells, preset):
    created, view, rows = run(csv(header, *cells), name, account=NEW)
    assert view['statement'] == 'holdings' and view['preset']['id'] == preset
    assert [row['status'] for row in rows.values()] == ['new', 'new']
    assert view['holdings']['positions'] == [
        {'ticker': 'SHA:600519', 'quantity': '100', 'cost_price': '1650', 'name': '贵州茅台'},
        {'ticker': 'SHE:000001', 'quantity': '2000', 'cost_price': '10.5', 'name': '平安银行'}]
    view, _rows = patch(created['id'], opening={'date': '2026-11-14', 'cash_balances': {'CNY': '12000.00'},
                                                 'contributed_cny': '200000'})
    assert view['blockers'] == []
    proposal = session.propose(created['id'])['proposal']
    [event] = proposal['events']
    assert event['kind'] == 'opening' and len(event['positions']) == 2
    assert ledger.confirm(proposal['id'])['applied'] == 1
    again = session.view(session.load(run(csv(header, *cells), name + '.again', account=NEW)[0]['id']))
    table = {line['ticker']: line for line in again['holdings']['reconciliation']}
    assert all(line['status'] == '一致' and line['entry'] is None for line in table.values()), table


def test_a_template_fund_value_of_zero_is_bookable(ledger):
    # The fund total may be 0 (everything redeemed); the template's sign rule must not refuse it.
    created, _view, rows = run(csv(['external_id', 'date', 'kind', 'account', 'currency', 'amount'],
                                   ['f-1', '2026-11-03', 'fund_value', ACCOUNT, 'CNY', '0']), 'fund.csv')
    assert rows[2]['status'] == 'new' and rows[2]['events'][0]['amount'] == '0'
