"""Source parsers against hand-built payloads in the recorded shapes. No network.

Eastmoney ``ulist.np/get`` (``fltt=2``): f2 latest, f18 previous close, f17 open,
f15 high, f16 low, f5 volume, f12 code, f13 market id, f14 name, f124 update
time in Unix seconds; ``"-"`` marks a missing value.

Yahoo ``v8/finance/chart``: ``meta.regularMarketPrice``, ``regularMarketTime``
(Unix seconds), ``chartPreviousClose``, ``currency``, ``symbol``.

All names and prices are invented.
"""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from core.market import http as market_http
from core.market.instruments import parse
from core.market.quotes import PREV_CLOSE, SourceError
from core.market.sources_eastmoney import EastmoneySource, quote_from_row
from core.market.sources_fake import FakeQuoteSource
from core.market.sources_yahoo import YahooSource
from tests.fakes import shanghai, write_fake_quotes

FETCHED = datetime(2026, 11, 17, 5, 44, 38, tzinfo=timezone.utc)
STAMP = 1794894276                      # 2026-11-17 13:44:36 +08:00
EXCHANGE_TIME = datetime(2026, 11, 17, 5, 44, 36, tzinfo=timezone.utc)


def row(market, code, price, prev_close, **extra):
    return {'f2': price, 'f5': 1200, 'f12': code, 'f13': market, 'f14': '示例证券', 'f15': 12.32, 'f16': 12.11,
            'f17': 12.25, 'f18': prev_close, 'f124': STAMP, **extra}


def eastmoney(rows, seen=None, status=200):
    def handler(request):
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={'rc': 0, 'rt': 11, 'data': {'total': len(rows), 'diff': rows}})
    return EastmoneySource(transport=httpx.MockTransport(handler), now_fn=lambda: FETCHED)


# -- Eastmoney ----------------------------------------------------------------

def test_eastmoney_row_becomes_a_decimal_quote_with_the_exchange_time():
    seen = []
    quote = eastmoney([row(0, '000002', 12.26, 12.3)], seen).fetch(['SHE:000002'])['SHE:000002']
    assert quote.price == Decimal('12.26') and quote.prev_close == Decimal('12.3')   # no float noise
    assert (quote.open, quote.high, quote.low) == (Decimal('12.25'), Decimal('12.32'), Decimal('12.11'))
    assert quote.volume == Decimal(120000)               # A-share lots of 100 shares
    assert quote.quote_time == EXCHANGE_TIME and quote.fetched_at == FETCHED
    assert (quote.currency, quote.source, quote.name) == ('CNY', 'eastmoney', '示例证券')
    assert quote.status == 'time_unknown'                # a source never calls its own quote live
    assert seen[0].url.params['secids'] == '0.000002' and seen[0].url.params['fltt'] == '2'


def test_eastmoney_prices_shenzhen_000001_from_the_shenzhen_row():
    # Both securities share the code; the market id in the response decides.
    seen = []
    rows = [row(1, '000001', 3595.22, 3602.57, f14='示例指数'), row(0, '000001', 12.26, 12.3, f14='示例银行')]
    quotes = eastmoney(rows, seen).fetch(['SHE:000001', 'IDX:000001'])
    assert (quotes['SHE:000001'].price, quotes['SHE:000001'].name) == (Decimal('12.26'), '示例银行')
    assert (quotes['IDX:000001'].price, quotes['IDX:000001'].name) == (Decimal('3595.22'), '示例指数')
    assert seen[0].url.params['secids'] == '0.000001,1.000001'
    assert len(seen) == 1                                # one batched request


def test_eastmoney_hong_kong_and_us_rows():
    rows = [row(116, '00001', 378.8, 390.4), row(107, 'EXMPL', 739.95, 754.37)]
    quotes = eastmoney(rows).fetch(['HKG:1', 'NYSE:EXMPL', 'NASDAQ:OTHER'])
    assert (quotes['HKG:1'].price, quotes['HKG:1'].currency) == (Decimal('378.8'), 'HKD')
    assert quotes['HKG:1'].volume == Decimal(1200)       # shares, not lots
    assert quotes['NYSE:EXMPL'].price == Decimal('739.95')   # found under the NYSE Arca id
    assert quotes['NASDAQ:OTHER'].status == 'unavailable' and quotes['NASDAQ:OTHER'].price is None


def test_eastmoney_untraded_security_shows_the_previous_close_and_says_so():
    untraded = row(116, '08001', '-', 0.188, f5='-', f15='-', f16='-', f17='-')
    quote = eastmoney([untraded]).fetch(['HKG:8001'])['HKG:8001']
    assert (quote.price, quote.prev_close, quote.price_basis) == (Decimal('0.188'), Decimal('0.188'), PREV_CLOSE)
    assert '昨收' in quote.reason and quote.open is None and quote.volume is None
    strict = quote_from_row(parse('HKG:8001'), untraded, FETCHED, prev_close_when_untraded=False)
    assert (strict.price, strict.status, strict.prev_close) == (None, 'unavailable', Decimal('0.188'))


def test_eastmoney_untraded_with_zeros_instead_of_dashes_is_also_untraded():
    untraded = row(1, '600001', '-', 10, f5=0, f15=0, f16=0, f17=0)
    quote = quote_from_row(parse('SHA:600001'), untraded, FETCHED)
    assert (quote.price, quote.price_basis) == (Decimal('10'), PREV_CLOSE)


@pytest.mark.parametrize('activity', [
    {'f5': 12345, 'f17': 11, 'f15': 12, 'f16': 10.5},        # the reviewer's row: traded all day
    {'f5': 12345, 'f17': '-', 'f15': '-', 'f16': '-'},      # volume alone
    {'f5': '-', 'f17': 11, 'f15': '-', 'f16': '-'},         # an open alone
    {'f5': '-', 'f17': '-', 'f15': 12, 'f16': '-'},
    {'f5': '-', 'f17': '-', 'f15': '-', 'f16': 10.5},
    {'f5': 'garbage', 'f17': '-', 'f15': '-', 'f16': '-'},  # unreadable is not "no volume"
])
def test_eastmoney_missing_price_after_trading_is_unavailable_not_the_previous_close(activity):
    # Regression: f2 '-' with f18 10 became price 10 "尚无成交" although the
    # payload showed volume and a day range; 100 shares were valued at 1000.
    traded = row(1, '600001', '-', 10, **activity)
    quote = quote_from_row(parse('SHA:600001'), traded, FETCHED)
    assert (quote.price, quote.status, quote.prev_close) == (None, 'unavailable', Decimal('10'))
    assert '成交' in quote.reason and '昨收' not in quote.reason.split('，')[0]


@pytest.mark.parametrize('fields', [{'f2': '-', 'f18': '-'}, {'f2': 0, 'f18': 0}, {'f2': -1, 'f18': '-'},
                                    {'f2': None, 'f18': None}])
def test_eastmoney_row_without_any_price_is_unavailable_not_zero(fields):
    quote = quote_from_row(parse('SHA:600001'), row(1, '600001', 1, 1, **fields), FETCHED)
    assert (quote.price, quote.prev_close, quote.status) == (None, None, 'unavailable')


def test_eastmoney_missing_timestamp_is_left_unknown():
    quote = quote_from_row(parse('SHA:600001'), row(1, '600001', 10.5, 10, f124='-'), FETCHED)
    assert quote.price == Decimal('10.5') and quote.quote_time is None and quote.status == 'time_unknown'


def test_index_range_equity_codes_are_not_requested_from_either_source():
    seen, asked = [], []
    rows = [row(1, '000001', 3595.22, 3602.57, f14='示例指数')]
    em = eastmoney(rows, seen).fetch(['SHA:000001', 'SHE:399001'])
    yh = yahoo({'000001.SS': chart('000001.SS', 3595.22, 'CNY')}, asked).fetch(['SHA:000001', 'SHE:399001'])
    assert seen == [] and asked == []
    for quote in (*em.values(), *yh.values()):
        assert quote.status == 'unavailable' and quote.price is None and '指数' in quote.reason


def test_eastmoney_codes_that_cannot_exist_are_not_requested():
    seen = []
    quotes = eastmoney([], seen).fetch(['SHA:DEMO', '000001'])
    assert seen == []
    assert quotes['SHA:DEMO'].status == 'unavailable' and quotes['000001'].status == 'unavailable'


@pytest.mark.parametrize('handler', [
    lambda request: httpx.Response(503),
    lambda request: httpx.Response(200, text='<html>blocked</html>'),
    lambda request: httpx.Response(200, json={'rc': 1, 'data': None}),
    lambda request: httpx.Response(200, json=['unexpected']),
    lambda request: (_ for _ in ()).throw(httpx.ConnectTimeout('timed out')),
])
def test_eastmoney_failures_are_reported_as_a_source_error(handler):
    source = EastmoneySource(transport=httpx.MockTransport(handler))
    with pytest.raises(SourceError):
        source.fetch(['SHA:600001'])
    with pytest.raises(SourceError):
        source.fetch_fx(['USD'])


@pytest.mark.parametrize('answer', [{'rc': 102, 'data': None}, {'rc': 0, 'data': None},
                                    {'rc': 0, 'data': {'total': 0, 'diff': []}}])
def test_eastmoney_knowing_none_of_the_securities_is_not_an_outage(answer):
    # rc 102 with no data is how the endpoint says "none of these exist".
    source = EastmoneySource(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=answer)))
    quote = source.fetch(['SHA:600001'])['SHA:600001']
    assert quote.status == 'unavailable' and '没有这只证券' in quote.reason


def test_eastmoney_fx_is_labelled_offshore():
    rows = [row(133, 'USDCNH', 6.6652, 6.6647), row(133, 'HKDCNH', 0.8495, 0.8494)]
    rates = eastmoney(rows).fetch_fx(['USD', 'HKD'])
    assert (rates['USD'].rate, rates['USD'].source) == (Decimal('6.6652'), 'eastmoney:USDCNH')
    assert rates['HKD'].rate == Decimal('0.8495') and rates['HKD'].quote_time == EXCHANGE_TIME


# -- Yahoo --------------------------------------------------------------------

def chart(symbol, price, currency, previous=390.4, **meta):
    return {'chart': {'error': None, 'result': [{
        'meta': {'currency': currency, 'symbol': symbol, 'regularMarketTime': STAMP, 'regularMarketPrice': price,
                 'regularMarketDayHigh': 386.6, 'regularMarketDayLow': 376.8, 'regularMarketVolume': 16793128,
                 'shortName': 'EXAMPLE', 'chartPreviousClose': previous, **meta},
        'timestamp': [STAMP],
        'indicators': {'quote': [{'open': [386.6000061035156], 'close': [378.79998779296875]}]}}]}}


def yahoo(charts, seen=None):
    def handler(request):
        symbol = request.url.path.rsplit('/', 1)[-1]
        if seen is not None:
            seen.append(symbol)
        answer = charts.get(symbol)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            return httpx.Response(404, json={'chart': {'result': None, 'error': {'code': 'Not Found'}}})
        return httpx.Response(200, json=answer)
    return YahooSource(transport=httpx.MockTransport(handler), now_fn=lambda: FETCHED)


def test_yahoo_chart_becomes_a_quote_with_the_market_time():
    seen = []
    quote = yahoo({'0001.HK': chart('0001.HK', 378.8, 'HKD')}, seen).fetch(['HKG:1'])['HKG:1']
    assert seen == ['0001.HK']
    assert (quote.price, quote.prev_close, quote.currency) == (Decimal('378.8'), Decimal('390.4'), 'HKD')
    assert quote.quote_time == EXCHANGE_TIME and quote.fetched_at == FETCHED
    assert quote.open == Decimal('386.6000061035156') and quote.volume == Decimal(16793128)
    assert (quote.source, quote.status) == ('yahoo', 'time_unknown')


def test_yahoo_is_addressed_by_exchange_prefix():
    seen = []
    charts = {'000001.SZ': chart('000001.SZ', 12.26, 'CNY'), '000001.SS': chart('000001.SS', 3595.22, 'CNY')}
    quotes = yahoo(charts, seen).fetch(['SHE:000001', 'IDX:000001'])
    assert sorted(seen) == ['000001.SS', '000001.SZ']
    assert quotes['SHE:000001'].price == Decimal('12.26') and quotes['IDX:000001'].price == Decimal('3595.22')


def test_yahoo_refuses_a_listing_in_another_currency_or_for_another_symbol():
    quotes = yahoo({'0001.HK': chart('0001.HK', 48.5, 'USD'), 'EXMPL': chart('OTHER', 10, 'USD')}).fetch(
        ['HKG:1', 'NASDAQ:EXMPL'])
    assert quotes['HKG:1'].status == 'unavailable' and '币种' in quotes['HKG:1'].reason
    assert quotes['NASDAQ:EXMPL'].status == 'unavailable' and quotes['NASDAQ:EXMPL'].price is None


def test_b_shares_are_quoted_in_the_currency_they_trade_in():
    # Shapes as probed on 2026-11-17: Eastmoney 1.900901 / 0.200596, Yahoo 900901.SS (USD) / 200596.SZ (HKD).
    seen = []
    em = eastmoney([row(1, '900901', 0.498, 0.498), row(0, '200596', 54.66, 54.73)], seen).fetch(
        ['SHA:900901', 'SHE:200596'])
    assert 'secids=1.900901%2C0.200596' in str(seen[0].url) or 'secids=1.900901,0.200596' in str(seen[0].url)
    assert (em['SHA:900901'].price, em['SHA:900901'].currency) == (Decimal('0.498'), 'USD')
    assert (em['SHE:200596'].price, em['SHE:200596'].currency) == (Decimal('54.66'), 'HKD')
    yh = yahoo({'900901.SS': chart('900901.SS', 0.498, 'USD'), '200596.SZ': chart('200596.SZ', 54.66, 'HKD')}).fetch(
        ['SHA:900901', 'SHE:200596'])
    assert (yh['SHA:900901'].price, yh['SHA:900901'].currency) == (Decimal('0.498'), 'USD')
    assert (yh['SHE:200596'].price, yh['SHE:200596'].currency) == (Decimal('54.66'), 'HKD')
    # A B-share symbol answered in renminbi is some other listing.
    wrong = yahoo({'900901.SS': chart('900901.SS', 3.5, 'CNY')}).fetch(['SHA:900901'])['SHA:900901']
    assert wrong.status == 'unavailable' and '币种' in wrong.reason


def test_yahoo_missing_price_or_time_is_never_filled_in():
    no_price = chart('EXMPL', None, 'USD')
    no_time = chart('OTHER', 10.5, 'USD', regularMarketTime=None)
    quotes = yahoo({'EXMPL': no_price, 'OTHER': no_time}).fetch(['NASDAQ:EXMPL', 'NASDAQ:OTHER'])
    assert (quotes['NASDAQ:EXMPL'].price, quotes['NASDAQ:EXMPL'].status) == (None, 'unavailable')
    assert quotes['NASDAQ:OTHER'].price == Decimal('10.5') and quotes['NASDAQ:OTHER'].quote_time is None


def test_yahoo_one_unknown_symbol_does_not_fail_the_others():
    quotes = yahoo({'EXMPL': chart('EXMPL', 10.5, 'USD')}).fetch(['NASDAQ:EXMPL', 'NASDAQ:GONE'])
    assert quotes['NASDAQ:EXMPL'].price == Decimal('10.5')
    assert quotes['NASDAQ:GONE'].status == 'unavailable' and '没有这只证券' in quotes['NASDAQ:GONE'].reason


def test_yahoo_unreachable_is_a_source_error():
    with pytest.raises(SourceError):
        yahoo({'EXMPL': httpx.ConnectError('refused')}).fetch(['NASDAQ:EXMPL'])
    with pytest.raises(SourceError):
        yahoo({'USDCNY=X': httpx.ReadTimeout('slow'), 'HKDCNY=X': httpx.ReadTimeout('slow')}).fetch_fx(['USD', 'HKD'])


def test_yahoo_fx_uses_the_onshore_pairs():
    seen = []
    charts = {'USDCNY=X': chart('USDCNY=X', 6.6644, 'CNY'), 'HKDCNY=X': chart('HKDCNY=X', 0.849, 'HKD')}
    rates = yahoo(charts, seen).fetch_fx(['USD', 'HKD'])
    assert sorted(seen) == ['HKDCNY=X', 'USDCNY=X']
    assert (rates['USD'].rate, rates['USD'].source, rates['USD'].quote_time) == (
        Decimal('6.6644'), 'yahoo:USDCNY=X', EXCHANGE_TIME)
    assert rates['HKD'].rate is None and rates['HKD'].status == 'unavailable'   # not quoted in CNY


def test_yahoo_fx_accepts_the_canonical_usd_symbol_but_no_other_pair():
    # USDCNY=X is sometimes answered as CNY=X. HKD=X is USD/HKD and must not pass for HKD/CNY.
    charts = {'USDCNY=X': chart('CNY=X', 6.6647, 'CNY'), 'HKDCNY=X': chart('HKD=X', 7.78, 'CNY')}
    rates = yahoo(charts).fetch_fx(['USD', 'HKD'])
    assert (rates['USD'].rate, rates['USD'].source) == (Decimal('6.6647'), 'yahoo:USDCNY=X')
    assert rates['HKD'].rate is None and rates['HKD'].status == 'unavailable'


# -- proxy selection ----------------------------------------------------------

def test_a_socks_proxy_without_support_falls_back_to_the_https_proxy(monkeypatch):
    built = []

    class Client:
        def __init__(self, **options):
            if 'proxy' not in options and options.get('trust_env', True):
                raise ImportError('socksio is not installed')
            built.append(options)

    monkeypatch.setattr(market_http.httpx, 'Client', Client)
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9')
    market_http.open_client(timeout=httpx.Timeout(1), headers={})
    assert built[-1]['proxy'] == 'http://127.0.0.1:9' and built[-1]['trust_env'] is False
    monkeypatch.delenv('HTTPS_PROXY')
    monkeypatch.delenv('https_proxy', raising=False)
    with pytest.raises(httpx.TransportError):
        market_http.open_client(timeout=httpx.Timeout(1), headers={})
    market_http.open_client(timeout=httpx.Timeout(1), headers={}, use_env_proxy=False)
    assert built[-1]['trust_env'] is False and 'proxy' not in built[-1]


# -- New York after the close: the regular close, never an extended-hours price --

NY_CLOSE = int(datetime(2026, 11, 17, 21, 0, 2, tzinfo=timezone.utc).timestamp())       # 16:00:02 New York
NY_EVENING = int(datetime(2026, 11, 18, 1, 0, 0, tzinfo=timezone.utc).timestamp())      # 20:00:00 New York


def _status(quote, at='2026-11-18 10:00'):
    from core.market.quotes import derive_status
    from tests.fakes import FakeClock
    return derive_status(quote, FakeClock(at).session())


def test_an_eastmoney_us_price_stamped_in_extended_hours_is_not_the_close():
    # Whether Eastmoney's f2/f124 follow extended-hours trades is not established (the
    # recorded samples only show f124 at 16:00 after the evening session): a later stamp
    # is not taken as the close.
    late = eastmoney([row(105, 'EXMPL', 101.5, 100, f124=NY_EVENING)]).fetch(['NASDAQ:EXMPL'])['NASDAQ:EXMPL']
    status, reason = _status(late)
    assert status == 'stale' and '盘后' in reason
    at_close = eastmoney([row(105, 'EXMPL', 100.5, 100, f124=NY_CLOSE)]).fetch(['NASDAQ:EXMPL'])['NASDAQ:EXMPL']
    assert _status(at_close) == ('closed', None)


def test_yahoos_regular_market_price_is_the_regular_session_close_whenever_it_is_stamped():
    late_print = int(datetime(2026, 11, 17, 21, 3, 0, tzinfo=timezone.utc).timestamp())    # 16:03 New York
    quote = yahoo({'EXMPL': chart('EXMPL', 100.5, 'USD', regularMarketTime=late_print)}).fetch(
        ['NASDAQ:EXMPL'])['NASDAQ:EXMPL']
    assert quote.regular_session is True
    assert _status(quote) == ('closed', None)


# -- the scripted source ------------------------------------------------------

NOW = shanghai('2026-11-17 10:00')


def fake(tmp_path):
    return FakeQuoteSource(tmp_path, now_fn=lambda: NOW)


def test_fake_source_rereads_the_file_on_every_fetch(tmp_path):
    source = fake(tmp_path)
    write_fake_quotes(tmp_path, {'SHA:600001': {'name': '示例', 'price': '10.50', 'prev_close': '10', 'age_seconds': 3}})
    first = source.fetch(['SHA:600001'])['SHA:600001']
    assert (first.price, first.prev_close, first.name, first.currency) == (Decimal('10.50'), Decimal('10'), '示例', 'CNY')
    assert first.quote_time == NOW - timedelta(seconds=3) and first.fetched_at == NOW and first.source == 'fake'
    write_fake_quotes(tmp_path, {'SHA:600001': {'price': '11', 'quote_time': '2026-11-17T09:00:00+08:00'}})
    second = source.fetch(['SHA:600001'])['SHA:600001']
    assert second.price == Decimal('11') and second.quote_time == shanghai('2026-11-17 09:00')


def test_fake_source_scripts_outages_and_never_invents_a_price(tmp_path):
    source = fake(tmp_path)
    missing = source.fetch(['SHA:600001'])['SHA:600001']
    assert missing.status == 'unavailable' and 'fake_quotes.json' in missing.reason       # no file at all
    write_fake_quotes(tmp_path, {'SHA:600001': {'price': '10'}, 'SHA:600002': {'unavailable': '停牌', 'prev_close': '9'},
                                 'SHA:600003': {'price': '0'}})
    quotes = source.fetch(['SHA:600001', 'SHA:600002', 'SHA:600003', 'SHA:600004'])
    assert quotes['SHA:600001'].price == Decimal('10') and quotes['SHA:600001'].quote_time is None
    assert (quotes['SHA:600002'].price, quotes['SHA:600002'].reason) == (None, '停牌')
    assert quotes['SHA:600003'].status == 'unavailable'
    assert quotes['SHA:600004'].status == 'unavailable' and '没有该代码' in quotes['SHA:600004'].reason
    write_fake_quotes(tmp_path, {'SHA:600001': {'price': '10'}}, fx={'USD': {'rate': '7'}}, outage='数据源维护')
    assert source.fetch(['SHA:600001'])['SHA:600001'].reason == '数据源维护'
    assert source.fetch_fx(['USD'])['USD'].rate is None
    (tmp_path / 'fake_quotes.json').write_text('{not json')
    assert source.fetch(['SHA:600001'])['SHA:600001'].status == 'unavailable'
    (tmp_path / 'fake_quotes.json').write_text(json.dumps(['a list']))
    assert source.fetch(['SHA:600001'])['SHA:600001'].status == 'unavailable'


def test_fake_source_finds_a_hong_kong_code_under_any_spelling(tmp_path):
    write_fake_quotes(tmp_path, {'HKG:700': {'price': '400', 'age_seconds': 3}})
    quotes = fake(tmp_path).fetch(['HKG:00700', 'HKG:700'])
    assert quotes['HKG:00700'].price == quotes['HKG:700'].price == Decimal('400')
    assert quotes['HKG:00700'].ticker == 'HKG:00700'


def test_fake_source_rates_and_pinned_clock(tmp_path):
    source = fake(tmp_path)
    assert source.now() == NOW
    write_fake_quotes(tmp_path, fx={'USD': {'rate': '7.05', 'age_seconds': 5}, 'HKD': {'unavailable': True}},
                      now='2026-11-18T14:30:00+08:00')
    assert source.now() == shanghai('2026-11-18 14:30')
    rates = source.fetch_fx(['USD', 'HKD'])
    assert rates['USD'].rate == Decimal('7.05') and rates['USD'].quote_time == shanghai('2026-11-18 14:29:55')
    assert rates['HKD'].rate is None and rates['HKD'].status == 'unavailable'
