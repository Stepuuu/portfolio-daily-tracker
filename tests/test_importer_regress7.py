"""Round 7 of the broker importer: a split of another listing leaves an untimed day's sales before its buys
(cr_v7a B1, cr_v7as B-1), while a split of a listing the day trades keeps the file's order (cr_v6as B1)."""
from decimal import Decimal

import pytest

from tests.importer_support import OPENING, csv, ledger_at, open_account, run
from tests.test_importer_regress5 import book_all, cash
from tests.test_importer_regress6 import BUY as SAME_BUY, DAY, SELL as SAME_SELL, SPLIT as SAME_SPLIT


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    ledger = ledger_at(tmp_path)
    open_account(ledger, cash_balances={'CNY': '5000.00'},
                 positions=OPENING['positions'] + [{'ticker': 'SHA:601318', 'name': '中国平安', 'quantity': '100',
                                                    'cost_price': '50'}])
    return ledger


OTHER_SPLIT = ['2026-11-05', '红股入账', '601318', '中国平安', '100', '', '', '', '0.00']


@pytest.mark.parametrize('lines, closing', [
    # A ¥15,005 buy, the ¥10,990 sale that funds it, a split of a third stock (5db322a: ¥985).
    ([['2026-11-05', '证券买入', '600036', '招商银行', '500', '30.00', '15000.00', '5.00', '-15005.00'],
      ['2026-11-05', '证券卖出', '000001', '平安银行', '1000', '11.00', '11000.00', '10.00', '10990.00'],
      OTHER_SPLIT], '985'),
    # Newest first: the buy, the unrelated split, the sale that funds the buy (5db322a: ¥11,485).
    ([['2026-11-05', '证券买入', '000001', '平安银行', '1000', '10.00', '10000.00', '5.00', '-10005.00'], OTHER_SPLIT,
      ['2026-11-05', '证券卖出', '600036', '招商银行', '1000', '16.50', '16500.00', '10.00', '16490.00']], '11485'),
], ids=['split of a third stock last', 'split of a third stock between, newest first'])
def test_a_split_of_a_listing_the_day_does_not_trade_leaves_the_sale_first(ledger, lines, closing):
    created, _view, rows = run(csv(DAY, *lines), 'day.csv')
    assert [rows[n]['status'] for n in (2, 3, 4)] == ['new'] * 3, [rows[n]['messages'] for n in (2, 3, 4)]
    book_all(ledger, created)
    assert cash(ledger) == Decimal(closing)


def test_a_split_of_a_listing_the_day_trades_still_keeps_the_file_order(ledger):
    # cr_v6as B1 beside it (40868ac: buy, split, sale of 600036 in file order).
    created, _view, rows = run(csv(DAY, SAME_BUY, SAME_SPLIT, SAME_SELL), 'day.csv')
    assert [rows[n]['status'] for n in (2, 3, 4)] == ['new'] * 3, [rows[n]['messages'] for n in (2, 3, 4)]
    proposal = book_all(ledger, created)
    assert [e['kind'] for e in proposal['events']] == ['buy', 'split', 'sell']
    assert cash(ledger) == Decimal('34775')    # ¥5,000 − 3,205 + 32,980
