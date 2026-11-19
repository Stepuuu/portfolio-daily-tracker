"""Tencent's quotes (``core.market.tencent``, ``sources_tencent.TencentSource``) and where they stand in Today's order.

The answers are the ones recorded on 2026-11-19 (``tests/tencent_recorded.py``), served through a mock
transport: no network. A variant of a recorded row (one field changed) says so.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from core.market import tencent
from core.market.instruments import INDICES, TENCENT_INDICES, parse
from core.market.quotes import PREV_CLOSE, QuoteService, SourceError
from core.market.sources_eastmoney import EastmoneySource
from core.market.sources_tencent import TencentSource
from core.market.sources_yahoo import YahooSource
from tests.fakes import FakeClock
from tests.tencent_recorded import FETCHED, NONE_FOUND, PRE_OPEN, ROWS, answer, fields

SHANGHAI = timezone(timedelta(hours=8))
NEW_YORK_EST = timezone(timedelta(hours=-5))


def tencent_source(rows=None, seen=None, status=200, body=None):
    def handler(request):
        asked = request.url.params.get('q', '')
        if seen is not None:
            seen.append(asked)
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, content=body if body is not None else answer(asked.split(','), rows),
                              headers={'Content-Type': 'text/html; charset=GBK'})
    return TencentSource(transport=httpx.MockTransport(handler), now_fn=lambda: FETCHED)


def one(ticker, rows=None):
    return tencent_source(rows).fetch([ticker])[ticker]


# -- symbols ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize('ticker, symbol', [
    ('SHA:600519', 'sh600519'), ('SHE:000001', 'sz000001'), ('SHA:510300', 'sh510300'), ('SHE:159915', 'sz159915'),
    ('SHA:900901', 'sh900901'), ('HKG:700', 'hk00700'), ('HKG:00700', 'hk00700'), ('HKG:9988', 'hk09988'),
    ('NASDAQ:AAPL', 'usAAPL'), ('NYSE:BRK.B', 'usBRK.B'), ('NYSE:BRK-B', 'usBRK.B'),
    ('IDX:000001', 'sh000001'), ('IDX:399006', 'sz399006'), ('IDX:HSI', 'hkHSI'), ('IDX:DJIA', 'us.DJI'),
    ('IDX:SPX', 'us.INX'), ('IDX:IXIC', 'us.IXIC'),
])
def test_tickers_are_addressed_by_exchange_prefix(ticker, symbol):
    assert parse(ticker).tencent_symbol == symbol


def test_every_index_the_app_knows_has_a_tencent_symbol():
    assert set(TENCENT_INDICES) == set(INDICES)


@pytest.mark.parametrize('ticker', ['SHA:000001', 'SHE:399001', 'SHA:DEMO', 'SHE:12345', 'HKG:0', 'HKG:ABC'])
def test_codes_that_cannot_exist_or_would_reach_an_index_are_not_requested(ticker):
    seen = []
    quote = tencent_source(seen=seen).fetch([ticker])[ticker]
    assert seen == [] and quote.status == 'unavailable' and quote.price is None


# -- each market's row --------------------------------------------------------------------------------------

def test_a_shanghai_row_becomes_a_decimal_quote_with_the_exchange_time():
    seen = []
    quote = tencent_source(seen=seen).fetch(['SHA:600519'])['SHA:600519']
    assert seen == ['sh600519']
    assert (quote.price, quote.prev_close) == (Decimal('1460.21'), Decimal('1442.66'))     # no float noise
    assert (quote.open, quote.high, quote.low) == (Decimal('1442.00'), Decimal('1464.99'), Decimal('1432.10'))
    assert quote.volume == Decimal(4269200)                 # 42692 lots of 100 shares
    assert quote.quote_time == datetime(2026, 11, 19, 16, 14, 55, tzinfo=SHANGHAI) and quote.fetched_at == FETCHED
    assert (quote.name, quote.currency, quote.source) == ('贵州茅台', 'CNY', 'tencent')
    assert quote.status == 'time_unknown' and quote.price_basis == 'last'   # a source never calls its own quote live
    assert quote.regular_session is False


def test_shenzhen_000001_and_the_shanghai_index_come_from_their_own_rows_in_one_request():
    seen = []
    quotes = tencent_source(seen=seen).fetch(['SHE:000001', 'IDX:000001'])
    assert seen == ['sz000001,sh000001']
    assert (quotes['SHE:000001'].price, quotes['SHE:000001'].name) == (Decimal('12.04'), '平安银行')
    assert (quotes['IDX:000001'].price, quotes['IDX:000001'].name) == (Decimal('3577.62'), '上证指数')
    assert quotes['IDX:000001'].volume == Decimal(412912755)       # an index's volume as given, as Eastmoney's


def test_etfs_and_b_shares_in_the_currency_they_trade_in():
    quotes = tencent_source().fetch(['SHA:510300', 'SHE:159915', 'SHA:900901', 'SHE:200596'])
    assert (quotes['SHA:510300'].price, quotes['SHA:510300'].volume) == (Decimal('4.060'), Decimal(2790941700))
    assert quotes['SHE:159915'].price == Decimal('2.308')
    assert (quotes['SHA:900901'].price, quotes['SHA:900901'].currency) == (Decimal('0.491'), 'USD')
    assert (quotes['SHE:200596'].price, quotes['SHE:200596'].currency) == (Decimal('53.90'), 'HKD')


def test_a_hong_kong_row_in_hong_kong_time_and_shares():
    quote = one('HKG:700')
    assert (quote.ticker, quote.price, quote.prev_close, quote.currency) == (
        'HKG:700', Decimal('348.400'), Decimal('372.800'), 'HKD')
    assert quote.volume == Decimal(76426269) and quote.name == '腾讯控股'
    assert quote.quote_time == datetime(2026, 11, 19, 16, 8, 13, tzinfo=SHANGHAI)    # Hong Kong is UTC+8 too
    hsi = one('IDX:HSI')
    assert (hsi.price, hsi.name, hsi.quote_time.hour) == (Decimal('22841.620'), '恒生指数', 18)


def test_us_rows_in_new_york_time():
    quotes = tencent_source().fetch(['NASDAQ:AAPL', 'NYSE:BRK.B', 'IDX:DJIA', 'IDX:SPX', 'IDX:IXIC'])
    aapl = quotes['NASDAQ:AAPL']
    assert (aapl.price, aapl.prev_close, aapl.currency) == (Decimal('349.71'), Decimal('352.45'), 'USD')
    assert aapl.quote_time == datetime(2026, 11, 19, 9, 58, 40, tzinfo=NEW_YORK_EST)
    assert aapl.quote_time.astimezone(timezone.utc) == FETCHED - timedelta(seconds=21)
    assert quotes['NYSE:BRK.B'].price == Decimal('569.13')            # BRK.B.N answers usBRK.B
    assert [quotes[ticker].name for ticker in ('IDX:DJIA', 'IDX:SPX', 'IDX:IXIC')] == ['道琼斯', '标普500', '纳斯达克']
    assert not any(quote.regular_session for quote in quotes.values())


# -- status ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('ticker, price, reason', [
    ('SHA:600185', '4.28', '停牌'),            # 'S': suspended, stamped 16:14 like the others
    ('HKG:00033', '0.400', '停牌'),
    ('HKG:00021', '0.058', '尚无成交'),        # no trade that day, no status: open, high, low and volume 0
])
def test_an_untraded_row_shows_the_previous_close_and_says_so(ticker, price, reason):
    quote = one(ticker)
    assert (quote.price, quote.prev_close, quote.price_basis) == (Decimal(price), Decimal(price), PREV_CLOSE)
    assert reason in quote.reason and quote.open is None and quote.volume is None


@pytest.mark.parametrize('ticker, reason', [('SHA:600087', '退市'), ('SHA:600349', '尚未上市')])
def test_a_delisted_or_never_listed_row_has_no_price_although_it_repeats_one(ticker, reason):
    quote = one(ticker)          # each repeats its last price under today's 09:00:00
    assert (quote.price, quote.status) == (None, 'unavailable') and reason in quote.reason
    assert quote.prev_close is not None and quote.name


def test_a_status_this_reading_does_not_know_is_unavailable():
    quote = one('SHA:600519', {'sh600519': fields('sh600519', f40='C')})
    assert (quote.price, quote.status) == (None, 'unavailable') and '未识别' in quote.reason


def test_before_new_yorks_open_the_previous_close_is_not_taken_for_a_trade():
    # 09:30:00, half a minute early: the previous close as the price, the pre-market's volume, no day range.
    quote = one('NASDAQ:AAPL', PRE_OPEN)
    assert (quote.price, quote.price_basis, quote.prev_close) == (Decimal('352.45'), PREV_CLOSE, Decimal('352.45'))


@pytest.mark.parametrize('ticker, rows', [
    # BRK.A's values at 09:30:01 (fields 3-6, 30, 33, 34 as recorded then) in BRK.B's row: one trade, open,
    # high and low still 0.
    ('NYSE:BRK.B', {'usBRK.B': fields('usBRK.B', f3='861695.21', f4='863450.00', f5='0.00', f6='1',
                                      f30='2026-11-19 09:30:01', f33='0.00', f34='0.00')}),
    # A mainland row with a volume but no day range.
    ('SHA:600185', {'sh600185': fields('sh600185', f6='1200')}),
])
def test_a_price_without_a_day_range_that_is_not_the_previous_close_is_unavailable(ticker, rows):
    quote = one(ticker, rows)
    assert (quote.price, quote.status) == (None, 'unavailable') and quote.prev_close is not None
    assert '开盘价' in quote.reason


def test_a_listing_in_another_currency_or_a_row_for_another_listing_is_refused():
    usd_counter = one('HKG:700', {'hk00700': fields('hk00700', f75='USD')})
    assert usd_counter.status == 'unavailable' and '币种' in usd_counter.reason
    # The Shanghai index's row answered under the Shenzhen bank's symbol: market id 1, not 51.
    wrong = one('SHE:000001', {'sz000001': ROWS['sh000001']})
    assert wrong.status == 'unavailable' and wrong.price is None and '不符' in wrong.reason
    truncated = one('SHA:600519', {'sh600519': '~'.join(ROWS['sh600519'].split('~')[:40])})
    assert truncated.status == 'unavailable'


def test_a_missing_time_is_left_unknown():
    quote = one('SHA:600519', {'sh600519': fields('sh600519', f30='')})
    assert quote.price == Decimal('1460.21') and quote.quote_time is None and quote.status == 'time_unknown'


# -- the answer as a whole -----------------------------------------------------------------------------------

def test_a_symbol_tencent_does_not_know_is_left_out_of_the_answer_not_an_outage():
    quotes = tencent_source().fetch(['SHA:600519', 'HKG:8001'])        # hk08001 is not in the recorded answer
    assert quotes['SHA:600519'].price == Decimal('1460.21')
    assert quotes['HKG:8001'].status == 'unavailable' and '没有这只证券' in quotes['HKG:8001'].reason
    none = tencent_source(body=NONE_FOUND.encode()).fetch(['HKG:8001'])['HKG:8001']   # when it knows none of them
    assert none.status == 'unavailable' and '没有这只证券' in none.reason


@pytest.mark.parametrize('make', [
    lambda: tencent_source(status=503),
    lambda: tencent_source(body=b'<html>blocked</html>'),
    lambda: tencent_source(body=b''),
    lambda: TencentSource(transport=httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError('connection closed')))),
    lambda: TencentSource(transport=httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout('slow')))),
])
def test_tencent_failures_are_reported_as_a_source_error(make):
    with pytest.raises(SourceError) as failure:
        make().fetch(['SHA:600519'])
    assert str(failure.value) in ('数据源出错', '数据源响应超时', '数据源连接失败', '数据源返回的数据无法解析')


def test_many_tickers_are_asked_in_batches():
    seen = []
    tickers = [f'SHA:6{number:05d}' for number in range(tencent.BATCH + 5)]
    tencent_source(seen=seen).fetch(tickers)
    assert [len(asked.split(',')) for asked in seen] == [tencent.BATCH, 5]


def test_tencent_is_reached_directly_unless_asked_to_use_the_proxy(monkeypatch):
    built = []

    class Client:
        def __init__(self, **options):
            built.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, params=None):
            return httpx.Response(200, content=answer(params['q'].split(',')), request=httpx.Request('GET', url))

    from core.market import http as market_http
    monkeypatch.setattr(market_http.httpx, 'Client', Client)
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9')
    assert TencentSource().fetch(['SHA:600519'])['SHA:600519'].price == Decimal('1460.21')
    assert built[-1]['trust_env'] is False and 'proxy' not in built[-1]
    assert built[-1]['timeout'].read == 3.0 and built[-1]['timeout'].connect == 2.0
    TencentSource(use_env_proxy=True).fetch(['SHA:600519'])
    assert built[-1].get('trust_env', True) is True and built[-1]['timeout'].read == 4.0


# -- at the session clock --------------------------------------------------------------------------------------

def test_recorded_rows_judged_at_the_time_they_were_recorded():
    """22:59 Shanghai on 2026-11-19: the mainland's and Hong Kong's closes are final, New York trades."""
    clock = FakeClock('2026-11-19 22:59:01')
    service = QuoteService([tencent_source()], clock.session(), ttl_seconds=0)
    quotes = service.get(['SHA:600519', 'SHE:000001', 'SHA:510300', 'IDX:000001', 'HKG:00700', 'IDX:HSI',
                          'NASDAQ:AAPL', 'IDX:SPX', 'SHA:600185', 'HKG:00021', 'HKG:00064', 'SHA:600087'])
    status = {ticker: quote.status for ticker, quote in quotes.items()}
    assert status == {
        'SHA:600519': 'closed', 'SHE:000001': 'closed', 'SHA:510300': 'closed', 'IDX:000001': 'closed',
        'HKG:00700': 'closed', 'IDX:HSI': 'closed',            # 16:08:13 and 18:31:05: at or after the 16:08 print
        'NASDAQ:AAPL': 'live', 'IDX:SPX': 'live',               # 21 seconds old
        'SHA:600185': 'prev_close', 'HKG:00021': 'prev_close',  # untraded today: the previous close, an estimate
        'HKG:00064': 'stale',                                   # suspended since 2026-11-06
        'SHA:600087': 'unavailable',
    }
    assert all(quotes[ticker].source == 'tencent' for ticker in quotes)
    assert quotes['SHA:600519'].calendar_confidence == 'calendar' and quotes['SHA:600519'].reason is None


def test_a_new_york_price_stamped_after_the_close_window_is_not_the_close():
    # Tencent does not name its price the regular session's: a 16:45 stamp may be an after-hours trade.
    late = {'usAAPL': fields('usAAPL', f30='2026-11-19 16:45:00')}
    at_bell = {'usAAPL': fields('usAAPL', f30='2026-11-19 16:00:00')}
    clock = FakeClock('2026-11-20 07:00')                       # 18:00 New York, the close settled
    for rows, expected in ((late, 'stale'), (at_bell, 'closed')):
        quote = QuoteService([tencent_source(rows)], clock.session(), ttl_seconds=0).get(['NASDAQ:AAPL'])['NASDAQ:AAPL']
        assert quote.status == expected


# -- in Today's order: Eastmoney, Tencent, Yahoo -----------------------------------------------------------------

class Counting:
    """A transport that counts its requests and answers with ``respond``."""

    def __init__(self, respond):
        self.respond, self.calls = respond, 0

    def __call__(self, request):
        self.calls += 1
        return self.respond(request)


def closed_connection(request):
    raise httpx.RemoteProtocolError('Server disconnected without sending a response.')


def chain(eastmoney, tencent_rows, yahoo):
    """``(service, transports)``: Today's three real sources in its order over counting mock transports."""
    transports = {'eastmoney': Counting(eastmoney), 'tencent': Counting(lambda request: httpx.Response(
                      200, content=answer(request.url.params['q'].split(','), tencent_rows))),
                  'yahoo': Counting(yahoo)}
    if tencent_rows is None:
        transports['tencent'] = Counting(closed_connection)
    sources = [EastmoneySource(transport=httpx.MockTransport(transports['eastmoney']), now_fn=lambda: FETCHED),
               TencentSource(transport=httpx.MockTransport(transports['tencent']), now_fn=lambda: FETCHED),
               YahooSource(transport=httpx.MockTransport(transports['yahoo']), now_fn=lambda: FETCHED)]
    return QuoteService(sources, FakeClock('2026-11-19 22:59:01').session(), ttl_seconds=0), transports


def yahoo_chart(request):
    symbol = request.url.path.rsplit('/', 1)[-1]
    stamp = int(datetime(2026, 11, 19, 7, 0, 2, tzinfo=timezone.utc).timestamp())
    return httpx.Response(200, json={'chart': {'result': [{'meta': {
        'symbol': symbol, 'currency': 'CNY', 'regularMarketPrice': 1460.21, 'regularMarketTime': stamp,
        'chartPreviousClose': 1442.66}}]}})


def test_when_eastmoney_cannot_be_reached_tencent_answers_and_yahoo_is_not_asked():
    service, transports = chain(closed_connection, ROWS, yahoo_chart)
    quote = service.get(['SHA:600519'])['SHA:600519']
    assert (quote.source, quote.price, quote.status) == ('tencent', Decimal('1460.21'), 'closed')
    assert (transports['eastmoney'].calls, transports['tencent'].calls, transports['yahoo'].calls) == (1, 1, 0)
    # Eastmoney is left alone for a while: the next read goes to Tencent at once.
    service.get(['SHA:600519'])
    assert (transports['eastmoney'].calls, transports['tencent'].calls) == (1, 2)


def test_when_eastmoney_and_tencent_fail_yahoo_answers_and_each_quote_names_its_source():
    service, transports = chain(closed_connection, None, yahoo_chart)
    quote = service.get(['SHA:600519'])['SHA:600519']
    assert (quote.source, quote.price) == ('yahoo', Decimal('1460.21'))
    assert (transports['eastmoney'].calls, transports['tencent'].calls, transports['yahoo'].calls) == (1, 1, 1)
    service.get(['SHA:600519'])                    # both failed sources wait out their retry interval
    assert (transports['eastmoney'].calls, transports['tencent'].calls, transports['yahoo'].calls) == (1, 1, 2)


def test_every_source_failing_leaves_the_quote_unavailable_with_each_ones_reason():
    service, _transports = chain(closed_connection, None, lambda request: httpx.Response(503))
    quote = service.get(['SHA:600519'])['SHA:600519']
    assert (quote.price, quote.status, quote.source) == (None, 'unavailable', 'eastmoney+tencent+yahoo')
    assert all(name in quote.reason for name in ('eastmoney：', 'tencent：', 'yahoo：'))


def test_an_answer_that_is_not_final_asks_the_next_source_and_the_best_one_wins():
    # Eastmoney shows the suspended-looking row (no price, the previous close: prev_close); Tencent the close.
    def eastmoney(request):
        return httpx.Response(200, json={'rc': 0, 'data': {'diff': [
            {'f12': '600519', 'f13': 1, 'f2': '-', 'f18': 1442.66, 'f5': '-', 'f15': '-', 'f16': '-', 'f17': '-',
             'f14': '贵州茅台', 'f124': int(datetime(2026, 11, 19, 8, 0, tzinfo=timezone.utc).timestamp())}]}})
    service, transports = chain(eastmoney, ROWS, yahoo_chart)
    quote = service.get(['SHA:600519'])['SHA:600519']
    assert (quote.source, quote.status, quote.price) == ('tencent', 'closed', Decimal('1460.21'))
    assert transports['yahoo'].calls == 0
    # A row Tencent prices only as the previous close does not end the fallback either: Yahoo is asked.
    service, transports = chain(eastmoney, {'sh600519': fields('sh600519', f5='0.00', f6='0', f33='0.00',
                                                                 f34='0.00', f3='1442.66')}, yahoo_chart)
    quote = service.get(['SHA:600519'])['SHA:600519']
    assert (quote.source, quote.status) == ('yahoo', 'closed') and transports['tencent'].calls == 1


# -- TRACKER_QUOTE_SOURCE and TRACKER_TENCENT_PROXY --------------------------------------------------------------

@pytest.mark.parametrize('choice, names, rates', [
    ('', ['eastmoney', 'tencent', 'yahoo'], ['yahoo', 'eastmoney']),
    ('tencent', ['tencent'], ['yahoo', 'eastmoney']),          # Tencent has no rates: Yahoo's, then Eastmoney's
    (' Tencent ', ['tencent'], ['yahoo', 'eastmoney']),
    ('eastmoney', ['eastmoney'], ['eastmoney']),
    ('yahoo', ['yahoo'], ['yahoo']),
])
def test_tracker_quote_source_selects_the_sources_in_todays_order(monkeypatch, choice, names, rates):
    from core.market.runtime import build_runtime
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    monkeypatch.setenv('TRACKER_QUOTE_SOURCE', choice)
    runtime = build_runtime()
    assert runtime.quotes.source_names == names and runtime.mode == 'live'
    assert [source.name for source in runtime.quotes.fx_sources] == rates


def test_tracker_tencent_proxy_sends_tencent_alone_through_the_proxy(monkeypatch):
    from core.market.runtime import build_runtime
    for name in ('TRACKER_DEMO_MODE', 'TRACKER_QUOTE_SOURCE', 'TRACKER_TENCENT_PROXY', 'TRACKER_EASTMONEY_PROXY'):
        monkeypatch.delenv(name, raising=False)
    eastmoney, tencent_, _yahoo = build_runtime().quotes.sources
    assert (eastmoney._use_env_proxy, tencent_._use_env_proxy) == (False, False)
    monkeypatch.setenv('TRACKER_TENCENT_PROXY', '1')
    eastmoney, tencent_, _yahoo = build_runtime().quotes.sources
    assert (eastmoney._use_env_proxy, tencent_._use_env_proxy) == (False, True)
