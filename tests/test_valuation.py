"""Valuation rules: currency conversion, honest gaps, and day profit across trades."""
from dataclasses import replace
from decimal import Decimal

import pytest

from core.market.valuation import (ADJUSTED_TODAY, NO_PREV_CLOSE, NO_PREVIOUS_STATE, OPENING_TODAY, SPLIT_TODAY,
                                   value_portfolio)
from tests.fakes import build_ledger, book, fx_rate, opening, position, quote, shanghai, trade

YESTERDAY, TODAY = '2026-11-16', '2026-11-17'
CN, CN2, HK, US = 'SHA:600001', 'SHE:000002', 'HKG:00001', 'NASDAQ:EXAMPLE'
RATES = {'USD': Decimal('7'), 'HKD': Decimal('0.9')}
D = Decimal


def priced(ticker, price, prev_close=None):
    return replace(quote(ticker, price, prev_close=prev_close, at=shanghai('2026-11-17 10:00')), status='live')


def valued(ledger, quotes, rates=RATES, **options):
    """Value the ledger as of TODAY against what was held at YESTERDAY's close."""
    events = [event for event in ledger.history() if event['date'] >= TODAY]
    return value_portfolio(ledger.state(), {q.ticker: q for q in quotes}, rates, ledger.state(YESTERDAY), events,
                           **options)


@pytest.fixture
def ledger(tmp_path):
    return build_ledger(tmp_path, [
        opening('Main', cash={'CNY': '1000', 'USD': '100', 'HKD': '0'}, fund_cny='500', date=YESTERDAY, positions=[
            position(CN, '100', '10', '示例甲'), position(US, '10', '50'), position(HK, '200', '4')]),
        opening('Other', cash={'CNY': '300'}, date=YESTERDAY, positions=[position(CN2, '100', '20')]),
    ])


QUOTES = [priced(CN, '12', '11'), priced(US, '60', '58'), priced(HK, '5', '5.5'), priced(CN2, '19', '20')]


def test_positions_cash_and_funds_are_valued_in_cny(ledger):
    result = valued(ledger, QUOTES)
    main, other = result.accounts
    cn, us, hk = main.positions
    assert (cn.market_value, cn.market_value_cny, cn.total_pnl, cn.total_pnl_pct) == (D(1200), D(1200), D(200), D(20))
    assert (us.market_value, us.market_value_cny, us.total_pnl, us.total_pnl_pct) == (D(600), D(4200), D(100), D(20))
    assert (hk.market_value, hk.market_value_cny, hk.total_pnl, hk.total_pnl_pct) == (D(1000), D(900), D(200), D(25))
    assert (cn.name, us.name, cn.currency, us.currency, hk.currency) == ('示例甲', US, 'CNY', 'USD', 'HKD')
    assert us.change_pct.quantize(D('0.01')) == D('3.45')
    assert main.cash == {'CNY': D(1000), 'USD': D(100), 'HKD': D(0)}
    assert (main.cash_cny, main.fund_cny, main.market_value_cny, main.total_cny) == (D(1700), D(500), D(6300), D(8500))
    assert main.total_pnl_cny == D(200) + D(100) * 7 + D(200) * D('0.9')
    assert (other.total_cny, other.total_pnl_cny) == (D(2200), D(-100))
    assert (result.total_cny, result.total_pnl_cny) == (D(10700), D(980))
    assert result.by_currency == {'CNY': {'market_value': D(3100), 'cash': D(1300)},
                                  'USD': {'market_value': D(600), 'cash': D(100)},
                                  'HKD': {'market_value': D(1000), 'cash': D(0)}}
    assert result.unavailable == ()


def test_weights_are_shares_of_the_whole_portfolio_including_cash_and_funds(ledger):
    result = valued(ledger, QUOTES)
    weights = [p.weight_pct for account in result.accounts for p in account.positions]
    assert weights[0] == D(1200) / D(10700) * 100
    cash_and_funds = sum(account.cash_cny + account.fund_cny for account in result.accounts)
    assert sum(weights) + cash_and_funds / result.total_cny * 100 == pytest.approx(D(100))


@pytest.mark.parametrize('broken', [
    None,                                                              # the source returned nothing
    quote(US, None, reason='停牌', prev_close='58'),                   # an unavailable quote
    replace(priced(US, '60'), price=D(0)),                             # a zero that slipped through
    replace(priced(US, '60'), price=D(-3)),
])
def test_a_missing_or_zero_price_is_null_all_the_way_up(ledger, broken):
    quotes = [q for q in QUOTES if q.ticker != US] + ([broken] if broken else [])
    result = valued(ledger, quotes)
    main, other = result.accounts
    us = main.positions[1]
    assert (us.price, us.market_value, us.market_value_cny, us.total_pnl, us.total_pnl_pct, us.day_pnl,
            us.day_pnl_cny, us.weight_pct, us.change_pct) == (None,) * 9
    assert us.quantity == D(10) and us.cost_price == D(50)            # what the ledger knows is still shown
    # Every total that contains the position is unknown: no partial sums, no zeros.
    assert (main.market_value_cny, main.total_cny, main.day_pnl_cny, main.total_pnl_cny) == (None,) * 4
    assert (result.total_cny, result.day_pnl_cny, result.total_pnl_cny) == (None,) * 3
    assert result.by_currency['USD'] == {'market_value': None, 'cash': D(100)}
    # What does not depend on it is unaffected.
    assert main.positions[0].market_value_cny == D(1200) and main.cash_cny == D(1700)
    assert other.total_cny == D(2200) and result.by_currency['CNY']['market_value'] == D(3100)
    assert all(p.weight_pct is None for account in result.accounts for p in account.positions)
    reasons = [entry for entry in result.unavailable if entry.get('ticker') == US]
    assert len(reasons) == 1 and reasons[0]['reason'] and 'field' not in reasons[0]
    if broken is not None and broken.reason:
        assert reasons[0]['reason'] == '停牌'


@pytest.mark.parametrize('rates', [
    {'USD': D(7)},                                                     # no HKD rate at all
    {'USD': D(7), 'HKD': fx_rate('HKD', None, reason='汇率源超时')},   # an unavailable rate
    {'USD': D(7), 'HKD': D(0)},
])
def test_a_missing_currency_rate_is_null_for_everything_in_that_currency(ledger, rates):
    result = valued(ledger, QUOTES, rates)
    main, other = result.accounts
    hk = main.positions[2]
    assert (hk.market_value, hk.total_pnl, hk.day_pnl) == (D(1000), D(200), D(-100))   # native figures survive
    assert (hk.market_value_cny, hk.day_pnl_cny, hk.weight_pct) == (None, None, None)
    assert (main.market_value_cny, main.total_cny, main.day_pnl_cny, main.total_pnl_cny) == (None,) * 4
    assert result.total_cny is None and result.day_pnl_cny is None
    assert main.cash_cny == D(1700)            # the HKD balance is zero, so it needs no rate
    assert other.total_cny == D(2200)
    entry = [entry for entry in result.unavailable if entry.get('currency') == 'HKD']
    assert len(entry) == 1 and entry[0]['reason']


def test_a_nonzero_cash_balance_needs_its_rate(ledger):
    result = valued(ledger, QUOTES, {'HKD': D('0.9')})
    main = result.accounts[0]
    assert main.cash_cny is None and main.total_cny is None and result.total_cny is None
    assert {'currency': 'USD', 'reason': '汇率不可用'} in result.unavailable


def test_fx_rate_objects_and_plain_rates_agree(ledger):
    at = shanghai('2026-11-17 10:00')
    rates = {'USD': fx_rate('USD', '7', at=at), 'HKD': fx_rate('HKD', '0.9', at=at)}
    assert valued(ledger, QUOTES, rates).total_cny == valued(ledger, QUOTES).total_cny == D(10700)


# -- estimated values ---------------------------------------------------------

def test_verified_inputs_give_complete_totals(ledger):
    result = valued(ledger, QUOTES)
    assert (result.quality, result.estimated) == ('complete', ())
    assert all(account.quality == 'complete' for account in result.accounts)
    assert not any(p.estimated or p.estimated_reason for a in result.accounts for p in a.positions)


@pytest.mark.parametrize('status', ['stale', 'time_unknown', 'prev_close'])
def test_a_row_valued_from_an_unverified_quote_is_estimated_and_so_are_its_totals(ledger, status):
    unverified = replace(priced(CN, '12', '11'), status=status, reason='示例原因')
    result = valued(ledger, [unverified] + QUOTES[1:])
    main, other = result.accounts
    cn = main.positions[0]
    assert cn.market_value_cny == D(1200)                       # still computed ...
    assert cn.estimated is True and '示例原因' in cn.estimated_reason   # ... but flagged
    assert not any(p.estimated for p in main.positions[1:])
    assert result.total_cny == D(10700) and result.unavailable == ()
    entry = {'ticker': CN, 'status': status, 'reason': '示例原因'}
    assert (result.quality, result.estimated) == ('estimated', (entry,))
    assert (main.quality, main.estimated) == ('estimated', (entry,))
    assert (other.quality, other.estimated) == ('complete', ())


def test_a_close_from_a_weekday_only_calendar_is_estimated(ledger):
    weekday = replace(priced(US, '60', '58'), status='closed', reason='该市场未内置节假日表', calendar_confidence='weekday_only')
    verified = replace(priced(CN, '12', '11'), status='closed', calendar_confidence='calendar')
    result = valued(ledger, [verified, weekday] + QUOTES[2:])
    cn, us, _ = result.accounts[0].positions
    assert (cn.estimated, us.estimated) == (False, True)
    assert result.estimated == ({'ticker': US, 'status': 'closed', 'reason': '该市场未内置节假日表'},)


@pytest.mark.parametrize('status', ['stale', 'time_unknown'])
def test_an_unverified_currency_rate_marks_what_it_converts_as_estimated(ledger, status):
    rates = {'USD': replace(fx_rate('USD', '7', at=shanghai('2026-10-01 10:00')), status=status, reason='汇率示例原因'),
             'HKD': D('0.9')}
    result = valued(ledger, QUOTES, rates)
    main, other = result.accounts
    cn, us, hk = main.positions
    assert result.total_cny == D(10700) and result.unavailable == ()
    assert us.estimated is True and 'USD' in us.estimated_reason and not cn.estimated and not hk.estimated
    entry = {'currency': 'USD', 'status': status, 'reason': '汇率示例原因'}
    assert result.estimated == (entry,) and result.quality == 'estimated'
    assert main.estimated == (entry,) and other.quality == 'complete'


def test_an_unavailable_input_is_null_not_estimated(ledger):
    quotes = [q for q in QUOTES if q.ticker != US] + [quote(US, None, reason='停牌')]
    result = valued(ledger, quotes)
    assert result.total_cny is None and result.estimated == () and result.quality == 'complete'
    assert {'ticker': US, 'reason': '停牌'} in result.unavailable


# -- day profit ---------------------------------------------------------------

def test_day_profit_without_trades_is_the_move_from_the_previous_close(ledger):
    result = valued(ledger, QUOTES)
    cn, us, hk = result.accounts[0].positions
    assert (cn.day_pnl, cn.day_pnl_cny) == (D(100), D(100))
    assert (us.day_pnl, us.day_pnl_cny) == (D(20), D(140))
    assert (hk.day_pnl, hk.day_pnl_cny) == (D(-100), D(-90))
    assert result.accounts[0].day_pnl_cny == D(150) and result.accounts[1].day_pnl_cny == D(-100)
    assert result.day_pnl_cny == D(50)


def test_shares_bought_today_earn_from_their_purchase_price(ledger):
    book(ledger, [trade('buy', CN, '50', '11.5', fee='5', account='Main', date=TODAY)])
    cn = valued(ledger, QUOTES).accounts[0].positions[0]
    # 150 x 12 - 100 x 11 - (50 x 11.5 + 5) = 120, not 150 x (12 - 11) = 150.
    assert cn.quantity == D(150) and cn.day_pnl == D(120)


def test_shares_sold_today_stop_earning_at_their_sale_price(ledger):
    book(ledger, [trade('sell', CN, '40', '11.5', fee='2', account='Main', date=TODAY)])
    cn = valued(ledger, QUOTES).accounts[0].positions[0]
    # 60 x 12 - 100 x 11 + (40 x 11.5 - 2) = 78, not 60 x (12 - 11) = 60.
    assert cn.quantity == D(60) and cn.day_pnl == D(78)


def test_buying_and_selling_the_same_day_in_a_foreign_currency(ledger):
    book(ledger, [{**trade('buy', US, '5', '59', fee='1', currency='USD', account='Main', date=TODAY),
                   'allow_margin': True},
                  trade('sell', US, '3', '61', fee='1', currency='USD', account='Main', date=TODAY)])
    us = valued(ledger, QUOTES).accounts[0].positions[1]
    # 12 x 60 - 10 x 58 - (5 x 59 + 1) + (3 x 61 - 1) = 26
    assert us.quantity == D(12) and (us.day_pnl, us.day_pnl_cny) == (D(26), D(182))


def test_a_position_sold_out_today_keeps_its_day_profit_in_the_total(ledger):
    book(ledger, [trade('sell', CN, '100', '11.5', fee='2', account='Main', date=TODAY)])
    result = valued(ledger, QUOTES)
    main = result.accounts[0]
    assert [p.ticker for p in main.positions] == [US, HK]
    closed, = main.closed_today
    # -100 x 11 + (100 x 11.5 - 2) = 48
    assert (closed.ticker, closed.quantity, closed.market_value, closed.day_pnl) == (CN, D(0), D(0), D(48))
    assert main.day_pnl_cny == D(48) + D(140) - D(90)
    assert main.market_value_cny == D(5100)            # the closed row adds no market value
    assert result.by_currency['CNY']['market_value'] == D(1900)


def test_a_sold_out_position_needs_the_previous_close_not_the_price(ledger):
    book(ledger, [trade('sell', CN, '100', '11.5', fee='2', account='Main', date=TODAY)])
    others = [q for q in QUOTES if q.ticker != CN]
    with_close = valued(ledger, others + [quote(CN, None, prev_close='11', reason='停牌')])
    assert with_close.accounts[0].closed_today[0].day_pnl == D(48)
    assert with_close.total_cny is not None            # nothing is held at the missing price
    without = valued(ledger, others)
    assert without.accounts[0].closed_today[0].day_pnl is None
    assert without.day_pnl_cny is None and without.total_cny is not None
    assert {'ticker': CN, 'field': 'day_pnl', 'reason': NO_PREV_CLOSE} in without.unavailable


def test_a_round_trip_within_the_day_needs_no_quote_at_all(ledger):
    book(ledger, [trade('buy', 'SHA:600009', '100', '5', fee='1', account='Main', date=TODAY),
                  trade('sell', 'SHA:600009', '100', '5.2', fee='1', account='Main', date=TODAY)])
    result = valued(ledger, QUOTES)
    closed, = result.accounts[0].closed_today
    assert (closed.ticker, closed.currency, closed.day_pnl) == ('SHA:600009', 'CNY', D(18))
    assert result.day_pnl_cny == D(50) + D(18)


def test_a_split_today_makes_day_profit_unavailable_with_a_reason(ledger):
    book(ledger, [{'kind': 'split', 'date': TODAY, 'account': 'Main', 'ticker': CN, 'ratio': '2'}])
    result = valued(ledger, [priced(CN, '6', '11')] + QUOTES[1:])
    cn = result.accounts[0].positions[0]
    assert (cn.quantity, cn.market_value, cn.total_pnl) == (D(200), D(1200), D(200))   # still valued
    assert cn.day_pnl is None and cn.day_pnl_cny is None
    assert result.accounts[0].day_pnl_cny is None and result.day_pnl_cny is None
    assert result.accounts[1].day_pnl_cny == D(-100)   # the other account is unaffected
    assert result.total_cny == D(10700)
    assert {'ticker': CN, 'field': 'day_pnl', 'reason': SPLIT_TODAY} in result.unavailable


def test_an_opening_balance_dated_today_has_no_day_profit_yet(tmp_path):
    ledger = build_ledger(tmp_path, [opening('Main', cash={'CNY': '100'}, date=TODAY, positions=[position(CN, '100', '10')])])
    result = valued(ledger, QUOTES)
    assert result.accounts[0].positions[0].day_pnl is None and result.day_pnl_cny is None
    assert result.total_cny == D(1300)
    assert {'ticker': CN, 'field': 'day_pnl', 'reason': OPENING_TODAY} in result.unavailable


def test_a_position_change_that_is_not_a_trade_is_not_explained_away(ledger):
    # The previous state says 100 shares, the ledger now says 100, but a buy of
    # 50 is claimed for today: the three cannot all be true.
    events = [trade('buy', CN, '50', '11.5', account='Main', date=TODAY)]
    result = value_portfolio(ledger.state(), {q.ticker: q for q in QUOTES}, RATES, ledger.state(YESTERDAY), events)
    assert result.accounts[0].positions[0].day_pnl is None
    assert {'ticker': CN, 'field': 'day_pnl', 'reason': ADJUSTED_TODAY} in result.unavailable


def test_reversed_trades_do_not_count_as_todays_trades(ledger):
    receipt = book(ledger, [trade('buy', CN, '50', '11.5', account='Main', date=TODAY)])
    bought = [event for event in ledger.history() if event['kind'] == 'buy'][0]
    book(ledger, [{'kind': 'reverse', 'date': TODAY, 'target_id': bought['id']}])
    assert receipt['applied'] == 1
    cn = valued(ledger, QUOTES).accounts[0].positions[0]
    assert cn.quantity == D(100) and cn.day_pnl == D(100)


def test_without_a_previous_state_day_profit_is_unavailable_everywhere(ledger):
    result = value_portfolio(ledger.state(), {q.ticker: q for q in QUOTES}, RATES, None, [])
    assert all(p.day_pnl is None and p.day_pnl_cny is None for a in result.accounts for p in a.positions)
    assert all(account.day_pnl_cny is None for account in result.accounts) and result.day_pnl_cny is None
    assert (result.total_cny, result.total_pnl_cny) == (D(10700), D(980))    # everything else still stands
    assert result.unavailable == ({'field': 'day_pnl', 'reason': NO_PREVIOUS_STATE},)
    custom = value_portfolio(ledger.state(), {}, RATES, None, [], day_reasons={'*': '账本回放失败'})
    assert {'field': 'day_pnl', 'reason': '账本回放失败'} in custom.unavailable


def test_a_ticker_level_reason_blocks_only_that_ticker(ledger):
    result = valued(ledger, QUOTES, day_reasons={US: '行情缺少交易所时间'})
    cn, us, hk = result.accounts[0].positions
    assert us.day_pnl is None and (cn.day_pnl, hk.day_pnl) == (D(100), D(-100))
    assert us.market_value_cny == D(4200) and result.total_cny == D(10700)
    assert result.day_pnl_cny is None
    assert {'ticker': US, 'field': 'day_pnl', 'reason': '行情缺少交易所时间'} in result.unavailable


def test_missing_previous_close_blocks_day_profit_but_not_value(ledger):
    result = valued(ledger, [priced(CN, '12')] + QUOTES[1:])
    cn = result.accounts[0].positions[0]
    assert (cn.market_value, cn.change_pct, cn.day_pnl) == (D(1200), None, None)
    assert result.total_cny == D(10700) and result.day_pnl_cny is None


def test_zero_cost_basis_has_no_percentage(tmp_path):
    ledger = build_ledger(tmp_path, [opening('Main', date=YESTERDAY, positions=[position(CN, '100', '0')])])
    cn = valued(ledger, QUOTES).accounts[0].positions[0]
    assert (cn.total_pnl, cn.total_pnl_pct) == (D(1200), None)


def test_an_account_with_only_cash_is_fully_known(tmp_path):
    ledger = build_ledger(tmp_path, [opening('Cash', cash={'CNY': '250.50'}, date=YESTERDAY)])
    result = valued(ledger, [], {})
    assert (result.total_cny, result.day_pnl_cny, result.total_pnl_cny) == (D('250.50'), D(0), D(0))
    assert result.unavailable == ()


# -- agreement with the snapshot engine ---------------------------------------

def test_totals_agree_with_calculate_snapshot(tmp_path):
    """The dashboard re-implements the engine's valuation; they must not drift."""
    from portfolio_snapshot import calculate_snapshot   # test-only: the backend image has no engine/

    ledger = build_ledger(tmp_path, [
        opening('长期账户', cash={'CNY': '20000', 'USD': '1000', 'HKD': '5000'}, contributed_cny='81000',
                date=YESTERDAY, positions=[position(CN, '1000', '50.25', '示例宽基'), position(HK, '300', '41.3')]),
        opening('研究账户', cash={'CNY': '10000'}, fund_cny='5000', contributed_cny='29000', date=YESTERDAY,
                positions=[position(US, '20.5', '100.1', '示例研究标的')]),
    ])
    book(ledger, [trade('buy', CN, '300', '51.07', fee='5.5', account='长期账户', date=TODAY),
                  trade('sell', HK, '100', '43.2', fee='12.34', currency='HKD', account='长期账户', date=TODAY),
                  # Bought on margin: the negative USD balance must convert the same way too.
                  {**trade('buy', US, '1.25', '117.3', fee='1', currency='USD', account='研究账户', date=TODAY),
                   'allow_margin': True}])
    prices = {CN: '52.37', HK: '44.15', US: '118.42'}
    rates = {'USD': '7.0523', 'HKD': '0.9017'}

    result = valued(ledger, [priced(t, p, p) for t, p in prices.items()], {c: D(r) for c, r in rates.items()})
    snapshot = calculate_snapshot(ledger.holdings(TODAY), {t: float(p) for t, p in prices.items()}, {},
                                  {'CNY': 1.0, **{c: float(r) for c, r in rates.items()}}, None, [])

    def cents(value):
        return float(value.quantize(D('0.01')))

    assert result.unavailable == ()
    assert cents(result.total_cny) == pytest.approx(snapshot['summary']['total_value'], abs=0.011)
    for account in result.accounts:
        group = snapshot['groups'][account.name]
        assert cents(account.total_cny) == pytest.approx(group['total_value'], abs=0.011)
        assert cents(account.market_value_cny) == pytest.approx(group['positions_value'], abs=0.011)
        assert cents(account.cash_cny) == pytest.approx(group['cash'], abs=0.011)
        assert float(account.fund_cny) == group['fund']
        assert {c: float(v) for c, v in account.cash.items()} == group['cash_balances']
        assert [p.ticker for p in account.positions] == [p['ticker'] for p in group['positions']]
        for mine, theirs in zip(account.positions, group['positions']):
            rate = D(1) if mine.currency == 'CNY' else D(rates[mine.currency])
            assert cents(mine.market_value_cny) == pytest.approx(theirs['market_value_cny'], abs=0.011)
            assert cents(mine.total_pnl * rate) == pytest.approx(theirs['profit_cny'], abs=0.011)
            assert float(mine.total_pnl_pct) == pytest.approx(theirs['profit_pct'], abs=0.006)
            assert mine.currency == theirs['currency'] and float(mine.quantity) == theirs['quantity']
    # The engine's total_profit is measured against contributed capital; the
    # sum of open-position profit is the same figure the engine reports per row.
    assert cents(result.total_pnl_cny) == pytest.approx(
        sum(p['profit_cny'] for g in snapshot['groups'].values() for p in g['positions']), abs=0.03)


B_SHARE = 'SHA:900901'     # trades in US dollars; the ledger books every SHA holding in CNY


@pytest.fixture
def b_shares_bookable(monkeypatch):
    """Rows booked before the ledger refused B shares (``core.ledger.bookable``)."""
    import core.ledger
    monkeypatch.setattr(core.ledger, 'bookable', core.ledger.instrument)


@pytest.mark.parametrize('quote_currency', ['USD', None])
def test_a_b_share_booked_in_cny_is_never_valued_at_its_dollar_price(tmp_path, quote_currency, b_shares_bookable):
    ledger = build_ledger(tmp_path, [opening('Main', cash={'CNY': '1000'}, date=YESTERDAY, positions=[
        position(CN, '100', '10'), position(B_SHARE, '1000', '0.5')])])
    b_share = replace(priced(B_SHARE, '0.498', '0.5'), currency=quote_currency)
    result = valued(ledger, [priced(CN, '12', '11'), b_share])
    cn, row = result.accounts[0].positions
    assert row.currency == 'CNY'
    assert (row.price, row.prev_close, row.market_value, row.market_value_cny, row.day_pnl, row.total_pnl) == (
        None, None, None, None, None, None)
    assert cn.market_value_cny == D(1200)
    assert result.total_cny is None and result.day_pnl_cny is None and result.total_pnl_cny is None
    assert result.by_currency['CNY']['market_value'] is None
    [entry] = result.unavailable
    assert entry['ticker'] == B_SHARE and 'B 股' in entry['reason'] and 'USD' in entry['reason']


def test_a_b_share_sold_today_has_no_day_profit_mixing_dollars_and_yuan(tmp_path, b_shares_bookable):
    ledger = build_ledger(tmp_path, [opening('Main', cash={'CNY': '1000'}, date=YESTERDAY, positions=[
        position(B_SHARE, '1000', '0.5')])])
    book(ledger, [trade('sell', B_SHARE, '1000', '0.52', account='Main', date=TODAY)])
    result = valued(ledger, [priced(B_SHARE, '0.52', '0.5')])
    [row] = result.accounts[0].closed_today
    assert (row.day_pnl, row.day_pnl_cny) == (None, None) and result.day_pnl_cny is None
    assert any(entry.get('field') == 'day_pnl' and 'B 股' in entry['reason'] for entry in result.unavailable)
    assert result.total_cny == D('1520.00')          # the cash from the sale, as the ledger booked it


def test_a_quote_in_another_currency_than_the_ledger_books_is_not_a_price_for_it(ledger):
    # Any source answering in another currency than the booking one is refused, not converted.
    wrong = replace(priced(HK, '5', '5.5'), currency='USD')
    result = valued(ledger, [priced(CN, '12', '11'), priced(US, '60', '58'), wrong, priced(CN2, '19', '20')])
    hk = result.accounts[0].positions[2]
    assert hk.price is None and hk.market_value_cny is None and result.total_cny is None
    assert any(entry.get('ticker') == HK and 'USD' in entry['reason'] and 'HKD' in entry['reason']
               for entry in result.unavailable)
