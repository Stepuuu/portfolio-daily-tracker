"""Business types → ledger events; fees, prices, 逆回购, 港股通, splits; review and skipped rows."""
from decimal import Decimal

import pytest

from core.importers import ImportFailure, session
from core.importers.transactions import classify, fee_candidates, fee_rule, ledger_product
from tests.importer_support import (ACCOUNT, OPENING, book, csv, fixture, ledger_at, open_account, patch, run)

TRADE = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额']


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    ledger = ledger_at(tmp_path)
    open_account(ledger)
    return ledger


def kinds(row):
    return [event['kind'] for event in row['events']]


def test_trades_repo_and_skips_from_a_ths_export(ledger):
    _s, view, rows = run(fixture('ths_trades_gbk.xls'), 'jgd.xls')
    assert view['preset']['label'] == '同花顺交割单' and view['preset']['note'] == '预设未经你的券商文件验证'
    assert (rows[3]['status'], kinds(rows[3]), rows[3]['ticker'], rows[3]['fee'], rows[3]['fee_source']) == \
        ('new', ['buy'], 'SHA:600519', '43.68', 'columns')
    event = rows[3]['events'][0]
    assert (event['quantity'], event['price'], event['fee'], event['currency'], event['source']) == \
        ('100', '1680', '43.68', 'CNY', f'import:{ACCOUNT}')
    assert event['external_id'] == 't:2026-11-03:600519:0012345'
    assert kinds(rows[4]) == ['sell'] and rows[4]['fee'] == '10.71' and rows[4]['ticker'] == 'SHE:000001'
    assert kinds(rows[10]) == ['sell'] and rows[10]['fee'] == '64.6'
    assert rows[6]['status'] == 'merged' and rows[6]['merged_into'] == 7
    assert kinds(rows[7]) == ['interest'] and rows[7]['events'][0]['amount'] == '4.89'
    assert (rows[8]['status'], rows[8]['messages']) == ('skipped', ['撤单 / 废单'])
    assert (rows[9]['status'], rows[9]['messages']) == ('skipped', ['申购配号'])
    assert rows[11]['status'] == 'skipped' and rows[11]['category'] == 'footer'
    assert view['counts']['new'] == 5 and view['counts']['included_events'] == 5
    assert {g['label'] for g in view['skipped']} >= {'撤单 / 废单', '申购配号', '逆回购 2 行合并为利息 4.89'}


def test_every_cash_business_type_from_a_statement(ledger):
    _s, view, rows = run(fixture('eastmoney_statement_bom.csv'), 'dzd.csv')
    expected = {2: ('deposit', '50000', None), 4: ('dividend', '1936', 'SHA:600036'),
                5: ('fee', '96.8', 'SHA:600036'), 7: ('interest', '12.34', None), 8: ('withdrawal', '20000', None)}
    for number, (kind, value, ticker) in expected.items():
        [event] = rows[number]['events']
        assert (event['kind'], event['amount'], event.get('ticker'), event['currency']) == (kind, value, ticker, 'CNY')
    [split] = rows[6]['events']
    assert split['kind'] == 'split' and split['ratio'] == '1.2' and split['ticker'] == 'SHA:600036'
    assert rows[9]['category'] == 'ipo_payment' and rows[10]['category'] == 'ipo'
    for number in (9, 10, 12, 13):
        assert rows[number]['status'] == 'review' and rows[number]['events'] == [] and not rows[number]['can_include']
    assert '资金调整' in rows[13]['messages'][0] and rows[13]['needs'] == ['kind']
    assert rows[11]['status'] == 'unsupported' and '北交所' in rows[11]['messages'][0]
    assert 'ticker' in rows[10]['needs'] and 'fee' in rows[10]['needs']


def test_ipo_rows_are_resolved_by_explicit_edits(ledger):
    created, _view, _rows = run(fixture('eastmoney_statement_bom.csv'), 'dzd.csv')
    _view, rows = patch(created['id'], rows=[{'row': 10, 'kind': 'buy', 'price': '10.00', 'fee': '0'},
                                             {'row': 9, 'kind': 'skip'}])
    [event] = rows[10]['events']
    assert rows[10]['status'] == 'new' and (event['kind'], event['ticker'], event['quantity'], event['price'], event['fee']) \
        == ('buy', 'SHA:603999', '500', '10', '0')
    assert rows[10]['fee_source'] == 'edited' and rows[10]['price_source'] == 'edited'
    assert rows[9]['status'] == 'skipped'
    _view, rows = patch(created['id'], rows=[{'row': 10, 'kind': 'buy', 'price': '10.00', 'fee': None}])
    assert rows[10]['status'] == 'review' and rows[10]['needs'] == ['fee'], 'a fee is never assumed to be 0'


def test_fee_sum_overlap_rules_and_derivation(ledger):
    _s, _view, rows = run(fixture('tdx_trades_gbk.txt'), 'tdx.txt')
    # 交易规费 = 经手费 + 证管费 is not counted twice.
    assert [rows[n]['fee'] for n in (5, 4, 3)] == ['5.52', '5.97', '8.15']
    assert [rows[n]['order'] for n in (5, 4, 3)] == [0, 1, 2], 'newest-first files are booked oldest first'
    header = TRADE + ['发生金额']
    _s, _v, rows = run(csv(header, ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00', '-168043.68']), 'a.csv')
    assert (rows[2]['fee'], rows[2]['fee_source']) == ('43.68', 'derived')
    _s, _v, rows = run(csv(header + ['手续费', '佣金', '过户费'],
                           ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00', '-168043.68',
                            '43.68', '42.00', '1.68']), 'b.csv')
    assert rows[2]['fee'] == '43.68' and '手续费列' in rows[2]['notes'][0]
    _s, _v, rows = run(csv(header + ['佣金', '净佣金', '规费', '过户费'],
                           ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00', '-168043.68',
                            '42.00', '41.00', '1.00', '1.68']), 'c.csv')
    assert rows[2]['fee'] == '43.68'
    assert fee_candidates([('佣金', Decimal(42)), ('净佣金', Decimal(41)), ('规费', Decimal(1))])[0][0] == 42


@pytest.mark.parametrize('parts, total, certain', [
    # 佣金 = 净佣金 + 规费 included in it: count the gross 佣金, never 净佣金 as well.
    ([('佣金', '5'), ('净佣金', '4')], '5', True),
    ([('佣金', '5'), ('净佣金', '4'), ('印花税', '1'), ('过户费', '0.1')], '6.1', True),
    ([('佣金', '5'), ('净佣金', '4'), ('规费', '1'), ('过户费', '0.1')], '5.1', True),     # the difference is the 规费
    ([('佣金', '5'), ('净佣金', '5'), ('规费', '1')], '6', True),                        # no difference: 规费 is extra
    ([('佣金', '5'), ('净佣金', '4'), ('规费', '0.5')], '5', False),                     # neither: uncertain
    ([('佣金', '5'), ('净佣金', '6')], '5', False),                                     # net above gross
    # 佣金 next to 规费 without 净佣金: 佣金 may or may not include it.
    ([('佣金', '5'), ('交易规费', '0.21')], '5.21', False),
    ([('佣金', '5'), ('印花税', '2.88'), ('过户费', '0.06')], '7.94', True),
    # 净佣金 alone lacks the 规费 unless its columns are there.
    ([('净佣金', '4')], '4', False),
    ([('净佣金', '4'), ('经手费', '0.17'), ('证管费', '0.04')], '4.21', True),
    # 交易规费 = 经手费 + 证管费: counted once when they agree, uncertain when they do not.
    ([('交易规费', '0.21'), ('经手费', '0.17'), ('证管费', '0.04'), ('手续费', '5')], '5.21', True),
    ([('交易规费', '0.3'), ('经手费', '0.17'), ('证管费', '0.04'), ('手续费', '5')], '5.21', False),
    # 手续费 is separate from 印花税 / 过户费, but may total 佣金.
    ([('手续费', '42'), ('印花税', '0'), ('过户费', '1.68')], '43.68', True),
    ([('手续费', '43.68'), ('佣金', '42')], '85.68', False),
    ([('手续费', '5'), ('净佣金', '4')], '9', False),
    # The same fee twice.
    ([('佣金', '5'), ('佣金', '5')], '10', False),
])
def test_fee_overlap_rules(parts, total, certain):
    candidates, problem = fee_rule([(name, Decimal(value)) for name, value in parts])
    assert candidates[0][0] == Decimal(total) and (problem is None) is certain, (candidates, problem)
    assert fee_candidates([(name, Decimal(value)) for name, value in parts]) == candidates


def test_gross_commission_is_the_fee_when_net_commission_is_also_listed(ledger):
    # Review finding 8: 佣金 5 next to 净佣金 4 without a cash column booked a fee of 4.
    _s, _v, rows = run(csv(TRADE + ['佣金', '净佣金'], ['2026-11-03', '600036', '招商银行', '证券买入', 100, '30.00', '3000.00',
                                                    '5.00', '4.00']), 'fees.csv')
    assert rows[2]['status'] == 'new' and rows[2]['fee'] == '5' and rows[2]['events'][0]['fee'] == '5'
    _s, _v, rows = run(csv(TRADE + ['佣金', '交易规费'], ['2026-11-03', '600036', '招商银行', '证券买入', 100, '30.00',
                                                     '3000.00', '5.00', '0.21']), 'regulatory.csv')
    assert rows[2]['status'] == 'review' and 'fee' in rows[2]['needs'] and '规费' in rows[2]['messages'][0]
    _s, _v, rows = run(csv(TRADE + ['佣金', '交易规费', '发生金额'], ['2026-11-03', '600036', '招商银行', '证券买入', 100,
                                                              '30.00', '3000.00', '5.00', '0.21', '-3005.00']), 'cash.csv')
    assert rows[2]['status'] == 'new' and rows[2]['fee'] == '5' and '佣金（已含规费）' in rows[2]['notes'][0]


@pytest.mark.parametrize('header, cells, message', [
    (TRADE, ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00'], '没有费用列'),
    (TRADE + ['手续费', '印花税'], ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00', '', ''],
     '费用列都是空的'),
    (TRADE + ['手续费', '发生金额'], ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00', '5.00',
                                    '-168043.68'], '不一致'),
    (TRADE + ['发生金额'], ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00', '-167000.00'],
     '负数'),
    (TRADE + ['发生金额'], ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00', '-178000.00'],
     '过高'),
    (TRADE + ['手续费', '佣金'], ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00', '43.68',
                                '42.00'], '同时出现'),
])
def test_unknown_or_inconsistent_fee_needs_review_never_zero(ledger, header, cells, message):
    _s, view, rows = run(csv(header, cells), 'fee.csv')
    assert rows[2]['status'] == 'review' and rows[2]['events'] == [] and rows[2]['fee'] is None
    assert message in rows[2]['messages'][0] and 'fee' in rows[2]['needs']
    assert view['counts']['included_events'] == 0


def test_price_comes_from_turnover_when_the_average_is_rounded(ledger):
    _s, _view, rows = run(fixture('ths_trades_gbk.xls'), 'jgd.xls')
    event = rows[5]['events'][0]
    # 9,605.00 / 300 does not terminate: the price has the places that make quantity × price exactly 9,605.00 in the
    # ledger's arithmetic, so the cash is the broker's to the cent (32.0166666667 booked 9,610.10000001).
    assert event['price'] == '32.016666666666666666666666667' and '除不尽' in rows[5]['notes'][0]
    assert (rows[5]['price_source'], rows[3]['price_source']) == ('derived', 'columns'), \
        'the page marks a price worked out from 成交金额 ÷ 数量'
    cash = ledger_product(Decimal(event['quantity']), Decimal(event['price'])) + Decimal(event['fee'])
    assert cash == Decimal('9610.10')
    _s, _view, rows = run(csv(TRADE + ['发生金额'], ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '16.80',
                                                    '168000.00', '-168043.68']), 'unit.csv')
    assert rows[2]['status'] == 'review' and '单位' in rows[2]['messages'][0]
    _s, _view, rows = run(csv(['成交日期', '证券代码', '操作', '成交数量', '成交金额', '发生金额'],
                              ['2026-11-03', '600519', '买入', 300, '9605.00', '-9610.10']), 'noprice.csv')
    assert rows[2]['events'][0]['price'] == '32.016666666666666666666666667' and rows[2]['fee'] == '5.1'
    assert rows[2]['price_source'] == 'derived' and rows[2]['fee_source'] == 'derived'


def test_repo_legs_pair_into_net_interest_or_need_review(ledger):
    header = ['成交日期', '证券代码', '证券名称', '业务名称', '成交数量', '成交金额', '发生金额']
    out = ['2026-11-05', '204001', 'GC001', '融券回购', 100, '100000.00', '-100001.00']
    back = ['2026-11-06', '204001', 'GC001', '融券购回', 100, '100000.00', '100005.89']
    _s, _view, rows = run(csv(header, out), 'a.csv')
    assert rows[2]['status'] == 'review' and '购回' in rows[2]['messages'][0]
    _s, _view, rows = run(csv(header, back), 'b.csv')
    assert rows[2]['status'] == 'review' and '借出' in rows[2]['messages'][0]
    _s, _view, rows = run(csv(header, out, back[:-1] + ['100000.50']), 'c.csv')
    assert rows[3]['status'] == 'review' and '不是正数' in rows[3]['messages'][0]
    two = ['2026-11-05', '131810', 'R-001', '', 200, '20000.00', '-20000.20']
    two_back = ['2026-11-06', '131810', 'R-001', '', 200, '20000.00', '20001.30']
    _s, _view, rows = run(csv(header, two, two_back), 'd.csv')
    assert rows[2]['status'] == 'merged' and rows[3]['events'][0]['amount'] == '1.1', 'repo code without an action'


REPO = ['成交日期', '证券代码', '证券名称', '业务名称', '发生金额']


def interest(rows):
    return sorted(e['amount'] for r in rows.values() for e in r['events'] if e['kind'] == 'interest')


def test_repo_legs_without_quantities_pair_only_by_plausible_principal(ledger):
    # Review finding 1: FIFO paired -10,000 with +100,010 and booked 90,010 of interest.
    _s, view, rows = run(csv(REPO, ['2026-11-03', '204001', 'GC001', '融券回购', '-10000.00'],
                             ['2026-11-03', '204001', 'GC001', '融券回购', '-100000.00'],
                             ['2026-11-04', '204001', 'GC001', '融券购回', '100010.00'],
                             ['2026-11-04', '204001', 'GC001', '融券购回', '10001.00']), 'repo.csv')
    assert interest(rows) == ['1', '10'], 'the file nets 11'
    assert (rows[3]['merged_into'], rows[2]['merged_into']) == (4, 5)


def test_ambiguous_repo_legs_all_need_review(ledger):
    _s, view, rows = run(csv(REPO, ['2026-11-03', '204001', 'GC001', '融券回购', '-100000.00'],
                             ['2026-11-03', '204001', 'GC001', '融券回购', '-100001.00'],
                             ['2026-11-04', '204001', 'GC001', '融券购回', '100006.00'],
                             ['2026-11-04', '204001', 'GC001', '融券购回', '100005.00']), 'ambiguous.csv')
    assert [rows[n]['status'] for n in (2, 3, 4, 5)] == ['review'] * 4 and interest(rows) == []
    assert all('不唯一' in rows[n]['messages'][0] for n in (2, 3, 4, 5))


def test_repo_interest_above_the_sanity_bound_is_never_booked(ledger):
    _s, _view, rows = run(csv(REPO, ['2026-11-03', '204001', 'GC001', '融券回购', '-100000.00'],
                              ['2026-11-04', '204001', 'GC001', '融券购回', '101000.00']), 'high.csv')
    assert [rows[2]['status'], rows[3]['status']] == ['review', 'review'] and interest(rows) == []
    assert '合理' in rows[3]['messages'][0]
    _s, _view, rows = run(csv(REPO, ['2026-11-03', '204007', 'GC007', '融券回购', '-100000.00'],
                              ['2026-11-04', '204007', 'GC007', '融券购回', '100005.00']), 'early.csv')
    assert interest(rows) == [] and rows[3]['status'] == 'review', 'a 7-day repo cannot come back after one day'


def test_repo_legs_pair_by_quantity_or_principal_when_the_file_has_them(ledger):
    header = ['成交日期', '证券代码', '证券名称', '业务名称', '成交数量', '成交金额', '发生金额']
    _s, _view, rows = run(csv(header, ['2026-11-03', '131810', 'R-001', '', 1000, '100000.00', '-100001.00'],
                              ['2026-11-03', '131810', 'R-001', '', 100, '10000.00', '-10000.10'],
                              ['2026-11-04', '131810', 'R-001', '', 100, '10000.00', '10000.60'],
                              ['2026-11-04', '131810', 'R-001', '', 1000, '100000.00', '100004.00']), 'qty.csv')
    assert interest(rows) == ['0.5', '3'] and rows[2]['merged_into'] == 5 and rows[3]['merged_into'] == 4


def test_rolled_repo_with_identical_lending_legs_pairs_in_order(ledger):
    _s, _view, rows = run(csv(REPO, ['2026-11-03', '204001', 'GC001', '融券回购', '-100001.00'],
                              ['2026-11-04', '204001', 'GC001', '融券购回', '100005.00'],
                              ['2026-11-04', '204001', 'GC001', '融券回购', '-100001.00'],
                              ['2026-11-05', '204001', 'GC001', '融券购回', '100006.00'],
                              ['2026-11-05', '204001', 'GC001', '融券回购', '-100001.00']), 'roll.csv')
    assert interest(rows) == ['4', '5'] and rows[2]['merged_into'] == 3 and rows[4]['merged_into'] == 5
    assert rows[6]['status'] == 'review' and '购回' in rows[6]['messages'][0], 'its buy-back is in the next file'


def test_hk_connect_rows_split_into_fx_and_trade(ledger):
    created, view, rows = run(fixture('hk_connect_bom.csv'), 'hk.csv')
    fx, buy = rows[2]['events']
    assert (fx['kind'], fx['currency'], fx['amount'], fx['to_currency'], fx['received_amount']) == \
        ('fx', 'CNY', '70008.35', 'HKD', '76096.03')
    assert (buy['kind'], buy['ticker'], buy['currency'], buy['quantity'], buy['price'], buy['fee']) == \
        ('buy', 'HKG:00700', 'HKD', '200', '380', '96.03')
    assert 'HK$96.03' in rows[2]['notes'][0]
    sell, back = rows[3]['events']
    assert (sell['kind'], sell['fee'], back['currency'], back['amount'], back['to_currency'], back['received_amount']) == \
        ('sell', '53.09', 'HKD', '39946.91', 'CNY', '36551.42')
    result = session.propose(created['id'])
    ledger.confirm(result['proposal']['id'])
    account = ledger.state()['accounts'][ACCOUNT]
    assert Decimal(account['cash_balances']['HKD']) == 0, 'HKD only passes through'
    assert Decimal(account['cash_balances']['CNY']) == Decimal('466543.07')
    assert account['positions']['HKG:00700']['quantity'] == '100'
    assert view['reconciliation']['consistent']


def test_hk_rows_without_a_cny_amount_or_currency_need_review(ledger):
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '佣金', '币种']
    _s, _v, rows = run(csv(header, ['2026-11-05', '00700', '腾讯控股', '港股通买入', 200, '380.00', '76000.00', '5.00',
                                    '人民币']), 'a.csv')
    assert rows[2]['status'] == 'review' and '人民币清算金额' in rows[2]['messages'][0]
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '发生金额', '交易市场']
    created, _v, rows = run(csv(header, ['2026-11-05', '00700', '腾讯控股', '买入', 200, '380.00', '76000.00',
                                         '-70008.35', '香港']), 'b.csv')
    assert rows[2]['status'] == 'review' and '结算币种' in rows[2]['messages'][0] and rows[2]['needs'] == ['currency']
    _view, rows = patch(created['id'], rows=[{'row': 2, 'currency': 'CNY', 'fee': '88.35', 'fx_rate': '0.92'}])
    assert [e['kind'] for e in rows[2]['events']] == ['fx', 'buy'] and rows[2]['events'][1]['fee'] == '96.03'
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '佣金', '发生金额']
    _s, _v, rows = run(csv(header, ['2026-11-05', '00700', '腾讯控股', '港股通买入', 200, '380.00', '76000.00', '5.00',
                                    '-76005.00']), 'c.csv')
    assert rows[2]['status'] == 'review' and '汇率是 1' in rows[2]['messages'][0] and '合理' in rows[2]['messages'][0], 'an HKD amount is not a CNY settlement'


def test_currency_evidence_is_never_discarded(ledger):
    # Review finding 7: 币种=EUR, HK$100 and 发生金额(美元) all became CNY 100.
    created, _v, rows = run(csv(['发生日期', '业务名称', '发生金额', '币种'], ['2026-11-03', '银行转证券', '100', 'EUR']),
                            'currency.csv')
    assert rows[2]['status'] == 'review' and 'EUR' in rows[2]['messages'][0] and 'currency' in rows[2]['needs']
    _view, rows = patch(created['id'], rows=[{'row': 2, 'currency': 'CNY'}])
    assert rows[2]['status'] == 'new' and rows[2]['events'][0]['currency'] == 'CNY', 'the person may decide'
    created, _v, rows = run(csv(['发生日期', '业务名称', '发生金额'], ['2026-11-03', '银行转证券', 'HK$100']), 'currency_symbol.csv')
    assert (rows[2]['status'], rows[2]['currency'], rows[2]['needs']) == ('review', 'HKD', ['fx_rate'])
    _view, rows = patch(created['id'], rows=[{'row': 2, 'fx_rate': '0.92'}])
    assert (rows[2]['events'][0]['currency'], rows[2]['events'][0]['amount']) == ('HKD', '100')
    _c, _v, rows = run(csv(['发生日期', '业务名称', '发生金额(美元)'], ['2026-11-03', '银行转证券', '100']), 'currency_header.csv')
    assert (rows[2]['status'], rows[2]['currency'], rows[2]['needs']) == ('review', 'USD', ['fx_rate'])
    _c, _v, rows = run(csv(['发生日期', '业务名称', '发生金额', '资金余额'], ['2026-11-03', '利息归本', 'US$1.00', '1.00']),
                       'interest.csv')
    assert rows[2]['status'] == 'new' and rows[2]['events'][0]['currency'] == 'USD', 'a supported currency is carried'


@pytest.mark.parametrize('header, cells, words', [
    (['发生日期', '业务名称', '发生金额', '币种'], ['2026-11-03', '银行转证券', 'HK$100', '人民币'], '不一致'),
    (['发生日期', '业务名称', '发生金额'], ['2026-11-03', '银行转证券', '$100'], '美元还是港币'),
    (['发生日期', '业务名称', '发生金额(欧元)'], ['2026-11-03', '银行转证券', '100'], '欧元'),
    (TRADE + ['手续费', '发生金额(美元)'], ['2026-11-03', '600036', '招商银行', '证券买入', 100, '30.00', '3000.00', '5.00',
                                       '-3005.00'], 'USD'),
    (['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格(美元)', '成交金额', '手续费'],
     ['2026-11-03', '600036', '招商银行', '证券买入', 100, '30.00', '3000.00', '5.00'], 'USD'),
])
def test_unsupported_or_conflicting_currency_needs_review(ledger, header, cells, words):
    _c, view, rows = run(csv(header, cells), 'conflict.csv')
    assert rows[2]['status'] == 'review' and rows[2]['events'] == [] and view['counts']['included_events'] == 0
    assert words in rows[2]['messages'][0]


def test_repo_and_dividend_amounts_keep_their_currency_evidence(ledger):
    # Review round 2, finding 3: EUR-marked 204001 legs became CNY interest 1, and a 600036 dividend read
    # from 成交金额(美元) became CNY 100.
    _c, view, rows = run(csv(['发生日期', '业务名称', '证券代码', '证券名称', '发生金额', '币种'],
                             ['2026-11-03', '', '204001', 'GC001', '-10000.00', 'EUR'],
                             ['2026-11-04', '', '204001', 'GC001', '10001.00', 'EUR']), 'repo_eur.csv')
    for number in (2, 3):
        assert rows[number]['status'] == 'review' and rows[number]['events'] == [], rows[number]
        assert 'EUR' in ' '.join(rows[number]['messages']) and 'currency' in rows[number]['needs']
    assert view['counts']['included_events'] == 0
    _c, _v, rows = run(csv(['发生日期', '业务名称', '证券代码', '证券名称', '发生金额', '币种'],
                           ['2026-11-03', '', '204001', 'GC001', '-10000.00', '美元'],
                           ['2026-11-04', '', '204001', 'GC001', '10001.00', '美元']), 'repo_usd.csv')
    assert [rows[2]['status'], rows[3]['status']] == ['review', 'review'], '逆回购 settles in CNY only'
    for header, cells in ((['发生日期', '业务名称', '证券代码', '证券名称', '成交金额(美元)'],
                           ['2026-11-03', '红利入账', '600036', '招商银行', '100']),
                          (['发生日期', '业务名称', '证券代码', '证券名称', '发生金额', '币种'],
                           ['2026-11-03', '红利入账', '600036', '招商银行', '100', '美元']),
                          (['发生日期', '业务名称', '证券代码', '证券名称', '发生金额(人民币)', '成交金额(美元)'],
                           ['2026-11-03', '红利入账', '600036', '招商银行', '', '100'])):
        _c, _v, rows = run(csv(header, cells), 'dividend.csv')
        assert rows[2]['status'] == 'review' and rows[2]['events'] == [], (header, rows[2])
        assert 'USD' in ' '.join(rows[2]['messages']) or '美元' in ' '.join(rows[2]['messages'])
    _c, _v, rows = run(csv(['发生日期', '业务名称', '证券代码', '证券名称', '成交金额'],
                           ['2026-11-03', '红利入账', '600036', '招商银行', '100']), 'dividend_cny.csv')
    assert rows[2]['status'] == 'new' and rows[2]['events'][0]['currency'] == 'CNY', 'no evidence at all is CNY'


@pytest.mark.parametrize('header, cells, words', [
    (TRADE + ['手续费'], ['2026-11-03', '600036', '招商银行', '证券买入', 100, '-10.00', '1000.00', '5.00'], '成交价格'),
    (TRADE + ['手续费'], ['2026-11-03', '600036', '招商银行', '证券卖出', 100, '-10.00', '', '5.00'], '成交价格'),
    (['发生日期', '业务名称', '证券代码', '证券名称', '成交金额'], ['2026-11-03', '红利入账', '600036', '招商银行', '-100'], '正负'),
    (TRADE + ['手续费'], ['2026-11-03', '600036', '招商银行', '证券买入', 100, '10.00', '1000.00', '-5.00'], '负数'),
])
def test_impossible_signs_need_review(ledger, header, cells, words):
    # Review round 2, finding 7: a buy at price -10 became a new buy at 10. A dividend read from a negative
    # turnover, and a negative fee no cash amount can check, were made positive the same way.
    _c, view, rows = run(csv(header, cells), 'sign.csv')
    assert rows[2]['status'] == 'review' and rows[2]['events'] == [] and view['counts']['included_events'] == 0
    assert words in ' '.join(rows[2]['messages']), rows[2]['messages']


def test_negative_fee_columns_are_read_when_the_cash_amount_confirms_them(ledger):
    _c, _v, rows = run(csv(TRADE + ['手续费', '发生金额'],
                           ['2026-11-03', '600036', '招商银行', '证券买入', 100, '10.00', '1000.00', '-5.00', '-1005.00']),
                       'negative_fee.csv')
    assert rows[2]['status'] == 'new' and rows[2]['events'][0]['fee'] == '5'


LOTS = ['成交日期', '证券代码', '证券名称', '操作', '成交数量(手)', '成交金额', '手续费']


def test_quantities_in_lots_are_converted_only_when_the_lot_is_certain(ledger):
    # Review finding 6: 成交数量(手)=1 with turnover 1,000 became 1 share at 1,000.
    _c, _v, rows = run(csv(LOTS, ['2026-11-03', '000001', '平安银行', '证券买入', 1, '1000.00', '5.00'],
                           ['2026-11-03', '510300', '沪深300ETF', '证券买入', 2, '780.00', '5.00'],
                           ['2026-11-03', '113050', '示例转债', '证券买入', 1, '1000.00', '0.10']), 'lots.csv')
    event = rows[2]['events'][0]
    assert rows[2]['status'] == 'new' and (event['quantity'], event['price']) == ('100', '10')
    assert any('手' in note for note in rows[2]['notes'])
    assert rows[3]['events'][0]['quantity'] == '200'
    assert rows[4]['status'] == 'review' and '每手' in rows[4]['messages'][0] and 'quantity' in rows[4]['needs']


def test_scaled_amounts_are_converted_when_exact_and_reviewed_when_rounded(ledger):
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额(万元)', '发生金额(万元)']
    _c, _v, rows = run(csv(header, ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '16.800000', '-16.804368'],
                           ['2026-11-04', '600519', '贵州茅台', '证券买入', 100, '1680.00', '16.8', '-16.80']), 'wan.csv')
    assert rows[2]['status'] == 'new' and rows[2]['turnover'] == '168000' and rows[2]['fee'] == '43.68'
    assert rows[3]['status'] == 'review' and '精确到' in rows[3]['messages'][0], 'a rounded 万元 figure is never used'


@pytest.mark.parametrize('header, cells', [
    (['成交日期', '证券代码', '证券名称', '操作', '成交数量(元)', '成交价格', '成交金额', '手续费'],
     ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00', '5.00']),
    (['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额(%)', '手续费'],
     ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00', '5.00']),
    (['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额(股)', '手续费'],
     ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '1680.00', '168000.00', '5.00']),
])
def test_a_unit_that_does_not_fit_the_column_needs_review(ledger, header, cells):
    _c, _v, rows = run(csv(header, cells), 'unit.csv')
    assert rows[2]['status'] == 'review' and '单位' in rows[2]['messages'][0]


def test_units_are_part_of_the_saved_mapping_signature(ledger):
    from core.importers import mapping
    import hashlib
    assert mapping.signature(['成交日期', '证券代码']) == hashlib.sha1('成交日期\x1f证券代码'.encode()).hexdigest(), \
        'unchanged for headers without units'
    assert mapping.signature(LOTS) != mapping.signature([h.replace('(手)', '(股)') for h in LOTS])
    assert mapping.signature(['变动额(含费)']) == mapping.signature(['变动额']), 'a description is not a unit'
    created, _v, _rows = run(csv(LOTS, ['2026-11-03', '000001', '平安银行', '证券买入', 1, '1000.00', '5.00']), 'a.csv')
    patch(created['id'], mapping={'fees': []})
    shares = [h.replace('(手)', '(股)') for h in LOTS]
    _c, view, _rows = run(csv(shares, ['2026-11-03', '000001', '平安银行', '证券买入', 100, '1000.00', '5.00']), 'b.csv')
    assert view['mapping_source'] != 'saved', 'a layout with other units does not reuse the mapping'


def test_holdings_quantities_in_lots(ledger):
    header = ['证券代码', '证券名称', '持仓数量(手)', '成本价', '市值']
    _c, _v, rows = run(csv(header, ['600519', '贵州茅台', 2, '1650.00', '336000.00'],
                           ['113050', '示例转债', 1, '100.00', '1000.00']), 'cc.csv', account='新账户')
    assert rows[2]['status'] == 'new' and rows[2]['quantity'] == '200'
    assert rows[3]['status'] == 'review' and '每手' in rows[3]['messages'][0]


def test_currency_cells_with_a_code_in_brackets_are_read():
    from core.importers.transactions import _currency
    assert [_currency(v) for v in ('人民币(RMB)', '港币 HKD', '美元（USD）', '人民币元', 'RMB')] == \
        ['CNY', 'HKD', 'USD', 'CNY', 'CNY']
    assert _currency('EUR') == 'EUR' and _currency('人民币/美元') not in ('CNY', 'USD', None), 'two currencies'
    assert _currency('') is None


def test_holdings_rows_whose_numbers_cannot_be_computed_need_review(ledger, monkeypatch):
    from decimal import InvalidOperation
    from core.importers import holdings

    def broken(*_args, **_kwargs):
        raise InvalidOperation('quantize')
    monkeypatch.setattr(holdings.codes, 'lot_size', broken)
    header = ['证券代码', '证券名称', '持仓数量(手)', '成本价', '市值']
    _c, _v, rows = run(csv(header, ['600519', '贵州茅台', 2, '1650.00', '336000.00']), 'cc.csv', account='新账户')
    assert rows[2]['status'] == 'review' and '无法计算' in rows[2]['messages'][-1]


def test_holdings_with_an_unsupported_or_conflicting_currency_need_review(ledger):
    header = ['证券代码', '证券名称', '股票余额', '成本价', '市值', '币种']
    _c, _v, rows = run(csv(header, ['600519', '贵州茅台', 100, '1650.00', '168000.00', 'EUR'],
                           ['600036', '招商银行', 100, 'US$30.00', '3300.00', ''],
                           ['000001', '平安银行', 100, '10.00', '1120.00', '人民币']), 'cc.csv', account='新账户')
    assert [rows[n]['status'] for n in (2, 3, 4)] == ['review', 'review', 'new']
    assert 'EUR' in rows[2]['messages'][0] and 'USD' in rows[3]['messages'][0]


def test_split_ratio_must_be_exact_and_needs_a_position(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    ledger = ledger_at(tmp_path)
    open_account(ledger, positions=[{'ticker': 'SHA:600036', 'quantity': '3000', 'cost_price': '30'}])
    header = ['发生日期', '业务名称', '证券代码', '证券名称', '成交数量', '股份余额']
    _s, _v, rows = run(csv(header, ['2026-11-05', '红股入账', '600036', '招商银行', 1000, 4000],
                           ['2026-11-05', '送股入帐', '600519', '贵州茅台', 10, 10],
                           ['2026-11-06', '转增股入账', '600036', '招商银行', 1500, 9999]), 's.csv')
    assert rows[2]['status'] == 'review' and '不能用有限小数' in rows[2]['messages'][0]
    assert rows[3]['status'] == 'review' and '没有这只证券' in rows[3]['messages'][0]
    assert rows[4]['status'] == 'review' and '股份余额' in rows[4]['messages'][0]
    _s, _v, rows = run(csv(header, ['2026-11-06', '转增股入账', '600036', '招商银行', 1500, 4500]), 't.csv')
    assert rows[2]['events'][0]['ratio'] == '1.5'


def test_side_from_sign_only_when_the_action_column_is_missing(ledger):
    header = ['成交日期', '证券代码', '证券名称', '成交数量', '成交价格', '成交金额', '手续费', '发生金额']
    _s, _v, rows = run(csv(header, ['2026-11-03', '600519', '贵州茅台', 100, '1680.00', '168000.00', '43.68', '-168043.68'],
                           ['2026-11-04', '600519', '贵州茅台', -100, '1700.00', '170000.00', '50.00', '169950.00']),
                       'nosign.csv')
    assert kinds(rows[2]) == ['buy'] and '金额正负' in rows[2]['notes'][0]
    assert kinds(rows[3]) == ['sell']
    assert classify('', None, None) == ('blank', '')
    assert classify('担保品买入', None, None)[0] == 'buy' and classify('融资买入', None, None)[0] == 'margin'
    assert classify('股息红利差异扣税', None, None)[0] == 'tax' and classify('红股入账', None, None)[0] == 'split'
    assert classify('新股申购', None, None) == ('skip', '新股申购（未扣款）')
    assert classify('ETF申购', None, Decimal(-1))[0] == 'fund' and classify('债转股', None, None)[0] == 'conversion'


FILLED = TRADE + ['手续费', '发生金额']


@pytest.mark.parametrize('action, quantity, cash, words', [
    ('买入冲正', -100, '3000.00', '冲正'),          # review finding 5: became an ordinary buy of 100
    ('卖出冲正', -100, '-2995.00', '冲正'),
    ('红冲', 100, '-3005.00', '冲正'),
    ('冲销买入', 100, '3005.00', '冲正'),
    ('撤销买入', 100, '3005.00', '冲正'),
    ('红利冲正', '', '-1936.00', '冲正'),
    ('证券买入', -100, '-3005.00', '负数'),         # shares leave on a buy
    ('证券买入', 100, '3005.00', '方向'),           # cash comes in on a buy
    ('证券卖出', 100, '-2995.00', '方向'),          # cash goes out on a sell
])
def test_corrections_and_opposite_signs_need_review(ledger, action, quantity, cash, words):
    _s, view, rows = run(csv(FILLED, ['2026-11-03', '600036', '招商银行', action, quantity, '30.00', '3000.00', '5.00', cash]),
                         'reversal.csv')
    assert rows[2]['status'] == 'review' and rows[2]['events'] == [] and view['counts']['included_events'] == 0
    assert words in rows[2]['messages'][0]


def test_a_sell_with_a_negative_quantity_is_still_a_sell(ledger):
    _s, _view, rows = run(csv(FILLED, ['2026-11-03', '600036', '招商银行', '证券卖出', -100, '30.00', '3000.00', '5.00',
                                      '2995.00']), 'sell.csv')
    assert rows[2]['status'] == 'new' and rows[2]['events'][0]['kind'] == 'sell' and rows[2]['events'][0]['quantity'] == '100'
    assert classify('撤单', None, None) == ('skip', '撤单 / 废单') and classify('撤销指定', None, None)[0] == 'skip'


def test_rows_the_ledger_would_refuse_need_review(ledger):
    header = TRADE + ['发生金额']
    _s, _v, rows = run(csv(header, ['2026-11-03', '600519', '贵州茅台', '证券卖出', 100, '1680.00', '168000.00', '167956.32'],
                           ['2026-11-04', '600519', '贵州茅台', '证券买入', 1000, '1680.00', '1680000.00', '-1680420.00'],
                           ['2026-10-30', '000001', '平安银行', '证券买入', 100, '10.00', '1000.00', '-1005.00'],
                           ['2026-11-01', '000001', '平安银行', '证券买入', 100, '10.00', '1000.00', '-1005.00']), 'r.csv')
    assert rows[5]['status'] == 'new' and '与账户期初同一天' in rows[5]['notes'][-1]
    assert rows[2]['status'] == 'review' and '只持有 0 股' in rows[2]['messages'][0]
    assert rows[3]['status'] == 'review' and '现金不足' in rows[3]['messages'][0] and 'allow_margin' in rows[3]['needs']
    assert rows[4]['status'] == 'skipped' and '早于账户期初' in rows[4]['messages'][0]


def test_explicit_margin_makes_a_large_buy_bookable(ledger):
    created, _v, _rows = run(csv(TRADE + ['发生金额'], ['2026-11-04', '600519', '贵州茅台', '证券买入', 1000, '1680.00',
                                                       '1680000.00', '-1680420.00']), 'm.csv')
    _view, rows = patch(created['id'], rows=[{'row': 2, 'allow_margin': True}])
    assert rows[2]['status'] == 'new' and rows[2]['events'][0]['allow_margin'] is True
    proposal = session.propose(created['id'])['proposal']
    assert any('negative cash' in w for w in proposal['warnings'])


def test_system_template_rows(ledger):
    header = ['external_id', 'date', 'kind', 'account', 'ticker', 'currency', 'quantity', 'price', 'fee', 'amount']
    data = csv(header, ['t-1', '2026-11-03', 'buy', ACCOUNT, 'SHA:600519', 'CNY', 100, 1680, 5, ''],
               ['t-2', '2026-11-03', 'deposit', ACCOUNT, '', 'CNY', '', '', '', 1000],
               ['t-3', '2026-11-03', 'opening', ACCOUNT, '', '', '', '', '', ''],
               ['t-4', '2026-11-03', 'deposit', '其他账户', '', 'CNY', '', '', '', 1],
               ['', '2026-11-03', 'deposit', ACCOUNT, '', 'CNY', '', '', '', 1],
               ['t-6', '2026-11-03', 'deposit', ACCOUNT, '', 'EUR', '', '', '', 1])
    created, view, rows = run(data, 'template.csv')
    assert view['statement'] == 'template' and view['preset']['label'] == '本系统模板'
    assert kinds(rows[2]) == ['buy'] and rows[2]['events'][0]['source'] == f'import:{ACCOUNT}'
    assert kinds(rows[3]) == ['deposit']
    assert [rows[n]['status'] for n in (4, 5, 6, 7)] == ['review'] * 4
    assert '其他账户' in rows[5]['messages'][0] and 'external_id' in rows[6]['messages'][0]
    assert len(session.propose(created['id'])['proposal']['events']) == 2


TEMPLATE = ['external_id', 'date', 'kind', 'account', 'ticker', 'currency', 'quantity', 'price', 'fee', 'amount']


@pytest.mark.parametrize('cells, words', [
    (['t-1', '2026-11-03', 'deposit', ACCOUNT, '', 'CNY', '', '', '', '1e16'], '科学计数法'),
    (['t-1', '2026-11-03', 'deposit', ACCOUNT, '', 'CNY', '', '', '', '2000000000000000'], '超出'),
    (['t-1', '2026-11-03', 'deposit', ACCOUNT, '', 'CNY', '', '', '', '0.12345678901'], '小数位数'),
    (['t-1', '2026-11-03', 'deposit', ACCOUNT, '', 'CNY', '', '', '', '-10'], '大于 0'),
    (['t-1', '2026-11-03', 'buy', ACCOUNT, 'SHA:600519', 'CNY', '1e2', '1680', '5', ''], '科学计数法'),
    (['t-1', '2026-11-03', 'buy', ACCOUNT, 'SHA:600519', 'CNY', '100', '2000000000', '5', ''], '超出'),
    (['t-1', '2026-11-03', 'buy', ACCOUNT, 'SHA:600519', 'CNY', '-100', '1680', '5', ''], '大于 0'),
    (['t-1', '2026-11-03', 'buy', ACCOUNT, 'SHA:600519', 'CNY', '100', '1680', '-5', ''], '不小于 0'),
])
def test_template_numbers_follow_the_rules_of_broker_rows_and_edits(ledger, cells, words):
    # Review round 2, finding 11: a template deposit with amount 1e16 became a new deposit of 10000000000000000.
    _c, view, rows = run(csv(TEMPLATE, cells), 'template.csv')
    assert rows[2]['status'] == 'review' and rows[2]['events'] == [] and view['counts']['included_events'] == 0
    assert words in rows[2]['messages'][0], rows[2]['messages']


@pytest.mark.parametrize('cells, words', [
    (['t-1', '2026-11-03', 'buy', ACCOUNT, 'SHA:000001', 'CNY', '100', '10', '5', ''], '代码'),
    (['t-1', '2026-11-03', 'buy', ACCOUNT, 'HKG:ABC', 'HKD', '100', '10', '5', ''], '代码'),
    (['t-1', '2026-11-03', 'buy', ACCOUNT, 'SHA:600519', 'USD', '1', '1680', '5', ''], 'USD'),
    (['t-1', '2026-11-03', 'dividend', ACCOUNT, 'SHA:600036', 'USD', '', '', '', '100'], 'USD'),
])
def test_template_tickers_and_currencies_are_checked_like_broker_rows(ledger, cells, words):
    # Sweep after review round 2: the template path accepted SHA:000001 and an A-share dividend in USD.
    _c, view, rows = run(csv(TEMPLATE, cells), 'template.csv')
    assert rows[2]['status'] == 'review' and rows[2]['events'] == [] and words in rows[2]['messages'][0], rows[2]


def test_holdings_statement_opens_a_new_account(ledger):
    created, view, rows = run(fixture('ths_holdings_gbk.xls'), 'cc.xls', account='新账户')
    assert view['statement'] == 'holdings' and view['holdings']['mode'] == 'opening'
    assert [rows[n]['status'] for n in range(4, 11)] == ['new', 'new', 'new', 'skipped', 'review', 'unsupported', 'skipped']
    assert '负数' in rows[8]['messages'][0] and '逆回购' in rows[7]['messages'][0]
    assert {'label': '余额', 'value': '12000.00'} in view['holdings']['summary']
    assert len(view['blockers']) == 2
    with pytest.raises(ImportFailure) as caught:
        session.propose(created['id'])
    assert caught.value.status == 409
    view, rows = patch(created['id'], rows=[{'row': 8, 'include': False}],
                       opening={'date': '2026-11-14', 'cash_balances': {'CNY': '12000.00'}})
    suggestion = view['holdings']['suggested_contributed_cny']
    assert suggestion == {'value': '217500', 'label': '按现金＋持仓成本＋基金估值估算，只是参考：请改成实际累计转入的本金'}
    assert view['blockers'] == ['请填写期初日期、各币种现金和累计净投入（持仓文件里没有这些数字）。']
    view, rows = patch(created['id'], opening={'contributed_cny': '200000'})
    assert view['blockers'] == []
    [event] = session.propose(created['id'])['proposal']['events']
    assert event['kind'] == 'opening' and event['account'] == '新账户' and event['contributed_cny'] == '200000'
    assert [(p['ticker'], p['quantity'], p['cost_price']) for p in event['positions']] == \
        [('SHA:600519', '100', '1650'), ('SHE:000001', '2000', '10.5'), ('SHA:510300', '5000', '3.9')]
    assert '摊薄成本' in event['note']


def test_holdings_statement_for_an_existing_account_only_reconciles(ledger):
    created, view, _rows = run(fixture('ths_holdings_gbk.xls'), 'cc.xls')
    table = {line['ticker']: line for line in view['holdings']['reconciliation']}
    assert table['SHE:000001']['status'] == '一致' and table['SHE:000001']['entry'] is None
    assert table['SHA:600519']['entry'] == {'kind': 'buy', 'account': ACCOUNT, 'ticker': 'SHA:600519',
                                            'quantity': '100', 'price': None, 'source': 'import'}
    assert table['SHA:600036']['entry']['kind'] == 'sell' and table['SHA:600036']['label'] == '账本多 1000'
    assert table['SHE:000001']['cost_note'] is None and table['SHA:600519']['ledger_cost'] is None
    with pytest.raises(ImportFailure, match='只用于对账') as caught:
        session.propose(created['id'])
    assert caught.value.status == 409
    assert len(ledger.history()) == 1


def test_holdings_with_an_unusable_cost_keep_their_quantity_in_the_comparison(tmp_path, monkeypatch):
    # Review finding 16: a negative cost made the row vanish, so the comparison reported file 0 and a sell of 100.
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    ledger = ledger_at(tmp_path)
    open_account(ledger, positions=[{'ticker': 'SHA:600036', 'quantity': '100', 'cost_price': '30'},
                                    {'ticker': 'SHE:000001', 'quantity': '2000', 'cost_price': '10'}])
    _c, view, rows = run(csv(['证券代码', '股票余额', '成本价', '交易市场'], ['600036', 100, '-5', '上海'],
                             ['000001', 2000, '10', '香港']), 'cost.csv')
    assert view['statement'] == 'holdings' and rows[2]['status'] == 'review' and rows[3]['status'] == 'review'
    table = {line['ticker']: line for line in view['holdings']['reconciliation']}
    line = table['SHA:600036']
    assert (line['file_quantity'], line['ledger_quantity'], line['status'], line['entry']) == ('100', '100', '一致', None)
    assert line['file_cost'] is None and '成本价' in line['cost_note']
    unresolved = table['SHE:000001']
    assert (unresolved['file_quantity'], unresolved['status'], unresolved['entry'], unresolved['row']) == \
        (None, '未核对', None, 3), 'an unresolved code that may be this position is never reported as missing'


def test_holdings_with_unreadable_numbers_are_never_reported_missing(tmp_path, monkeypatch):
    # Review round 2, finding 5: SHA:600036 / 1000 / cost 未知 showed file quantity 0 and a prefilled sell of 1000;
    # the cost was parsed before the ticker was resolved and a qualified code never matched the ledger position.
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    ledger = ledger_at(tmp_path)
    open_account(ledger, positions=[{'ticker': 'SHA:600036', 'quantity': '1000', 'cost_price': '30'},
                                    {'ticker': 'SHE:000001', 'quantity': '2000', 'cost_price': '10'},
                                    {'ticker': 'HKG:00700', 'quantity': '200', 'cost_price': '300'},
                                    {'ticker': 'SHA:510300', 'quantity': '500', 'cost_price': '4'}])
    _c, view, rows = run(csv(['证券代码', '股票余额', '成本价', '币种'], ['SHA:600036', 1000, '未知', '人民币'],
                             ['000001.SZ', '两千', '10', '人民币'], ['00700.HK', '1e3', '300', '港币'],
                             ['SHA:510300', 500, '4', '人民币']), 'cost.csv')
    assert view['statement'] == 'holdings' and [rows[n]['status'] for n in (2, 3, 4, 5)] == ['review'] * 3 + ['new']
    table = {line['ticker']: line for line in view['holdings']['reconciliation']}
    line = table['SHA:600036']
    assert (line['file_quantity'], line['status'], line['entry'], line['file_cost']) == ('1000', '一致', None, None)
    assert '成本价' in line['cost_note']
    for ticker, row in (('SHE:000001', 3), ('HKG:00700', 4)):
        assert (table[ticker]['status'], table[ticker]['entry'], table[ticker]['row']) == ('未核对', None, row), ticker
    assert table['SHA:510300']['status'] == '一致'
    assert not any(item['entry'] for item in table.values()), 'no position is reported missing'


def test_a_valid_edit_replaces_an_unreadable_cell(ledger):
    # Review round 2, finding 10: quantity 1e-100 corrected to "100" kept its parse error, and a holdings cost 1e1
    # corrected to price "10" had no effect.
    cases = [
        (['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交金额', '手续费'],
         ['2026-11-03', '600519', '贵州茅台', '证券买入', '1e-100', '1000.00', '5.00'], {'quantity': '100'},
         {'kind': 'buy', 'quantity': '100', 'price': '10', 'fee': '5'}),
        (TRADE + ['手续费'], ['2026-11-03', '600036', '招商银行', '证券买入', 100, '30.00', '3e3', '5.00'], {'price': '30'},
         {'kind': 'buy', 'quantity': '100', 'price': '30', 'fee': '5'}),
        (TRADE + ['手续费', '发生金额'], ['2026-11-03', '600036', '招商银行', '证券买入', 100, '30.00', '3000.00', '5.00', '0.3万'],
         {'amount': '3005'}, {'kind': 'buy', 'quantity': '100', 'price': '30', 'fee': '5'}),
        (TRADE + ['手续费'], ['2026-11-03', '600036', '招商银行', '证券买入', 100, '30.00', '3000.00', '5%'], {'fee': '5'},
         {'kind': 'buy', 'fee': '5'}),
        (['发生日期', '业务名称', '发生金额'], ['2026-11-03', '银行转证券', '1e3'], {'amount': '1000'},
         {'kind': 'deposit', 'amount': '1000'}),
    ]
    for header, cells, edit, expected in cases:
        created, _v, rows = run(csv(header, cells), 'repair.csv')
        assert rows[2]['status'] == 'review', (cells, rows[2])
        _view, rows = patch(created['id'], rows=[{'row': 2, **edit}])
        assert rows[2]['status'] == 'new', (cells, rows[2]['messages'])
        event = rows[2]['events'][0]
        assert {key: event[key] for key in expected} == expected
    created, view, rows = run(csv(['证券代码', '股票余额', '成本价'], ['600519', 100, '1e1'], ['000001', '1e3', '10']),
                              'holdings.csv', account='新账户')
    assert [rows[2]['status'], rows[3]['status']] == ['review', 'review']
    view, rows = patch(created['id'], rows=[{'row': 2, 'price': '10'}, {'row': 3, 'quantity': '1000'}])
    assert [rows[2]['status'], rows[3]['status']] == ['new', 'new']
    assert view['holdings']['positions'] == [{'ticker': 'SHA:600519', 'quantity': '100', 'cost_price': '10'},
                                             {'ticker': 'SHE:000001', 'quantity': '1000', 'cost_price': '10'}]


def test_transactions_for_an_account_without_opening_are_blocked(ledger):
    created, view, rows = run(fixture('tdx_trades_gbk.txt'), 'tdx.txt', account='没有期初的账户')
    assert view['account_exists'] is False and '还没有期初余额' in view['blockers'][0]
    assert rows[3]['status'] == 'new' and view['reconciliation']['available'] is False
    with pytest.raises(ImportFailure) as caught:
        session.propose(created['id'])
    assert caught.value.status == 409


def test_opening_values_and_row_edits_are_validated(ledger):
    created, _v, _rows = run(fixture('ths_trades_gbk.xls'), 'jgd.xls')
    for body, message in [({'rows': [{'row': 999}]}, '没有第 999 行'),
                          ({'rows': [{'row': 3, 'fee': '-1'}]}, '费用'),
                          ({'rows': [{'row': 3, 'fee': 5}]}, '数字字符串'),
                          ({'rows': [{'row': 3, 'quantity': '0'}]}, '大于 0'),
                          ({'rows': [{'row': 3, 'ticker': 'SHA:787001'}]}, '代码无效'),
                          ({'rows': [{'row': 3, 'kind': 'transfer'}]}, '类型只能是'),
                          ({'rows': [{'row': 3, 'date': '2026-13-01'}]}, '日期'),
                          ({'rows': [{'row': 3, 'colour': 'red'}]}, '不能修改'),
                          ({'opening': {'cash_balances': {'EUR': '1'}}}, 'CNY'),
                          ({'opening': {'date': 'tomorrow'}}, '期初日期'),
                          ({'mapping': {'date': 99}}, '列序号'),
                          ({'mapping': {'date': 2}}, '对应了两次'),
                          ({'mapping': {'colour': 1}}, '未知的字段'),
                          ({'statement': 'everything'}, '文件类型'),
                          ({'short_code_market': 'US'}, '短代码')]:
        with pytest.raises(ImportFailure, match=message):
            session.patch(created['id'], body)
    assert OPENING['account'] == ACCOUNT
