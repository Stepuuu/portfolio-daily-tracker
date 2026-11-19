"""Shared vectors: the snapshot and Today judge the same Yahoo answer, rate and ledger figures alike.

Each quote case is one Yahoo chart answer seen at one moment. Today's verdict comes from its own Yahoo source
and decision table (``sources_yahoo.quote_from_chart``, observed then, ``quotes.assess``); the snapshot's from
``fetch_prices``. Both are pinned here, so a change to either rule fails this file until the other follows:

* a verified close is booked unmarked; an estimate Today shows (a close the calendar cannot verify, a holding
  that did not trade in its latest session) is booked and marked, at the same price;
* a price of the latest session from before its closing print (Today: stale, so an estimate) waits for the
  print plus the sources' delay; then it is booked as the day's last price and marked, at the same price, as
  Today shows it (a thinly traded stock with no trade in the closing auction, a source that does not restamp);
* a session still under way (the midday break too) waits for its close, even where Today shows the
  morning's last price, since the snapshot books closes only; anything else is not booked.

Round 4 of the market rules: a source's regular-session flag (Yahoo) lifts only the upper bound, so a stalled
14:42 New York price is not the close (收盘前的最后一笔成交，未能核实为收盘价); the morning's last price is a
print at 11:29:00 or later (11:59:00 in Hong Kong), and an earlier one is delayed while a delayed source may
still be catching up (seen within 20 minutes of it; round 5: seen later, it is the morning's last price again);
the currency weekend runs from Friday 17:00 to Sunday 17:00 New York time with an hour's tolerance before the
close; Today's quantity and cost carry every digit the ledger holds, and so does the snapshot's arithmetic.
Round 5: the snapshot takes its exchange rate from the source Today takes it from (Yahoo first, Eastmoney when
Yahoo's rate is stale or has no time; the latest of them, marked, only when neither is verified). Fictional
prices.
"""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, localcontext

import pytest

import portfolio_snapshot as snapshot
from core.market import quotes
from core.market.calendar import SessionClock
from core.market.instruments import parse
from core.market.sources_yahoo import quote_from_chart
from core.market.today import WORKING_DIGITS
from core.market.valuation import value_portfolio
from tests.engine_fakes import position
from tests.fakes import build_ledger, opening, quote, shanghai
from tests.fakes import position as ledger_position

UTC = timezone.utc
DAY = '2026-11-17'                     # Tuesday; New York is on EST (UTC-5)
NY, CN, HK = 'NYSE:EXMP', 'SHA:600001', 'HKG:00003'
CURRENCY = {NY: 'USD', CN: 'CNY', HK: 'HKD'}


def at(*parts):
    return datetime(*parts, tzinfo=UTC)


class Response:
    def __init__(self, meta):
        self.meta = meta

    def raise_for_status(self):
        pass

    def json(self):
        return {'chart': {'result': [{'meta': self.meta}]}}


def chart(ticker, stamp):
    return {'regularMarketPrice': 10.5, 'currency': CURRENCY[ticker], 'regularMarketTime': int(stamp.timestamp()),
            'symbol': parse(ticker).yahoo_symbol, 'chartPreviousClose': 10.0}


def todays(ticker, stamp, now):
    """``(status, reason, price)`` Today gives this Yahoo answer seen at ``now``."""
    seen = replace(quote_from_chart(parse(ticker), {'chart': {'result': [{'meta': chart(ticker, stamp)}]}}, now),
                   observed_at=now)
    verdict = quotes.assess(seen, SessionClock(lambda: now))
    return verdict.status, verdict.reason, seen.price


def snapshots(monkeypatch, ticker, stamp, now, day=DAY):
    """``(what the snapshot does with the same answer, the price it books or None)``: ``booked``,
    ``estimate``, ``wait <moment>`` or ``missing``."""
    monkeypatch.setattr(snapshot.requests, 'get', lambda url, **options: Response(chart(ticker, stamp)))
    monkeypatch.setattr(snapshot, '_now', lambda: now)
    not_final, estimated = {}, {}
    prices, _ = snapshot.fetch_prices({'groups': {'E': {'positions': [position(ticker)]}}}, {}, day, not_final,
                                      estimated)
    if ticker in prices:
        return ('estimate' if ticker in estimated else 'booked'), prices[ticker]
    return (f'wait {not_final[ticker].astimezone(UTC):%H:%M}' if ticker in not_final else 'missing'), None


# (ticker, exchange time, seen at, Today's status, words of Today's reason or None, what the snapshot does)
QUOTE_CASES = [
    # New York, judged once its close has settled (16:30): the regular-session flag lifts only the upper bound.
    (NY, at(2026, 11, 17, 21, 0, 0), at(2026, 11, 17, 22, 0), 'closed', None, 'booked'),         # 16:00:00
    (NY, at(2026, 11, 17, 20, 59, 30), at(2026, 11, 17, 22, 0), 'closed', None, 'booked'),       # 15:59:30
    # A regular-session price before the bell is not the close: past the wait, the day's last price, marked.
    (NY, at(2026, 11, 17, 19, 42), at(2026, 11, 17, 22, 0), 'stale', '收盘前的最后一笔成交', 'estimate'),  # 14:42
    (NY, at(2026, 11, 17, 20, 58, 30), at(2026, 11, 17, 22, 0), 'stale', '收盘前的最后一笔成交', 'estimate'),
    # The mainland and Hong Kong: the close is the closing auction's print.
    (CN, at(2026, 11, 17, 7, 0, 2), at(2026, 11, 17, 7, 40), 'closed', None, 'booked'),          # 15:00:02
    # Stamped before the closing print: waits for the print plus 20 minutes, then the day's last price, marked.
    (CN, at(2026, 11, 17, 6, 42), at(2026, 11, 17, 7, 40), 'stale', '早于收盘竞价', 'estimate'),   # 14:42 at 15:40
    (CN, at(2026, 11, 17, 6, 42), at(2026, 11, 17, 7, 10), 'stale', '早于收盘竞价', 'wait 07:20'),  # at 15:10
    (HK, at(2026, 11, 17, 8, 8, 2), at(2026, 11, 17, 8, 10), 'closed', None, 'booked'),          # 16:08:02
    (HK, at(2026, 11, 17, 7, 55, 12), at(2026, 11, 17, 8, 10), 'stale', '早于收盘竞价', 'wait 08:28'),
    (HK, at(2026, 11, 17, 7, 55, 12), at(2026, 11, 17, 8, 40), 'stale', '早于收盘竞价', 'estimate'),  # at 16:40
    # The midday break: Today shows the morning's last price; the snapshot waits for the day's close.
    (CN, at(2026, 11, 17, 3, 29, 30), at(2026, 11, 17, 3, 45), 'closed', None, 'wait 07:00'),     # 11:29:30
    (CN, at(2026, 11, 17, 3, 28), at(2026, 11, 17, 3, 45), 'delayed', '落后', 'wait 07:00'),       # 11:28
    (CN, at(2026, 11, 17, 3, 28), at(2026, 11, 17, 3, 55), 'closed', None, 'wait 07:00'),         # 11:28 at 11:55
    (HK, at(2026, 11, 17, 3, 59, 10), at(2026, 11, 17, 4, 20), 'closed', None, 'wait 08:10'),     # 11:59:10
    (HK, at(2026, 11, 17, 3, 50), at(2026, 11, 17, 4, 5), 'delayed', '落后', 'wait 08:10'),        # 11:50 at 12:05
    (HK, at(2026, 11, 17, 3, 50), at(2026, 11, 17, 4, 25), 'closed', None, 'wait 08:10'),         # 11:50 at 12:25
    # Estimates: no trade in the latest session (suspended), a year the holiday table does not cover.
    (CN, at(2026, 11, 16, 7, 0), at(2026, 11, 17, 7, 40), 'stale', '早于最近一个交易时段', 'estimate'),
    (CN, at(2030, 1, 8, 7, 0), at(2030, 1, 8, 7, 40), 'closed', '节假日表', 'estimate'),
]


@pytest.mark.parametrize('ticker, stamp, now, status, words, booked', QUOTE_CASES)
def test_the_snapshot_and_today_judge_a_quote_alike(monkeypatch, ticker, stamp, now, status, words, booked):
    found, reason, price = todays(ticker, stamp, now)
    assert found == status and (words in reason if words else reason is None), reason
    day = now.astimezone(timezone.utc).date().isoformat() if stamp.year == 2030 else DAY
    outcome, booked_price = snapshots(monkeypatch, ticker, stamp, now, day)
    assert outcome == booked
    if booked_price is not None:
        # The same price as Today's; marked exactly when Today shows an estimate (anything but a verified close).
        assert booked_price == float(price)
        assert (outcome == 'estimate') == (found != 'closed' or reason is not None)


# (rate's exchange time or None, seen at, Today's status, whether the snapshot marks what it converts)
FRIDAY_CLOSE = at(2026, 11, 20, 22, 0)             # 17:00 New York, Friday 2026-11-20
RATE_CASES = [
    (at(2026, 11, 17, 7, 39, 50), at(2026, 11, 17, 7, 40), 'live', False),
    (at(2026, 11, 17, 7, 0), at(2026, 11, 17, 7, 40), 'delayed', False),
    (at(2026, 11, 16, 7, 40), at(2026, 11, 17, 7, 40), 'stale', True),             # yesterday's
    (None, at(2026, 11, 17, 7, 40), 'time_unknown', True),
    (at(2026, 11, 20, 21, 30), at(2026, 11, 21, 4, 0), 'closed', False),            # Friday 16:30, on Saturday
    (at(2026, 11, 20, 20, 30), at(2026, 11, 21, 4, 0), 'stale', True),             # Friday 15:30: over an hour
    (at(2026, 11, 20, 21, 59), at(2026, 11, 22, 22, 30), 'stale', True),           # Sunday 17:30: reopened
]


@pytest.mark.parametrize('stamp, now, status, marked', RATE_CASES)
def test_the_snapshot_and_today_judge_a_rate_alike(monkeypatch, stamp, now, status, marked):
    rate = quotes.build_fx('USD', 'yahoo', now, rate='7.1234', quote_time=stamp)
    assert quotes.derive_fx_status(rate, now)[0] == status
    meta = {'regularMarketPrice': 7.1234, 'currency': 'CNY', 'symbol': 'USDCNY=X'}
    if stamp is not None:
        meta['regularMarketTime'] = int(stamp.timestamp())
    monkeypatch.setattr(snapshot.requests, 'get', lambda url, **options: Response(meta))
    monkeypatch.setattr(snapshot, '_now', lambda: now)
    estimated = {}
    assert snapshot.fetch_fx_rates({'fx_tickers': {'USD_CNY': 'USDCNY=X'}}, estimated) == {'CNY': 1.0, 'USD': 7.1234}
    assert ('USD' in estimated) is marked


# -- which source's rate ---------------------------------------------------------------------------------------
# Today asks Yahoo (the onshore rate) first and Eastmoney (offshore, CNH) next (``core.market.runtime``); a rate
# that is stale or has no time yields to the next source, and only when no source has a verified one is the
# latest of them used, as an estimate (``QuoteService._fetch_fx``). The snapshot takes the same rate. Each case is
# one Yahoo chart answer and one Eastmoney row (None: the request fails) seen at one moment.
TUESDAY_16_12 = at(2026, 11, 17, 8, 12)
SATURDAY = at(2026, 11, 21, 4, 0)


def yahoo_rate(rate, stamp, currency='CNY'):
    meta = {'regularMarketPrice': rate, 'currency': currency, 'symbol': 'USDCNY=X'}
    if stamp is not None:
        meta['regularMarketTime'] = int(stamp.timestamp())
    return {'chart': {'result': [{'meta': meta}]}}


def eastmoney_rate(rate, stamp):
    row = {'f12': 'USDCNH', 'f13': 133, 'f14': '美元离岸人民币', 'f2': rate}
    if stamp is not None:
        row['f124'] = int(stamp.timestamp())
    return {'rc': 0, 'data': {'total': 1, 'diff': [row]}}


NO_ROW = {'rc': 102, 'data': None}

# (Yahoo's answer, Eastmoney's answer, now, the rate taken or None, marked, the sources asked)
SOURCE_CASES = [
    # The reviewer's case: Yahoo's 7.00 is 72 minutes old, Eastmoney's 7.01 is current: 7.01, unmarked.
    (yahoo_rate(7.0, at(2026, 11, 17, 7, 0)), eastmoney_rate(7.01, at(2026, 11, 17, 8, 11, 50)), TUESDAY_16_12,
     7.01, False, ['yahoo', 'eastmoney']),
    (yahoo_rate(7.0, at(2026, 11, 17, 8, 11, 50)), eastmoney_rate(7.01, at(2026, 11, 17, 8, 11, 50)), TUESDAY_16_12,
     7.0, False, ['yahoo']),                                                    # Yahoo's current rate comes first
    (yahoo_rate(7.0, at(2026, 11, 17, 7, 40)), eastmoney_rate(7.01, at(2026, 11, 17, 8, 11, 50)), TUESDAY_16_12,
     7.0, False, ['yahoo']),                                                    # delayed is verified too
    (yahoo_rate(7.0, None), eastmoney_rate(7.01, at(2026, 11, 17, 8, 11, 50)), TUESDAY_16_12,
     7.01, False, ['yahoo', 'eastmoney']),                                      # no time: yields
    (None, eastmoney_rate(7.01, at(2026, 11, 17, 8, 11, 50)), TUESDAY_16_12,
     7.01, False, ['yahoo', 'eastmoney']),                                      # Yahoo cannot be reached
    (yahoo_rate(7.0, at(2026, 11, 17, 8, 11, 50), currency='USD'), eastmoney_rate(7.01, at(2026, 11, 17, 8, 11, 50)),
     TUESDAY_16_12, 7.01, False, ['yahoo', 'eastmoney']),                       # not a rate in yuan
    # No verified rate anywhere: the latest of them, marked.
    (yahoo_rate(7.0, at(2026, 11, 17, 7, 0)), eastmoney_rate(7.01, at(2026, 11, 17, 6, 0)), TUESDAY_16_12,
     7.0, True, ['yahoo', 'eastmoney']),
    (yahoo_rate(7.0, at(2026, 11, 17, 6, 0)), eastmoney_rate(7.01, at(2026, 11, 17, 7, 0)), TUESDAY_16_12,
     7.01, True, ['yahoo', 'eastmoney']),
    (yahoo_rate(7.0, None), eastmoney_rate(7.01, at(2026, 11, 17, 6, 0)), TUESDAY_16_12,
     7.01, True, ['yahoo', 'eastmoney']),                                       # a time beats none
    (yahoo_rate(7.0, at(2026, 11, 17, 7, 0)), None, TUESDAY_16_12, 7.0, True, ['yahoo', 'eastmoney']),
    (yahoo_rate(7.0, at(2026, 11, 17, 7, 0)), NO_ROW, TUESDAY_16_12, 7.0, True, ['yahoo', 'eastmoney']),
    (None, None, TUESDAY_16_12, None, False, ['yahoo', 'eastmoney']),            # no rate at all
    # Saturday: Friday's last rate is the market's, verified.
    (yahoo_rate(7.0, at(2026, 11, 20, 21, 59)), eastmoney_rate(7.01, at(2026, 11, 21, 3, 59)), SATURDAY,
     7.0, False, ['yahoo']),
]


class AnsweringSource:
    """Today's side of a case: its own parsers (``fx_from_chart``, ``fx_from_row``) over the same answer."""

    def __init__(self, name, answer, now, asked):
        self.name, self.answer, self.now, self.asked = name, answer, now, asked

    def fetch_fx(self, currencies):
        from core.market.sources_eastmoney import fx_from_row, parse_rows
        from core.market.sources_yahoo import fx_from_chart
        self.asked.append(self.name)
        if self.answer is None:
            raise quotes.SourceError('the source cannot be reached in this test')
        if self.name == 'yahoo':
            return {'USD': fx_from_chart('USD', self.answer, self.now)}
        return {'USD': fx_from_row('USD', parse_rows(self.answer).get('133.USDCNH'), self.now)}


def todays_rate(yahoo, eastmoney, now, order=('yahoo', 'eastmoney')):
    """``(rate, status, sources asked)`` of USD/CNY as Today's quote service takes it from the sources ``order``
    names (Today's rate sources, in its order)."""
    asked = []
    answers = {'yahoo': yahoo, 'eastmoney': eastmoney}
    service = quotes.QuoteService([], SessionClock(lambda: now), fx_sources=[
        AnsweringSource(name, answers[name], now, asked) for name in order])
    rate = service._fetch_fx(['USD'], force=True)['USD']    # what ``fx`` asks for each currency
    return rate, quotes.derive_fx_status(rate, now)[0], asked


def snapshots_rate(monkeypatch, yahoo, eastmoney, now):
    """``(rates, estimated, sources asked)`` of USD/CNY as the snapshot takes them (``TRACKER_QUOTE_SOURCE`` as the
    caller left it)."""
    asked = []

    class Answer:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    def get(url, **options):
        name = 'eastmoney' if 'eastmoney' in url else 'yahoo'
        asked.append(name)
        payload = eastmoney if name == 'eastmoney' else yahoo
        if payload is None:
            raise snapshot.requests.ConnectionError('the source cannot be reached in this test')
        return Answer(payload)
    monkeypatch.setattr(snapshot.requests, 'get', get)
    monkeypatch.setattr(snapshot, '_now', lambda: now)
    estimated = {}
    rates = snapshot.fetch_fx_rates({'fx_tickers': {'USD_CNY': 'USDCNY=X'}}, estimated)
    return rates, estimated, asked


@pytest.mark.parametrize('yahoo, eastmoney, now, taken, marked, asked', SOURCE_CASES)
def test_the_snapshot_takes_the_rate_today_takes(monkeypatch, yahoo, eastmoney, now, taken, marked, asked):
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    rate, status, today_asked = todays_rate(yahoo, eastmoney, now)
    assert today_asked == asked
    assert (float(rate.rate) if rate.rate is not None else None) == taken
    assert (status in (quotes.STALE, quotes.TIME_UNKNOWN)) is marked
    rates, estimated, snapshot_asked = snapshots_rate(monkeypatch, yahoo, eastmoney, now)
    assert snapshot_asked == asked
    assert rates.get('USD') == taken and ('USD' in estimated) is marked


# Round 6: ``TRACKER_QUOTE_SOURCE`` selects Today's sources, its rate sources too (``core.market.runtime``); the
# snapshot takes its rate from the same ones (with ``yahoo``, Yahoo's stale 7.00, marked, not Eastmoney's 7.01).
@pytest.mark.parametrize('choice', ['yahoo', 'eastmoney', ''])
@pytest.mark.parametrize('yahoo, eastmoney, now, _taken, _marked, _asked', SOURCE_CASES)
def test_the_snapshot_takes_the_rate_today_takes_from_the_selected_sources(monkeypatch, choice, yahoo, eastmoney,
                                                                           now, _taken, _marked, _asked):
    from core.market.runtime import build_runtime
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    monkeypatch.setenv('TRACKER_QUOTE_SOURCE', choice)
    order = [source.name for source in build_runtime().quotes.fx_sources]
    rate, status, today_asked = todays_rate(yahoo, eastmoney, now, order)
    rates, estimated, snapshot_asked = snapshots_rate(monkeypatch, yahoo, eastmoney, now)
    assert snapshot_asked == today_asked
    assert rates.get('USD') == (float(rate.rate) if rate.rate is not None else None)
    assert ('USD' in estimated) is (status in (quotes.STALE, quotes.TIME_UNKNOWN))


# -- every digit the ledger holds -------------------------------------------------------------------------------
# Today values the ledger's own figures (``today.WORKING_DIGITS``); the snapshot reads the same ledger and must not
# round its quantities, costs and balances to binary floats first.

def live(ticker, price):
    return replace(quote(ticker, price, at=shanghai(f'{DAY} 10:00')), status='live')


@pytest.mark.parametrize('quantity, cost, price, value, cost_value', [
    ('0.99999999999999999999', '1', '1.005', 1.00, 1.00),          # as a float the quantity is 1: 1.01
    ('1', '1.00499999999999999999', '2', 2.00, 1.00),               # as a float the cost is 1.005: 1.01
    ('3', '10.00333333333333333333333333', '10.5', 31.50, 30.01),   # an average cost as the ledger replays it
])
def test_the_snapshot_values_every_digit_the_ledger_holds(tmp_path, monkeypatch, quantity, cost, price, value,
                                                          cost_value):
    ledger = build_ledger(tmp_path, [opening('Example', cash={'CNY': '0.005'}, contributed_cny='100',
                                             date='2026-11-16', positions=[ledger_position(CN, quantity, cost)])])
    with localcontext() as context:
        context.prec = WORKING_DIGITS
        today = value_portfolio(ledger.state(), {CN: live(CN, price)}, {}, None, [])
    row, account = today.accounts[0].positions[0], today.accounts[0]
    assert (float(row.market_value_cny), float(row.cost_cny)) == (value, cost_value)
    monkeypatch.setattr(snapshot, 'PORTFOLIO_DIR', str(tmp_path))
    holdings = snapshot.load_holdings(DAY, {}, persist=False)
    saved = snapshot.calculate_snapshot(holdings, {CN: float(price)}, {}, {'CNY': 1.0}, None, [])
    mine = saved['groups']['Example']['positions'][0]
    assert (mine['market_value_cny'], mine['cost_value_cny']) == (value, cost_value)
    assert mine['profit_cny'] == float(row.total_pnl_cny)
    assert saved['groups']['Example']['cash'] == float(account.cash_cny)
    assert saved['summary']['total_value'] == float(today.total_cny)
