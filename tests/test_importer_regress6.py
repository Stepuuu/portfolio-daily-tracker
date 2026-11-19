"""Round 6 of the broker importer: two regressions of round 5 found by the final check (cr_v6a R1, cr_v6as B1).

Each pins the 40868ac figure next to the round-4/5 scenario it must not undo.
"""
from decimal import Decimal

import pytest

from tests.importer_support import csv, run, xlsx_bytes
from tests.test_importer_regress5 import FILLS, MORNING, book_all, cash, ledger  # noqa: F401 (fixture)


# -- R1. a trade number written 000123 in one export and 123 in the other is one number (dedupe.py:323) -----------

@pytest.mark.parametrize('number, status, include', [(123, 'possible_duplicate', False), (124, 'new', True)],
                         ids=['the same number as a workbook number', 'another number'])
def test_a_workbook_trade_number_without_leading_zeros_is_the_same_fill(ledger, number, status, include):
    morning = MORNING[:-1] + ['000123']
    created, _view, _rows = run(csv(FILLS, morning), 'morning.csv')
    book_all(ledger, created)
    assert cash(ledger) == Decimal('496785')
    workbook = xlsx_bytes([FILLS, morning[:-1] + [str(number)]], numeric=lambda r, c, v: r > 0 and c == len(FILLS) - 1)
    created, _view, rows = run(workbook, 'morning.xlsx')
    assert (rows[2]['status'], rows[2]['include']) == (status, include), rows[2]['messages']   # 40868ac: 可能重复
    if include:
        book_all(ledger, created)
        assert cash(ledger) == Decimal('493570')   # a distinct fill books by default (g4as-2.3)
    else:
        assert cash(ledger) == Decimal('496785')


# -- B1. an untimed day with a split keeps the file's order (transactions.py:1658) --------------------------------

DAY = ['发生日期', '业务名称', '证券代码', '证券名称', '成交数量', '成交价格', '成交金额', '手续费', '发生金额']
BUY = ['2026-11-05', '证券买入', '000001', '平安银行', '200', '16.00', '3200.00', '5.00', '-3205.00']
SPLIT = ['2026-11-05', '红股入账', '600036', '招商银行', '1000', '', '', '', '0.00']
SELL = ['2026-11-05', '证券卖出', '600036', '招商银行', '2000', '16.50', '33000.00', '20.00', '32980.00']


def test_an_untimed_day_with_a_split_books_in_file_order(ledger):
    created, _view, rows = run(csv(DAY, BUY, SPLIT, SELL), 'day.csv')
    assert [rows[n]['status'] for n in (2, 3, 4)] == ['new'] * 3, [rows[n]['messages'] for n in (2, 3, 4)]
    proposal = book_all(ledger, created)
    assert [e['kind'] for e in proposal['events']] == ['buy', 'split', 'sell']
    assert cash(ledger) == Decimal('529775')          # 40868ac: ¥529,775


def test_an_untimed_day_of_trades_alone_still_books_the_sale_first(ledger):
    # The round-4 scenario (g4a-2.1) beside it: newest first, no split, the sale pays for the buy.
    created, _view, rows = run(csv(DAY, ['2026-11-05', '证券买入', '000001', '平安银行', '50000', '10.00', '500000.00', '5.00',
                                         '-500005.00'], SELL[:4] + ['1000'] + SELL[5:6] + ['16500.00', '10.00', '16490.00']),
                               'day.csv')
    assert [rows[2]['status'], rows[3]['status']] == ['new', 'new'], [rows[2]['messages'], rows[3]['messages']]
    book_all(ledger, created)
    assert cash(ledger) == Decimal('16485')
