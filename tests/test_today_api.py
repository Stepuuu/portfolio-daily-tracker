"""GET /api/today: the response contract, honest gaps, the empty state and demo mode."""
import json
import re
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import features
from core.market.runtime import get_market_runtime, set_market_runtime
from tests.fakes import (FakeClock, ScriptedSource, build_ledger, book, fake_runtime, fx_rate, opening, position, quote,
                         shanghai, trade, write_fake_quotes)

FRIDAY, MONDAY, TUESDAY = '2026-11-13', '2026-11-16', '2026-11-17'
CN, HK, US = 'SHA:600001', 'HKG:00001', 'NASDAQ:EXAMPLE'
DECIMAL = re.compile(r'-?\d+(\.\d+)?')
OFFSET_TIME = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00')

TOP_KEYS = {'as_of', 'source', 'ledger_revision', 'session', 'fx', 'accounts', 'totals', 'notices'}
SESSION_KEYS = {'market', 'phase', 'trading_day', 'calendar_confidence'}
FX_KEYS = {'rate', 'source', 'quote_time', 'status'}
ACCOUNT_KEYS = {'name', 'positions', 'cash', 'cash_cny', 'fund_cny', 'total_cny'}
POSITION_KEYS = {'ticker', 'name', 'currency', 'quantity', 'cost_price', 'price', 'prev_close', 'change_pct',
                 'market_value', 'market_value_cny', 'day_pnl', 'day_pnl_cny', 'total_pnl', 'total_pnl_pct',
                 'weight_pct', 'quote'}
QUOTE_KEYS = {'source', 'quote_time', 'fetched_at', 'status', 'reason'}
TOTAL_KEYS = {'by_currency', 'total_cny', 'day_pnl_cny', 'total_pnl_cny', 'unavailable'}
DERIVED = ['price', 'change_pct', 'market_value', 'market_value_cny', 'day_pnl', 'day_pnl_cny', 'total_pnl',
           'total_pnl_pct', 'weight_pct']
STATUSES = {'live', 'delayed', 'stale', 'closed', 'prev_close', 'time_unknown', 'unavailable'}


@pytest.fixture
def desk(tmp_path, monkeypatch):
    """An isolated portfolio directory and no leftover runtime."""
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    set_market_runtime(None)
    yield tmp_path
    set_market_runtime(None)


@pytest.fixture
def client():
    app = FastAPI()
    for router, prefix, tags in features.routers():
        app.include_router(router, prefix=prefix, tags=tags)
    return TestClient(app)


def holdings(directory):
    return build_ledger(directory, [
        opening('Main', cash={'CNY': '1000', 'USD': '100'}, fund_cny='500', date=FRIDAY, positions=[
            position(CN, '100', '10', '示例甲'), position(US, '10', '50'), position(HK, '200', '4')]),
        opening('Other', cash={'CNY': '300'}, date=FRIDAY),
    ])


def market(clock, **overrides):
    """A scripted source: mainland and Hong Kong trading now, New York closed at 05:00 Shanghai time."""
    now = clock.now()
    quotes = {CN: quote(CN, '12', prev_close='11', at=now - timedelta(seconds=3), name='示例甲'),
              HK: quote(HK, '5', prev_close='5.5', at=now - timedelta(seconds=2)),
              US: quote(US, '60', prev_close='58', at=shanghai('2026-11-17 05:00'))}
    quotes.update(overrides)
    return ScriptedSource(quotes, {'USD': fx_rate('USD', '7', at=now - timedelta(seconds=8)),
                                   'HKD': fx_rate('HKD', '0.9', at=now - timedelta(seconds=8))})


def install(clock, source):
    set_market_runtime(fake_runtime(clock, source))
    return source


def money_fields(payload):
    """Every money or percentage string in the payload, with its path."""
    found = []
    for account in payload['accounts']:
        for row in account['positions'] + account.get('closed_today', []):
            found += [(f"{row['ticker']}.{key}", row[key]) for key in DERIVED + ['prev_close', 'cost_price', 'quantity']]
        found += [(f"{account['name']}.{key}", account[key]) for key in ('cash_cny', 'fund_cny', 'total_cny')]
        found += [(f"{account['name']}.cash.{c}", v) for c, v in account['cash'].items()]
    found += [(f'totals.{key}', payload['totals'][key]) for key in ('total_cny', 'day_pnl_cny', 'total_pnl_cny')]
    for currency, entry in payload['totals']['by_currency'].items():
        found += [(f'totals.{currency}.{key}', value) for key, value in entry.items()]
    found += [(f'fx.{c}', entry['rate']) for c, entry in payload['fx'].items()]
    return found


# -- the contract -------------------------------------------------------------

def test_response_follows_the_contract(desk, client):
    holdings(desk)
    install(FakeClock('2026-11-17 10:00'), market(FakeClock('2026-11-17 10:00')))
    response = client.get('/api/today')
    assert response.status_code == 200
    data = response.json()

    assert TOP_KEYS <= set(data) and 'notice' not in data
    assert OFFSET_TIME.fullmatch(data['as_of']) and data['as_of'] == '2026-11-17T10:00:00+08:00'
    assert data['source'] == 'scripted' and data['ledger_revision'] == 1
    assert SESSION_KEYS <= set(data['session'])
    assert {key: data['session'][key] for key in SESSION_KEYS} == {
        'market': 'CN', 'phase': 'morning', 'trading_day': TUESDAY, 'calendar_confidence': 'calendar'}
    assert set(data['fx']) == {'USD', 'HKD'}
    for rate in data['fx'].values():
        assert FX_KEYS <= set(rate) and rate['status'] == 'live' and OFFSET_TIME.fullmatch(rate['quote_time'])
    assert data['fx']['USD']['rate'] == '7' and data['fx']['HKD']['rate'] == '0.9'

    assert [account['name'] for account in data['accounts']] == ['Main', 'Other']
    for account in data['accounts']:
        assert ACCOUNT_KEYS <= set(account)
        for row in account['positions']:
            assert POSITION_KEYS <= set(row) and QUOTE_KEYS <= set(row['quote'])
            assert row['quote']['status'] in STATUSES
            assert OFFSET_TIME.fullmatch(row['quote']['quote_time']) and OFFSET_TIME.fullmatch(row['quote']['fetched_at'])
    assert TOTAL_KEYS <= set(data['totals'])
    for entry in data['totals']['by_currency'].values():
        assert set(entry) == {'market_value', 'cash'}
    # Money and quantities are decimal strings, never JSON numbers.
    for path, value in money_fields(data):
        assert isinstance(value, str) and DECIMAL.fullmatch(value), path


def test_values_in_the_response(desk, client):
    holdings(desk)
    install(FakeClock('2026-11-17 10:00'), market(FakeClock('2026-11-17 10:00')))
    data = client.get('/api/today').json()
    main, other = data['accounts']
    cn, us, hk = main['positions']
    assert cn == {
        'ticker': CN, 'name': '示例甲', 'currency': 'CNY', 'quantity': '100', 'cost_price': '10', 'price': '12',
        'prev_close': '11', 'change_pct': '9.09', 'market_value': '1200.00', 'market_value_cny': '1200.00',
        'day_pnl': '100.00', 'day_pnl_cny': '100.00', 'total_pnl': '200.00', 'total_pnl_cny': '200.00',
        'total_pnl_pct': '20.00',
        'weight_pct': '13.64',
        # Added by round 5 (B3): the weight the alert monitor compares with a cap, cut at 20 places.
        'weight_pct_exact': '13.63636363636363636363',
        'quote': {'source': 'scripted', 'quote_time': '2026-11-17T09:59:57+08:00',
                  'fetched_at': '2026-11-17T09:59:57+08:00', 'status': 'live', 'reason': None,
                  'session_date': '2026-11-17'},
        'estimated': False, 'estimated_reason': None,
        # Added by the market UI review: the row's listing (``core.market.search.identity_key``).
        'identity': CN}
    assert (us['market_value'], us['market_value_cny'], us['day_pnl'], us['day_pnl_cny']) == (
        '600.00', '4200.00', '20.00', '140.00')
    assert us['quote']['status'] == 'closed' and us['quote']['quote_time'] == '2026-11-17T05:00:00+08:00'
    # Changed with the HK/US tables: New York's 2026 holidays are known, so its close is verified.
    assert (us['estimated'], us['estimated_reason'], us['quote']['reason']) == (False, None, None)
    assert (hk['market_value_cny'], hk['day_pnl_cny'], hk['total_pnl_pct']) == ('900.00', '-90.00', '25.00')
    assert main['cash'] == {'CNY': '1000.00', 'USD': '100.00'}
    assert (main['cash_cny'], main['fund_cny'], main['total_cny']) == ('1700.00', '500.00', '8500.00')
    assert (other['positions'], other['total_cny']) == ([], '300.00')
    assert data['totals'] == {
        'by_currency': {'CNY': {'market_value': '1200.00', 'cash': '1300.00'},
                        'USD': {'market_value': '600.00', 'cash': '100.00'},
                        'HKD': {'market_value': '1000.00', 'cash': '0.00'}},
        'total_cny': '8800.00', 'day_pnl_cny': '150.00', 'total_pnl_cny': '1080.00', 'unavailable': [],
        'quality': 'complete', 'estimated': [],
        'total_pnl_kind': 'unrealized', 'contributed_cny': '0.00', 'cumulative_pnl_cny': '8800.00',
        'cumulative_pnl_pct': None, 'day_pnl_excludes': ['dividend', 'fee'],
        'day_pnl_pct': '1.73',            # 150 / (8800 - 150)
        'total_pnl_pct': '20.69'}         # 1080 / (1000 + 10 x 50 x 7 + 200 x 4 x 0.9)
    assert (main['quality'], other['quality']) == ('complete', 'complete')


def cents(text):
    from decimal import Decimal
    return Decimal(text)


def test_displayed_rows_add_up_to_the_displayed_totals(desk, client):
    # Reviewer's reproduction: five one-share positions at 10.005 each showed
    # 10.01 apiece (50.05 together) under a total of 50.03.
    tickers = [f'SHA:60000{n}' for n in range(1, 6)]
    build_ledger(desk, [opening('Main', date=FRIDAY, positions=[position(t, '1', '10') for t in tickers])])
    clock = FakeClock('2026-11-17 10:00')
    install(clock, ScriptedSource({t: quote(t, '10.005', prev_close='10', at=clock.now()) for t in tickers}, {}))
    data = client.get('/api/today').json()
    main = data['accounts'][0]
    assert [row['market_value_cny'] for row in main['positions']] == ['10.01'] * 5
    assert main['market_value_cny'] == main['total_cny'] == data['totals']['total_cny'] == '50.05'
    assert main['day_pnl_cny'] == data['totals']['day_pnl_cny'] == '0.05'          # 5 x 0.01, not 0.03
    assert main['total_pnl_cny'] == data['totals']['total_pnl_cny'] == '0.05'
    assert data['totals']['by_currency']['CNY']['market_value'] == '50.05'


def test_every_displayed_total_is_the_sum_of_its_displayed_parts(desk, client):
    build_ledger(desk, [
        opening('Main', cash={'CNY': '1000.005', 'USD': '100.005', 'HKD': '0.125'}, fund_cny='500.004', date=FRIDAY,
                positions=[position(CN, '333', '10.0003'), position(US, '7', '50.0007'), position(HK, '201', '4.0001')]),
        opening('Other', cash={'CNY': '300.335'}, date=FRIDAY, positions=[position('SHA:600002', '3', '9.995')])])
    clock = FakeClock('2026-11-17 10:00')
    now = clock.now()
    install(clock, ScriptedSource(
        {CN: quote(CN, '12.345', prev_close='11.115', at=now), HK: quote(HK, '5.005', prev_close='5.555', at=now),
         US: quote(US, '60.015', prev_close='58.005', at=shanghai('2026-11-17 05:00')),
         'SHA:600002': quote('SHA:600002', '10.005', prev_close='9.995', at=now)},
        {'USD': fx_rate('USD', '7.0123', at=now), 'HKD': fx_rate('HKD', '0.90123', at=now)}))
    data = client.get('/api/today').json()
    totals = data['totals']
    for account in data['accounts']:
        rows = account['positions']
        assert sum(cents(r['market_value_cny']) for r in rows) == cents(account['market_value_cny'])
        assert (cents(account['market_value_cny']) + cents(account['cash_cny']) + cents(account['fund_cny'])
                == cents(account['total_cny']))
        assert sum(cents(r['day_pnl_cny']) for r in rows + account['closed_today']) == cents(account['day_pnl_cny'])
        assert sum(cents(r['total_pnl_cny']) for r in rows) == cents(account['total_pnl_cny'])
    for key in ('total_cny', 'day_pnl_cny', 'total_pnl_cny'):
        assert sum(cents(account[key]) for account in data['accounts']) == cents(totals[key]), key
    for currency, entry in totals['by_currency'].items():
        rows = [r for a in data['accounts'] for r in a['positions'] if r['currency'] == currency]
        assert sum(cents(r['market_value']) for r in rows) == cents(entry['market_value']), currency
        assert sum(cents(a['cash'].get(currency, '0')) for a in data['accounts']) == cents(entry['cash']), currency


def test_total_profit_is_labelled_unrealized_next_to_a_cumulative_profit(desk, client):
    # Reviewer's reproduction: 100 shares opened at 10 with contributed capital
    # 1000, all sold at 12. Today's total_pnl_cny was 0 while the snapshot's
    # total_profit was 200; the narrower meaning needs a label, and the
    # cumulative figure belongs next to it.
    ledger = build_ledger(desk, [opening('Main', date='2026-11-12', contributed_cny='1000',
                                         positions=[position(CN, '100', '10')])])
    book(ledger, [trade('sell', CN, '100', '12', account='Main', date=FRIDAY)])
    install(FakeClock('2026-11-17 10:00'), ScriptedSource({}, {}))
    totals = client.get('/api/today').json()['totals']
    assert (totals['total_pnl_cny'], totals['total_pnl_kind']) == ('0.00', 'unrealized')
    assert (totals['total_cny'], totals['contributed_cny']) == ('1200.00', '1000.00')
    assert (totals['cumulative_pnl_cny'], totals['cumulative_pnl_pct']) == ('200.00', '20.00')
    assert totals['day_pnl_excludes'] == ['dividend', 'fee']


def test_cumulative_profit_follows_the_totals_quality_rules(desk, client):
    holdings(desk)                                         # no contributed capital recorded
    clock = FakeClock('2026-11-17 10:00')
    source = install(clock, market(clock))
    totals = client.get('/api/today').json()['totals']
    assert (totals['contributed_cny'], totals['cumulative_pnl_cny'], totals['cumulative_pnl_pct']) == (
        '0.00', '8800.00', None)                           # no percentage of nothing
    source.quotes[HK] = quote(HK, None, reason='停牌')
    clock.advance(seconds=10)
    totals = client.get('/api/today').json()['totals']
    assert (totals['total_cny'], totals['cumulative_pnl_cny'], totals['cumulative_pnl_pct']) == (None, None, None)
    assert totals['contributed_cny'] == '0.00' and totals['total_pnl_kind'] == 'unrealized'


def test_percentages_of_the_totals_have_defined_denominators_and_no_fake_zero(desk, client):
    holdings(desk)
    clock = FakeClock('2026-11-17 10:00')
    source = install(clock, market(clock))
    totals = client.get('/api/today').json()['totals']
    assert (totals['day_pnl_pct'], totals['total_pnl_pct']) == ('1.73', '20.69')
    # Without day profit there is no day percentage; without a price, no total percentage.
    source.quotes[US] = quote(US, None, reason='停牌')
    clock.advance(seconds=10)
    totals = client.get('/api/today').json()['totals']
    assert (totals['day_pnl_cny'], totals['day_pnl_pct'], totals['total_pnl_pct']) == (None, None, None)


def test_a_zero_cost_basis_has_no_total_percentage(desk, client):
    build_ledger(desk, [opening('Main', date=FRIDAY, positions=[position(CN, '100', '0')])])
    clock = FakeClock('2026-11-17 10:00')
    install(clock, ScriptedSource({CN: quote(CN, '12', prev_close='12', at=clock.now())}, {}))
    totals = client.get('/api/today').json()['totals']
    assert (totals['total_pnl_cny'], totals['total_pnl_pct']) == ('1200.00', None)
    assert (totals['day_pnl_cny'], totals['day_pnl_pct']) == ('0.00', '0.00')


def test_quotes_are_cached_briefly_and_refresh_asks_again(desk, client):
    holdings(desk)
    clock = FakeClock('2026-11-17 10:00')
    source = install(clock, market(clock))
    client.get('/api/today')
    client.get('/api/today')
    assert len(source.calls) == 1 and len(source.fx_calls) == 1
    assert client.get('/api/today', params={'refresh': 1}).status_code == 200
    assert len(source.calls) == 2 and len(source.fx_calls) == 2


# -- the empty state ----------------------------------------------------------

def test_no_ledger_is_a_clear_empty_state_not_an_error_or_sample_data(desk, client):
    source = install(FakeClock('2026-11-17 10:00'), market(FakeClock('2026-11-17 10:00')))
    response = client.get('/api/today')
    assert response.status_code == 200
    data = response.json()
    assert TOP_KEYS <= set(data)
    assert data['accounts'] == [] and data['ledger_revision'] == 0 and '账本' in data['notice']
    assert data['totals'] == {'by_currency': {}, 'total_cny': None, 'day_pnl_cny': None, 'total_pnl_cny': None,
                              'unavailable': [], 'quality': None, 'estimated': [], 'total_pnl_kind': 'unrealized',
                              'contributed_cny': None, 'cumulative_pnl_cny': None, 'cumulative_pnl_pct': None,
                              'day_pnl_excludes': ['dividend', 'fee'], 'day_pnl_pct': None, 'total_pnl_pct': None}
    assert all(rate['rate'] is None and rate['status'] == 'unavailable' for rate in data['fx'].values())
    assert data['session']['phase'] == 'morning'
    assert source.calls == [] and source.fx_calls == []           # nothing to value, nothing fetched
    assert not (desk / 'ledger.sqlite3').exists()                 # a read must not create the ledger


def test_an_unconfirmed_proposal_is_not_holdings(desk, client):
    ledger = build_ledger(desk)
    ledger.propose([opening('Main', cash={'CNY': '1000'}, positions=[position(CN, '100', '10')])])
    install(FakeClock('2026-11-17 10:00'), market(FakeClock('2026-11-17 10:00')))
    data = client.get('/api/today').json()
    assert data['accounts'] == [] and data['notice']


# -- reading the ledger -------------------------------------------------------

class RacingLedger:
    """Confirms a one-unit deposit during each of the first ``races`` history() calls."""

    def __init__(self, ledger, races=10 ** 6):
        self.ledger, self.races, self.history_calls = ledger, races, 0

    def state(self, as_of=None):
        return self.ledger.state(as_of)

    def history(self):
        self.history_calls += 1
        if self.history_calls <= self.races:
            book(self.ledger, [{'kind': 'deposit', 'date': FRIDAY, 'account': 'Main', 'currency': 'CNY', 'amount': '1'}])
        return self.ledger.history()

    def __getattr__(self, name):
        return getattr(self.ledger, name)


def test_a_ledger_that_keeps_changing_gives_an_explicit_unavailable_response(desk, client, monkeypatch):
    # Reviewer's reproduction: after three attempts the state was revision 3
    # (cash 1002) while the history already held revision 4 (cash 1003), and
    # the two were valued together.
    from backend.api import today as today_api
    racing = RacingLedger(build_ledger(desk, [opening('Main', cash={'CNY': '1000'}, date=FRIDAY)]))
    # Changed in review round 3: the page reads a read-only snapshot (read_confirmed); a Ledger that
    # keeps changing is still refused by read_ledger, which build_today applies to any ledger it is given.
    monkeypatch.setattr(today_api, 'read_confirmed', lambda **kwargs: racing)
    source = install(FakeClock('2026-11-17 10:00'), market(FakeClock('2026-11-17 10:00')))
    response = client.get('/api/today')
    assert response.status_code == 200
    data = response.json()
    assert data['ledger_revision'] is None and data['accounts'] == [] and '账本' in data['notice']
    assert (data['totals']['total_cny'], data['totals']['day_pnl_cny']) == (None, None)
    assert source.calls == [] and source.fx_calls == []      # nothing is valued from a mixed read


def _hold_ledger(path, mode, seconds):
    """Another connection keeps ``BEGIN <mode>`` on the ledger for ``seconds``; returns the releasing timer."""
    import sqlite3
    import threading
    holder = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    holder.execute(f'BEGIN {mode}')

    def release():
        holder.execute('ROLLBACK')
        holder.close()
    timer = threading.Timer(seconds, release)
    timer.start()
    return timer


@pytest.mark.parametrize('mode', ['IMMEDIATE', 'EXCLUSIVE'])
def test_a_ledger_writer_never_holds_up_today(desk, client, mode):
    # The reviewer's reproduction: with BEGIN IMMEDIATE held, GET /api/today took 15 s
    # and raised OperationalError.
    import time
    holdings(desk)
    install(FakeClock('2026-11-17 10:00'), market(FakeClock('2026-11-17 10:00')))
    timer = _hold_ledger(desk / 'ledger.sqlite3', mode, 3.1)
    try:
        started = time.monotonic()
        response = client.get('/api/today')
        assert time.monotonic() - started < 1.6, 'no GET waits behind a ledger writer for more than about a second'
    finally:
        timer.join(10)
    assert response.status_code == 200
    data = response.json()
    if mode == 'IMMEDIATE':
        assert data['ledger_revision'] == 1 and [account['name'] for account in data['accounts']] == ['Main', 'Other']
    else:
        assert data['accounts'] == [] and data['ledger_revision'] is None and '账本' in data['notice']
        assert data['totals']['total_cny'] is None


def test_a_damaged_ledger_is_a_notice_not_an_error_or_an_empty_ledger(desk, client):
    import sqlite3
    db = sqlite3.connect(desk / 'ledger.sqlite3')
    db.execute('CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, '
               'payload TEXT NOT NULL, proposal_id TEXT NOT NULL)')
    db.commit()
    db.close()
    size = (desk / 'ledger.sqlite3').stat().st_size
    install(FakeClock('2026-11-17 10:00'), market(FakeClock('2026-11-17 10:00')))
    response = client.get('/api/today')
    assert response.status_code == 200
    data = response.json()
    assert data['accounts'] == [] and data['ledger_revision'] is None and '损坏' in data['notice'], data['notice']
    assert (desk / 'ledger.sqlite3').stat().st_size == size, 'a read never repairs or initialises the ledger'


def test_every_read_only_reader_says_a_damaged_or_busy_ledger_cannot_be_read(desk, monkeypatch):
    # The weight rules' holdings and the close snapshot read through confirmed_state, which raises
    # LedgerUnavailable (LedgerBusy, LedgerDamaged) for a ledger it cannot read: never an escape, never "no ledger".
    import sqlite3
    from core import ledger as core_ledger
    from core.alerts import holdings
    from core.routines import close_snapshot
    with sqlite3.connect(desk / 'ledger.sqlite3') as db:
        db.execute('CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, '
                   'payload TEXT NOT NULL, proposal_id TEXT NOT NULL)')
    clock = FakeClock('2026-11-17 10:00')
    assert holdings.confirmed_holdings(clock) == (None, holdings.LEDGER_UNREADABLE)
    assert close_snapshot._ledger_state(desk, '2026-11-17') is ...

    def busy(*args, **kwargs):
        raise core_ledger.LedgerBusy()
    monkeypatch.setattr(holdings, 'confirmed_state', busy)
    monkeypatch.setattr(close_snapshot, 'confirmed_state', busy)
    assert holdings.confirmed_holdings(clock) == (None, holdings.LEDGER_BUSY)
    assert close_snapshot._ledger_state(desk, '2026-11-17') is ...


def test_a_ledger_that_settles_is_read_at_one_revision(desk):
    from decimal import Decimal
    from core.ledger import replay
    from core.market.today import build_today, read_ledger
    racing = RacingLedger(build_ledger(desk, [opening('Main', cash={'CNY': '1000'}, date=FRIDAY)]), races=1)
    revision, events = read_ledger(racing)
    assert revision == 2 and len(events) == 2 and racing.ledger.state()['revision'] == 2
    assert replay(events)['accounts']['Main']['cash_balances'] == {'CNY': '1001'}
    racing.races, racing.history_calls = 2, 0
    clock = FakeClock('2026-11-17 10:00')
    data = build_today(racing, fake_runtime(clock, market(clock)))
    # The cash shown is the cash of the revision reported, never a neighbour's.
    assert data['ledger_revision'] == racing.ledger.state()['revision'] == 4
    assert Decimal(data['accounts'][0]['cash']['CNY']) == Decimal(1003)


def test_events_dated_after_today_are_not_holdings_yet(desk, client):
    # Reviewer's reproduction: a confirmed purchase dated 18 November showed on
    # the 17th as 100 shares worth 1200 with a day profit of 200, although
    # ledger.state('2026-11-17') has cash 1000 and no position.
    ledger = build_ledger(desk, [opening('Main', cash={'CNY': '1000'}, date=FRIDAY)])
    book(ledger, [trade('buy', CN, '100', '10', account='Main', date='2026-11-18')])
    clock = FakeClock('2026-11-17 10:00')
    install(clock, market(clock))
    data = client.get('/api/today').json()
    main, = data['accounts']
    assert (main['positions'], main['closed_today'], main['cash']) == ([], [], {'CNY': '1000.00'})
    assert (data['totals']['total_cny'], data['totals']['day_pnl_cny']) == ('1000.00', '0.00')
    assert data['ledger_revision'] == 2
    assert {'kind': 'future_events', 'count': 1, 'message': '有 1 笔未来日期的记录尚未生效'} in data['notices']
    # It takes effect on its date.
    clock.set('2026-11-18 10:00')
    data = client.get('/api/today').json()
    assert [row['ticker'] for row in data['accounts'][0]['positions']] == [CN]
    assert not [n for n in data['notices'] if n['kind'] == 'future_events']


def test_a_ledger_whose_every_event_is_in_the_future_has_no_holdings_today(desk, client):
    build_ledger(desk, [opening('Main', cash={'CNY': '1000'}, date='2026-11-20'),
                        opening('Other', cash={'CNY': '5'}, date='2026-11-21')])
    install(FakeClock('2026-11-17 10:00'), market(FakeClock('2026-11-17 10:00')))
    data = client.get('/api/today').json()
    assert data['accounts'] == [] and data['notice'] and data['ledger_revision'] == 1
    assert {'kind': 'future_events', 'count': 2, 'message': '有 2 笔未来日期的记录尚未生效'} in data['notices']


def test_a_correction_dated_in_the_future_is_applied_and_said_so(desk, client):
    # A mistaken buy is reversed by an entry dated 20 November and the real
    # buy re-entered on its own date. Both buys dated Monday cannot be paid
    # for, so the ledger's dated view for today applies the later reversal
    # (as_of_corrected_accounts); Today must say so and still add up.
    ledger = build_ledger(desk, [opening('Main', cash={'CNY': '1000'}, date=FRIDAY)])
    book(ledger, [trade('buy', CN, '100', '10', account='Main', date=MONDAY)])
    mistaken = [event for event in ledger.history() if event['kind'] == 'buy'][0]
    book(ledger, [{'kind': 'reverse', 'date': '2026-11-20', 'target_id': mistaken['id']}])
    book(ledger, [trade('buy', CN, '100', '10', account='Main', date=MONDAY)])
    assert ledger.state(TUESDAY)['as_of_corrected_accounts'] == ['Main']
    clock = FakeClock('2026-11-17 10:00')
    install(clock, market(clock))
    data = client.get('/api/today').json()
    cn, = data['accounts'][0]['positions']
    assert (cn['quantity'], cn['day_pnl']) == ('100', '100.00')
    assert data['accounts'][0]['cash'] == {'CNY': '0.00'}
    corrected, = [n for n in data['notices'] if n['kind'] == 'corrected_view']
    assert corrected['accounts'] == ['Main'] and corrected['message']
    assert {'kind': 'future_events', 'count': 1, 'message': '有 1 笔未来日期的记录尚未生效'} in data['notices']


# -- outages and old prices ---------------------------------------------------

def test_outage_scripted_through_fake_quotes_json_is_unavailable_never_zero(desk, client, monkeypatch):
    monkeypatch.setenv('TRACKER_QUOTE_SOURCE', 'fake')
    holdings(desk)
    script = {CN: {'price': '12', 'prev_close': '11', 'age_seconds': 3},
              HK: {'price': '5', 'prev_close': '5.5', 'age_seconds': 2},
              US: {'price': '60', 'prev_close': '58', 'quote_time': '2026-11-17T05:00:00+08:00'}}
    rates = {'USD': {'rate': '7', 'age_seconds': 8}, 'HKD': {'rate': '0.9', 'age_seconds': 8}}
    write_fake_quotes(desk, script, fx=rates, now='2026-11-17T10:00:00+08:00')
    before = client.get('/api/today').json()
    assert before['source'] == 'fake' and before['totals']['total_cny'] == '8800.00'
    assert before['accounts'][0]['positions'][0]['quote']['status'] == 'live'

    write_fake_quotes(desk, script, fx=rates, now='2026-11-17T10:00:05+08:00', outage='模拟：行情源中断')
    response = client.get('/api/today')
    assert response.status_code == 200
    data = response.json()
    for row in data['accounts'][0]['positions']:
        assert row['quote']['status'] == 'unavailable' and row['quote']['reason'] == 'fake：模拟：行情源中断'
        assert [row[key] for key in DERIVED] == [None] * len(DERIVED), row['ticker']
        assert row['quantity'] and row['cost_price']             # ledger facts are still there
    assert data['accounts'][0]['total_cny'] is None
    assert data['accounts'][1]['total_cny'] == '300.00'           # cash only: still known
    assert (data['totals']['total_cny'], data['totals']['day_pnl_cny'], data['totals']['total_pnl_cny']) == (None,) * 3
    assert all(entry['market_value'] is None for entry in data['totals']['by_currency'].values())
    assert all(rate['rate'] is None and rate['status'] == 'unavailable' for rate in data['fx'].values())
    subjects = {entry.get('ticker') or entry.get('currency') for entry in data['totals']['unavailable']}
    assert subjects == {CN, US, HK, 'USD', 'HKD'}
    assert all(entry['reason'] for entry in data['totals']['unavailable'])
    # An unknown figure is never rendered as zero.
    unknown = [path for path, value in money_fields(data) if value is not None and float(value) == 0
               and not path.startswith(('Main.cash', 'Other', 'totals.CNY.cash', 'totals.USD.cash', 'totals.HKD.cash'))]
    assert unknown == []

    # One ticker failing is enough to withhold the totals that contain it.
    write_fake_quotes(desk, {**script, HK: {'unavailable': '停牌', 'prev_close': '5.5'}}, fx=rates,
                      now='2026-11-17T10:00:10+08:00')
    partial = client.get('/api/today').json()
    cn, us, hk = partial['accounts'][0]['positions']
    assert cn['market_value_cny'] == '1200.00' and us['market_value_cny'] == '4200.00'
    assert hk['price'] is None and hk['prev_close'] == '5.5' and hk['quote']['reason'] == 'fake：停牌'
    assert partial['totals']['total_cny'] is None and partial['totals']['by_currency']['CNY']['market_value'] == '1200.00'
    assert partial['totals']['unavailable'] == [{'ticker': HK, 'reason': 'fake：停牌'}]


def test_b_shares_already_in_the_ledger_are_never_added_to_cny_totals(desk, client, monkeypatch):
    # Shanghai B shares trade in US dollars and Shenzhen B shares in Hong Kong
    # dollars, but the ledger books every SHA/SHE holding (cost, trades, cash)
    # in CNY. Before this, 1000 SHA:900901 at 0.498 USD were added as 498 CNY.
    monkeypatch.setenv('TRACKER_QUOTE_SOURCE', 'fake')
    import core.ledger   # booked before the ledger refused B shares (core.ledger.bookable)
    monkeypatch.setattr(core.ledger, 'bookable', core.ledger.instrument)
    build_ledger(desk, [opening('Main', cash={'CNY': '1000'}, date=FRIDAY, positions=[
        position(CN, '100', '10'), position('SHA:900901', '1000', '0.5'), position('SHE:200596', '100', '50')])])
    script = {CN: {'price': '12', 'prev_close': '11', 'age_seconds': 3},
              'SHA:900901': {'price': '0.498', 'prev_close': '0.5', 'age_seconds': 3},
              'SHE:200596': {'price': '54.66', 'prev_close': '54.73', 'age_seconds': 3}}
    rates = {'USD': {'rate': '7', 'age_seconds': 8}, 'HKD': {'rate': '0.9', 'age_seconds': 8}}
    write_fake_quotes(desk, script, fx=rates, now='2026-11-17T10:00:00+08:00')
    data = client.get('/api/today').json()
    cn, sha_b, she_b = data['accounts'][0]['positions']
    assert cn['market_value_cny'] == '1200.00'
    for row, currency in ((sha_b, 'USD'), (she_b, 'HKD')):
        assert row['currency'] == 'CNY'                                  # what the ledger booked
        assert [row[key] for key in DERIVED] == [None] * len(DERIVED), row['ticker']
        assert row['prev_close'] is None and row['estimated'] is False
    totals = data['totals']
    assert (totals['total_cny'], totals['day_pnl_cny'], totals['total_pnl_cny']) == (None, None, None)
    assert totals['by_currency']['CNY']['market_value'] is None and data['accounts'][0]['total_cny'] is None
    reasons = {entry['ticker']: entry['reason'] for entry in totals['unavailable']}
    assert set(reasons) == {'SHA:900901', 'SHE:200596'}
    assert 'B 股' in reasons['SHA:900901'] and 'USD' in reasons['SHA:900901']
    assert 'B 股' in reasons['SHE:200596'] and 'HKD' in reasons['SHE:200596']
    # On their own, the quotes are fine and say what they are in.
    quotes = client.get('/api/quotes', params={'tickers': 'SHA:900901,SHE:200596'}).json()['quotes']
    assert [(q['price'], q['currency'], q['quote']['status']) for q in quotes] == [
        ('0.498', 'USD', 'live'), ('54.66', 'HKD', 'live')]


def test_an_equity_ticker_in_an_index_range_is_not_valued_at_the_index(desk, client):
    # Reviewer's reproduction: 100 "shares" of SHA:000001 were valued at the
    # Shanghai Composite's 3595.22 with status live and nothing unavailable.
    build_ledger(desk, [opening('Main', cash={'CNY': '100'}, date=FRIDAY, positions=[position('SHA:000001', '100', '10')])])
    clock = FakeClock('2026-11-17 10:00')
    install(clock, ScriptedSource({'SHA:000001': quote('SHA:000001', '3595.22', prev_close='3602.57',
                                                       at=clock.now() - timedelta(seconds=3))}, {}))
    data = client.get('/api/today').json()
    row, = data['accounts'][0]['positions']
    assert row['quote']['status'] == 'unavailable' and row['price'] is None and row['market_value'] is None
    assert data['totals']['total_cny'] is None
    entry, = [entry for entry in data['totals']['unavailable'] if entry.get('ticker') == 'SHA:000001']
    assert '指数' in entry['reason'] and '核对' in entry['reason']


def test_hong_kong_spellings_in_the_ledger_are_quoted_once_and_kept_as_entered(desk, client):
    build_ledger(desk, [opening('A', date=FRIDAY, positions=[position('HKG:700', '100', '300')]),
                        opening('B', date=FRIDAY, positions=[position('HKG:00700', '200', '300')])])
    clock = FakeClock('2026-11-17 10:00')
    source = install(clock, ScriptedSource({'HKG:700': quote('HKG:700', '400', prev_close='390',
                                                             at=clock.now() - timedelta(seconds=3))},
                                           {'HKD': fx_rate('HKD', '0.9', at=clock.now())}))
    data = client.get('/api/today').json()
    (a,), (b,) = [account['positions'] for account in data['accounts']]
    assert (a['ticker'], a['price'], b['ticker'], b['price']) == ('HKG:700', '400', 'HKG:00700', '400')
    assert source.calls == [('HKG:700',)]


def test_a_price_of_an_unsupported_magnitude_is_unavailable_not_a_crash(desk, client):
    # Reviewer's reproduction: a provider price of 1e100 reached the formatter,
    # which raised decimal.InvalidOperation and took the endpoint down.
    holdings(desk)
    clock = FakeClock('2026-11-17 10:00')
    install(clock, market(clock, **{CN: quote(CN, '1e100', prev_close='11', at=clock.now() - timedelta(seconds=3))}))
    response = client.get('/api/today')
    assert response.status_code == 200
    data = response.json()
    cn, us, hk = data['accounts'][0]['positions']
    assert (cn['price'], cn['market_value'], cn['quote']['status']) == (None, None, 'unavailable')
    assert '范围' in cn['quote']['reason']
    assert (us['market_value_cny'], hk['market_value_cny']) == ('4200.00', '900.00')   # the others are untouched
    assert data['totals']['total_cny'] is None and data['accounts'][1]['total_cny'] == '300.00'


def test_ledger_amounts_at_the_ledgers_own_bounds_never_crash_the_formatter(desk, client):
    # The ledger accepts amounts up to 1e18 and 100 decimal places; products
    # and ratios of such values exceed the default 28-digit decimal context.
    tiny = '0.' + '0' * 80 + '1'
    build_ledger(desk, [opening('Main', cash={'USD': '1000000000000000000'}, date=FRIDAY, positions=[
        position(US, '1000000000000000000', '50'), position(CN, tiny, tiny)])])
    clock = FakeClock('2026-11-17 10:00')
    install(clock, market(clock, **{US: quote(US, '99999999', prev_close='58', at=shanghai('2026-11-17 05:00'))}))
    response = client.get('/api/today')
    assert response.status_code == 200
    data = response.json()
    us, cn = data['accounts'][0]['positions']
    assert us['market_value'] == '99999999000000000000000000.00'
    assert us['market_value_cny'] == '699999993000000000000000000.00'
    assert cn['total_pnl_pct'] is not None and DECIMAL.fullmatch(cn['total_pnl_pct'])


def test_a_missing_last_price_with_trading_activity_is_not_valued_at_the_previous_close(desk, client):
    # Reviewer's reproduction, through the real Eastmoney parser: f2 '-',
    # f18 10, volume and a day range present, current f124. 100 shares were
    # valued at 1000 with status closed and a day profit of 0.
    import httpx
    from core.market.sources_eastmoney import EastmoneySource
    build_ledger(desk, [opening('Main', cash={'CNY': '100'}, date=FRIDAY, positions=[position(CN, '100', '9')])])
    clock = FakeClock('2026-11-17 10:00')
    payload = {'rc': 0, 'data': {'total': 1, 'diff': [{
        'f2': '-', 'f5': 12345, 'f12': '600001', 'f13': 1, 'f14': '示例', 'f15': 12, 'f16': 10.5, 'f17': 11,
        'f18': 10, 'f124': int((clock.now() - timedelta(seconds=5)).timestamp())}]}}
    source = EastmoneySource(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
                             now_fn=clock.now)
    set_market_runtime(fake_runtime(clock, source))
    data = client.get('/api/today').json()
    cn, = data['accounts'][0]['positions']
    assert (cn['price'], cn['market_value'], cn['day_pnl'], cn['quote']['status']) == (None, None, None, 'unavailable')
    assert cn['prev_close'] == '10'
    assert data['totals']['total_cny'] is None and data['totals']['day_pnl_cny'] is None


def test_a_quote_source_that_is_down_never_becomes_a_500(desk, client):
    holdings(desk)
    clock = FakeClock('2026-11-17 10:00')
    source = install(clock, market(clock))
    source.down = '请求失败（ConnectTimeout）'
    response = client.get('/api/today')
    assert response.status_code == 200
    data = response.json()
    assert data['totals']['total_cny'] is None
    assert all(row['quote']['status'] == 'unavailable' and 'ConnectTimeout' in row['quote']['reason']
               for row in data['accounts'][0]['positions'])


def test_an_old_exchange_time_during_the_session_is_stale_not_live(desk, client):
    holdings(desk)
    clock = FakeClock('2026-11-17 14:00')
    install(clock, market(clock, **{CN: quote(CN, '12', prev_close='11', at=shanghai('2026-11-17 11:30'))}))
    cn = client.get('/api/today').json()['accounts'][0]['positions'][0]
    assert cn['quote']['status'] == 'stale' and cn['quote']['reason']
    assert cn['quote']['quote_time'] == '2026-11-17T11:30:00+08:00'
    assert cn['price'] == '12' and cn['market_value'] == '1200.00'    # shown, but labelled


def test_a_stale_currency_rate_makes_the_totals_estimated_not_complete(desk, client):
    # Reviewer's reproduction: USD cash 100 at a rate from 1 October, queried
    # on 17 November, gave total_cny 700.00 with nothing unavailable and
    # nothing to say the total rests on a six-week-old rate.
    build_ledger(desk, [opening('Main', cash={'USD': '100'}, date=FRIDAY)])
    clock = FakeClock('2026-11-17 10:00')
    install(clock, ScriptedSource({}, {'USD': fx_rate('USD', '7', at=shanghai('2026-10-01 10:00')),
                                       'HKD': fx_rate('HKD', '0.9', at=clock.now())}))
    data = client.get('/api/today').json()
    assert data['fx']['USD']['status'] == 'stale'
    totals, main = data['totals'], data['accounts'][0]
    assert (totals['total_cny'], totals['unavailable']) == ('700.00', [])
    entry, = totals['estimated']
    assert (entry['currency'], entry['status']) == ('USD', 'stale') and entry['reason']
    assert totals['quality'] == 'estimated'
    assert (main['quality'], main['estimated']) == ('estimated', [entry])


def test_rows_valued_from_unverified_quotes_are_estimated_with_a_reason(desk, client):
    # Reviewer: a stale stock quote produced numeric totals without warnings.
    holdings(desk)
    clock = FakeClock('2026-11-17 14:00')
    install(clock, market(clock, **{CN: quote(CN, '12', prev_close='11', at=shanghai('2026-11-17 11:30'))}))
    data = client.get('/api/today').json()
    main, other = data['accounts']
    cn, us, hk = main['positions']
    assert cn['quote']['status'] == 'stale' and cn['market_value_cny'] == '1200.00'
    assert cn['estimated'] is True and cn['estimated_reason'] == cn['quote']['reason']
    assert hk['quote']['status'] == 'live' and (hk['estimated'], hk['estimated_reason']) == (False, None)
    # Changed with the HK/US tables: New York's close is verified now, only the stale quote is estimated.
    assert us['quote']['status'] == 'closed' and (us['estimated'], us['estimated_reason']) == (False, None)
    totals = data['totals']
    assert totals['total_cny'] == '8800.00' and totals['quality'] == 'estimated'
    assert [(e.get('ticker'), e['status']) for e in totals['estimated']] == [(CN, 'stale')]
    assert all(e['reason'] for e in totals['estimated'])
    assert main['quality'] == 'estimated' and (other['quality'], other['estimated']) == ('complete', [])


def test_only_verified_inputs_give_complete_totals(desk, client):
    build_ledger(desk, [opening('Main', cash={'CNY': '1000'}, date=FRIDAY, positions=[position(CN, '100', '10')])])
    clock = FakeClock('2026-11-17 10:00')
    install(clock, ScriptedSource({CN: quote(CN, '12', prev_close='11', at=clock.now() - timedelta(seconds=3))}, {}))
    data = client.get('/api/today').json()
    assert (data['totals']['quality'], data['totals']['estimated']) == ('complete', [])
    assert data['accounts'][0]['positions'][0]['estimated'] is False


def test_hong_kong_and_new_york_closes_are_verified_only_in_years_the_tables_cover(desk, client):
    holdings(desk)
    clock = FakeClock('2026-11-17 10:00')
    install(clock, market(clock))
    data = client.get('/api/today').json()
    assert {market: entry['calendar_confidence'] for market, entry in data['session']['markets'].items()} == {
        'CN': 'calendar', 'HK': 'calendar', 'US': 'calendar'}
    assert (data['totals']['quality'], data['totals']['estimated']) == ('complete', [])
    # Tuesday 14 November 2028: no table for any market; New York's close is an estimate again.
    clock = FakeClock('2028-11-14 10:00')
    install(clock, market(clock, **{US: quote(US, '60', prev_close='58', at=shanghai('2028-11-14 05:00'))}))
    data = client.get('/api/today').json()
    assert {market: entry['calendar_confidence'] for market, entry in data['session']['markets'].items()} == {
        'CN': 'weekday_only', 'HK': 'weekday_only', 'US': 'weekday_only'}
    _cn, us, hk = data['accounts'][0]['positions']
    assert us['quote']['status'] == 'closed' and us['estimated'] is True and '节假日' in us['estimated_reason']
    assert hk['quote']['status'] == 'live' and hk['estimated'] is False
    assert data['totals']['quality'] == 'estimated'
    assert [(e.get('ticker'), e['status']) for e in data['totals']['estimated']] == [(US, 'closed')]


def test_a_hong_kong_half_day_close_is_verified_in_the_afternoon(desk, client):
    holdings(desk)
    clock = FakeClock('2026-12-24 14:00')       # Christmas Eve: Hong Kong traded until 12:00 only
    install(clock, market(clock, **{HK: quote(HK, '5', prev_close='5.5', at=shanghai('2026-12-24 12:08')),
                                    US: quote(US, '60', prev_close='58', at=shanghai('2026-12-24 05:00'))}))
    data = client.get('/api/today').json()
    assert data['session']['markets']['HK'] == {
        'phase': 'closed', 'trading_day': '2026-12-24', 'is_trading_day': True, 'calendar_confidence': 'calendar',
        'next_open': '2026-12-28T09:30:00+08:00'}
    _cn, _us, hk = data['accounts'][0]['positions']
    assert hk['ticker'] == HK and hk['quote']['status'] == 'closed' and hk['estimated'] is False
    assert (data['totals']['quality'], data['totals']['estimated']) == ('complete', [])


def test_an_unscheduled_closure_is_never_shown_as_a_live_or_verified_price(desk, client):
    # A market can stay shut on a day the calendar lists as a trading day: an
    # exchange outage, or in Hong Kong a typhoon or black rainstorm before HKEX's
    # Severe Weather Trading (in effect since 23 September 2024). The quote then
    # stops at the previous close and must be shown as stale (estimated), during
    # the session and after it.
    holdings(desk)
    clock = FakeClock('2026-11-17 11:00')
    install(clock, market(clock, **{HK: quote(HK, '5', prev_close='5.5', at=shanghai('2026-11-16 16:08'))}))
    data = client.get('/api/today').json()
    assert data['session']['markets']['HK']['phase'] == 'open'
    hk = data['accounts'][0]['positions'][2]
    assert hk['quote']['status'] == 'stale' and hk['estimated'] is True
    assert '未更新' in hk['quote']['reason'] and '恶劣天气' not in hk['quote']['reason']
    assert hk['estimated_reason'] == hk['quote']['reason']
    assert data['totals']['quality'] == 'estimated'
    assert [(e.get('ticker'), e['status']) for e in data['totals']['estimated']] == [(HK, 'stale')]
    clock.set('2026-11-17 16:30')                # after the session the calendar believes in
    hk = client.get('/api/today').json()['accounts'][0]['positions'][2]
    assert hk['quote']['status'] == 'stale' and hk['estimated'] is True


def test_a_quote_without_an_exchange_time_is_valued_but_has_no_day_profit(desk, client):
    holdings(desk)
    clock = FakeClock('2026-11-17 10:00')
    install(clock, market(clock, **{CN: quote(CN, '12', prev_close='11', at=None, fetched_at=clock.now())}))
    data = client.get('/api/today').json()
    cn = data['accounts'][0]['positions'][0]
    assert cn['quote']['status'] == 'time_unknown' and cn['quote']['quote_time'] is None
    assert cn['market_value_cny'] == '1200.00' and cn['day_pnl'] is None
    assert data['totals']['total_cny'] == '8800.00' and data['totals']['day_pnl_cny'] is None
    reason, = [entry for entry in data['totals']['unavailable'] if entry.get('ticker') == CN]
    assert reason['field'] == 'day_pnl' and '交易所时间' in reason['reason']


def test_a_previous_close_before_the_first_trade_is_labelled_and_earns_nothing(desk, client):
    # Changed by the review: the status was 'closed'; it is now 'prev_close'.
    from core.market.quotes import PREV_CLOSE
    ledger = holdings(desk)
    clock = FakeClock('2026-11-17 10:00')
    untraded = quote(CN, '11', prev_close='11', at=shanghai('2026-11-17 09:20'), price_basis=PREV_CLOSE,
                     reason='本交易日尚无成交（或停牌），价格为昨收')
    install(clock, market(clock, **{CN: untraded}))
    data = client.get('/api/today').json()
    cn = data['accounts'][0]['positions'][0]
    assert (cn['quote']['status'], cn['price'], cn['day_pnl']) == ('prev_close', '11', '0.00')
    assert '昨收' in cn['quote']['reason'] and data['totals']['day_pnl_cny'] == '50.00'
    # A trade recorded for a session the quote has not caught up with cannot be measured against it.
    book(ledger, [trade('buy', CN, '50', '11.5', account='Main', date=TUESDAY)])
    data = client.get('/api/today').json()
    cn = data['accounts'][0]['positions'][0]
    assert cn['day_pnl'] is None and cn['market_value'] == '1650.00' and data['totals']['day_pnl_cny'] is None
    reason, = [entry for entry in data['totals']['unavailable'] if entry.get('ticker') == CN]
    assert reason['field'] == 'day_pnl' and '昨收' in reason['reason']


# -- where each market's day begins -------------------------------------------

def test_day_profit_is_measured_per_market_session(desk, client):
    # Tuesday 10:00 in Shanghai. The New York quote is Monday's close, so a US
    # purchase dated Monday happened inside the session being valued, while a
    # mainland purchase dated Monday happened before today's session.
    ledger = holdings(desk)
    book(ledger, [{**trade('buy', US, '5', '59', fee='1', currency='USD', account='Main', date=MONDAY),
                   'allow_margin': True},
                  trade('buy', CN, '50', '10.8', fee='5', account='Main', date=MONDAY)])
    install(FakeClock('2026-11-17 10:00'), market(FakeClock('2026-11-17 10:00')))
    cn, us, _ = client.get('/api/today').json()['accounts'][0]['positions']
    assert (us['quantity'], us['day_pnl']) == ('15', '24.00')       # 15 x 60 - 10 x 58 - (5 x 59 + 1)
    assert (cn['quantity'], cn['day_pnl']) == ('150', '150.00')     # 150 x (12 - 11): held before today


def test_a_cost_and_a_quantity_are_handed_over_exactly_as_the_ledger_holds_them(desk, client):
    # Review round 4: 200 at 10 and 100 at 10.01 average 10.00333333333333333333333333 in the ledger; Today
    # sent 10.003333 (the page shows it ≈ past 8 places, lib/format.ts fmtLong).
    ledger = build_ledger(desk, [opening('Main', cash={'CNY': '10000', 'USD': '100'}, date=FRIDAY,
                                         positions=[position(US, '0.123456789', '50')])])
    book(ledger, [trade('buy', CN, '200', '10', account='Main', date=MONDAY),
                  trade('buy', CN, '100', '10.01', account='Main', date=MONDAY)])
    clock = FakeClock('2026-11-17 10:00')
    install(clock, market(clock))
    rows = {row['ticker']: row for row in client.get('/api/today').json()['accounts'][0]['positions']}
    held = ledger.state()['accounts']['Main']['positions']
    assert rows[CN]['cost_price'] == held[CN]['cost_price']
    assert held[CN]['cost_price'].startswith('10.00333333333333333333333333'), held[CN]['cost_price']
    assert rows[US]['quantity'] == held[US]['quantity'] == '0.123456789'


def test_todays_trades_and_sold_out_positions(desk, client):
    ledger = holdings(desk)
    book(ledger, [trade('sell', HK, '200', '5.2', fee='10', currency='HKD', account='Main', date=TUESDAY),
                  trade('buy', CN, '50', '11.5', fee='5', account='Main', date=TUESDAY)])
    install(FakeClock('2026-11-17 10:00'), market(FakeClock('2026-11-17 10:00')))
    data = client.get('/api/today').json()
    main = data['accounts'][0]
    assert [row['ticker'] for row in main['positions']] == [CN, US]
    assert main['positions'][0]['day_pnl'] == '120.00'              # 150 x 12 - 100 x 11 - (50 x 11.5 + 5)
    closed, = main['closed_today']
    assert (closed['ticker'], closed['quantity'], closed['market_value']) == (HK, '0', '0.00')
    assert (closed['day_pnl'], closed['day_pnl_cny']) == ('-70.00', '-63.00')   # -200 x 5.5 + (200 x 5.2 - 10)
    assert data['totals']['day_pnl_cny'] == '197.00'                # 120 + 140 - 63
    assert data['ledger_revision'] == 2


@pytest.mark.parametrize('opened,sold,now,close_time', [
    # Reviewer's reproduction: Spring Festival. On 22 February the latest
    # session is 13 February, nine days earlier; the seven-day window missed
    # the sale, so no quote was asked for and day profit was a plain 0.00.
    ('2026-02-12', '2026-02-13', '2026-02-22 12:00', '2026-02-13 15:00'),
    # National Day: 8 October before the open, the latest session is 30 September.
    ('2026-09-29', '2026-09-30', '2026-10-08 09:00', '2026-09-30 15:00'),
])
def test_a_position_sold_out_in_the_latest_session_is_found_after_a_long_closure(desk, client, opened, sold, now,
                                                                                    close_time):
    ledger = build_ledger(desk, [opening('Main', date=opened, positions=[position(CN, '100', '10')])])
    book(ledger, [trade('sell', CN, '100', '12', account='Main', date=sold)])
    clock = FakeClock(now)
    source = install(clock, ScriptedSource({CN: quote(CN, '12', prev_close='11', at=shanghai(close_time))}, {}))
    data = client.get('/api/today').json()
    assert source.calls and CN in source.calls[0]
    main, = data['accounts']
    closed, = main['closed_today']
    assert (closed['ticker'], closed['day_pnl'], closed['quote']['status']) == (CN, '100.00', 'closed')
    assert (main['day_pnl_cny'], data['totals']['day_pnl_cny'], data['totals']['unavailable']) == ('100.00', '100.00', [])


def sold_on_friday(desk):
    ledger = build_ledger(desk, [opening('Main', date='2026-11-12', positions=[position(CN, '100', '10')])])
    book(ledger, [trade('sell', CN, '100', '12', account='Main', date=FRIDAY)])
    return ledger


def test_a_sold_out_position_without_a_reference_price_is_null_not_zero(desk, client):
    # Reviewer's reproduction: Monday 08:00, the sale was Friday. With
    # Friday's quote closed_today holds +100; with the quote unavailable the
    # row disappeared and day profit became 0.00 with no reason.
    sold_on_friday(desk)
    clock = FakeClock('2026-11-16 08:00')
    source = install(clock, ScriptedSource({CN: quote(CN, '12', prev_close='11', at=shanghai('2026-11-13 15:00'))}, {}))
    with_quote = client.get('/api/today').json()
    assert with_quote['accounts'][0]['closed_today'][0]['day_pnl'] == '100.00'
    assert with_quote['totals']['day_pnl_cny'] == '100.00'

    source.quotes[CN] = quote(CN, None, prev_close='11', reason='停牌')
    clock.advance(seconds=10)
    data = client.get('/api/today').json()
    closed, = data['accounts'][0]['closed_today']
    assert (closed['ticker'], closed['day_pnl'], closed['quote']['status']) == (CN, None, 'unavailable')
    assert data['accounts'][0]['day_pnl_cny'] is None and data['totals']['day_pnl_cny'] is None
    entry, = [e for e in data['totals']['unavailable'] if e.get('ticker') == CN]
    assert entry['field'] == 'day_pnl' and entry['reason']
    assert data['totals']['total_cny'] == '1200.00'                 # nothing is held at the missing price


def test_a_sold_out_position_whose_quote_has_no_exchange_time_is_null_not_zero(desk, client):
    sold_on_friday(desk)
    clock = FakeClock('2026-11-16 08:00')
    install(clock, ScriptedSource({CN: quote(CN, '12', prev_close='11', at=None, fetched_at=clock.now())}, {}))
    data = client.get('/api/today').json()
    closed, = data['accounts'][0]['closed_today']
    assert closed['day_pnl'] is None and data['totals']['day_pnl_cny'] is None
    assert any(e.get('ticker') == CN and '交易所时间' in e['reason'] for e in data['totals']['unavailable'])


def test_a_round_trip_needs_no_reference_price_even_without_a_quote(desk, client):
    ledger = build_ledger(desk, [opening('Main', cash={'CNY': '1000'}, date='2026-11-12')])
    book(ledger, [trade('buy', CN, '100', '5', fee='1', account='Main', date=FRIDAY),
                  trade('sell', CN, '100', '5.2', fee='1', account='Main', date=FRIDAY)])
    clock = FakeClock('2026-11-16 08:00')
    install(clock, ScriptedSource({}, {}))
    data = client.get('/api/today').json()
    closed, = data['accounts'][0]['closed_today']
    assert closed['day_pnl'] == '18.00' and data['totals']['day_pnl_cny'] == '18.00'


BEHIND = '行情尚未更新到最新交易日'


def test_trades_after_the_quoted_session_make_its_day_profit_null(desk, client):
    # Reviewer's reproduction: 100 held before 13 November, 50 bought at 15
    # on the 16th, valued on the 17th from a quote still at 13 November (12,
    # previous close 11): day profit 150x12 - 100x11 - 50x15 = -50 with
    # nothing unavailable. Friday's own result was +100; today's is unknown.
    ledger = build_ledger(desk, [opening('Main', cash={'CNY': '750'}, date='2026-11-12', positions=[
        position(CN, '100', '10')])])
    book(ledger, [trade('buy', CN, '50', '15', account='Main', date=MONDAY)])
    clock = FakeClock('2026-11-17 10:00')
    install(clock, ScriptedSource({CN: quote(CN, '12', prev_close='11', at=shanghai('2026-11-13 15:00'))}, {}))
    data = client.get('/api/today').json()
    cn, = data['accounts'][0]['positions']
    assert cn['quote']['status'] == 'stale' and cn['market_value'] == '1800.00' and cn['estimated'] is True
    assert cn['day_pnl'] is None and data['totals']['day_pnl_cny'] is None
    assert {'ticker': CN, 'field': 'day_pnl', 'reason': BEHIND} in data['totals']['unavailable']


def test_a_sale_after_the_quoted_session_makes_the_closed_row_null(desk, client):
    ledger = build_ledger(desk, [opening('Main', date='2026-11-11', positions=[position(CN, '100', '10')])])
    book(ledger, [trade('sell', CN, '100', '12', account='Main', date=FRIDAY)])
    clock = FakeClock('2026-11-16 08:00')
    # Monday before the open, Friday's session is current, but the quote is
    # stuck on Thursday's close, before the Friday sale.
    install(clock, ScriptedSource({CN: quote(CN, '11', prev_close='10.5', at=shanghai('2026-11-12 15:00'))}, {}))
    data = client.get('/api/today').json()
    closed, = data['accounts'][0]['closed_today']
    assert closed['day_pnl'] is None and data['totals']['day_pnl_cny'] is None
    assert {'ticker': CN, 'field': 'day_pnl', 'reason': BEHIND} in data['totals']['unavailable']


def test_a_us_trade_dated_after_new_yorks_latest_session_is_not_netted_against_it(desk, client):
    # Tuesday 10:00 in Shanghai: the New York quote is Monday's close. A trade
    # dated Tuesday (New York's Tuesday has not opened) is after that session.
    ledger = holdings(desk)
    book(ledger, [{**trade('buy', US, '5', '59', currency='USD', account='Main', date=TUESDAY), 'allow_margin': True}])
    install(FakeClock('2026-11-17 10:00'), market(FakeClock('2026-11-17 10:00')))
    data = client.get('/api/today').json()
    cn, us, hk = data['accounts'][0]['positions']
    assert us['quantity'] == '15' and us['day_pnl'] is None and cn['day_pnl'] == '100.00'
    assert {'ticker': US, 'field': 'day_pnl', 'reason': BEHIND} in data['totals']['unavailable']


def date_hints(data):
    return [notice for notice in data['notices'] if notice['kind'] == 'exchange_local_dates']


def test_recent_us_trades_bring_a_hint_that_dates_are_new_york_dates(desk, client):
    # Review finding 11: the ledger holds dates only, so a US trade must be
    # dated by New York's calendar; a Shanghai date can put it in the wrong
    # session. No behaviour changes, but the owner is told while it matters.
    ledger = holdings(desk)
    install(FakeClock('2026-11-17 10:00'), market(FakeClock('2026-11-17 10:00')))
    assert date_hints(client.get('/api/today').json()) == []          # an opening balance is not a trade
    book(ledger, [{**trade('buy', US, '5', '59', currency='USD', account='Main', date=MONDAY), 'allow_margin': True}])
    hint, = date_hints(client.get('/api/today', params={'refresh': 1}).json())
    assert '纽约' in hint['message'] and '日期' in hint['message']


def test_older_us_trades_and_mainland_trades_bring_no_date_hint(desk, client):
    ledger = holdings(desk)
    book(ledger, [{**trade('buy', US, '5', '59', currency='USD', account='Main', date='2026-11-13'),
                   'allow_margin': True}, trade('buy', CN, '10', '11', account='Main', date=TUESDAY)])
    install(FakeClock('2026-11-18 10:00'), market(FakeClock('2026-11-18 10:00')))
    # Wednesday: New York's last two sessions are Monday and Tuesday.
    assert date_hints(client.get('/api/today').json()) == []


def test_weekend_shows_fridays_close_and_fridays_day_profit(desk, client):
    build_ledger(desk, [opening('Main', cash={'CNY': '0'}, date='2026-11-12', positions=[position(CN, '100', '10')])])
    clock = FakeClock('2026-11-15 12:00')                           # Sunday
    install(clock, ScriptedSource({CN: quote(CN, '12', prev_close='11', at=shanghai('2026-11-13 15:00'))}, {}))
    data = client.get('/api/today').json()
    cn = data['accounts'][0]['positions'][0]
    assert data['session']['phase'] == 'closed' and data['session']['trading_day'] == '2026-11-13'
    assert cn['quote']['status'] == 'closed' and cn['day_pnl'] == '100.00'
    assert data['totals']['total_cny'] == '1200.00'                 # no foreign currency, so no rate is needed
    assert data['fx']['USD']['status'] == 'unavailable'


# -- demo mode ----------------------------------------------------------------

DEMO_DAY = date(2026, 11, 17)   # the day the demo fixtures are made for (tests/test_fixture_dates.py)

def demo_client(tmp_path, monkeypatch, ledger):
    from create_demo import create_demo
    import backend.main as main
    create_demo(tmp_path / 'portfolio', ledger=ledger, today=DEMO_DAY)   # a fixed day, never the runner's
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path / 'portfolio'))
    monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    set_market_runtime(None)
    # No lifespan: demo mode starts no agent service. The real application only answers
    # loopback Host headers, so the client must not use the default "testserver".
    return TestClient(main.app, base_url='http://127.0.0.1')


def test_demo_mode_serves_the_seeded_ledger_with_scripted_quotes(tmp_path, monkeypatch):
    client = demo_client(tmp_path, monkeypatch, ledger=True)
    try:
        response = client.get('/api/today', params={'refresh': 1})
        assert response.status_code == 200
        data = response.json()
        assert get_market_runtime().mode == 'fake' and data['source'] == 'fake'
        assert [account['name'] for account in data['accounts']] == ['长期账户', '研究账户']
        rows = [row for account in data['accounts'] for row in account['positions']]
        assert [row['ticker'] for row in rows] == ['SHA:DEMO', 'NASDAQ:DEMO']
        assert all(row['quote']['source'] == 'fake' and row['quote']['status'] in {'live', 'closed'} for row in rows)
        assert data['totals']['unavailable'] == []
        # The demo's last daily snapshot was computed by the engine from the
        # same holdings, prices and rates (SHA:DEMO 60, NASDAQ:DEMO 117.5, USD 7.05, HKD 0.9: 123117.50).
        # The ledger also holds the events booked after the opening (core/demo_seed.py), which the
        # snapshot knows nothing of. Their effect on each account's total in CNY, worked out by hand
        # at those same prices (a trade's fee is part of its cash amount):
        #   长期账户  cash   buy 500 SHE:DEMOZ at 12.3 + fee 5                  -6155
        #                    buy 200 SHA:DEMO at 58.8 + fee 5                    -11765
        #                    sell 500 SHE:DEMOZ at 13.6 - fee 5                  +6795
        #                    dividend 360, fee 12                                +360 -12
        #                    = -10777 (cash 20000 -> 9223)
        #           holdings 200 more SHA:DEMO at 60                             +12000
        #                    SHE:DEMOZ was sold out again                        0
        #                    = +1223  -> 91550 + 1223 = 92773
        #   研究账户  repo interest +8.62                                          -> 31567.5 + 8.62 = 31576.12
        #   total     123117.50 + 1223 + 8.62 = 124349.12
        snapshot = json.loads(sorted((tmp_path / 'portfolio/snapshots').glob('*.json'))[-1].read_text())
        after_opening = {'长期账户': 1223.0, '研究账户': 8.62}
        assert snapshot['summary']['total_value'] == pytest.approx(123117.5, abs=0.011)
        assert float(data['totals']['total_cny']) == pytest.approx(
            snapshot['summary']['total_value'] + sum(after_opening.values()), abs=0.011) == pytest.approx(124349.12, abs=0.011)
        for account in data['accounts']:
            assert float(account['total_cny']) == pytest.approx(
                snapshot['groups'][account['name']]['total_value'] + after_opening[account['name']], abs=0.011)
        # Demo mode is read-only.
        assert client.post('/api/today').status_code == 403
        assert client.post('/api/ledger/proposals', json={'events': [opening('X')]}).status_code == 403
    finally:
        set_market_runtime(None)


def test_demo_without_a_ledger_has_quotes_for_its_holdings_and_an_empty_today(tmp_path, monkeypatch):
    client = demo_client(tmp_path, monkeypatch, ledger=False)
    try:
        directory = tmp_path / 'portfolio'
        script = json.loads((directory / 'fake_quotes.json').read_text())
        held = {p['ticker'] for file in (directory / 'holdings').glob('*.json')
                for group in json.loads(file.read_text())['groups'].values() for p in group['positions']}
        assert set(script['quotes']) == held == {'SHA:DEMO', 'NASDAQ:DEMO'}
        assert set(script['fx']) == {'USD', 'HKD'}
        assert not (directory / 'ledger.sqlite3').exists()         # the opening-balance journey starts empty
        data = client.get('/api/today').json()
        assert data['accounts'] == [] and data['notice']
    finally:
        set_market_runtime(None)


def test_create_demo_runs_as_a_standalone_script(tmp_path):
    # The browser journeys start it this way, without pytest's import path.
    import os
    import subprocess
    import sys
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / 'engine/scripts/create_demo.py'
    env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
    for name, extra in (('plain', []), ('seeded', ['--ledger'])):
        target = tmp_path / name
        subprocess.run([sys.executable, str(script), '--output', str(target), *extra], check=True, env=env,
                       cwd=tmp_path, capture_output=True)
        assert (target / 'fake_quotes.json').is_file()
        assert (target / 'ledger.sqlite3').exists() == bool(extra)


def test_create_demo_still_works_where_the_dashboard_package_is_absent(tmp_path, monkeypatch):
    # The engine-only image ships core/ledger.py but not the rest of the dashboard.
    import sys
    from create_demo import create_demo
    monkeypatch.setitem(sys.modules, 'core.demo_seed', None)
    assert create_demo(tmp_path / 'plain') == 20
    assert not (tmp_path / 'plain/fake_quotes.json').exists()
    with pytest.raises(ImportError):
        create_demo(tmp_path / 'seeded', ledger=True, today=DEMO_DAY)


# -- GET /api/quotes ----------------------------------------------------------

QUOTE_OBJECT_KEYS = {'ticker', 'name', 'price', 'prev_close', 'change_pct', 'currency', 'quote', 'estimated',
                     'estimated_reason'}


def test_quotes_endpoint_returns_the_same_quote_objects_as_today_rows(desk, client):
    holdings(desk)
    clock = FakeClock('2026-11-17 10:00')
    source = install(clock, market(clock))
    data = client.get('/api/quotes', params={'tickers': f'{CN}, {US} ,hkg:00001'}).json()
    assert data['as_of'] == '2026-11-17T10:00:00+08:00' and data['source'] == 'scripted'
    cn, us, hk = data['quotes']
    assert [q['ticker'] for q in data['quotes']] == [CN, US, HK]
    assert all(set(q) == QUOTE_OBJECT_KEYS for q in data['quotes'])
    assert cn == {'ticker': CN, 'name': '示例甲', 'price': '12', 'prev_close': '11', 'change_pct': '9.09',
                  'currency': 'CNY', 'estimated': False, 'estimated_reason': None,
                  'quote': {'source': 'scripted', 'quote_time': '2026-11-17T09:59:57+08:00',
                            'fetched_at': '2026-11-17T09:59:57+08:00', 'status': 'live', 'reason': None,
                            'session_date': '2026-11-17'}}
    # Changed with the HK/US tables: a covered year's New York close is verified.
    assert (us['quote']['status'], us['quote']['session_date'], us['estimated']) == ('closed', '2026-11-16', False)
    assert us['estimated_reason'] is None and us['quote']['reason'] is None
    row = client.get('/api/today').json()['accounts'][0]['positions'][0]
    assert {key: row[key] for key in ('ticker', 'price', 'prev_close', 'change_pct', 'currency', 'quote')} == {
        key: cn[key] for key in ('ticker', 'price', 'prev_close', 'change_pct', 'currency', 'quote')}
    assert len(source.calls) == 1                           # the cache is shared with /api/today


def test_quotes_endpoint_reports_bad_tickers_and_outages_per_ticker_never_as_an_error(desk, client):
    clock = FakeClock('2026-11-17 10:00')
    source = install(clock, market(clock, **{CN: quote(CN, '1e100', prev_close='11', at=clock.now())}))
    data = client.get('/api/quotes', params={'tickers': '000001,SHA:000001,LSE:VOD,SHA:600001,HKG:00001,HKG:1'})
    assert data.status_code == 200
    quotes = {q['ticker']: q for q in data.json()['quotes']}
    assert list(quotes) == ['000001', 'SHA:000001', 'LSE:VOD', CN, HK, 'HKG:1']
    for ticker in ('000001', 'SHA:000001', 'LSE:VOD', CN):
        q = quotes[ticker]
        assert (q['price'], q['quote']['status']) == (None, 'unavailable') and q['quote']['reason'], ticker
        assert q['estimated'] is False
    assert '指数' in quotes['SHA:000001']['quote']['reason'] and '范围' in quotes[CN]['quote']['reason']
    assert quotes['HKG:1']['price'] == quotes[HK]['price'] == '5'
    assert source.calls == [(CN, HK)]                       # one request; HKG:1 is the same listing
    source.down = '请求失败（ConnectTimeout）'
    down = client.get('/api/quotes', params={'tickers': US, 'refresh': 1})
    assert down.status_code == 200 and down.json()['quotes'][0]['quote']['status'] == 'unavailable'


@pytest.mark.parametrize('tickers', ['', ' , ,', ','.join(f'SHA:6{n:05d}' for n in range(51))])
def test_quotes_endpoint_needs_between_one_and_fifty_tickers(desk, client, tickers):
    source = install(FakeClock('2026-11-17 10:00'), market(FakeClock('2026-11-17 10:00')))
    response = client.get('/api/quotes', params={'tickers': tickers})
    assert response.status_code == 422 and isinstance(response.json()['detail'], str)
    assert source.calls == []


def test_quotes_endpoint_accepts_fifty_and_counts_duplicates_once(desk, client):
    source = install(FakeClock('2026-11-17 10:00'), market(FakeClock('2026-11-17 10:00')))
    fifty = [f'SHA:6{n:05d}' for n in range(50)]
    assert len(client.get('/api/quotes', params={'tickers': ','.join(fifty)}).json()['quotes']) == 50
    repeated = ','.join([CN] * 60)
    assert [q['ticker'] for q in client.get('/api/quotes', params={'tickers': repeated}).json()['quotes']] == [CN]
    assert client.get('/api/quotes').status_code == 422


def test_quotes_endpoint_is_served_by_the_real_application(tmp_path, monkeypatch):
    client = demo_client(tmp_path, monkeypatch, ledger=False)
    try:
        response = client.get('/api/quotes', params={'tickers': 'SHA:DEMO,NASDAQ:DEMO', 'refresh': 1})
        assert response.status_code == 200
        quotes = response.json()['quotes']
        assert [q['ticker'] for q in quotes] == ['SHA:DEMO', 'NASDAQ:DEMO']
        assert all(q['quote']['source'] == 'fake' and q['price'] and q['quote']['session_date'] for q in quotes)
        assert client.get('/api/quotes').status_code == 422
    finally:
        set_market_runtime(None)
