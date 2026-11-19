"""QuoteService: caching, fallthrough, and a status that never flatters an old price."""
import asyncio
from datetime import date, timedelta
from decimal import Decimal

import pytest

from core.market.quotes import PREV_CLOSE, QuoteService, build_quote, derive_fx_status
from tests.fakes import FakeClock, ScriptedSource, fx_rate, quote, shanghai

A, B, HK, US = 'SHA:600001', 'SHE:000002', 'HKG:00001', 'NASDAQ:EXAMPLE'


def service(clock, *sources, **options):
    return QuoteService(list(sources), clock.session(), **options)


def live_source(clock, *tickers, name='scripted'):
    return ScriptedSource({ticker: quote(ticker, '10.50', prev_close='10', at=clock.now() - timedelta(seconds=3),
                                         source=name) for ticker in tickers}, name=name)


# -- batching and the cache ---------------------------------------------------

def test_one_batched_call_and_decimal_prices():
    clock = FakeClock('2026-11-17 10:00')
    source = live_source(clock, A, B)
    quotes = service(clock, source).get([A, B, A, ' ' + B.lower()])
    assert source.calls == [(A, B)]
    assert list(quotes) == [A, B]
    assert quotes[A].price == Decimal('10.50') and isinstance(quotes[A].price, Decimal)
    assert (quotes[A].status, quotes[A].source, quotes[A].reason) == ('live', 'scripted', None)


def test_cache_is_reused_within_the_ttl_and_refetched_after_it():
    clock = FakeClock('2026-11-17 10:00')
    source = live_source(clock, A)
    quotes = service(clock, source, ttl_seconds=5)
    quotes.get([A])
    clock.advance(seconds=4)
    quotes.get([A])
    assert len(source.calls) == 1
    clock.advance(seconds=1)
    quotes.get([A])
    assert len(source.calls) == 2


def test_refresh_bypasses_the_cache_and_only_missing_tickers_are_fetched():
    clock = FakeClock('2026-11-17 10:00')
    source = live_source(clock, A, B)
    quotes = service(clock, source)
    quotes.get([A])
    quotes.get([A, B])
    assert source.calls == [(A,), (B,)]
    quotes.get([A], refresh=True)
    assert source.calls[-1] == (A,)


def test_hong_kong_spellings_of_one_code_share_one_request_and_one_cache_entry():
    clock = FakeClock('2026-11-17 10:00')
    source = live_source(clock, 'HKG:700')
    quotes = service(clock, source)
    result = quotes.get(['HKG:700', 'HKG:00700', 'hkg:0700'])
    assert source.calls == [('HKG:700',)]                  # asked once, in the first spelling requested
    assert list(result) == ['HKG:700', 'HKG:00700', 'HKG:0700']
    assert all(q.ticker == ticker and q.price == Decimal('10.50') and q.status == 'live' for ticker, q in result.items())
    assert quotes.get(['HKG:00700'])['HKG:00700'].price == Decimal('10.50')
    assert len(source.calls) == 1                          # the cache knows it is the same instrument


def test_a_ttl_of_zero_disables_the_cache():
    clock = FakeClock('2026-11-17 10:00')
    source = live_source(clock, A)
    quotes = service(clock, source, ttl_seconds=0, fx_ttl_seconds=0)
    assert quotes.get([A])[A].price == Decimal('10.50')
    source.quotes[A] = quote(A, '10.70', at=clock.now())
    assert quotes.get([A])[A].price == Decimal('10.70')
    assert len(source.calls) == 2
    quotes.fx(), quotes.fx()
    assert len(source.fx_calls) == 2


def test_a_cached_quote_is_never_still_live_when_its_exchange_time_has_aged():
    clock = FakeClock('2026-11-17 10:00')
    source = live_source(clock, A)
    quotes = service(clock, source, ttl_seconds=3600)
    assert quotes.get([A])[A].status == 'live'
    clock.advance(minutes=5)
    aged = quotes.get([A])[A]
    assert len(source.calls) == 1            # served from the cache ...
    assert aged.status == 'delayed'          # ... but not as live
    assert '5 分钟' in aged.reason
    clock.advance(minutes=30)
    assert quotes.get([A])[A].status == 'stale'


def test_an_outage_after_a_good_quote_is_unavailable_not_the_old_price():
    clock = FakeClock('2026-11-17 10:00')
    source = live_source(clock, A)
    quotes = service(clock, source)
    assert quotes.get([A])[A].price == Decimal('10.50')
    source.down = '请求失败（ConnectTimeout）'
    clock.advance(seconds=6)
    after = quotes.get([A])[A]
    assert (after.price, after.status) == (None, 'unavailable')
    assert 'ConnectTimeout' in after.reason


# -- fallthrough --------------------------------------------------------------

def test_each_ticker_falls_through_to_the_next_source_that_can_price_it():
    clock = FakeClock('2026-11-17 10:00')
    first, second = live_source(clock, A, name='first'), live_source(clock, A, B, name='second')
    quotes = service(clock, first, second).get([A, B])
    assert (quotes[A].source, quotes[B].source) == ('first', 'second')
    assert second.calls == [(B,)]            # not asked again for what the first source priced


def test_an_unusable_answer_from_the_first_source_falls_through():
    clock = FakeClock('2026-11-17 10:00')
    first = ScriptedSource({A: quote(A, '0', at=clock.now(), source='first')}, name='first')
    second = live_source(clock, A, name='second')
    assert service(clock, first, second).get([A])[A].source == 'second'


def test_a_stale_primary_price_does_not_keep_the_fallbacks_verified_close_out():
    # The reviewer's reproduction: at 21:00 the primary's 19 stamped 14:56 ended the
    # fallback before the second source's 15:00 close of 20 was asked for.
    clock = FakeClock('2026-11-17 21:00')
    first = ScriptedSource({A: quote(A, '19', prev_close='18', at=shanghai('2026-11-17 14:56:00'), source='first')},
                           name='first')
    second = ScriptedSource({A: quote(A, '20', prev_close='18', at=shanghai('2026-11-17 15:00:02'), source='second')},
                            name='second')
    result = service(clock, first, second).get([A])[A]
    assert (result.price, result.source, result.status, result.reason) == (Decimal('20'), 'second', 'closed', None)
    assert result.quote_time == shanghai('2026-11-17 15:00:02')
    assert second.calls == [(A,)]


def test_a_verified_price_ends_the_fallback():
    clock = FakeClock('2026-11-17 21:00')
    first = ScriptedSource({A: quote(A, '20', at=shanghai('2026-11-17 15:00:02'), source='first')}, name='first')
    second = ScriptedSource({A: quote(A, '21', at=shanghai('2026-11-17 15:00:03'), source='second')}, name='second')
    result = service(clock, first, second).get([A])[A]
    assert (result.source, result.price) == ('first', Decimal('20')) and second.calls == []


def test_without_a_verified_candidate_the_most_recent_one_is_kept_with_its_own_source_and_time():
    clock = FakeClock('2026-11-17 21:00')
    first = ScriptedSource({A: quote(A, '18', at=shanghai('2026-11-17 14:40:00'), source='first')}, name='first')
    second = ScriptedSource({A: quote(A, '19', at=shanghai('2026-11-17 14:56:00'), source='second')}, name='second')
    result = service(clock, first, second).get([A])[A]
    assert (result.price, result.source, result.status) == (Decimal('19'), 'second', 'stale')
    assert result.quote_time == shanghai('2026-11-17 14:56:00') and '14:56' in result.reason


def test_an_extended_hours_us_price_gives_way_to_the_regular_close_of_the_fallback():
    from datetime import datetime
    from core.market.calendar import ZONES

    def new_york(text):
        return datetime.fromisoformat(text).replace(tzinfo=ZONES['US'])
    clock = FakeClock('2026-11-18 09:30')            # 20:30 in New York on the 17th
    first = ScriptedSource({US: quote(US, '101', at=new_york('2026-11-17 19:58:00'), source='eastmoney',
                                      regular_session=False)}, name='eastmoney')
    second = ScriptedSource({US: quote(US, '100', at=new_york('2026-11-17 16:00:02'), source='yahoo',
                                       regular_session=True)}, name='yahoo')
    result = service(clock, first, second).get([US])[US]
    assert (result.price, result.source, result.status) == (Decimal('100'), 'yahoo', 'closed')


def _new_york(text):
    from datetime import datetime
    from core.market.calendar import ZONES
    return datetime.fromisoformat(text).replace(tzinfo=ZONES['US'])


def test_once_settled_yahoos_regular_close_wins_over_an_eastmoney_index_stamped_before_the_bell():
    # 2026-11-18, 100 minutes after the close: Eastmoney still stamped the S&P 500 15:59:46.
    from core.market.valuation import quote_estimate
    clock = FakeClock()
    clock.moment = _new_york('2026-11-18 17:42')
    first = ScriptedSource({'IDX:SPX': quote('IDX:SPX', '6800', at=_new_york('2026-11-18 15:59:46'), source='eastmoney',
                                             fetched_at=clock.now(), regular_session=False)}, name='eastmoney')
    second = ScriptedSource({'IDX:SPX': quote('IDX:SPX', '6801', at=_new_york('2026-11-18 16:00:00'), source='yahoo',
                                              fetched_at=clock.now(), regular_session=True)}, name='yahoo')
    result = service(clock, first, second).get(['IDX:SPX'])['IDX:SPX']
    assert (result.price, result.source, result.status) == (Decimal('6801'), 'yahoo', 'closed')
    assert quote_estimate(result) is None


def test_before_the_close_settles_every_source_is_asked_and_the_answer_stays_estimated():
    # 2026-11-18, 12 minutes after the close: neither provider had the official close yet.
    from core.market.valuation import quote_estimate
    clock = FakeClock()
    clock.moment = _new_york('2026-11-18 16:12')
    first = ScriptedSource({US: quote(US, '352.27', at=_new_york('2026-11-18 16:00:00'), source='eastmoney',
                                      fetched_at=clock.now(), regular_session=False)}, name='eastmoney')
    second = ScriptedSource({US: quote(US, '352.45', at=_new_york('2026-11-18 16:00:01'), source='yahoo',
                                       fetched_at=clock.now(), regular_session=True)}, name='yahoo')
    result = service(clock, first, second).get([US])[US]
    assert first.calls and second.calls, 'a close that is not final yet does not end the fallback'
    assert (result.source, result.status) == ('yahoo', 'closed') and quote_estimate(result) is not None


@pytest.mark.parametrize('ticker,observed,stamp,after,final', [
    # New York: seen at 05:29:58 Beijing (16:29:58 there), two seconds before the close settles at 16:30
    (US, '2026-11-18 05:29:58', '2026-11-18 05:00:00', '2026-11-18 05:30:01', '2026-11-18 05:00:00'),
    # Hong Kong: seen during the closing auction, which ends at 16:10
    (HK, '2026-11-17 16:09:58', '2026-11-17 16:08:30', '2026-11-17 16:10:01', '2026-11-17 16:08:30'),
    # the mainland: seen during the closing call auction, which ends at 15:00
    (A, '2026-11-17 14:59:58', '2026-11-17 14:59:57', '2026-11-17 15:00:01', '2026-11-17 15:00:00'),
])
def test_a_price_seen_before_the_close_is_final_is_never_promoted_by_the_clock(ticker, observed, stamp, after, final):
    # Review round 4: the cache verified the 05:29:58 price $352.27 at 05:30:01 without fetching the final
    # $352.45. Only a quote observed once the close is final (SessionClock.close_settled) is a verified close.
    from core.market.valuation import quote_estimate
    clock = FakeClock(observed)
    source = ScriptedSource({ticker: quote(ticker, '352.27', at=shanghai(stamp), fetched_at=clock.now())})
    prices = service(clock, source)            # the quote cache keeps an answer for 5 seconds
    first = prices.get([ticker])[ticker]
    assert quote_estimate(first) is not None or first.status in ('live', 'delayed'), first
    source.quotes[ticker] = quote(ticker, '352.45', at=shanghai(final), fetched_at=shanghai(after))
    clock.moment = shanghai(after)
    later = prices.get([ticker])[ticker]
    assert len(source.calls) == 2, 'a close seen before it was final is fetched again once it is'
    assert (later.price, later.status, quote_estimate(later)) == (Decimal('352.45'), 'closed', None)
    # The source cannot be reached: the old price is never promoted to the verified close.
    clock.moment = shanghai(observed)
    stuck = service(clock, ScriptedSource({ticker: quote(ticker, '352.27', at=shanghai(stamp),
                                                         fetched_at=clock.now())}))
    stuck.get([ticker])
    stuck.sources[0].down = '数据源维护'
    clock.moment = shanghai(after)
    answer = stuck.get([ticker])[ticker]
    assert not (answer.status == 'closed' and quote_estimate(answer) is None), answer


def test_unpriced_everywhere_is_unavailable_with_every_reason():
    clock = FakeClock('2026-11-17 10:00')
    first = ScriptedSource({A: quote(A, None, prev_close='9.90', name='示例', reason='停牌', source='first')}, name='first')
    second = ScriptedSource(name='second')
    result = service(clock, first, second).get([A])[A]
    assert (result.price, result.status, result.currency) == (None, 'unavailable', 'CNY')
    assert 'first：停牌' in result.reason and 'second：未返回该代码' in result.reason
    assert (result.name, result.prev_close) == ('示例', Decimal('9.90'))   # what was known is kept


@pytest.mark.parametrize('price', [None, 0, '0', '-1', '-', '', 'abc', float('nan'), float('inf'), True])
def test_missing_zero_or_negative_prices_are_unavailable(price):
    clock = FakeClock('2026-11-17 10:00')
    source = ScriptedSource({A: build_quote(A, 'scripted', clock.now(), price=price, quote_time=clock.now())})
    result = service(clock, source).get([A])[A]
    assert result.price is None and result.status == 'unavailable' and result.reason


@pytest.mark.parametrize('price', ['1e100', '1E+9', '123456789.5', '1e-50', '0.00001'])
def test_prices_of_an_unsupported_magnitude_are_unavailable(price):
    clock = FakeClock('2026-11-17 10:00')
    good = quote(A, '10', at=clock.now())
    # Built directly, so only QuoteService's own checks stand between it and a caller.
    forged = good.__class__(**{**good.__dict__, 'price': Decimal(price), 'status': 'live'})
    for candidate in (build_quote(A, 'scripted', clock.now(), price=price, quote_time=clock.now()), forged):
        result = service(clock, ScriptedSource({A: candidate})).get([A])[A]
        assert (result.price, result.status) == (None, 'unavailable') and '范围' in result.reason


def test_other_fields_of_an_unsupported_magnitude_are_dropped_not_kept():
    clock = FakeClock('2026-11-17 10:00')
    built = build_quote(A, 'scripted', clock.now(), price='10', prev_close='1e100', open='1e-60', high='1e300',
                        low='-5', volume='1e40', quote_time=clock.now())
    assert built.price == Decimal('10')
    assert (built.prev_close, built.open, built.high, built.low, built.volume) == (None,) * 5
    assert build_quote(A, 'scripted', clock.now(), price='99999999', volume='123456').volume == Decimal('123456')


@pytest.mark.parametrize('rate', ['1e100', '1e-30', '100000'])
def test_fx_rates_of_an_unsupported_magnitude_are_unavailable(rate):
    clock = FakeClock('2026-11-17 10:00')
    source = ScriptedSource(rates={'USD': fx_rate('USD', rate, at=clock.now()), 'HKD': fx_rate('HKD', '0.9', at=clock.now())})
    rates = service(clock, source).fx()
    assert (rates['USD'].rate, rates['USD'].status) == (None, 'unavailable') and '范围' in rates['USD'].reason
    assert rates['HKD'].rate == Decimal('0.9')


def test_a_source_that_raises_never_escapes_get():
    class Broken:
        name = 'broken'

        def fetch(self, tickers):
            raise RuntimeError('boom')

    clock = FakeClock('2026-11-17 10:00')
    result = service(clock, Broken()).get([A])[A]
    # Changed by the untrusted-text review: the reason named the exception's
    # class ('RuntimeError'). A class name is not ours to quote; the reason is
    # one of core.provider_errors' fixed texts.
    assert result.status == 'unavailable' and result.reason == 'broken：数据源出错'
    assert service(clock, Broken(), live_source(clock, A, name='second')).get([A])[A].source == 'second'


def test_a_source_cannot_smuggle_in_a_bad_quote():
    clock = FakeClock('2026-11-17 10:00')
    good = quote(A, '10', at=clock.now())
    forged = good.__class__(**{**good.__dict__, 'price': Decimal('-5'), 'status': 'live'})
    wrong_ticker = quote(B, '10', at=clock.now())
    quotes = service(clock, ScriptedSource({A: forged, HK: wrong_ticker})).get([A, HK])
    assert quotes[A].status == 'unavailable' and quotes[A].price is None
    assert quotes[HK].status == 'unavailable'


def test_bare_codes_are_unavailable_and_never_sent_to_a_source():
    clock = FakeClock('2026-11-17 10:00')
    source = live_source(clock, A)
    result = service(clock, source).get(['000001'])['000001']
    assert result.status == 'unavailable' and source.calls == []


def test_an_equity_ticker_in_an_index_range_is_unavailable_and_never_sent_to_a_source():
    clock = FakeClock('2026-11-17 10:00')
    # A provider asked for this code would answer with the index.
    source = ScriptedSource({'SHA:000001': quote('SHA:000001', '3595.22', prev_close='3602.57', at=clock.now())})
    result = service(clock, source).get(['SHA:000001'])['SHA:000001']
    assert (result.price, result.status, result.currency) == (None, 'unavailable', 'CNY')
    assert '指数' in result.reason and '核对' in result.reason
    assert source.calls == []


def test_a_dead_source_is_left_alone_for_a_while_then_retried():
    clock = FakeClock('2026-11-17 10:00')
    first, second = live_source(clock, A, name='first'), live_source(clock, A, name='second')
    first.down = '请求失败（ConnectError）'
    quotes = service(clock, first, second, ttl_seconds=5)
    assert quotes.get([A])[A].source == 'second'
    clock.advance(seconds=10)
    assert quotes.get([A])[A].source == 'second'
    assert len(first.calls) == 1             # skipped while backing off
    quotes.get([A], refresh=True)
    assert len(first.calls) == 2             # an explicit refresh asks again
    first.down = None
    first.quotes[A] = quote(A, '10.60', at=clock.now() + timedelta(minutes=2), source='first')
    clock.advance(minutes=2)
    assert quotes.get([A])[A].source == 'first'


def test_async_wrapper_returns_the_same_quotes():
    clock = FakeClock('2026-11-17 10:00')
    quotes = service(clock, live_source(clock, A))
    assert asyncio.run(quotes.aget([A]))[A] == quotes.get([A])[A]


# -- status from the exchange time and the session clock ----------------------

def status_at(now, ticker, exchange_time, **fields):
    clock = FakeClock(now)
    at = shanghai(exchange_time) if exchange_time else None
    result = service(clock, ScriptedSource({ticker: quote(ticker, '10', at=at, fetched_at=clock.now(), **fields)}))
    return result.get([ticker])[ticker]


@pytest.mark.parametrize('now,exchange_time,status', [
    ('2026-11-17 10:00:00', '2026-11-17 09:59:57', 'live'),
    ('2026-11-17 10:00:00', '2026-11-17 09:59:00', 'live'),
    ('2026-11-17 10:00:00', '2026-11-17 09:58:59', 'delayed'),
    ('2026-11-17 10:00:00', '2026-11-17 09:45:00', 'delayed'),       # a 15-minute delayed feed
    ('2026-11-17 10:00:00', '2026-11-17 09:39:59', 'stale'),
    ('2026-11-17 14:00:00', '2026-11-17 11:30:00', 'stale'),         # stuck since the morning
    ('2026-11-17 10:00:00', '2026-11-16 15:00:00', 'stale'),         # yesterday's close during a session
    ('2026-11-17 12:15:00', '2026-11-17 11:30:00', 'closed'),        # lunch break
    ('2026-11-17 12:15:00', '2026-11-16 15:00:00', 'stale'),         # lunch break, but no quote today
    ('2026-11-17 15:30:00', '2026-11-17 15:00:00', 'closed'),
    # Changed by the review: this was 'closed'. A quote that stopped 50
    # minutes before the close is not established as the close.
    ('2026-11-17 15:30:00', '2026-11-17 14:10:00', 'stale'),         # stopped well before the close
    # Changed again by the closing-auction review: this was 'closed' ("within
    # the last 20 minutes"). The close is the 15:00 closing call auction's
    # print; a 14:45 price is not it (tests/test_quote_status_table.py).
    ('2026-11-17 15:30:00', '2026-11-17 14:45:00', 'stale'),
    ('2026-11-17 15:00:00', '2026-11-17 09:59:00', 'stale'),         # the reviewer's case at the bell
    ('2026-11-17 14:59:00', '2026-11-17 09:59:00', 'stale'),
    ('2026-11-17 12:15:00', '2026-11-17 10:00:00', 'stale'),         # lunch break, stuck since 10:00
    ('2026-11-16 08:00:00', '2026-11-13 15:00:00', 'closed'),        # Monday before the open: Friday's close
    ('2026-11-16 08:00:00', '2026-11-13 13:30:00', 'stale'),
    ('2026-11-15 12:00:00', '2026-11-13 14:00:00', 'stale'),         # Sunday, stuck since Friday 14:00
    ('2026-11-17 09:20:00', '2026-11-17 09:14:00', 'closed'),        # stamped before the auction: Monday's close
    ('2026-11-17 20:00:00', '2026-11-16 15:00:00', 'stale'),         # closed, but the quote is a day behind
    ('2026-11-17 08:00:00', '2026-11-16 15:00:00', 'closed'),        # before the open: last close
    ('2026-11-15 12:00:00', '2026-11-13 15:00:00', 'closed'),        # Sunday: Friday's close
    ('2026-11-15 12:00:00', '2026-11-12 15:00:00', 'stale'),
    ('2026-10-06 10:30:00', '2026-09-30 15:00:00', 'closed'),        # National Day: last close before it
    ('2026-11-17 09:20:00', '2026-11-17 09:19:58', 'live'),          # call auction, indicative price
    ('2026-11-17 09:20:00', '2026-11-16 15:00:00', 'closed'),        # call auction, source still on the close
])
def test_status_for_a_mainland_quote(now, exchange_time, status):
    result = status_at(now, A, exchange_time)
    assert result.status == status
    assert result.price == Decimal('10')     # the price is shown either way; the label differs
    assert (result.reason is None) == (status in {'live', 'closed'})


def test_no_exchange_time_is_time_unknown_never_live():
    result = status_at('2026-11-17 10:00:00', A, None)
    assert result.status == 'time_unknown' and result.quote_time is None and '交易所时间' in result.reason


def test_an_exchange_time_in_the_future_is_time_unknown():
    assert status_at('2026-11-17 10:00:00', A, '2026-11-17 10:01:30').status == 'live'      # small clock skew
    assert status_at('2026-11-17 10:00:00', A, '2026-11-17 10:30:00').status == 'time_unknown'


def test_a_previous_close_shown_before_the_first_trade_is_prev_close_not_live_or_closed():
    # Changed by the review: this was 'closed', the same label as a verified
    # close. A security that has not traded this session has the distinct
    # status 'prev_close'.
    result = status_at('2026-11-17 10:00:00', A, '2026-11-17 09:59:58', price_basis=PREV_CLOSE,
                       reason='本交易日尚无成交（或停牌），价格为昨收')
    assert result.status == 'prev_close' and '昨收' in result.reason


def test_us_quote_is_judged_on_new_york_time():
    # 10:00 Tuesday in Shanghai: New York closed Monday at 16:00 (05:00 Tuesday in Shanghai).
    assert status_at('2026-11-17 10:00:00', US, '2026-11-17 05:00:00').status == 'closed'
    assert status_at('2026-11-17 23:00:00', US, '2026-11-17 22:59:30').status == 'live'
    assert status_at('2026-11-17 23:00:00', US, '2026-11-17 05:00:00').status == 'stale'


def test_markets_without_holiday_data_accept_only_the_latest_weekday_close_and_say_why():
    # Changed by the review: a quote up to seven days old used to be accepted
    # as the close of a weekday-only market. Only the most recent weekday
    # session's close is accepted now, and it is marked unverified.
    # Changed again with the HK/US tables: 2026 and 2027 are known there, so
    # this is shown in 2028 (Tuesday 14 November).
    recent = status_at('2028-11-14 20:00:00', HK, '2028-11-14 16:08:00')   # after the closing auction
    assert recent.status == 'closed' and '节假日' in recent.reason
    assert (recent.session_date, recent.calendar_confidence) == (date(2028, 11, 14), 'weekday_only')
    older = status_at('2028-11-14 20:00:00', HK, '2028-11-10 16:08:00')
    assert older.status == 'stale' and older.reason
    assert status_at('2028-11-14 20:00:00', HK, '2028-11-02 16:08:00').status == 'stale'
    # During presumed trading hours an old quote is stale, holiday or not.
    assert status_at('2028-11-14 10:00:00', HK, '2028-11-13 16:08:00').status == 'stale'
    # A mainland year without a holiday table is weekday-only too.
    unverified = status_at('2027-03-09 20:00:00', A, '2027-03-09 15:00:00')
    assert unverified.status == 'closed' and unverified.calendar_confidence == 'weekday_only' and unverified.reason


def test_hong_kong_and_new_york_closes_in_a_covered_year_are_verified():
    hk = status_at('2026-11-17 20:00:00', HK, '2026-11-17 16:08:00')
    assert (hk.status, hk.reason, hk.session_date, hk.calendar_confidence) == (
        'closed', None, date(2026, 11, 17), 'calendar')
    us = status_at('2026-11-17 10:00:00', US, '2026-11-17 05:00:00')      # Monday 16:00 in New York
    assert (us.status, us.reason, us.calendar_confidence) == ('closed', None, 'calendar')
    # Over Thanksgiving (Thursday 26 November) Wednesday's close is the latest one.
    wednesday = status_at('2026-11-27 10:00:00', US, '2026-11-26 05:00:00')
    assert (wednesday.status, wednesday.session_date, wednesday.reason) == ('closed', date(2026, 11, 25), None)


def test_a_hong_kong_quote_in_the_closing_auction_session_is_current_not_the_close():
    # From 16:00 to a random close between 16:08 and 16:10 the closing price is
    # still being set; now that Hong Kong's closes are verified, a quote then
    # must not be labelled the day's close.
    assert status_at('2026-11-17 16:05:00', HK, '2026-11-17 16:04:50').status == 'live'
    assert status_at('2026-11-17 16:05:00', HK, '2026-11-17 15:59:00').status == 'delayed'
    assert status_at('2026-11-17 16:05:00', HK, '2026-11-17 15:30:00').status == 'stale'
    after = status_at('2026-11-17 16:11:00', HK, '2026-11-17 16:08:30')
    assert (after.status, after.reason) == ('closed', None)
    # On a half day the auction runs from 12:00 to 12:10.
    assert status_at('2026-12-24 12:05:00', HK, '2026-12-24 12:04:50').status == 'live'
    assert status_at('2026-12-24 12:11:00', HK, '2026-12-24 12:08:30').status == 'closed'


def test_a_close_from_an_early_close_day_is_verified_once_the_session_has_ended():
    # Across Hong Kong's Christmas closure the half day's close is the latest one.
    eve = status_at('2026-12-28 08:30:00', HK, '2026-12-24 12:08:00')
    assert (eve.status, eve.session_date, eve.reason) == ('closed', date(2026, 12, 24), None)
    # Hong Kong's Christmas Eve half day ends at 12:00; the closing auction prints a few minutes later.
    after = status_at('2026-12-24 14:00:00', HK, '2026-12-24 12:08:00')
    assert (after.status, after.reason, after.session_date) == ('closed', None, date(2026, 12, 24))
    # Changed by the closing-auction review: this was 'closed'. 11:50 is
    # before the half day's closing auction printed its close (12:08 at the earliest).
    assert status_at('2026-12-24 14:00:00', HK, '2026-12-24 11:50:00').status == 'stale'
    early = status_at('2026-12-24 14:00:00', HK, '2026-12-24 11:30:00')    # stopped 30 minutes before noon
    assert early.status == 'stale' and '11:30' in early.reason
    # In the morning of the half day the market trades as usual.
    assert status_at('2026-12-24 11:00:00', HK, '2026-12-24 10:59:58').status == 'live'
    # New York closes at 13:00 on the day after Thanksgiving (02:00 Saturday in Shanghai).
    black_friday = status_at('2026-11-28 09:00:00', US, '2026-11-28 02:00:00')
    assert (black_friday.status, black_friday.reason, black_friday.session_date) == (
        'closed', None, date(2026, 11, 27))
    # Changed in review round 4: 12:20 is not the close, from any source; the regular-session flag lifts only
    # the upper bound of the close-print window.
    assert status_at('2026-11-28 09:00:00', US, '2026-11-28 01:20:00', regular_session=False).status == 'stale'
    assert status_at('2026-11-28 09:00:00', US, '2026-11-28 01:20:00').status == 'stale'


def test_a_previous_close_quote_from_an_old_session_is_stale_not_prev_close_or_closed():
    # Reviewer's reproduction: a previous-close quote stamped 2026-01-05 was
    # 'closed' on 17 November and described as today's untraded security.
    old = status_at('2026-11-17 10:00:00', A, '2026-01-05 15:00:00', price_basis=PREV_CLOSE)
    assert old.status == 'stale' and '2026-01-05' in old.reason and old.session_date == date(2026, 1, 5)
    # Monday before the open: a security suspended all of Friday is Friday's prev_close.
    friday = status_at('2026-11-16 08:00:00', A, '2026-11-13 15:00:00', price_basis=PREV_CLOSE)
    assert friday.status == 'prev_close' and friday.session_date == date(2026, 11, 13)


def test_each_quote_names_the_session_its_price_belongs_to():
    assert status_at('2026-11-17 10:00:00', A, '2026-11-17 09:59:57').session_date == date(2026, 11, 17)
    close = status_at('2026-11-17 08:00:00', A, '2026-11-16 15:00:00')
    assert (close.session_date, close.calendar_confidence) == (date(2026, 11, 16), 'calendar')
    us = status_at('2026-11-17 10:00:00', US, '2026-11-17 05:00:00')       # Monday 16:00 in New York
    assert (us.status, us.session_date, us.calendar_confidence) == ('closed', date(2026, 11, 16), 'calendar')
    assert status_at('2026-11-17 10:00:00', A, None).session_date is None


def test_hong_kong_opening_auction_price_is_current_not_stale():
    # 09:00-09:30 is Hong Kong's pre-opening session; its 09:20 match is today's price.
    assert status_at('2026-11-17 09:25:00', HK, '2026-11-17 09:20:00').status == 'delayed'
    assert status_at('2026-11-17 09:25:00', HK, '2026-11-17 09:24:50').status == 'live'
    assert status_at('2026-11-17 09:25:00', HK, '2026-11-16 16:08:00').status == 'closed'


# -- currency rates -----------------------------------------------------------

def test_fx_rates_carry_source_time_and_status():
    clock = FakeClock('2026-11-17 10:00')
    source = ScriptedSource(rates={'USD': fx_rate('USD', '7.05', at=clock.now() - timedelta(seconds=8)),
                                   'HKD': fx_rate('HKD', '0.9', at=clock.now() - timedelta(minutes=30))})
    rates = service(clock, source).fx()
    assert set(rates) == {'USD', 'HKD'}
    assert (rates['USD'].rate, rates['USD'].status, rates['USD'].source) == (Decimal('7.05'), 'live', 'scripted')
    assert rates['USD'].quote_time == clock.now() - timedelta(seconds=8)
    assert rates['HKD'].status == 'delayed'


def test_fx_falls_through_and_reports_what_is_missing():
    clock = FakeClock('2026-11-17 10:00')
    first = ScriptedSource(rates={'USD': fx_rate('USD', '7.05', at=clock.now(), source='first')}, name='first')
    second = ScriptedSource(rates={'USD': fx_rate('USD', '7.06', at=clock.now(), source='second'),
                                   'HKD': fx_rate('HKD', '0', at=clock.now(), source='second')}, name='second')
    rates = service(clock, first, second).fx()
    assert rates['USD'].source == 'first'
    assert (rates['HKD'].rate, rates['HKD'].status) == (None, 'unavailable')
    assert 'first：未返回该币种' in rates['HKD'].reason and 'second' in rates['HKD'].reason


def test_fx_is_cached_separately_and_an_outage_is_unavailable():
    clock = FakeClock('2026-11-17 10:00')
    source = ScriptedSource(rates={'USD': fx_rate('USD', '7.05', at=clock.now()), 'HKD': fx_rate('HKD', '0.9', at=clock.now())})
    quotes = service(clock, source, fx_ttl_seconds=60)
    quotes.fx()
    clock.advance(seconds=59)
    quotes.fx()
    assert len(source.fx_calls) == 1
    source.down = '请求失败（ReadTimeout）'
    clock.advance(seconds=2)
    assert quotes.fx()['USD'].status == 'unavailable' and quotes.fx()['USD'].rate is None


@pytest.mark.parametrize('now,rate_time,status', [
    ('2026-11-17 10:00', '2026-11-17 09:57', 'live'),
    ('2026-11-17 10:00', '2026-11-17 09:30', 'delayed'),
    ('2026-11-17 10:00', '2026-11-17 08:00', 'stale'),
    ('2026-11-15 12:00', '2026-11-14 05:58', 'closed'),     # Sunday: Friday's last rate (21:58 UTC)
    ('2026-11-15 12:00', '2026-11-13 12:00', 'stale'),      # Sunday, but the rate stopped on Friday noon
    ('2026-11-16 12:00', '2026-11-14 05:58', 'stale'),      # Monday: the market reopened hours ago
])
def test_fx_status_by_age_with_a_weekend_exception(now, rate_time, status):
    assert derive_fx_status(fx_rate('USD', '7.05', at=shanghai(rate_time)), shanghai(now))[0] == status


# Review round 4: the weekend began at a fixed 22:00 UTC, which is 17:00 in New York only in winter; on Friday
# 10 July (EDT) a rate stamped 16:59:58 New York missed it by two seconds and was stale all weekend. The
# currencies close at 17:00 New York time. Observed in November 2026: Eastmoney's USDCNH/HKDCNH stop at 16:59,
# Yahoo's USDCNY=X/HKDCNY=X print until 17:00-18:00; a rate that was delayed at the close (an hour) counts.
FRIDAY_STAMPS = ['16:30:00', '16:40:00', '16:50:00', '16:55:00', '16:59:00', '16:59:58', '17:00:00']


@pytest.mark.parametrize('friday', ['2026-07-10', '2026-11-13'])      # New York summer and winter time
@pytest.mark.parametrize('stamp', FRIDAY_STAMPS + ['16:00:00', '17:30:00', '18:00:00'])
def test_fx_stamped_at_the_friday_close_is_the_weekend_rate(friday, stamp):
    from datetime import datetime
    from core.market.calendar import ZONES
    new_york = lambda text: datetime.fromisoformat(text).replace(tzinfo=ZONES['US'])
    rate = fx_rate('USD', '7.05', at=new_york(f'{friday} {stamp}'))
    saturday = (date.fromisoformat(friday) + timedelta(days=1)).isoformat()
    sunday = (date.fromisoformat(friday) + timedelta(days=2)).isoformat()
    assert derive_fx_status(rate, new_york(f'{saturday} 12:00')) == ('closed', None), (friday, stamp)
    assert derive_fx_status(rate, new_york(f'{sunday} 16:59'))[0] == 'closed'
    assert derive_fx_status(rate, new_york(f'{sunday} 18:30'))[0] == 'stale', 'the market reopens on Sunday 17:00'


@pytest.mark.parametrize('friday', ['2026-07-10', '2026-11-13'])
def test_fx_stopped_well_before_the_friday_close_is_stale_all_weekend(friday):
    from datetime import datetime
    from core.market.calendar import ZONES
    new_york = lambda text: datetime.fromisoformat(text).replace(tzinfo=ZONES['US'])
    saturday = (date.fromisoformat(friday) + timedelta(days=1)).isoformat()
    assert derive_fx_status(fx_rate('USD', '7.05', at=new_york(f'{friday} 15:59:59')),
                            new_york(f'{saturday} 12:00'))[0] == 'stale'


def test_a_stale_fx_rate_yields_to_the_next_source():
    # Review round 4: at 23:00 a stale Yahoo USD/CNY 7.00 kept Eastmoney's fresh 7.01 from being asked.
    clock = FakeClock('2026-11-17 23:00')
    first = ScriptedSource(rates={'USD': fx_rate('USD', '7.00', at=clock.now() - timedelta(hours=3), source='yahoo'),
                                  'HKD': fx_rate('HKD', '0.90', at=clock.now(), source='yahoo')}, name='yahoo')
    second = ScriptedSource(rates={'USD': fx_rate('USD', '7.01', at=clock.now() - timedelta(seconds=4),
                                                  source='eastmoney')}, name='eastmoney')
    rates = service(clock, first, second).fx()
    assert (rates['USD'].rate, rates['USD'].source, rates['USD'].status) == (Decimal('7.01'), 'eastmoney', 'live')
    assert second.fx_calls == [('USD',)], 'only the currency the first source could not give fresh is asked'
    assert rates['HKD'].source == 'yahoo'


def test_when_every_fx_source_is_stale_the_latest_rate_is_kept_with_its_source():
    clock = FakeClock('2026-11-17 23:00')
    first = ScriptedSource(rates={'USD': fx_rate('USD', '7.00', at=clock.now() - timedelta(hours=3), source='yahoo')},
                           name='yahoo')
    second = ScriptedSource(rates={'USD': fx_rate('USD', '7.02', at=clock.now() - timedelta(hours=2),
                                                  source='eastmoney')}, name='eastmoney')
    rates = service(clock, first, second).fx()
    assert (rates['USD'].rate, rates['USD'].source, rates['USD'].status) == (Decimal('7.02'), 'eastmoney', 'stale')


def test_fx_without_a_time_is_time_unknown():
    assert derive_fx_status(fx_rate('USD', '7.05'), shanghai('2026-11-17 10:00'))[0] == 'time_unknown'
