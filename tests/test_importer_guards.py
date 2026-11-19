"""Every event-building path × every fact a booking needs (``transactions.GUARDS``), as a grid.

The facts (``CHECKS``): listing, direction, quantity and its unit, price, currency,
fee, account identity, not already in the ledger, signs, bounds. For each cell that
applies, a row that violates exactly that fact runs through the real importer and
must not be booked (review, unsupported, a blocker, or excluded as a duplicate);
the same row without the violation must be. A cell without a probe, or a function
of the importer package that builds events without being listed in ``GUARDS``, fails
the suite. So a missing check shows up as an empty cell, not as the next review finding.
"""
import ast
from pathlib import Path

import pytest

from core.importers import transactions
from core.importers.transactions import CHECKS, EVENT_CONSTRUCTOR, GUARDS
from tests.importer_support import ACCOUNT, book, csv, ledger_at, open_account, run

NEW = '新账户'   # an account without opening: a holdings file opens it


def _with(row, **changes):
    """``row`` (a dict of column → cell) with some cells changed, as a list in header order."""
    return {**row, **changes}


def _cells(header, row):
    return [row.get(column, '') for column in header]


# path: (account, header, the clean rows, the row number whose booking is checked)
TRADE_HEADER = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '发生金额', '币种', '资金账号']
TRADE = {'成交日期': '2026-11-03', '证券代码': '600036', '证券名称': '招商银行', '操作': '证券买入', '成交数量': '100',
         '成交价格': '30.00', '成交金额': '3000.00', '手续费': '5.00', '发生金额': '-3005.00', '币种': '人民币'}
CONNECT_HEADER = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '佣金', '发生金额', '结算汇率', '币种',
                  '交易市场', '资金账号']
CONNECT = {'成交日期': '2026-11-05', '证券代码': '00700', '证券名称': '腾讯控股', '操作': '港股通买入', '成交数量': '200',
           '成交价格': '380.00', '成交金额': '76000.00', '佣金': '5.00', '发生金额': '-69925.00', '结算汇率': '0.9200',
           '币种': '人民币', '交易市场': '沪港通'}
CASH_HEADER = ['发生日期', '业务名称', '证券代码', '证券名称', '发生金额', '币种', '资金账号']
DIVIDEND = {'发生日期': '2026-11-03', '业务名称': '红利入账', '证券代码': '600036', '证券名称': '招商银行', '发生金额': '100.00',
            '币种': '人民币'}
LEND = {'发生日期': '2026-11-03', '证券代码': '204001', '证券名称': 'GC001', '发生金额': '-10000.00', '币种': '人民币'}
BACK = {'发生日期': '2026-11-04', '证券代码': '204001', '证券名称': 'GC001', '发生金额': '10001.00', '币种': '人民币'}
SPLIT_HEADER = ['发生日期', '业务名称', '证券代码', '证券名称', '成交数量', '发生金额', '资金账号']
SPLIT = {'发生日期': '2026-11-05', '业务名称': '红股入账', '证券代码': '600036', '证券名称': '招商银行', '成交数量': '300',
         '发生金额': '0.00'}
TEMPLATE_HEADER = ['external_id', 'date', 'kind', 'account', 'ticker', 'currency', 'quantity', 'price', 'fee', 'amount']
TEMPLATE = {'external_id': 't-1', 'date': '2026-11-03', 'kind': 'buy', 'account': ACCOUNT, 'ticker': 'SHA:600519',
            'currency': 'CNY', 'quantity': '100', 'price': '1680', 'fee': '5'}
DEPOSIT = {'external_id': 't-1', 'date': '2026-11-03', 'kind': 'deposit', 'account': ACCOUNT, 'currency': 'CNY',
           'amount': '10'}
HOLDING_HEADER = ['证券代码', '证券名称', '股票余额', '成本价', '币种', '资金账号']
HOLDING = {'证券代码': '600519', '证券名称': '贵州茅台', '股票余额': '100', '成本价': '1650.00', '币种': '人民币'}


def booked(**fields):
    """An event entered another way (a manual entry) that a probe row would duplicate."""
    return {'account': ACCOUNT, 'source': 'manual', **fields}


MANUAL_BUY = booked(kind='buy', date='2026-11-03', ticker='SHA:600036', currency='CNY', quantity='100', price='30',
                    fee='5')


def _three_accounts(row, date_column):
    """Two rows of 资金账号 111 and one of 222 (the minority, row 4), on different dates."""
    return [_with(row, **{'资金账号': '111', date_column: '2026-11-03'}),
            _with(row, **{'资金账号': '111', date_column: '2026-11-04'}),
            _with(row, **{'资金账号': '222', date_column: '2026-11-05'})]


CLEAN = {
    'trade': (ACCOUNT, TRADE_HEADER, [TRADE], 2),
    'connect': (ACCOUNT, CONNECT_HEADER, [CONNECT], 2),
    'cash_flow': (ACCOUNT, CASH_HEADER, [DIVIDEND], 2),
    'repo': (ACCOUNT, CASH_HEADER, [LEND, BACK], 3),
    'split': (ACCOUNT, SPLIT_HEADER, [SPLIT], 2),
    'template': (ACCOUNT, TEMPLATE_HEADER, [TEMPLATE], 2),
    'opening': (NEW, HOLDING_HEADER, [HOLDING], 2),
}

PROBES = {
    ('trade', 'ticker'): (ACCOUNT, TRADE_HEADER, [_with(TRADE, 证券代码='SHA:000001')], 2),
    ('trade', 'direction'): (ACCOUNT, TRADE_HEADER + ['业务名称'], [_with(TRADE, 业务名称='')], 2),
    ('trade', 'quantity'): (ACCOUNT, [*TRADE_HEADER[:4], '成交数量(手)', *TRADE_HEADER[5:]],
                            [{**_with(TRADE, 证券代码='113050', 证券名称='示例转债', 成交价格='100.00', 成交金额='1000.00',
                                      发生金额='-1005.00'), '成交数量(手)': '1'}], 2),
    ('trade', 'price'): (ACCOUNT, TRADE_HEADER, [_with(TRADE, 成交价格='', 成交金额='')], 2),
    ('trade', 'currency'): (ACCOUNT, TRADE_HEADER, [_with(TRADE, 币种='欧元')], 2),
    ('trade', 'fee'): (ACCOUNT, TRADE_HEADER, [_with(TRADE, 手续费='', 发生金额='')], 2),
    ('trade', 'account'): (ACCOUNT, TRADE_HEADER, _three_accounts(TRADE, '成交日期'), 4),
    ('trade', 'duplicate'): (ACCOUNT, TRADE_HEADER, [TRADE], 2, [MANUAL_BUY]),
    ('trade', 'sign'): (ACCOUNT, TRADE_HEADER, [_with(TRADE, 成交价格='-30.00')], 2),
    ('trade', 'bounds'): (ACCOUNT, TRADE_HEADER, [_with(TRADE, 成交数量='2000000000000', 成交金额='60000000000000.00',
                                                      发生金额='-60000000000005.00')], 2),
    ('connect', 'ticker'): (ACCOUNT, CONNECT_HEADER, [_with(CONNECT, 证券代码='SHA:00700')], 2),
    ('connect', 'direction'): (ACCOUNT, CONNECT_HEADER + ['业务名称'], [_with(CONNECT, 业务名称='港股通卖出')], 2),
    ('connect', 'quantity'): (ACCOUNT, [*CONNECT_HEADER[:4], '成交数量(手)', *CONNECT_HEADER[5:]],
                              [{**CONNECT, '成交数量(手)': '2'}], 2),
    ('connect', 'price'): (ACCOUNT, CONNECT_HEADER, [_with(CONNECT, 成交价格='', 成交金额='')], 2),
    ('connect', 'currency'): (ACCOUNT, CONNECT_HEADER, [_with(CONNECT, 币种='美元')], 2),
    ('connect', 'fee'): (ACCOUNT, CONNECT_HEADER, [_with(CONNECT, 佣金='')], 2),
    ('connect', 'account'): (ACCOUNT, CONNECT_HEADER, _three_accounts(CONNECT, '成交日期'), 4),
    ('connect', 'duplicate'): (ACCOUNT, CONNECT_HEADER, [CONNECT], 2,
                               [booked(kind='fx', date='2026-11-05', currency='CNY', amount='69925',
                                       to_currency='HKD', received_amount='76005.43')]),
    ('connect', 'sign'): (ACCOUNT, CONNECT_HEADER, [_with(CONNECT, 发生金额='69925.00')], 2),
    ('connect', 'bounds'): (ACCOUNT, CONNECT_HEADER, [_with(CONNECT, 成交价格='2000000000.00', 成交金额='400000000000.00',
                                                          发生金额='-368000000005.00')], 2),
    ('cash_flow', 'ticker'): (ACCOUNT, CASH_HEADER, [_with(DIVIDEND, 证券代码='SHA:000001')], 2),
    ('cash_flow', 'direction'): (ACCOUNT, CASH_HEADER + ['摘要'], [_with(DIVIDEND, 摘要='股息红利差异扣税')], 2),
    ('cash_flow', 'currency'): (ACCOUNT, CASH_HEADER, [_with(DIVIDEND, 币种='欧元')], 2),
    ('cash_flow', 'fee'): (ACCOUNT, CASH_HEADER + ['手续费'], [_with(DIVIDEND, 手续费='5.00')], 2),
    ('cash_flow', 'account'): (ACCOUNT, CASH_HEADER, _three_accounts(DIVIDEND, '发生日期'), 4),
    ('cash_flow', 'duplicate'): (ACCOUNT, CASH_HEADER, [DIVIDEND], 2,
                                 [booked(kind='dividend', date='2026-11-03', ticker='SHA:600036', currency='CNY',
                                         amount='100')]),
    ('cash_flow', 'sign'): (ACCOUNT, CASH_HEADER, [_with(DIVIDEND, 发生金额='-100.00')], 2),
    ('cash_flow', 'bounds'): (ACCOUNT, CASH_HEADER, [_with(DIVIDEND, 发生金额='2000000000000000.00')], 2),
    ('repo', 'direction'): (ACCOUNT, CASH_HEADER, [_with(LEND, 业务名称='融券购回'), BACK], 3),
    ('repo', 'quantity'): (ACCOUNT, CASH_HEADER + ['成交数量'], [_with(LEND, 成交数量='100'), _with(BACK, 成交数量='200')], 3),
    ('repo', 'currency'): (ACCOUNT, CASH_HEADER, [_with(LEND, 币种='欧元'), _with(BACK, 币种='欧元')], 3),
    ('repo', 'account'): (ACCOUNT, CASH_HEADER, [_with(LEND, 资金账号='111'), _with(BACK, 资金账号='222')], 3),
    ('repo', 'duplicate'): (ACCOUNT, CASH_HEADER, [LEND, BACK], 3,
                            [booked(kind='interest', date='2026-11-04', currency='CNY', amount='1')]),
    ('repo', 'sign'): (ACCOUNT, CASH_HEADER, [_with(LEND, 发生金额='10000.00'), BACK], 3),
    ('repo', 'bounds'): (ACCOUNT, CASH_HEADER, [LEND, _with(BACK, 发生金额='20000.00')], 3),
    ('split', 'ticker'): (ACCOUNT, SPLIT_HEADER, [_with(SPLIT, 证券代码='SHA:000001')], 2),
    ('split', 'direction'): (ACCOUNT, SPLIT_HEADER + ['摘要'], [_with(SPLIT, 摘要='红利入账')], 2),
    ('split', 'quantity'): (ACCOUNT, SPLIT_HEADER, [_with(SPLIT, 成交数量='')], 2),
    ('split', 'fee'): (ACCOUNT, SPLIT_HEADER + ['手续费'], [_with(SPLIT, 手续费='5.00')], 2),
    ('split', 'account'): (ACCOUNT, SPLIT_HEADER, _three_accounts(SPLIT, '发生日期'), 4),
    ('split', 'duplicate'): (ACCOUNT, SPLIT_HEADER, [SPLIT], 2,
                             [booked(kind='split', date='2026-11-05', ticker='SHA:600036', ratio='1.3')]),
    ('split', 'sign'): (ACCOUNT, SPLIT_HEADER, [_with(SPLIT, 成交数量='-300')], 2),
    ('split', 'bounds'): (ACCOUNT, SPLIT_HEADER, [_with(SPLIT, 成交数量='2000000')], 2),
    ('template', 'ticker'): (ACCOUNT, TEMPLATE_HEADER, [_with(TEMPLATE, ticker='SHA:000001')], 2),
    ('template', 'direction'): (ACCOUNT, TEMPLATE_HEADER, [_with(TEMPLATE, kind='buyy')], 2),
    ('template', 'quantity'): (ACCOUNT, TEMPLATE_HEADER, [_with(TEMPLATE, quantity='')], 2),
    ('template', 'price'): (ACCOUNT, TEMPLATE_HEADER, [_with(TEMPLATE, price='')], 2),
    ('template', 'currency'): (ACCOUNT, TEMPLATE_HEADER, [_with(DEPOSIT, kind='dividend', ticker='SHA:600036',
                                                               currency='USD', amount='100')], 2),
    ('template', 'fee'): (ACCOUNT, TEMPLATE_HEADER, [_with(TEMPLATE, fee='')], 2),
    ('template', 'account'): (ACCOUNT, TEMPLATE_HEADER, [_with(DEPOSIT, account='其他账户')], 2),
    ('template', 'duplicate'): (ACCOUNT, TEMPLATE_HEADER, [TEMPLATE], 2,
                                [booked(kind='buy', date='2026-11-03', ticker='SHA:600519', currency='CNY',
                                        quantity='100', price='1680', fee='5')]),
    ('template', 'sign'): (ACCOUNT, TEMPLATE_HEADER, [_with(DEPOSIT, amount='-10')], 2),
    ('template', 'bounds'): (ACCOUNT, TEMPLATE_HEADER, [_with(DEPOSIT, amount='1e16')], 2),
    ('opening', 'ticker'): (NEW, HOLDING_HEADER, [_with(HOLDING, 证券代码='SHA:000001')], 2),
    ('opening', 'quantity'): (NEW, HOLDING_HEADER, [_with(HOLDING, 股票余额='')], 2),
    ('opening', 'price'): (NEW, HOLDING_HEADER, [_with(HOLDING, 成本价='')], 2),
    ('opening', 'currency'): (NEW, HOLDING_HEADER, [_with(HOLDING, 币种='欧元')], 2),
    ('opening', 'account'): (NEW, HOLDING_HEADER, [_with(HOLDING, 资金账号='111'),
                                                   _with(HOLDING, 证券代码='000001', 证券名称='平安银行', 资金账号='111'),
                                                   _with(HOLDING, 证券代码='510300', 证券名称='沪深300ETF', 资金账号='222')], 4),
    ('opening', 'sign'): (NEW, HOLDING_HEADER, [_with(HOLDING, 成本价='-5')], 2),
    ('opening', 'bounds'): (NEW, HOLDING_HEADER, [_with(HOLDING, 股票余额='2000000000000')], 2),
}

CELLS = [(path, check) for path in GUARDS for check in CHECKS if not GUARDS[path][check].startswith('n/a')]


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    ledger = ledger_at(tmp_path)
    open_account(ledger)
    return ledger


def _import(ledger, case, name):
    account, header, rows, number, *setup = case
    for event in (setup[0] if setup else []):
        book(ledger, [event])
    _created, view, by_row = run(csv(header, *[_cells(header, row) for row in rows]), name, account=account)
    return view, by_row[number]


def test_every_cell_of_the_guard_table_has_a_probe():
    assert set(CLEAN) == set(GUARDS)
    for path, guards in GUARDS.items():
        assert set(guards) == {'builder', *CHECKS}, path
        for check in CHECKS:
            if guards[check].startswith('n/a'):
                assert len(guards[check]) > len('n/a: ') and (path, check) not in PROBES, (path, check)
            else:
                assert (path, check) in PROBES, f'no probe for {path} × {check}: add one to PROBES'


@pytest.mark.parametrize('path', sorted(GUARDS))
def test_the_clean_row_of_each_path_is_booked(ledger, path):
    view, row = _import(ledger, CLEAN[path], f'{path}.csv')
    assert row['status'] == 'new' and row['include'], (path, row['messages'])
    assert not view['blockers'] or view['statement'] == 'holdings', (path, view['blockers'])   # an opening asks for its date
    assert row['events'] or view['statement'] == 'holdings', path


@pytest.mark.parametrize('path, check', CELLS)
def test_a_row_that_fails_one_check_is_never_booked(ledger, path, check):
    view, row = _import(ledger, PROBES[(path, check)], f'{path}_{check}.csv')
    conflicts = [c['field'] for c in view['column_conflicts'] if not c['resolved']]
    if check == 'duplicate':
        assert row['status'] in ('possible_duplicate', 'imported', 'changed') and not row['include'], \
            (path, check, row['status'], row['messages'])
    elif check == 'direction' and conflicts:
        assert 'action' in conflicts and view['blockers'], (path, check, view['blockers'])   # cannot be proposed
    else:
        assert row['status'] in ('review', 'unsupported') and row['events'] == [] and not row['include'], \
            (path, check, row['status'], row['messages'])
    if check == 'account':
        assert 'account' in row['needs'] or '账户' in row['messages'][0], row['messages']
    if view['statement'] == 'holdings':
        assert all(p['ticker'] != row['ticker'] or row['ticker'] is None for p in view['holdings']['positions'])


def _calls(function: ast.AST):
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in ('_event', 'normalize'):
                yield node.func.id
            elif isinstance(node.func, ast.Attribute) and node.func.attr in ('_event', 'opening_event'):
                yield node.func.attr


def test_every_event_builder_is_in_the_guard_table():
    package = Path(transactions.__file__).parent
    builders = set()
    for source in sorted(package.glob('*.py')):
        tree = ast.parse(source.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(_calls(node)):
                builders.add(f'{source.stem}.{node.name}')
    listed = {guards['builder'] for guards in GUARDS.values()}
    assert builders - {EVENT_CONSTRUCTOR} == listed, \
        'a function builds ledger events without a row in transactions.GUARDS (or a listed one no longer does)'
