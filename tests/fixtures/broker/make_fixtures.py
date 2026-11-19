"""Regenerate the synthetic broker export fixtures in this directory.

    python tests/fixtures/broker/make_fixtures.py

Every value is fictional. Running balances (资金余额, 股份余额) are computed here
with Decimal so each file is internally consistent. See README.md.
"""
from decimal import Decimal as D
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPENING_CASH = D('500000.00')
OPENING_POSITIONS = {'600036': D(1000), '000001': D(2000)}  # see tests/importer_support.OPENING


def money(value):
    return f'{value:.2f}'


class Book:
    def __init__(self, cash=OPENING_CASH, shares=None):
        self.cash = cash
        self.shares = dict(shares or OPENING_POSITIONS)

    def apply(self, code, cash_change, share_change=D(0)):
        self.cash += cash_change
        if code:
            self.shares[code] = self.shares.get(code, D(0)) + share_change
        return money(self.cash), str(self.shares.get(code, D(0))) if code else ''


def ths_trades():
    """同花顺交割单 saved as .xls: really GBK tab-separated text with a title line and a footer."""
    header = ['成交日期', '成交时间', '证券代码', '证券名称', '操作', '成交数量', '成交均价', '成交金额', '手续费', '印花税',
              '过户费', '其他杂费', '发生金额', '资金余额', '股份余额', '成交编号', '合同编号', '股东帐户', '交易市场']
    book = Book()
    rows = []

    def add(day, clock, code, name, action, qty, price, turnover, fee, stamp, transfer, other, cash, trade_id,
            holder, market, share_change=D(0)):
        balance, shares = book.apply(code if code not in ('204001',) else None, cash, share_change)
        if code == '204001':
            shares = '0'
        rows.append([day, clock, code, name, action, str(qty), price, money(turnover), money(fee), money(stamp),
                     money(transfer), money(other), money(cash), balance, shares, trade_id, f'H{trade_id}', holder, market])

    add('20261103', '09:31:25', '600519', '贵州茅台', '证券买入', 100, '1680.00', D('168000'), D('42.00'), D(0), D('1.68'),
        D(0), D('-168043.68'), '0012345', 'A000000001', '上海A股', D(100))
    add('20261104', '10:01:00', '000001', '平安银行', '证券卖出', 1000, '11.20', D('11200'), D('5.00'), D('5.60'), D('0.11'),
        D(0), D('11189.29'), '0012399', '0000000001', '深圳A股', D(-1000))
    # One order filled at two prices, shown as one row: the average price is rounded.
    add('20261104', '10:30:00', '600036', '招商银行', '证券买入', 300, '32.017', D('9605.00'), D('5.00'), D(0), D('0.10'),
        D(0), D('-9610.10'), '0012401', 'A000000001', '上海A股', D(300))
    add('20261105', '14:00:00', '204001', 'GC001', '融券回购', 100, '2.150', D('100000'), D('1.00'), D(0), D(0), D(0),
        D('-100001.00'), '0012410', 'A000000001', '上海A股')
    add('20261106', '09:00:00', '204001', 'GC001', '融券购回', 100, '0.000', D('100000'), D(0), D(0), D(0), D(0),
        D('100005.89'), '0012411', 'A000000001', '上海A股')
    add('20261107', '09:15:00', '000001', '平安银行', '撤单', 0, '0.00', D(0), D(0), D(0), D(0), D(0), D(0), '0',
        '0000000001', '深圳A股')
    add('20261110', '09:30:00', '787001', '示例配号', '申购配号', 1000, '0.00', D(0), D(0), D(0), D(0), D(0), D(0), '0',
        'A000000001', '上海A股')
    add('20261112', '13:05:00', '600519', '贵州茅台', '证券卖出', 50, '1700.00', D('85000'), D('21.25'), D('42.50'),
        D('0.85'), D(0), D('84935.40'), '0012470', 'A000000001', '上海A股', D(-50))
    lines = ['同花顺 交割单 2026-11-01 至 2026-11-14 （虚构数据）', '\t'.join(header)]
    lines += ['\t'.join(row) for row in rows]
    lines.append('合计' + '\t' * (len(header) - 1))
    (HERE / 'ths_trades_gbk.xls').write_bytes(('\r\n'.join(lines) + '\r\n').encode('gb18030'))
    return header, rows


def eastmoney_statement():
    """东方财富-style 资金对账单 as UTF-8 CSV with BOM: cash flows, dividends, review and unsupported rows."""
    header = ['发生日期', '成交时间', '业务名称', '证券代码', '证券名称', '成交数量', '成交价格', '成交金额', '手续费', '印花税',
              '过户费', '其他费', '发生金额', '资金余额', '股份余额', '成交编号', '股东代码', '币种']
    book = Book()
    rows = []

    def add(day, clock, action, code, name, qty, price, turnover, fees, cash, trade_id='', share_change=D(0)):
        balance, shares = book.apply(code or None, cash, share_change)
        fee_cells = [money(f) if f is not None else '' for f in fees]
        rows.append([day, clock, action, code, name, str(qty) if qty is not None else '', price,
                     money(turnover) if turnover is not None else '', *fee_cells, money(cash), balance,
                     shares, trade_id, ('A000000001' if code[:1] in '5679' else '0000000001') if code else '', '人民币'])

    no_fee = [None, None, None, None]
    add('2026-11-02', '08:30:00', '银行转证券', '', '', None, '', None, no_fee, D('50000.00'))
    add('2026-11-02', '10:12:00', '证券买入', '600036', '招商银行', 500, '32.10', D('16050.00'),
        [D('5.00'), D(0), D('0.16'), D(0)], D('-16055.16'), '2026110200001', D(500))
    add('2026-11-03', '15:30:00', '红利入账', '600036', '招商银行', None, '', None, no_fee, D('1936.00'))
    add('2026-11-04', '15:30:00', '股息红利差异扣税', '600036', '招商银行', None, '', None, no_fee, D('-96.80'))
    add('2026-11-05', '15:30:00', '红股入账', '600036', '招商银行', 300, '0.00', D(0), no_fee, D(0), '', D(300))
    add('2026-11-06', '15:30:00', '利息归本', '', '', None, '', None, no_fee, D('12.34'))
    add('2026-11-07', '09:00:00', '证券转银行', '', '', None, '', None, no_fee, D('-20000.00'))
    add('2026-11-09', '15:00:00', '中签缴款', '732999', '示例新股', 500, '10.00', D('5000.00'), no_fee, D('-5000.00'))
    add('2026-11-10', '15:00:00', '新股入帐', '603999', '示例新股', 500, '0.00', D(0), no_fee, D(0), '', D(500))
    add('2026-11-11', '10:00:00', '证券买入', '832000', '示例北证', 100, '10.00', D('1000.00'),
        [D('5.00'), D(0), D(0), D(0)], D('-1005.00'), '2026111100002')
    add('2026-11-12', '15:00:00', '基金申购', '519999', '示例基金', None, '', D('10000.00'), no_fee, D('-10000.00'))
    add('2026-11-13', '15:00:00', '资金调整', '', '', None, '', None, no_fee, D('0.50'))
    text = ','.join(header) + '\r\n' + ''.join(','.join(row) + '\r\n' for row in rows)
    (HERE / 'eastmoney_statement_bom.csv').write_bytes(b'\xef\xbb\xbf' + text.encode('utf-8'))
    return header, rows


def tdx_trades():
    """通达信 历史成交 export: GBK tab text with ="..." cells, newest first, overlapping fee columns."""
    header = ['成交日期', '业务名称', '证券代码', '证券名称', '成交价格', '成交数量', '剩余数量', '成交金额', '清算金额', '剩余金额',
              '佣金', '印花税', '过户费', '交易规费', '经手费', '证管费', '成交编号', '股东代码']
    book = Book()
    rows = []

    def add(day, action, code, name, price, qty, turnover, fees, trade_id, holder):
        commission, stamp, transfer, regulatory, handling, supervision = fees
        total = commission + stamp + transfer + handling + supervision
        cash = -(turnover + total) if '买' in action else turnover - total
        balance, shares = book.apply(code, cash, D(qty) if '买' in action else -D(qty))
        rows.append([day, action, f'="{code}"', name, price, str(qty), shares, money(turnover), money(cash), balance,
                     money(commission), money(stamp), money(transfer), money(regulatory), money(handling),
                     money(supervision), f'="{trade_id}"', f'="{holder}"'])

    add('20261103', '证券买入', '000001', '平安银行', '11.00', 1000, D('11000.00'),
        (D('5.00'), D(0), D('0.11'), D('0.41'), D('0.34'), D('0.07')), '700001', '0000000001')
    add('20261105', '证券买入', '510300', '沪深300ETF', '3.912', 5000, D('19560.00'),
        (D('5.00'), D(0), D(0), D('0.97'), D('0.80'), D('0.17')), '700002', 'A000000001')
    add('20261110', '证券卖出', '000001', '平安银行', '11.50', 500, D('5750.00'),
        (D('5.00'), D('2.88'), D('0.06'), D('0.21'), D('0.17'), D('0.04')), '700003', '0000000001')
    lines = ['历史成交查询 起始日期:20261101 终止日期:20261114 （虚构数据）', '\t'.join(header)]
    lines += ['\t'.join(row) for row in reversed(rows)]  # newest first, as on screen
    (HERE / 'tdx_trades_gbk.txt').write_bytes(('\r\n'.join(lines) + '\r\n').encode('gb18030'))
    return header, rows


def eastmoney_html():
    """A web page saved with an .xls name (GB2312 meta charset)."""
    header = ['成交日期', '成交时间', '证券代码', '证券名称', '委托方向', '成交数量', '成交价格', '成交金额', '佣金', '印花税',
              '过户费', '发生金额', '资金余额', '成交编号']
    body = [
        ['20261103', '09:35:00', '000001', '平安银行', '买入', '1000', '11.00', '11000.00', '5.00', '0.00', '0.11',
         '-11005.11', money(OPENING_CASH - D('11005.11')), '800001'],
        ['20261106', '14:20:00', '000001', '平安银行', '卖出', '500', '11.40', '5700.00', '5.00', '2.85', '0.06',
         '5692.09', money(OPENING_CASH - D('11005.11') + D('5692.09')), '800002'],
    ]
    cells = lambda row, tag: ''.join(f'<{tag}>{value}</{tag}>' for value in row)
    html = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312"></head><body>'
            '<table border="1"><tr><td colspan="14">当日成交查询（虚构数据）</td></tr>'
            f'<tr>{cells(header, "th")}</tr>' + ''.join(f'<tr>{cells(row, "td")}</tr>' for row in body) +
            '</table></body></html>')
    (HERE / 'eastmoney_trades_html.xls').write_bytes(html.encode('gb18030'))


def ths_holdings():
    header = ['证券代码', '证券名称', '股票余额', '可用余额', '冻结数量', '成本价', '市价', '市值', '浮动盈亏', '盈亏比例(%)',
              '交易市场', '股东帐户']
    rows = [
        ['600519', '贵州茅台', '100', '100', '0', '1650.000', '1680.00', '168000.00', '3000.00', '1.82', '上海A股', 'A000000001'],
        ['000001', '平安银行', '2000', '1500', '500', '10.500', '11.20', '22400.00', '1400.00', '6.67', '深圳A股', '0000000001'],
        ['510300', '沪深300ETF', '5000', '5000', '0', '3.900', '3.950', '19750.00', '250.00', '1.28', '上海A股', 'A000000001'],
        ['204001', 'GC001', '100', '0', '100', '0.000', '0.000', '10000.00', '0.00', '0.00', '上海A股', 'A000000001'],
        ['300750', '宁德时代', '200', '200', '0', '-5.000', '220.00', '44000.00', '45000.00', '0.00', '深圳A股', '0000000001'],
        ['832000', '示例北证', '100', '100', '0', '10.000', '10.50', '1050.00', '50.00', '5.00', '北京', '0000000001'],
        ['600036', '招商银行', '0', '0', '0', '0.000', '33.00', '0.00', '0.00', '0.00', '上海A股', 'A000000001'],
    ]
    lines = ['资金账号: 000000000 （虚构数据）', '人民币: 余额:12000.00 可用:11800.00 参考市值:255200.00 资产:267200.00',
             '\t'.join(header)] + ['\t'.join(row) for row in rows]
    (HERE / 'ths_holdings_gbk.xls').write_bytes(('\r\n'.join(lines) + '\r\n').encode('gb18030'))


def hk_connect():
    """港股通 rows settled in CNY: HKD prices, CNY 发生金额 and fees, the settlement rate."""
    header = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '佣金', '印花税', '交易费',
              '交易系统使用费', '股份交收费', '发生金额', '资金余额', '结算汇率', '币种', '交易市场']
    cash = OPENING_CASH
    rows = []
    for day, action, qty, price, turnover, fees, rate in [
            ('20261105', '港股通买入', 200, '380.00', D('76000.00'), [D('5.00'), D('77.00'), D('4.33'), D('0.50'), D('1.52')], D('0.9200')),
            ('20261112', '港股通卖出', 100, '400.00', D('40000.00'), [D('5.00'), D('40.00'), D('2.28'), D('0.50'), D('0.80')], D('0.9150'))]:
        cny = (turnover * rate).quantize(D('0.01'))
        change = -(cny + sum(fees)) if '买' in action else cny - sum(fees)
        cash += change
        rows.append([day, '00700', '腾讯控股', action, str(qty), price, money(turnover), *[money(f) for f in fees],
                     money(change), money(cash), str(rate), '人民币', '沪港通'])
    text = ','.join(header) + '\r\n' + ''.join(','.join(row) + '\r\n' for row in rows)
    (HERE / 'hk_connect_bom.csv').write_bytes(b'\xef\xbb\xbf' + text.encode('utf-8'))


if __name__ == '__main__':
    ths_trades()
    eastmoney_statement()
    tdx_trades()
    eastmoney_html()
    ths_holdings()
    hk_connect()
    print('fixtures written to', HERE.name)
