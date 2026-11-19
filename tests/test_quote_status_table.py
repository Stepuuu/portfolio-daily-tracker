"""Every row of the quote-status table in ``core/market/quotes.py``, per market and phase.

Times are exchange-local (Shanghai for CN and HK, New York for US). Tuesday
17 November 2026 is an ordinary trading day everywhere; 24 December is a
Hong Kong half day (12:00, closing auction to 12:10), 27 November a New York
13:00 close, 26 November Thanksgiving, 1-7 October the mainland's National
Day closure; 2027 (CN) and 2028 (HK, US) have no holiday table.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from core.market.calendar import ZONES
from core.market.quotes import PREV_CLOSE, QuoteService
from core.market.today import build_today
from core.market.valuation import quote_estimate
from tests.fakes import FakeClock, ScriptedSource, build_ledger, fake_runtime, fx_rate, opening, position, quote

TICKERS = {'CN': 'SHA:600001', 'HK': 'HKG:00001', 'US': 'NASDAQ:EXAMPLE'}
VERIFIED = {'live', 'delayed', 'closed'}


def local(market, text):
    return datetime.fromisoformat(text).replace(tzinfo=ZONES[market])


def judge(market, now, stamped, **fields):
    ticker = TICKERS[market]
    clock = FakeClock()
    clock.moment = local(market, now)
    stamp = local(market, stamped) if stamped else None
    source = ScriptedSource({ticker: quote(ticker, '10', at=stamp, fetched_at=clock.now(), **fields)})
    return QuoteService([source], clock.session()).get([ticker])[ticker]


# (market, now, exchange time, status, calendar known)
ROWS = [
    # -- mainland: call auction 09:15-09:25(-09:30), 09:30-11:30, 13:00-15:00 with the closing call auction 14:57-15:00
    ('CN', '2026-11-17 08:00', '2026-11-16 15:00:02', 'closed', True),     # pre-open: Monday's close
    ('CN', '2026-11-17 08:00', '2026-11-16 14:56:00', 'stale', True),      # pre-open: before Monday's closing auction
    ('CN', '2026-11-17 09:20', '2026-11-17 09:19:58', 'live', True),       # opening call auction, indicative price
    ('CN', '2026-11-17 09:20', '2026-11-16 15:00:02', 'closed', True),     # opening call auction, still Monday's close
    ('CN', '2026-11-17 09:20', '2026-11-16 14:56:00', 'stale', True),
    ('CN', '2026-11-17 10:00', '2026-11-17 09:59:57', 'live', True),       # continuous trading
    ('CN', '2026-11-17 10:00', '2026-11-17 09:45:00', 'delayed', True),
    ('CN', '2026-11-17 10:00', '2026-11-17 09:39:59', 'stale', True),
    ('CN', '2026-11-17 10:00', '2026-11-16 15:00:02', 'stale', True),      # yesterday's close during the session
    ('CN', '2026-11-17 12:15', '2026-11-17 11:29:58', 'closed', True),     # lunch: the morning's last price
    ('CN', '2026-11-17 12:15', '2026-11-17 11:10:00', 'closed', True),     # lunch: 20 minutes before 11:30
    ('CN', '2026-11-17 12:15', '2026-11-17 11:09:59', 'stale', True),
    # Review round 4: a stamp more than a minute before 11:30 seen within 20 minutes of it is delayed, as a
    # delayed source (Yahoo, about 15 minutes) may still be catching up with the morning. Review round 5:
    # only then; seen later (from 11:48:59 for 11:28:59, at 11:50 for any), it is the morning's last price.
    ('CN', '2026-11-17 11:31', '2026-11-17 11:16:00', 'delayed', True),    # Yahoo's 15-minute delay
    ('CN', '2026-11-17 11:31', '2026-11-17 11:29:00', 'closed', True),
    ('CN', '2026-11-17 11:31', '2026-11-17 11:28:59', 'delayed', True),
    ('CN', '2026-11-17 11:35', '2026-11-17 11:28:59', 'delayed', True),
    ('CN', '2026-11-17 11:48:59', '2026-11-17 11:28:59', 'delayed', True),
    ('CN', '2026-11-17 11:49', '2026-11-17 11:28:59', 'closed', True),     # a thinly traded ETF's last fill
    ('CN', '2026-11-17 12:30', '2026-11-17 11:28:59', 'closed', True),
    ('CN', '2026-11-17 12:59', '2026-11-17 11:28:59', 'closed', True),
    ('CN', '2026-11-17 11:36', '2026-11-17 11:16:00', 'delayed', True),
    ('CN', '2026-11-17 11:37', '2026-11-17 11:16:00', 'closed', True),     # seen over 20 minutes after it
    ('CN', '2026-11-17 11:50', '2026-11-17 11:16:00', 'closed', True),
    ('CN', '2026-11-17 11:50', '2026-11-17 11:09:59', 'stale', True),
    ('CN', '2026-11-17 14:58', '2026-11-17 14:56:00', 'delayed', True),    # closing call auction
    ('CN', '2026-11-17 14:58', '2026-11-17 14:37:00', 'stale', True),
    ('CN', '2026-11-17 15:01', '2026-11-17 14:56:00', 'stale', True),      # mirror case: not the auction's print
    ('CN', '2026-11-17 15:01', '2026-11-17 14:59:59', 'stale', True),
    ('CN', '2026-11-17 15:01', '2026-11-17 15:00:03', 'closed', True),
    ('CN', '2026-11-17 15:30', '2026-11-17 14:45:00', 'stale', True),
    ('CN', '2026-11-17 20:00', '2026-11-16 15:00:02', 'stale', True),      # after close, a day behind
    ('CN', '2026-11-15 12:00', '2026-11-13 15:00:02', 'closed', True),     # Sunday: Friday's close
    ('CN', '2026-11-15 12:00', '2026-11-13 14:59:59', 'stale', True),
    ('CN', '2026-10-06 10:30', '2026-09-30 15:00:00', 'closed', True),     # National Day: the close before it
    ('CN', '2026-10-06 10:30', '2026-09-30 14:50:00', 'stale', True),
    ('CN', '2027-03-09 20:00', '2027-03-09 15:00:00', 'closed', False),    # no 2027 table: unverified close
    ('CN', '2027-03-09 20:00', '2027-03-09 14:56:00', 'stale', False),
    # -- Hong Kong: pre-opening 09:00-09:30, 09:30-12:00, 13:00-16:00, closing auction 16:00 to 16:08-16:10
    ('HK', '2026-11-17 08:30', '2026-11-16 16:08:30', 'closed', True),
    ('HK', '2026-11-17 08:30', '2026-11-16 15:59:00', 'stale', True),      # before Monday's closing auction print
    ('HK', '2026-11-17 09:25', '2026-11-17 09:24:50', 'live', True),       # pre-opening session
    ('HK', '2026-11-17 09:25', '2026-11-17 09:20:00', 'delayed', True),
    ('HK', '2026-11-17 09:25', '2026-11-17 09:00:30', 'stale', True),      # today's, but 24 minutes old
    ('HK', '2026-11-17 09:25', '2026-11-16 16:08:00', 'closed', True),
    ('HK', '2026-11-17 10:00', '2026-11-17 09:59:30', 'live', True),
    ('HK', '2026-11-17 10:00', '2026-11-17 09:45:00', 'delayed', True),
    ('HK', '2026-11-17 10:00', '2026-11-17 09:39:00', 'stale', True),
    ('HK', '2026-11-17 12:30', '2026-11-17 11:59:30', 'closed', True),     # lunch
    ('HK', '2026-11-17 12:30', '2026-11-17 11:40:00', 'closed', True),
    ('HK', '2026-11-17 12:30', '2026-11-17 11:39:59', 'stale', True),
    ('HK', '2026-11-17 12:05', '2026-11-17 11:50:00', 'delayed', True),    # review rounds 4 and 5, as above
    ('HK', '2026-11-17 12:05', '2026-11-17 11:59:00', 'closed', True),
    ('HK', '2026-11-17 12:05', '2026-11-17 11:58:59', 'delayed', True),
    ('HK', '2026-11-17 12:18:59', '2026-11-17 11:58:59', 'delayed', True),
    ('HK', '2026-11-17 12:19', '2026-11-17 11:58:59', 'closed', True),
    ('HK', '2026-11-17 12:30', '2026-11-17 11:58:59', 'closed', True),
    ('HK', '2026-11-17 12:20', '2026-11-17 11:50:00', 'closed', True),
    ('HK', '2026-11-17 16:05', '2026-11-17 16:04:50', 'live', True),       # closing auction: not the close yet
    ('HK', '2026-11-17 16:05', '2026-11-17 15:59:00', 'delayed', True),
    ('HK', '2026-11-17 16:05', '2026-11-17 15:44:00', 'stale', True),      # the reviewer's reproduction
    ('HK', '2026-11-17 16:05', '2026-11-16 16:08:30', 'stale', True),
    ('HK', '2026-11-17 16:11', '2026-11-17 16:08:30', 'closed', True),     # after the auction: its print
    ('HK', '2026-11-17 16:11', '2026-11-17 15:59:30', 'stale', True),      # before the auction
    ('HK', '2026-11-17 16:11', '2026-11-17 16:05:00', 'stale', True),      # during the auction, before its match
    ('HK', '2026-11-17 20:00', '2026-11-17 16:09:10', 'closed', True),
    ('HK', '2026-12-24 11:00', '2026-12-24 10:59:58', 'live', True),       # half day, morning
    ('HK', '2026-12-24 12:05', '2026-12-24 12:04:50', 'live', True),       # half day, closing auction
    ('HK', '2026-12-24 12:05', '2026-12-24 11:50:00', 'delayed', True),
    ('HK', '2026-12-24 12:05', '2026-12-24 11:44:00', 'stale', True),      # the reviewer's half-day reproduction
    ('HK', '2026-12-24 12:11', '2026-12-24 12:08:30', 'closed', True),
    ('HK', '2026-12-24 12:11', '2026-12-24 11:58:00', 'stale', True),
    ('HK', '2026-12-24 14:00', '2026-12-24 12:08:00', 'closed', True),     # half day, afternoon: no session
    ('HK', '2026-12-24 14:00', '2026-12-24 11:50:00', 'stale', True),
    ('HK', '2026-12-25 10:00', '2026-12-24 12:08:00', 'closed', True),     # Christmas: the half day's close
    ('HK', '2026-12-25 10:00', '2026-12-24 11:58:00', 'stale', True),
    ('HK', '2026-11-15 12:00', '2026-11-13 16:08:30', 'closed', True),     # Sunday
    ('HK', '2028-11-14 20:00', '2028-11-14 16:08:00', 'closed', False),    # no 2028 table
    ('HK', '2028-11-14 20:00', '2028-11-14 15:59:00', 'stale', False),
    # -- New York: 09:30-16:00 with the closing auction at 16:00 (13:00 on an early-close day)
    ('US', '2026-11-17 08:00', '2026-11-16 16:00:02', 'closed', True),
    ('US', '2026-11-17 08:00', '2026-11-16 15:59:00', 'stale', True),
    ('US', '2026-11-17 10:00', '2026-11-17 09:59:30', 'live', True),
    ('US', '2026-11-17 10:00', '2026-11-17 09:45:00', 'delayed', True),
    ('US', '2026-11-17 10:00', '2026-11-17 09:39:00', 'stale', True),
    ('US', '2026-11-17 10:00', '2026-11-16 16:00:02', 'stale', True),
    # Changed with the 2026-11-18 observations: these rows were observed at 16:02 / 13:02, before the close
    # settles (16:30 / 13:30), which US_OBSERVED below covers; here they are observed after it.
    ('US', '2026-11-17 16:31', '2026-11-17 15:40:00', 'stale', True),      # mirror case
    ('US', '2026-11-17 16:31', '2026-11-17 15:59:58', 'stale', True),      # the closing auction has not printed it
    ('US', '2026-11-17 16:31', '2026-11-17 16:00:02', 'closed', True),
    ('US', '2026-11-27 12:30', '2026-11-27 12:29:55', 'live', True),       # early close at 13:00
    ('US', '2026-11-27 13:31', '2026-11-27 12:40:00', 'stale', True),
    ('US', '2026-11-27 13:31', '2026-11-27 13:00:01', 'closed', True),
    ('US', '2026-11-26 12:00', '2026-11-25 16:00:01', 'closed', True),     # Thanksgiving
    ('US', '2026-11-26 12:00', '2026-11-25 15:50:00', 'stale', True),
    ('US', '2026-11-15 12:00', '2026-11-13 16:00:01', 'closed', True),     # Sunday
    ('US', '2028-11-14 20:00', '2028-11-14 16:00:05', 'closed', False),    # no 2028 table
    ('US', '2028-11-14 20:00', '2028-11-14 15:45:00', 'stale', False),
]


@pytest.mark.parametrize('market,now,stamped,status,known', ROWS,
                         ids=[f'{row[0]} {row[1]} q{row[2][5:]}' for row in ROWS])
def test_status_table(market, now, stamped, status, known):
    # Judged by the exchange time alone: a source that does not name a price the regular session's (Eastmoney).
    # Changed with the 2026-11-18 observations: a New York regular-session price is the close whatever its
    # stamp once the close has settled (US_OBSERVED), so the US rows say regular_session=False.
    result = judge(market, now, stamped, regular_session=False)
    assert result.status == status, result.reason
    assert result.price == Decimal('10')
    # A delayed quote is verified but says how far behind it is.
    assert (result.reason is None) == (status in ('live', 'closed') and known), result.reason
    assert (quote_estimate(result) is None) == (status in VERIFIED and known)
    assert result.calendar_confidence == ('calendar' if known else 'weekday_only')


def test_the_table_has_no_duplicate_rows():
    assert len({row[:3] for row in ROWS}) == len(ROWS)


def test_a_stale_reason_says_what_is_missing():
    auction = judge('HK', '2026-11-17 16:05', '2026-11-17 15:44:00')
    assert '收盘竞价' in auction.reason and '21' in auction.reason
    after = judge('CN', '2026-11-17 15:01', '2026-11-17 14:56:00')
    assert '14:56' in after.reason and '15:00' in after.reason
    hk_after = judge('HK', '2026-11-17 16:11', '2026-11-17 15:59:30')
    assert '16:08' in hk_after.reason
    opening = judge('HK', '2026-11-17 09:25', '2026-11-17 09:00:30')
    assert '24' in opening.reason and opening.session_date == date(2026, 11, 17)
    lunch = judge('CN', '2026-11-17 12:15', '2026-11-17 11:09:59')
    assert lunch.reason == ('行情停在当地时间 11:09，早于午间休市前 20 分钟，不能确认为上午的最后价格'
                            '（可能无成交、停牌或数据源中断）')


@pytest.mark.parametrize('market,now,stamped,status', [
    ('CN', '2026-11-17 10:00', '2026-11-17 09:59:58', 'prev_close'),     # untraded in today's session
    ('CN', '2026-11-17 08:00', '2026-11-16 15:00:00', 'prev_close'),     # untraded all Monday
    ('CN', '2026-11-17 10:00', '2026-11-13 15:00:00', 'stale'),          # stuck in an older session
    ('HK', '2026-11-17 16:05', '2026-11-17 15:44:00', 'prev_close'),
])
def test_previous_close_rows(market, now, stamped, status):
    assert judge(market, now, stamped, price_basis=PREV_CLOSE).status == status


def test_rows_without_a_usable_time_or_price():
    assert judge('CN', '2026-11-17 10:00', None).status == 'time_unknown'
    assert judge('CN', '2026-11-17 10:00', '2026-11-17 10:30:00').status == 'time_unknown'    # ahead of our clock
    assert judge('CN', '2026-11-17 10:00', '2026-11-17 10:01:30').status == 'live'            # within the tolerance
    ticker = TICKERS['CN']
    clock = FakeClock('2026-11-17 10:00')
    gone = QuoteService([ScriptedSource({})], clock.session()).get([ticker])[ticker]
    assert gone.status == 'unavailable' and gone.price is None


def _after_us_close(day, close, seconds):
    """``day`` ``close`` (HH:MM) plus ``seconds`` in New York, as the table's text."""
    moment = datetime.fromisoformat(f'{day} {close}') + timedelta(seconds=seconds)
    return moment.isoformat(sep=' ')


def _us_window_rows():
    # The bound comes from the one table in the calendar, so these rows follow it if it is changed.
    from core.market.calendar import CLOSE_PRINT_WINDOW
    bound = int(CLOSE_PRINT_WINDOW['US'][1].total_seconds())
    return [
        # (now, exchange time, the source says it is the regular session's price, status)
        ('2026-11-17 20:30', _after_us_close('2026-11-17', '16:00', 0), False, 'closed'),
        ('2026-11-17 20:30', _after_us_close('2026-11-17', '16:00', bound), False, 'closed'),
        ('2026-11-17 20:30', _after_us_close('2026-11-17', '16:00', bound + 1), False, 'stale'),
        ('2026-11-17 20:30', '2026-11-17 20:00:00', False, 'stale'),     # the reviewer's reproduction
        ('2026-11-18 08:00', '2026-11-17 19:59:00', False, 'stale'),     # still not the close the next morning
        ('2026-11-17 20:30', '2026-11-17 20:00:00', True, 'closed'),     # Yahoo's regularMarketPrice / Time
        ('2026-11-27 15:00', _after_us_close('2026-11-27', '13:00', bound), False, 'closed'),   # early close
        ('2026-11-27 15:00', '2026-11-27 14:00:00', False, 'stale'),
    ]


@pytest.mark.parametrize('row', range(8))
def test_a_us_price_stamped_after_the_close_print_window_is_not_the_close(row):
    now, stamped, regular, status = _us_window_rows()[row]
    result = judge('US', now, stamped, regular_session=regular)
    assert result.status == status, (now, stamped, regular, result.reason)
    if status == 'stale':
        assert '盘后' in result.reason and quote_estimate(result) is not None
    else:
        assert result.reason is None and quote_estimate(result) is None


# New York's 2026-11-18 session as two providers showed it (Eastmoney ulist f2/f124, which does not name the
# regular session; Yahoo chart regularMarketPrice/Time, which does), probed 12 and 100 minutes after the close.
# 12 minutes after it the official closes were not out yet (Eastmoney AAPL 352.27, KO 98.08, SIRI 32.21; Yahoo
# already 352.45, but not KO 98.04 / SIRI 32.22); 100 minutes after it both showed the official closes (AAPL
# 352.45, KO 98.09, SIRI 32.22), Eastmoney stamping every stock 16:00:00 during after-hours trading.
EARLY, SETTLED = '2026-11-18 16:12', '2026-11-18 17:42'
US_OBSERVED = [
    # (observed at, ticker, exchange time, source names the regular session, status, verified)
    (EARLY, 'NASDAQ:AAPL', '2026-11-18 16:00:00', False, 'closed', False),      # Eastmoney: not final yet
    (EARLY, 'NYSE:KO', '2026-11-18 15:59:59', False, 'closed', False),          # Eastmoney, a second before the bell
    (EARLY, 'NASDAQ:AAPL', '2026-11-18 16:00:01', True, 'closed', False),       # Yahoo: not final either
    (EARLY, 'NYSE:BRK.B', '2026-11-18 15:59:59', True, 'closed', False),        # Yahoo
    (EARLY, 'IDX:SPX', '2026-11-18 15:59:46', False, 'closed', False),          # Eastmoney's S&P 500
    (EARLY, 'NASDAQ:AAPL', '2026-11-18 15:30:00', False, 'stale', False),       # stopped long before the bell
    (EARLY, 'NASDAQ:AAPL', '2026-11-18 16:11:00', False, 'stale', False),       # an after-hours trade
    ('2026-11-18 16:29:59', 'NASDAQ:AAPL', '2026-11-18 16:00:00', False, 'closed', False),
    ('2026-11-18 16:30', 'NASDAQ:AAPL', '2026-11-18 16:00:00', False, 'closed', True),   # settled
    (SETTLED, 'NASDAQ:AAPL', '2026-11-18 16:00:00', False, 'closed', True),     # Eastmoney, official close
    (SETTLED, 'NYSE:BRK.B', '2026-11-18 16:00:02', True, 'closed', True),       # Yahoo
    (SETTLED, 'NYSE:F', '2026-11-18 16:00:39', True, 'closed', True),           # Yahoo
    (SETTLED, 'NYSE:F', '2026-11-18 15:59:59', True, 'closed', True),           # Yahoo's regular price, any stamp
    (SETTLED, 'IDX:IXIC', '2026-11-18 16:00:00', False, 'closed', True),        # Eastmoney's "NDX"
    (SETTLED, 'IDX:SPX', '2026-11-18 15:59:46', False, 'stale', False),         # Eastmoney's S&P 500 and Dow
    (SETTLED, 'NASDAQ:AAPL', '2026-11-18 16:10:00', False, 'closed', True),     # the window's last second
    (SETTLED, 'NASDAQ:AAPL', '2026-11-18 16:10:01', False, 'stale', False),     # may be an after-hours trade
    (SETTLED, 'NASDAQ:AAPL', '2026-11-18 17:41:00', True, 'closed', True),      # Yahoo: no upper bound
    # Review round 4: the regular-session flag lifts only the upper bound. A stamp more than a minute before
    # the bell is the last trade before the close, not verified as the close (at 16:40, AAPL stamped 14:42).
    ('2026-11-18 16:40', 'NASDAQ:AAPL', '2026-11-18 14:42:00', True, 'stale', False),
    ('2026-11-18 16:40', 'NASDAQ:AAPL', '2026-11-18 15:58:59', True, 'stale', False),
    ('2026-11-18 16:40', 'NASDAQ:AAPL', '2026-11-18 15:59:00', True, 'closed', True),
    (EARLY, 'NASDAQ:AAPL', '2026-11-18 14:42:00', True, 'stale', False),
    # an early-close day: the close at 13:00 settles at 13:30
    ('2026-11-27 13:12', 'NASDAQ:AAPL', '2026-11-27 13:00:00', False, 'closed', False),
    ('2026-11-27 13:12', 'NASDAQ:AAPL', '2026-11-27 13:00:01', True, 'closed', False),
    ('2026-11-27 14:42', 'NASDAQ:AAPL', '2026-11-27 13:00:00', False, 'closed', True),
    ('2026-11-27 14:42', 'IDX:SPX', '2026-11-27 12:59:46', False, 'stale', False),
    ('2026-11-27 14:42', 'NASDAQ:AAPL', '2026-11-27 13:10:01', False, 'stale', False),
]


def observe(now, ticker, stamped, regular):
    clock = FakeClock()
    clock.moment = local('US', now)
    source = ScriptedSource({ticker: quote(ticker, '10', at=local('US', stamped), fetched_at=clock.now(),
                                           regular_session=regular)})
    return QuoteService([source], clock.session()).get([ticker])[ticker]


@pytest.mark.parametrize('now,ticker,stamped,regular,status,verified', US_OBSERVED,
                         ids=[f'{row[0][5:]} {row[1]} q{row[2][11:]}{" regular" if row[3] else ""}'
                              for row in US_OBSERVED])
def test_new_york_closes_as_observed_on_2026_11_18(now, ticker, stamped, regular, status, verified):
    result = observe(now, ticker, stamped, regular)
    assert result.status == status, result.reason
    assert (quote_estimate(result) is None) == verified, result.reason
    if status == 'closed' and not verified:
        assert '尚未最终确定' in result.reason and quote_estimate(result)[1] == result.reason


def test_a_regular_session_price_from_before_the_close_says_it_is_the_last_trade_before_it():
    result = observe('2026-11-18 16:40', 'NASDAQ:AAPL', '2026-11-18 14:42:00', True)
    assert result.reason.startswith('收盘前的最后一笔成交，未能核实为收盘价'), result.reason
    assert quote_estimate(result) is not None


def test_the_new_york_rows_have_no_duplicates():
    assert len({row[:4] for row in US_OBSERVED}) == len(US_OBSERVED)


@pytest.mark.parametrize('now,quality', [(EARLY, 'estimated'), (SETTLED, 'complete')])
def test_a_new_york_close_makes_the_totals_complete_only_once_it_has_settled(tmp_path, now, quality):
    ticker = 'NASDAQ:AAPL'
    ledger = build_ledger(tmp_path, [opening('Main', cash={'CNY': '1000'}, date='2026-11-13',
                                             positions=[position(ticker, '10', '300')])])
    clock = FakeClock()
    clock.moment = local('US', now)
    source = ScriptedSource({ticker: quote(ticker, '352.45', prev_close='350', at=local('US', '2026-11-18 16:00:00'),
                                           fetched_at=clock.now(), regular_session=False)},
                            {'USD': fx_rate('USD', '7.1', at=clock.now()), 'HKD': fx_rate('HKD', '0.9', at=clock.now())})
    today = build_today(ledger, fake_runtime(clock, source))
    row = today['accounts'][0]['positions'][0]
    assert row['quote']['status'] == 'closed'
    assert row['estimated'] is (quality == 'estimated')
    assert today['totals']['quality'] == quality


def test_the_quotes_settle_delay_and_the_backtests_bar_finality_are_one_constant():
    from backtesting.data import store
    from core.market.calendar import CLOSE_PRINT_WINDOW, CLOSE_SETTLE, SessionClock
    assert store.PUBLICATION_MARGIN is CLOSE_SETTLE and CLOSE_PRINT_WINDOW['US'][2] is CLOSE_SETTLE
    clock = SessionClock()
    for day in (date(2026, 11, 18), date(2026, 11, 27)):      # a full day and an early close
        assert store.bar_final_at(day, 'US') == clock.close_settled('US', day)


def test_hong_kong_closes_stamped_by_both_providers_are_verified_after_the_auction():
    # Observed on 2026-11-19 01:53 Beijing time: Eastmoney and Yahoo both stamp every Hong
    # Kong close at 16:08:02-16:08:03, whatever second the random close fell on.
    for stamped in ('2026-11-18 16:08:02', '2026-11-18 16:08:03', '2026-11-18 16:08:28'):
        assert judge('HK', '2026-11-19 01:53', stamped).status == 'closed', stamped
        assert judge('HK', '2026-11-18 16:09', stamped).status in ('live', 'delayed'), 'not the close during the auction'


@pytest.mark.parametrize('now,stamped', [('2026-11-17 16:05', '2026-11-17 15:44:00'),
                                         ('2026-12-24 12:05', '2026-12-24 11:44:00')])
def test_an_old_quote_in_the_closing_auction_makes_the_totals_estimated(tmp_path, now, stamped):
    ticker = TICKERS['HK']
    day = now[:10]
    ledger = build_ledger(tmp_path, [opening('Main', cash={'CNY': '1000'}, date='2026-11-13',
                                             positions=[position(ticker, '100', '4')])])
    clock = FakeClock(now)
    source = ScriptedSource({ticker: quote(ticker, '5', prev_close='4.8', at=local('HK', stamped),
                                           fetched_at=clock.now())},
                            {'USD': fx_rate('USD', '7', at=clock.now()), 'HKD': fx_rate('HKD', '0.9', at=clock.now())})
    today = build_today(ledger, fake_runtime(clock, source))
    row = today['accounts'][0]['positions'][0]
    assert row['quote']['status'] == 'stale' and row['quote']['reason']
    assert row['estimated'] is True
    assert today['totals']['quality'] == 'estimated', day


def today_at(tmp_path, market, now, stamped, regular=True):
    """Today's row and totals for one holding last priced at ``stamped``, seen at ``now`` (exchange-local)."""
    ticker = TICKERS[market]
    ledger = build_ledger(tmp_path, [opening('Main', cash={'CNY': '1000'}, date='2026-11-13',
                                             positions=[position(ticker, '100', '4')])])
    clock = FakeClock()
    clock.moment = local(market, now)
    source = ScriptedSource({ticker: quote(ticker, '5', prev_close='4.8', at=local(market, stamped),
                                           fetched_at=clock.now(), regular_session=regular)},
                            {'USD': fx_rate('USD', '7', at=clock.now()), 'HKD': fx_rate('HKD', '0.9', at=clock.now())})
    today = build_today(ledger, fake_runtime(clock, source))
    return today['accounts'][0]['positions'][0], today['totals']


@pytest.mark.parametrize('market,now,stamped,status', [
    # cr_v5bs B1: a thinly traded ETF's last morning fill at 11:28:59 was stale with estimated totals
    # from 11:49 to 12:59 (closed at 40868ac). Seen within 20 minutes of it, as a delayed source may still
    # be catching up, it is delayed (verified as well).
    ('CN', '2026-11-17 11:49', '2026-11-17 11:28:59', 'closed'),
    ('CN', '2026-11-17 12:30', '2026-11-17 11:28:59', 'closed'),
    ('CN', '2026-11-17 12:59', '2026-11-17 11:28:59', 'closed'),
    ('CN', '2026-11-17 11:35', '2026-11-17 11:28:59', 'delayed'),
    ('CN', '2026-11-17 11:31', '2026-11-17 11:16:00', 'delayed'),    # Yahoo's 15-minute delay
    ('CN', '2026-11-17 11:50', '2026-11-17 11:16:00', 'closed'),
    ('HK', '2026-11-17 12:19', '2026-11-17 11:58:59', 'closed'),
    ('HK', '2026-11-17 12:45', '2026-11-17 11:58:59', 'closed'),
    ('HK', '2026-11-17 12:05', '2026-11-17 11:58:59', 'delayed'),
])
def test_a_thinly_traded_holdings_last_morning_fill_leaves_the_midday_totals_complete(tmp_path, market, now,
                                                                                     stamped, status):
    row, totals = today_at(tmp_path, market, now, stamped)
    assert row['quote']['status'] == status, row['quote']['reason']
    assert row['estimated'] is False
    assert totals['quality'] == 'complete', totals['estimated']


def test_a_morning_price_seen_while_a_delayed_source_may_catch_up_is_not_promoted_by_the_clock():
    # Seen at 11:48:58, 19 min 59 s after its stamp: delayed. Read from the cache (5 s) at 11:49:01 it is still
    # that observation; read again once the cache has expired (a new observation), the morning's last price.
    ticker = TICKERS['CN']
    clock = FakeClock()
    clock.moment = local('CN', '2026-11-17 11:48:58')
    source = ScriptedSource({ticker: quote(ticker, '10', at=local('CN', '2026-11-17 11:28:59'),
                                           fetched_at=clock.now())})
    service = QuoteService([source], clock.session())
    assert service.get([ticker])[ticker].status == 'delayed'
    clock.moment = local('CN', '2026-11-17 11:49:01')
    assert service.get([ticker])[ticker].status == 'delayed' and len(source.calls) == 1
    clock.moment = local('CN', '2026-11-17 11:49:04')
    assert service.get([ticker])[ticker].status == 'closed' and len(source.calls) == 2


@pytest.mark.parametrize('market,now,stamped,regular,reason', [
    # The afternoon's analogue, checked in review round 5 (unchanged): a source stamping a thinly traded
    # holding's last fill, minutes before the bell, keeps it stale all evening, so the row and the totals
    # are estimated (the snapshot books that price marked 含估算 after its wait). A provider that restamps
    # every close after the bell (Eastmoney 15:34 / 16:11, both 16:08:02 in Hong Kong, Eastmoney 16:00:00
    # in New York) makes it a verified close (the table above).
    ('CN', '2026-11-17 20:00', '2026-11-17 14:52:00', True,
     '行情停在当地时间 14:52，早于收盘竞价的成交（当地时间 15:00），不能确认为收盘价（可能收盘价尚未发布、无成交、停牌或数据源中断）'),
    ('HK', '2026-11-17 20:00', '2026-11-17 15:52:00', True,
     '行情停在当地时间 15:52，早于收盘竞价的成交（当地时间 16:08），不能确认为收盘价（可能收盘价尚未发布、无成交、停牌或数据源中断）'),
    ('US', '2026-11-17 20:00', '2026-11-17 15:52:10', True,
     '收盘前的最后一笔成交，未能核实为收盘价（行情时间为当地 15:52:10，收盘 16:00）'),
    ('US', '2026-11-17 20:00', '2026-11-17 15:52:10', False,
     '行情停在当地时间 15:52，早于收盘竞价的成交（当地时间 16:00），不能确认为收盘价（可能收盘价尚未发布、无成交、停牌或数据源中断）'),
])
def test_a_thinly_traded_holdings_last_fill_before_the_bell_is_stale_all_evening(tmp_path, market, now, stamped,
                                                                                 regular, reason):
    row, totals = today_at(tmp_path, market, now, stamped, regular)
    assert (row['quote']['status'], row['quote']['reason']) == ('stale', reason)
    assert row['estimated'] is True
    assert totals['quality'] == 'estimated'
