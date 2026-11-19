"""Adversarial sweep of the broker importer: small synthetic files, one fact varied at a time.

Each case starts from a clean file of one kind (a buy, a sell, a dividend, a deposit, a 逆回购 pair, a 送股, a
港股通 trade, a Hong Kong and a US trade, a holdings file) and changes exactly one thing: a blank, contradictory
or duplicated column, a unit, a sign, a currency, a code, a date outside the statement, a footer or a repeated
header, full-width digits, thousands separators, scientific notation, negative zero, padding, a BOM, GBK. The
expectation is either the exact events (or opening positions) of the checked row, or that the row cannot be
booked (需处理 / 不支持 / skipped, or a blocker for the whole import): never a silently different event.
"""
import pytest

from tests.importer_support import ACCOUNT, ledger_at, open_account, run

NOT_BOOKED = 'not booked'
NEW = '新账户'


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    ledger = ledger_at(tmp_path)
    open_account(ledger, cash_balances={'CNY': '500000.00', 'HKD': '100000.00', 'USD': '10000.00'})
    return ledger


def text(header, *rows, delimiter=',', encoding='utf-8', bom=False, title=()):
    lines = [delimiter.join(line) for line in title] + [delimiter.join(header)]
    lines += [delimiter.join(str(cell) for cell in row) for row in rows]
    return (b'\xef\xbb\xbf' if bom else b'') + ('\n'.join(lines) + '\n').encode(encoding)


def changed(header, row, **cells):
    out = list(row)
    for column, value in cells.items():
        out[header.index(column)] = value
    return out


def renamed(header, old, new):
    return [new if column == old else column for column in header]


# -- the clean files -----------------------------------------------------------------------------------------

TRADE = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '发生金额']
BUY = ['2026-11-03', '600036', '招商银行', '证券买入', '100', '30.00', '3000.00', '5.00', '-3005.00']
BUY_EVENT = [{'kind': 'buy', 'ticker': 'SHA:600036', 'currency': 'CNY', 'quantity': '100', 'price': '30', 'fee': '5'}]
SELL = changed(TRADE, BUY, 操作='证券卖出', 发生金额='2995.00')
SELL_EVENT = [{**BUY_EVENT[0], 'kind': 'sell'}]

CASH = ['发生日期', '业务名称', '证券代码', '证券名称', '发生金额']
DIVIDEND = ['2026-11-03', '红利入账', '600036', '招商银行', '100.00']
DIVIDEND_EVENT = [{'kind': 'dividend', 'ticker': 'SHA:600036', 'currency': 'CNY', 'amount': '100'}]
FUNDS = ['发生日期', '业务名称', '发生金额']
DEPOSIT = ['2026-11-03', '银行转证券', '10.00']
DEPOSIT_EVENT = [{'kind': 'deposit', 'currency': 'CNY', 'amount': '10'}]
LEND = ['2026-11-03', '融券回购', '204001', 'GC001', '-10000.00']
BACK = ['2026-11-04', '融券购回', '204001', 'GC001', '10001.00']
INTEREST = [{'kind': 'interest', 'currency': 'CNY', 'amount': '1'}]
SPLIT_HEADER = ['发生日期', '业务名称', '证券代码', '证券名称', '成交数量', '发生金额']
SPLIT = ['2026-11-05', '红股入账', '600036', '招商银行', '300', '0.00']
SPLIT_EVENT = [{'kind': 'split', 'ticker': 'SHA:600036', 'ratio': '1.3'}]

CONNECT_HEADER = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '佣金', '发生金额', '结算汇率',
                  '币种', '交易市场']
CONNECT = ['2026-11-05', '00700', '腾讯控股', '港股通买入', '200', '380.00', '76000.00', '5.00', '-69925.00', '0.9200',
           '人民币', '沪港通']
CONNECT_EVENTS = [{'kind': 'fx', 'currency': 'CNY', 'amount': '69925', 'to_currency': 'HKD'},
                  {'kind': 'buy', 'ticker': 'HKG:00700', 'currency': 'HKD', 'quantity': '200', 'price': '380'}]
HK_HEADER = TRADE + ['币种']
HK = ['2026-11-03', '00700', '腾讯控股', '买入', '100', '300.00', '30000.00', '5.00', '-30005.00', '港币']
HK_EVENT = [{'kind': 'buy', 'ticker': 'HKG:00700', 'currency': 'HKD', 'quantity': '100', 'price': '300', 'fee': '5'}]
US = ['2026-11-03', 'AAPL.O', 'Apple', '买入', '10', '200.00', '2000.00', '1.00', '-2001.00', '美元']
US_EVENT = [{'kind': 'buy', 'ticker': 'NASDAQ:AAPL', 'currency': 'USD', 'quantity': '10', 'price': '200', 'fee': '1'}]

HOLDING_HEADER = ['证券代码', '证券名称', '股票余额', '成本价']
HOLDING = ['600519', '贵州茅台', '100', '1650.00']
POSITION = [{'ticker': 'SHA:600519', 'quantity': '100', 'cost_price': '1650'}]

PERIOD = [['交割单 2026-11-01 至 2026-11-14']]
FOOTER = ['合计', '', '', '', '100', '', '3000.00', '5.00', '-3005.00']

# (id, file bytes, file name, row checked, expected events / positions or NOT_BOOKED, account)
CASES = [
    # -- buy
    ('buy clean', text(TRADE, BUY), 'b.csv', 2, BUY_EVENT),
    ('buy with a UTF-8 BOM', text(TRADE, BUY, bom=True), 'b.csv', 2, BUY_EVENT),
    ('buy in GBK', text(TRADE, BUY, encoding='gb18030'), 'b.csv', 2, BUY_EVENT),
    ('buy in UTF-16', text(TRADE, BUY, encoding='utf-16'), 'b.txt', 2, BUY_EVENT),
    ('buy tab separated', text(TRADE, BUY, delimiter='\t'), 'b.xls', 2, BUY_EVENT),
    ('buy with padded cells', text(TRADE, [f' {c}  ' for c in BUY]), 'b.csv', 2, BUY_EVENT),
    ('buy in full-width digits', text(TRADE, changed(TRADE, BUY, 成交数量='１００', 成交价格='３０.００')), 'b.csv', 2,
     BUY_EVENT),
    ('buy with thousands separators', text(TRADE, changed(TRADE, BUY, 成交金额='"3,000.00"', 发生金额='"-3,005.00"')),
     'b.csv', 2, BUY_EVENT),
    ('buy with a decimal comma, semicolons', text(TRADE, changed(TRADE, BUY, 成交价格='30,00', 成交金额='3000,00',
                                                                    手续费='5,00', 发生金额='-3005,00'), delimiter=';'),
     'b.csv', 2, BUY_EVENT),
    ('buy with a comma in a non-grouping place', text(TRADE, changed(TRADE, BUY, 成交金额='"30,00.00"')), 'b.csv', 2,
     NOT_BOOKED),
    ('buy with a quantity in scientific notation', text(TRADE, changed(TRADE, BUY, 成交数量='1E2')), 'b.csv', 2,
     NOT_BOOKED),
    ('buy with a negative zero fee', text(TRADE, changed(TRADE, BUY, 手续费='-0.00', 发生金额='-3000.00')), 'b.csv', 2,
     [{**BUY_EVENT[0], 'fee': '0'}]),
    ('buy with a blank quantity', text(TRADE, changed(TRADE, BUY, 成交数量='')), 'b.csv', 2, NOT_BOOKED),
    ('buy with a fractional A-share quantity', text(TRADE, changed(TRADE, BUY, 成交数量='100.5', 成交金额='3015.00',
                                                                      发生金额='-3020.00')), 'b.csv', 2, NOT_BOOKED),
    ('buy without price and turnover', text(TRADE, changed(TRADE, BUY, 成交价格='', 成交金额='')), 'b.csv', 2,
     NOT_BOOKED),
    ('buy with two price columns that differ', text(TRADE + ['成交均价'], BUY + ['30.01']), 'b.csv', 2, NOT_BOOKED),
    ('buy with two price columns that agree', text(TRADE + ['成交均价'], BUY + ['30']), 'b.csv', 2, BUY_EVENT),
    ('buy with two cash columns that differ', text(TRADE + ['清算金额'], BUY + ['-3006.00']), 'b.csv', 2, NOT_BOOKED),
    ('buy with a quantity in lots', text(renamed(TRADE, '成交数量', '成交数量(手)'), changed(TRADE, BUY, 成交数量='1')),
     'b.csv', 2, BUY_EVENT),
    ('buy of a bond with a quantity in lots', text(renamed(TRADE, '成交数量', '成交数量(手)'),
                                                   changed(TRADE, BUY, 证券代码='113050', 成交数量='1', 成交价格='100.00',
                                                           成交金额='1000.00', 发生金额='-1005.00')), 'b.csv', 2,
     NOT_BOOKED),
    ('buy with a quantity in 万股 that may be rounded', text(renamed(TRADE, '成交数量', '成交数量(万股)'),
                                                            changed(TRADE, BUY, 成交数量='0.01')), 'b.csv', 2, NOT_BOOKED),
    ('buy with 币种 美元', text(TRADE + ['币种'], BUY + ['美元']), 'b.csv', 2, NOT_BOOKED),
    ('buy with a 发生金额(美元) header', text(renamed(TRADE, '发生金额', '发生金额(美元)'), BUY), 'b.csv', 2, NOT_BOOKED),
    ('buy with a ¥ in the turnover', text(TRADE, changed(TRADE, BUY, 成交金额='¥3000.00')), 'b.csv', 2, BUY_EVENT),
    ('buy with HK$ in the price', text(TRADE, changed(TRADE, BUY, 成交价格='HK$30.00')), 'b.csv', 2, NOT_BOOKED),
    ('buy whose cash comes in', text(TRADE, changed(TRADE, BUY, 发生金额='3005.00')), 'b.csv', 2, NOT_BOOKED),
    ('buy with a negative quantity', text(TRADE, changed(TRADE, BUY, 成交数量='-100')), 'b.csv', 2, NOT_BOOKED),
    ('buy whose preferred action column is blank', text(['业务名称'] + TRADE, [''] + BUY), 'b.csv', 2, NOT_BOOKED),
    ('buy with a blank fee and no cash amount', text(TRADE, changed(TRADE, BUY, 手续费='', 发生金额='')), 'b.csv', 2,
     NOT_BOOKED),
    ('buy coded 600036.SH', text(TRADE, changed(TRADE, BUY, 证券代码='600036.SH')), 'b.csv', 2, BUY_EVENT),
    ('buy coded SH600036', text(TRADE, changed(TRADE, BUY, 证券代码='SH600036')), 'b.csv', 2, BUY_EVENT),
    ('buy coded SHA:600036', text(TRADE, changed(TRADE, BUY, 证券代码='SHA:600036')), 'b.csv', 2, BUY_EVENT),
    ('buy on the market of the other exchange', text(TRADE + ['交易市场'], BUY + ['深圳A股']), 'b.csv', 2, NOT_BOOKED),
    ('buy of an A-share code on NASDAQ', text(TRADE + ['交易市场'], BUY + ['NASDAQ']), 'b.csv', 2, NOT_BOOKED),
    ('buy with a short numeric code', text(TRADE, changed(TRADE, BUY, 证券代码='36')), 'b.csv', 2, NOT_BOOKED),
    ('buy dated inside the statement', text(TRADE, BUY, title=PERIOD), 'b.csv', 3, BUY_EVENT),
    ('buy dated outside the statement', text(TRADE, changed(TRADE, BUY, 成交日期='2026-11-20'), title=PERIOD), 'b.csv',
     3, NOT_BOOKED),
    ('buy followed by a totals row', text(TRADE, BUY, FOOTER), 'b.csv', 2, BUY_EVENT),
    ('a totals row is never booked', text(TRADE, BUY, FOOTER), 'b.csv', 3, NOT_BOOKED),
    ('a spaced totals row is never booked', text(TRADE, BUY, ['合 计'] + FOOTER[1:]), 'b.csv', 3, NOT_BOOKED),
    ('a totals row without a label is never booked', text(TRADE, BUY, [''] + FOOTER[1:]), 'b.csv', 3, NOT_BOOKED),
    ('buy after a repeated header', text(TRADE, BUY, TRADE, changed(TRADE, BUY, 成交日期='2026-11-04')), 'b.csv', 4,
     BUY_EVENT),
    # -- sell
    ('sell clean', text(TRADE, SELL), 's.csv', 2, SELL_EVENT),
    ('sell with a negative quantity', text(TRADE, changed(TRADE, SELL, 成交数量='-100')), 's.csv', 2, SELL_EVENT),
    ('sell with a negative turnover and no cash', text(TRADE[:-1], changed(TRADE, SELL, 成交金额='-3000.00')[:-1]),
     's.csv', 2, NOT_BOOKED),
    ('sell whose cash goes out', text(TRADE, changed(TRADE, SELL, 发生金额='-2995.00')), 's.csv', 2, NOT_BOOKED),
    ('sell of more than is held', text(TRADE, changed(TRADE, SELL, 成交数量='3001', 成交金额='90030.00',
                                                     发生金额='90025.00')), 's.csv', 2, NOT_BOOKED),
    # -- dividend
    ('dividend clean', text(CASH, DIVIDEND), 'd.csv', 2, DIVIDEND_EVENT),
    ('dividend with a negative amount', text(CASH, changed(CASH, DIVIDEND, 发生金额='-100.00')), 'd.csv', 2, NOT_BOOKED),
    ('dividend in euro', text(CASH + ['币种'], DIVIDEND + ['欧元']), 'd.csv', 2, NOT_BOOKED),
    ('dividend under a 成交金额(NZD) header', text(renamed(CASH, '发生金额', '成交金额(NZD)'), DIVIDEND), 'd.csv', 2,
     NOT_BOOKED),
    ('dividend with a fee', text(CASH + ['手续费'], DIVIDEND + ['5.00']), 'd.csv', 2, NOT_BOOKED),
    ('dividend abbreviated in 万', text(CASH, changed(CASH, DIVIDEND, 发生金额='0.01万')), 'd.csv', 2, NOT_BOOKED),
    ('dividend whose turnover disagrees', text(CASH + ['成交金额'], DIVIDEND + ['200.00']), 'd.csv', 2, NOT_BOOKED),
    ('dividend marked US$', text(CASH, changed(CASH, DIVIDEND, 发生金额='US$100.00')), 'd.csv', 2, NOT_BOOKED),
    # -- deposit
    ('deposit clean', text(FUNDS, DEPOSIT), 'f.csv', 2, DEPOSIT_EVENT),
    ('deposit with 币种 人民币(RMB)', text(FUNDS + ['币种'], DEPOSIT + ['人民币(RMB)']), 'f.csv', 2, DEPOSIT_EVENT),
    ('deposit marked US$', text(FUNDS, changed(FUNDS, DEPOSIT, 发生金额='US$10.00')), 'f.csv', 2, NOT_BOOKED),
    ('deposit with a decimal comma, semicolons', text(FUNDS, changed(FUNDS, DEPOSIT, 发生金额='12,50'), delimiter=';'),
     'f.csv', 2, [{**DEPOSIT_EVENT[0], 'amount': '12.5'}]),
    ('deposit whose comma could be either, semicolons', text(FUNDS, changed(FUNDS, DEPOSIT, 发生金额='1,250'),
                                                             delimiter=';'), 'f.csv', 2, NOT_BOOKED),
    ('deposit without a currency next to a 港币 one', text(FUNDS + ['币种'], DEPOSIT + [''],
                                                           ['2026-11-04', '银行转证券', '10.00', '港币']), 'f.csv', 2,
     NOT_BOOKED),
    ('deposit in brackets (negative)', text(FUNDS, changed(FUNDS, DEPOSIT, 发生金额='(10.00)')), 'f.csv', 2,
     NOT_BOOKED),
    # -- 逆回购
    ('repo pair clean', text(CASH, LEND, BACK), 'r.csv', 3, INTEREST),
    ('repo pair whose stated directions contradict the cash',
     text(CASH, changed(CASH, LEND, 业务名称='融券购回'), changed(CASH, BACK, 业务名称='融券回购')), 'r.csv', 3, NOT_BOOKED),
    ('repo pair in euro', text(CASH + ['币种'], LEND + ['EUR'], BACK + ['EUR']), 'r.csv', 3, NOT_BOOKED),
    ('repo buy-back before the lending', text(CASH, LEND, changed(CASH, BACK, 发生日期='2026-11-02')), 'r.csv', 3,
     NOT_BOOKED),
    # -- 送股
    ('split clean', text(SPLIT_HEADER, SPLIT), 't.csv', 2, SPLIT_EVENT),
    ('split with cash moving', text(SPLIT_HEADER, changed(SPLIT_HEADER, SPLIT, 发生金额='100.00')), 't.csv', 2,
     NOT_BOOKED),
    ('split without a quantity', text(SPLIT_HEADER, changed(SPLIT_HEADER, SPLIT, 成交数量='')), 't.csv', 2, NOT_BOOKED),
    ('split of a fractional quantity', text(SPLIT_HEADER, changed(SPLIT_HEADER, SPLIT, 成交数量='300.5')), 't.csv', 2,
     NOT_BOOKED),
    # -- 港股通
    ('connect clean', text(CONNECT_HEADER, CONNECT), 'c.csv', 2, CONNECT_EVENTS),
    ('connect with a negative rate', text(CONNECT_HEADER, changed(CONNECT_HEADER, CONNECT, 结算汇率='-0.92')), 'c.csv',
     2, NOT_BOOKED),
    ('connect settled in 美元', text(CONNECT_HEADER, changed(CONNECT_HEADER, CONNECT, 币种='美元')), 'c.csv', 2,
     NOT_BOOKED),
    ('connect whose rate is far outside HKD/CNY', text(CONNECT_HEADER, changed(CONNECT_HEADER, CONNECT, 结算汇率='1.5')),
     'c.csv', 2, NOT_BOOKED),
    ('connect without a CNY amount', text(CONNECT_HEADER, changed(CONNECT_HEADER, CONNECT, 发生金额='')), 'c.csv', 2,
     NOT_BOOKED),
    # -- Hong Kong and US trades
    ('HK trade clean', text(HK_HEADER, HK), 'h.csv', 2, HK_EVENT),
    ('HK trade without a currency', text(TRADE, HK[:-1]), 'h.csv', 2, NOT_BOOKED),
    ('HK trade coded 700', text(HK_HEADER, changed(HK_HEADER, HK, 证券代码='700')), 'h.csv', 2, HK_EVENT),
    ('HK trade coded 00700.HK', text(HK_HEADER, changed(HK_HEADER, HK, 证券代码='00700.HK')), 'h.csv', 2, HK_EVENT),
    ('HK trade with a quantity in lots', text(renamed(HK_HEADER, '成交数量', '成交数量(手)'),
                                              changed(HK_HEADER, HK, 成交数量='1')), 'h.csv', 2, NOT_BOOKED),
    ('HK trade of a fractional quantity', text(HK_HEADER, changed(HK_HEADER, HK, 成交数量='100.5', 成交金额='30150.00',
                                                                  发生金额='-30155.00')), 'h.csv', 2, NOT_BOOKED),
    ('US trade clean', text(HK_HEADER, US), 'u.csv', 2, US_EVENT),
    ('US trade without an exchange', text(HK_HEADER, changed(HK_HEADER, US, 证券代码='AAPL')), 'u.csv', 2, NOT_BOOKED),
    ('US trade on a Shanghai market', text(HK_HEADER + ['交易市场'], US + ['上海']), 'u.csv', 2, NOT_BOOKED),
    ('US trade settled in 人民币', text(HK_HEADER, changed(HK_HEADER, US, 币种='人民币')), 'u.csv', 2, NOT_BOOKED),
    ('US trade of a fractional share', text(HK_HEADER, changed(HK_HEADER, US, 成交数量='0.5', 成交金额='100.00',
                                                               发生金额='-101.00')), 'u.csv', 2,
     [{**US_EVENT[0], 'quantity': '0.5'}]),
    # -- structure: cells split or shifted, a second table, a merged cell in a web page
    ('deposit whose unquoted thousands comma splits the cell', '发生日期,业务名称,发生金额\n2026-11-03,银行转证券,1,000.00\n'
     .encode(), 'f.csv', 2, NOT_BOOKED),
    ('deposit with a space inside the number', text(FUNDS, changed(FUNDS, DEPOSIT, 发生金额='1 000.00')), 'f.csv', 2,
     NOT_BOOKED),
    ('a second table whose columns are in another order',
     text(TRADE, BUY, [*TRADE[:4], '成交价格', '成交数量', *TRADE[6:]],
          ['2026-11-04', '600036', '招商银行', '证券买入', '30.00', '100', '3000.00', '5.00', '-3005.00']), 'b.csv', 4,
     NOT_BOOKED),
    ('a HK-shaped code on a Shenzhen market', text(TRADE + ['交易市场'], changed(TRADE, BUY, 证券代码='00700') + ['深圳A股']),
     'b.csv', 2, NOT_BOOKED),
    ('a merged currency cell in a web page',
     ('<html><body><table><tr><th>发生日期</th><th>业务名称</th><th>发生金额</th><th>币种</th></tr>'
      '<tr><td>2026-11-03</td><td>利息归本</td><td>10</td><td rowspan="2">USD</td></tr>'
      '<tr><td>2026-11-04</td><td>利息归本</td><td>10</td></tr></table></body></html>').encode(), 'w.xls', 3,
     [{'kind': 'interest', 'currency': 'USD', 'amount': '10'}]),
    # -- holdings (a new account)
    ('holdings clean', text(HOLDING_HEADER, HOLDING), 'k.csv', 2, POSITION, NEW),
    ('holdings in GBK, tab separated', text(HOLDING_HEADER, HOLDING, delimiter='\t', encoding='gb18030'), 'k.xls', 2,
     POSITION, NEW),
    ('holdings with thousands separators', text(HOLDING_HEADER, changed(HOLDING_HEADER, HOLDING, 股票余额='"1,000"')),
     'k.csv', 2, [{**POSITION[0], 'quantity': '1000'}], NEW),
    ('holdings without a cost', text(HOLDING_HEADER, changed(HOLDING_HEADER, HOLDING, 成本价='')), 'k.csv', 2,
     NOT_BOOKED, NEW),
    ('holdings with a negative cost', text(HOLDING_HEADER, changed(HOLDING_HEADER, HOLDING, 成本价='-5')), 'k.csv', 2,
     NOT_BOOKED, NEW),
    ('holdings of a bond in lots', text(renamed(HOLDING_HEADER, '股票余额', '股票余额(手)'),
                                        changed(HOLDING_HEADER, HOLDING, 证券代码='113050', 成本价='100')), 'k.csv', 2,
     NOT_BOOKED, NEW),
    ('holdings in euro', text(HOLDING_HEADER + ['币种'], HOLDING + ['欧元']), 'k.csv', 2, NOT_BOOKED, NEW),
    ('holdings listing a ticker twice', text(HOLDING_HEADER, HOLDING, changed(HOLDING_HEADER, HOLDING, 股票余额='50')),
     'k.csv', 2, NOT_BOOKED, NEW),
    ('holdings of a fractional A-share quantity', text(HOLDING_HEADER, changed(HOLDING_HEADER, HOLDING,
                                                                               股票余额='100.5')), 'k.csv', 2,
     NOT_BOOKED, NEW),
]


def _matches(actual, expected):
    return len(actual) == len(expected) and all({key: item.get(key) for key in want} == want
                                                for item, want in zip(actual, expected))


@pytest.mark.parametrize('case', CASES, ids=[case[0] for case in CASES])
def test_one_fact_varied_gives_the_exact_events_or_review(ledger, case):
    name, data, filename, number, expected, *account = case
    _created, view, rows = run(data, filename, account=account[0] if account else ACCOUNT)
    row = rows[number]
    booked = row['include'] and row['status'] == 'new'
    holdings = view['statement'] == 'holdings'
    if expected == NOT_BOOKED:
        assert not booked or (view['blockers'] and not holdings), (name, row['status'], row['events'], row['messages'])
        if holdings:
            assert all(position['ticker'] != row['ticker'] for position in view['holdings']['positions']), name
        return
    assert booked, (name, row['status'], row['messages'], view['blockers'])
    if holdings:
        assert _matches(view['holdings']['positions'], expected), (name, view['holdings']['positions'])
    else:
        assert not view['blockers'], (name, view['blockers'])
        assert _matches(row['events'], expected), (name, row['events'])


def test_the_sweep_is_wide_enough():
    assert len(CASES) >= 40 and len({case[0] for case in CASES}) == len(CASES)


# -- cash to the cent: a trade's cash effect is the broker's stated amount ----------------------------------------
# The ledger books a trade's cash as quantity × price ± fee. When the file's price is rounded (32.017 for 9,605.00 /
# 300) or missing, the booked price must still make that product the stated 成交金额 exactly, and the fee must be the
# stated 发生金额's remainder: after confirming, every cash balance is the opening plus the file's 发生金额, to the cent,
# with no sub-cent tail (¥418,475.79999999).

TRADE_NO_PRICE = [column for column in TRADE if column != '成交价格']
HK_NO_PRICE = TRADE_NO_PRICE + ['币种']


def _no_price(row):
    return [cell for column, cell in zip(TRADE, row) if column != '成交价格']


CASH_CASES = [
    # (id, file bytes, {currency: change of the balance})
    ('A-share buy priced only by 成交金额 ÷ 数量 (32.01666…)',
     text(TRADE_NO_PRICE, _no_price(changed(TRADE, BUY, 成交数量='300', 成交金额='9605.00', 发生金额='-9610.00'))),
     {'CNY': '-9610.00'}),
    ('A-share buy with a rounded 成交价格 32.017',
     text(TRADE, changed(TRADE, BUY, 成交数量='300', 成交价格='32.017', 成交金额='9605.00', 发生金额='-9610.00')),
     {'CNY': '-9610.00'}),
    ('odd-lot A-share sell of 7 shares (31.7457…)',
     text(TRADE, changed(TRADE, SELL, 成交数量='7', 成交价格='31.746', 成交金额='222.22', 手续费='0.10',
                         发生金额='222.12')), {'CNY': '222.12'}),
    ('three fills with repeating prices in one file',
     text(TRADE_NO_PRICE,
          _no_price(changed(TRADE, BUY, 成交数量='300', 成交金额='9605.00', 发生金额='-9610.00')),
          _no_price(changed(TRADE, BUY, 成交日期='2026-11-04', 成交数量='7', 成交金额='222.22', 手续费='0.10',
                            发生金额='-222.32')),
          _no_price(changed(TRADE, SELL, 成交日期='2026-11-05', 成交数量='300', 成交金额='10000.01',
                            发生金额='9995.01'))), {'CNY': '162.69'}),
    ('fee columns a cent away from the stated 发生金额',
     text(TRADE, changed(TRADE, BUY, 成交数量='300', 成交价格='32.00', 成交金额='9600.00', 发生金额='-9605.01')),
     {'CNY': '-9605.01'}),
    ('HK buy in HKD of an odd lot priced by 成交金额 ÷ 数量 (384.333…)',
     text(HK_NO_PRICE, _no_price(changed(TRADE, HK[:-1], 成交数量='30', 成交金额='11530.00', 手续费='50.15',
                                         发生金额='-11580.15')) + ['港币']), {'HKD': '-11580.15'}),
    ('US buy of 3 shares priced by 成交金额 ÷ 数量 (233.333…)',
     text(HK_NO_PRICE, _no_price(changed(TRADE, US[:-1], 成交数量='3', 成交金额='700.00', 手续费='1.00',
                                         发生金额='-701.00')) + ['美元']), {'USD': '-701.00'}),
    ('港股通 buy with a rounded HKD price (384.333…)',
     text(CONNECT_HEADER, changed(CONNECT_HEADER, CONNECT, 成交数量='300', 成交价格='384.333', 成交金额='115300.00',
                                  发生金额='-106081.00')), {'CNY': '-106081.00', 'HKD': '0'}),
]


@pytest.mark.parametrize('case', CASH_CASES, ids=[case[0] for case in CASH_CASES])
def test_imported_cash_is_the_brokers_to_the_cent(ledger, case):
    from decimal import Decimal

    from core.importers import session
    from core.ledger import amount

    name, data, change = case
    before = ledger.state()['accounts'][ACCOUNT]['cash_balances']
    created, view, rows = run(data, 'cash.csv')
    assert not view['blockers'], (name, view['blockers'])
    assert all(row['status'] == 'new' for row in rows.values()), [(n, r['status'], r['messages']) for n, r in rows.items()]
    proposal = session.propose(created['id'])['proposal']
    for event in proposal['events']:
        for key in ('quantity', 'price', 'fee', 'amount', 'received_amount'):
            if key in event:
                amount(event[key])   # within the ledger's own limits (bounds, at most 100 places)
        for key in ('fee', 'amount', 'received_amount'):   # money is booked in cents
            if key in event:
                assert Decimal(event[key]).as_tuple().exponent >= -2, (name, key, event[key])
    ledger.confirm(proposal['id'])
    after = ledger.state()['accounts'][ACCOUNT]['cash_balances']
    for unit, delta in change.items():
        assert Decimal(after[unit]) == Decimal(before[unit]) + Decimal(delta), (name, unit, before[unit], after[unit])
    for unit, value in after.items():
        assert Decimal(value).as_tuple().exponent >= -2, (name, unit, value)
