"""A close snapshot books closes only: a price of a session still trading is not a close.

For day D each market's price must be the close of a session dated on or before D that has ended when the
quote is fetched (the post-market brief's rule: a session still trading gives 盘中价, not 收盘). So a retry of
Tuesday's snapshot at 23:05 Shanghai does not book New York's Tuesday session, open since 22:30; it waits
(the ticker has no price and the snapshot is not saved) until that session has closed. Hong Kong at 15:40
(its session runs to 16:00, the closing auction to about 16:10) likewise. Stubbed fetches; fictional prices.
"""
from datetime import datetime, timezone

import pytest

import portfolio_snapshot as snapshot
from tests.engine_fakes import position

DAY = '2026-11-17'
UTC = timezone.utc


def epoch(*parts):
    return int(datetime(*parts, tzinfo=UTC).timestamp())


class Response:
    def __init__(self, meta):
        self.meta = meta

    def raise_for_status(self):
        pass

    def json(self):
        return {'chart': {'result': [{'meta': self.meta}]}}


def fetched(monkeypatch, ticker, currency, stamp, now):
    monkeypatch.setattr(snapshot.requests, 'get', lambda url, **options: Response(
        {'regularMarketPrice': 10.5, 'currency': currency, 'regularMarketTime': stamp}))
    monkeypatch.setattr(snapshot, '_now', lambda: now)
    holdings = {'groups': {'Example': {'positions': [position(ticker)]}}}
    return snapshot.fetch_prices(holdings, {}, DAY)[0]


# Tuesday 2026-11-17. New York (EST, UTC-5) trades 14:30-21:00 UTC; Hong Kong 01:30-08:00 UTC (16:10 with the
# closing auction); Shanghai and Shenzhen close at 07:00 UTC.
@pytest.mark.parametrize('ticker, currency, stamp, now', [
    ('NASDAQ:EXMP', 'USD', epoch(2026, 11, 17, 15, 0), datetime(2026, 11, 17, 15, 5, tzinfo=UTC)),    # 23:05 Shanghai
    ('NYSE:EXMP', 'USD', epoch(2026, 11, 17, 20, 59), datetime(2026, 11, 17, 20, 59, 30, tzinfo=UTC)),  # a minute left
    ('HKG:00003', 'HKD', epoch(2026, 11, 17, 7, 39), datetime(2026, 11, 17, 7, 40, tzinfo=UTC)),       # 15:40
    ('HKG:00003', 'HKD', epoch(2026, 11, 17, 8, 0), datetime(2026, 11, 17, 8, 5, tzinfo=UTC)),         # closing auction
    ('SHA:600001', 'CNY', epoch(2026, 11, 17, 6, 0), datetime(2026, 11, 17, 6, 1, tzinfo=UTC)),        # 14:01
    # New York's official closes reach Yahoo minutes after the bell (2026-11-18: 12 minutes after it KO was
    # 98.04, the close 98.09): not a close until 16:30 New York time (calendar.CLOSE_SETTLE).
    ('NYSE:EXMP', 'USD', epoch(2026, 11, 17, 21, 0), datetime(2026, 11, 17, 21, 12, tzinfo=UTC)),     # 16:12 New York
    ('NYSE:EXMP', 'USD', epoch(2026, 11, 17, 21, 0), datetime(2026, 11, 17, 21, 29, 59, tzinfo=UTC)),
])
def test_a_price_of_a_session_still_trading_is_not_booked(monkeypatch, capsys, ticker, currency, stamp, now):
    assert fetched(monkeypatch, ticker, currency, stamp, now) == {}
    assert ticker in capsys.readouterr().err


@pytest.mark.parametrize('ticker, currency, stamp, now', [
    ('NASDAQ:EXMP', 'USD', epoch(2026, 11, 16, 21, 0), datetime(2026, 11, 17, 7, 40, tzinfo=UTC)),     # Monday's close at 15:40
    ('NASDAQ:EXMP', 'USD', epoch(2026, 11, 17, 21, 0), datetime(2026, 11, 17, 22, 0, tzinfo=UTC)),     # Tuesday's, at 06:00
    ('NASDAQ:EXMP', 'USD', epoch(2026, 11, 17, 21, 0), datetime(2026, 11, 17, 21, 30, tzinfo=UTC)),    # settled: 16:30 New York
    ('HKG:00003', 'HKD', epoch(2026, 11, 17, 8, 8), datetime(2026, 11, 17, 8, 25, tzinfo=UTC)),        # 16:25
    ('SHA:600001', 'CNY', epoch(2026, 11, 17, 7, 0), datetime(2026, 11, 17, 7, 40, tzinfo=UTC)),       # 15:40
])
def test_the_close_of_a_session_that_has_ended_is_booked(monkeypatch, ticker, currency, stamp, now):
    assert fetched(monkeypatch, ticker, currency, stamp, now) == {ticker: 10.5}


def test_the_engine_only_close_times_are_the_calendars_on_a_normal_day():
    from datetime import date, time
    from core.market.calendar import SessionClock, ZONES
    clock = SessionClock()
    for exchange, market in snapshot.CALENDAR_MARKETS.items():
        settled = clock.close_settled(market, date(2026, 11, 17)).astimezone(ZONES[market])
        assert settled.time() == time(*snapshot.STANDARD_CLOSE_FINAL[exchange]), exchange


def test_the_reason_names_the_session_and_when_it_closes(monkeypatch):
    meta = {'regularMarketPrice': 10.5, 'currency': 'USD', 'regularMarketTime': epoch(2026, 11, 17, 15, 0)}
    problem = snapshot.quote_problem('NASDAQ:EXMP', meta, DAY, now=datetime(2026, 11, 17, 15, 5, tzinfo=UTC))
    assert '2026-11-17' in problem and 'still trading' in problem


# -- waiting for a close that is not final yet ---------------------------------------------------------
# What the engine tells its caller when the only prices missing are of sessions whose close is not final yet
# (calendar SessionClock.close_settled): the moment each becomes final, so the close snapshot routine waits
# until then instead of failing. Any other missing price is the usual failure.

def fetched_with_waits(monkeypatch, ticker, currency, stamp, now, day=DAY):
    monkeypatch.setattr(snapshot.requests, 'get', lambda url, **options: Response(
        {'regularMarketPrice': 10.5, 'currency': currency, 'regularMarketTime': stamp}))
    monkeypatch.setattr(snapshot, '_now', lambda: now)
    holdings = {'groups': {'Example': {'positions': [position(ticker)]}}}
    not_final = {}
    prices, _currencies = snapshot.fetch_prices(holdings, {}, day, not_final)
    return prices, not_final


@pytest.mark.parametrize('ticker, currency, stamp, now, day, final', [
    # Hong Kong at 15:40: its close is final at 16:10 (the closing auction).
    ('HKG:00003', 'HKD', epoch(2026, 11, 17, 7, 39), datetime(2026, 11, 17, 7, 40, tzinfo=UTC), DAY,
     datetime(2026, 11, 17, 8, 10, tzinfo=UTC)),
    # A retry at 23:05 Shanghai: New York's Tuesday session settles at 16:30 New York, 05:30 Shanghai.
    ('NASDAQ:EXMP', 'USD', epoch(2026, 11, 17, 15, 0), datetime(2026, 11, 17, 15, 5, tzinfo=UTC), DAY,
     datetime(2026, 11, 17, 21, 30, tzinfo=UTC)),
    # New York's early close on 2026-11-27 (13:00): settled at 13:30 New York.
    ('NYSE:EXMP', 'USD', epoch(2026, 11, 27, 17, 10), datetime(2026, 11, 27, 17, 15, tzinfo=UTC), '2026-11-27',
     datetime(2026, 11, 27, 18, 30, tzinfo=UTC)),
])
def test_a_close_not_final_yet_is_reported_with_when_it_becomes_final(monkeypatch, ticker, currency, stamp, now,
                                                                       day, final):
    prices, not_final = fetched_with_waits(monkeypatch, ticker, currency, stamp, now, day)
    assert prices == {} and not_final == {ticker: final}


@pytest.mark.parametrize('ticker, currency, stamp, now, day', [
    # A New York holding at 15:40 Shanghai: Monday's close, final since 05:30, is booked; nothing waits.
    ('NASDAQ:EXMP', 'USD', epoch(2026, 11, 16, 21, 0), datetime(2026, 11, 17, 7, 40, tzinfo=UTC), DAY),
    # Hong Kong's half day 2026-12-24 (12:00, final at 12:10) while Shanghai trades a full day: booked at 15:40.
    ('HKG:00003', 'HKD', epoch(2026, 12, 24, 4, 8), datetime(2026, 12, 24, 7, 40, tzinfo=UTC), '2026-12-24'),
    # Hong Kong closed (2026-12-25) while Shanghai trades: its last close, of the 24th, is booked at 15:40.
    ('HKG:00003', 'HKD', epoch(2026, 12, 24, 4, 8), datetime(2026, 12, 25, 7, 40, tzinfo=UTC), '2026-12-25'),
    # Shanghai's own close at 15:40.
    ('SHA:600001', 'CNY', epoch(2026, 11, 17, 7, 0), datetime(2026, 11, 17, 7, 40, tzinfo=UTC), DAY),
])
def test_a_final_close_is_booked_without_waiting(monkeypatch, ticker, currency, stamp, now, day):
    prices, not_final = fetched_with_waits(monkeypatch, ticker, currency, stamp, now, day)
    assert prices == {ticker: 10.5} and not_final == {}


def test_a_failed_download_is_not_a_wait(monkeypatch):
    def offline(url, **options):
        raise OSError('the network is switched off in this test')
    monkeypatch.setattr(snapshot.requests, 'get', offline)
    monkeypatch.setattr(snapshot, '_now', lambda: datetime(2026, 11, 17, 7, 40, tzinfo=UTC))
    not_final = {}
    prices, _ = snapshot.fetch_prices({'groups': {'E': {'positions': [position('HKG:00003')]}}}, {}, DAY, not_final)
    assert prices == {} and not_final == {}


def generated(missing_tickers, not_final, day=DAY):
    """The engine's exit text when ``missing_tickers`` have no price (``_generate`` saves nothing then)."""
    from types import SimpleNamespace
    holdings = {'date': day, 'groups': {'E': {'cash': 0, 'fund': 0, 'cost_basis': 0, 'positions': [
        position(ticker) for ticker in ['SHA:600001', *missing_tickers]]}}}
    quotes = ({'SHA:600001': 10.0}, {'SHA:600001': 'CNY'}, {'CNY': 1.0}, not_final)
    with pytest.raises(SystemExit) as stopped:
        snapshot._generate(SimpleNamespace(date=day, dry_run=True), {}, holdings, quotes)
    return str(stopped.value.code)


def test_only_closes_not_final_yet_missing_says_when_the_snapshot_will_be_made():
    text = generated(['HKG:00003'], {'HKG:00003': datetime(2026, 11, 17, 8, 10, tzinfo=UTC)})
    assert '等待港股收盘价确定，16:10 后记入' in text and 'HKG:00003' in text
    assert snapshot.MISSING_PRICE_MESSAGE not in text and '网络' not in text
    assert text.splitlines()[-1] == 'CLOSE_NOT_FINAL_UNTIL=2026-11-17T16:10:00+08:00 MARKETS=HK'


def test_a_new_york_close_settling_after_midnight_says_the_next_day():
    text = generated(['NASDAQ:EXMP'], {'NASDAQ:EXMP': datetime(2026, 11, 17, 21, 30, tzinfo=UTC)})
    assert '等待美股收盘价确定，次日 05:30 后记入' in text
    assert text.splitlines()[-1] == 'CLOSE_NOT_FINAL_UNTIL=2026-11-18T05:30:00+08:00 MARKETS=US'


def test_any_other_missing_price_is_the_usual_failure():
    text = generated(['HKG:00003', 'HKG:00005'], {'HKG:00003': datetime(2026, 11, 17, 8, 10, tzinfo=UTC)})
    assert snapshot.MISSING_PRICE_MESSAGE in text and 'CLOSE_NOT_FINAL_UNTIL' not in text
