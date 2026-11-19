"""The legacy quote providers: a missing value is 不可用 (None, null), never 0. Fictional data, no network."""
import asyncio

import pytest

from core.agent_tools.grounding import ungrounded_numbers
from core.models import Market
from core.tools import compare_stocks_tool, get_market_indices_tool, get_stock_quote_tool
from providers.market_data.akshare_provider import AKShareProvider
from providers.market_data.eastmoney_provider import EastmoneyDirectProvider
from providers.market_data.google_finance_provider import GoogleFinanceProvider

# Eastmoney before the open or for a suspended stock: "-" for every price, zero volume, the previous close known.
SUSPENDED = {'f43': '-', 'f44': '-', 'f45': '-', 'f46': '-', 'f47': 0, 'f48': 0, 'f57': '600001', 'f58': '示例甲',
             'f60': 11.8, 'f168': '-', 'f162': '-', 'f169': '-', 'f170': '-'}


class Answering:
    """A legacy provider that answers every symbol with Eastmoney's parse of ``payload``."""
    name = 'eastmoney'

    def __init__(self, payload):
        self.payload = payload

    async def get_quote(self, symbol, market):
        return EastmoneyDirectProvider()._quote_from_payload(symbol, dict(self.payload, f57=symbol))


def test_eastmoney_s_missing_price_is_unavailable_never_zero():
    """Release gate: 「-」 with previous close 11.80 became ¥0.00 and -100% in get_stock_quote, and grounded both."""
    quote = EastmoneyDirectProvider()._quote_from_payload('600001', SUSPENDED)
    assert (quote.price, quote.open, quote.high, quote.low) == (None, None, None, None)
    assert quote.prev_close == 11.8
    assert quote.change is None and quote.change_pct is None and quote.amplitude is None
    assert quote.to_dict()['change_pct'] is None

    result = asyncio.run(get_stock_quote_tool(Answering(SUSPENDED)).function(symbol='600001'))
    assert result['price'] is None and result['change'] is None and result['change_pct'] is None
    assert result['open'] is None and result['high'] is None and result['low'] is None
    assert result['status'] == 'unavailable' and '不可用' in str(result['reason'])
    assert ungrounded_numbers('示例甲现价 0.00 元，跌幅 -100%。', [result]) == ['0.00元', '100%']


@pytest.mark.parametrize('field', ['f43', 'f44', 'f45', 'f46', 'f47', 'f48', 'f60', 'f168', 'f162'])
@pytest.mark.parametrize('marker', ['-', '', None, '--', 'N/A'])
def test_every_eastmoney_field_reads_a_missing_marker_as_none(field, marker):
    payload = {'f43': 12.3, 'f44': 12.5, 'f45': 12.0, 'f46': 12.1, 'f47': 1000, 'f48': 123000.0, 'f57': '600001',
               'f58': '示例甲', 'f60': 11.8, 'f168': 1.2, 'f162': 15.5}
    payload[field] = marker
    quote = EastmoneyDirectProvider()._quote_from_payload('600001', payload)
    assert quote is not None, 'one missing field does not lose the whole quote'
    names = {'f43': 'price', 'f44': 'high', 'f45': 'low', 'f46': 'open', 'f47': 'volume', 'f48': 'amount',
             'f60': 'prev_close', 'f168': 'turnover', 'f162': 'pe'}
    assert getattr(quote, names[field]) is None
    if field in ('f43', 'f60'):
        assert quote.change is None and quote.change_pct is None


def test_indices_and_comparisons_say_unavailable_too():
    provider = Answering(SUSPENDED)
    indices = asyncio.run(get_market_indices_tool(provider).function())['indices']
    assert indices and all(item['price'] is None and item['change_pct'] is None and item['status'] == 'unavailable'
                           for item in indices)
    compared = asyncio.run(compare_stocks_tool(provider).function(symbols=['600001']))['comparison']
    assert compared[0]['price'] is None and compared[0]['change_pct'] is None and compared[0]['status'] == 'unavailable'


def test_the_chat_service_quote_says_null_never_zero():
    from backend.services.agent_service import AgentService
    quote = EastmoneyDirectProvider()._quote_from_payload('600001', SUSPENDED)
    serialized = AgentService()._serialize_quote(quote)
    assert serialized['price'] is None and serialized['change'] is None and serialized['change_pct'] is None
    no_previous = EastmoneyDirectProvider()._quote_from_payload('600001', dict(SUSPENDED, f43=12.3, f60='-'))
    serialized = AgentService()._serialize_quote(no_previous)
    assert serialized['price'] == 12.3 and serialized['change'] is None and serialized['change_pct'] is None


def test_akshare_reads_a_missing_marker_or_nan_as_none():
    import pandas as pd
    frame = pd.DataFrame([{'代码': '600001', '名称': '示例甲', '最新价': '-', '今开': float('nan'), '最高': '-',
                           '最低': '-', '昨收': 11.8, '成交量': '-', '成交额': float('nan')}])
    provider = AKShareProvider()
    provider._ak = type('FakeAkshare', (), {'stock_zh_a_spot_em': staticmethod(lambda: frame)})()
    quote = provider._get_a_share_quote_sync('600001')
    assert (quote.price, quote.open, quote.high, quote.low, quote.volume, quote.amount) == (None,) * 6
    assert quote.prev_close == 11.8 and quote.change_pct is None


def test_yahoo_reads_a_missing_or_zero_price_as_none():
    provider = GoogleFinanceProvider()
    quote = provider._build_quote('AAPL', Market.US_STOCK, {'shortName': 'Example', 'regularMarketPreviousClose': 190.5})
    assert quote.price is None and quote.open is None and quote.change_pct is None and quote.amount is None
    zero = provider._build_quote('AAPL', Market.US_STOCK, {'regularMarketPrice': 0, 'regularMarketPreviousClose': 0})
    assert zero.price is None and zero.prev_close is None
    live = provider._build_quote('AAPL', Market.US_STOCK, {'regularMarketPrice': 191.2, 'regularMarketPreviousClose': 190.5,
                                                           'regularMarketVolume': 0})
    assert live.price == 191.2 and live.volume == 0, 'a zero volume is a real zero'


def test_the_legacy_portfolio_keeps_no_zero_price_from_a_missing_quote(tmp_path, monkeypatch):
    from providers.portfolio.manual import ManualPortfolioProvider
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))          # no ledger: the legacy portfolio is in use
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)

    class Missing:
        async def get_quotes(self, symbols, market):
            return [EastmoneyDirectProvider()._quote_from_payload(symbol, dict(SUSPENDED, f57=symbol)) for symbol in symbols]

        async def get_quote(self, symbol, market):
            return EastmoneyDirectProvider()._quote_from_payload(symbol, dict(SUSPENDED, f57=symbol))

    portfolio = ManualPortfolioProvider(data_file=str(tmp_path / 'portfolio.json'), market_provider=Missing())
    portfolio.add_position(symbol='600001', name='示例甲', quantity=100, cost_price=10.0, current_price=12.0)
    asyncio.run(portfolio.refresh())
    [position] = asyncio.run(portfolio.get_positions())
    assert position.current_price != 0, 'a missing quote never values the holding at zero'


# ---------------------------------------------------------------- round 5: a missing price is a miss; given fields stay

def offline_eastmoney(payload):
    """Eastmoney's own get_quote over ``payload``, without the network."""
    class Offline(EastmoneyDirectProvider):
        async def _fetch_quote_data(self, symbol):
            return dict(payload, f57=symbol)
    return Offline()


class Fallback:
    """A second legacy source that has the price."""
    name = 'fallback-example'
    supported_markets = [Market.A_SHARE]

    async def get_quote(self, symbol, market):
        from datetime import datetime
        from core.models import Quote, Stock
        return Quote(stock=Stock(symbol=symbol, name='示例甲', market=Market.A_SHARE), price=12.06, open=11.9,
                     high=12.1, low=11.8, prev_close=11.8, volume=1000, amount=12060.0, timestamp=datetime.now())

    async def get_quotes(self, symbols, market):
        return [await self.get_quote(symbol, market) for symbol in symbols]


def multi(*providers):
    from providers.market_data.multi_source_provider import MultiSourceProvider
    return MultiSourceProvider(list(providers))


@pytest.mark.parametrize('volume', ['-', 0])
def test_r5_a_missing_price_asks_the_next_source(volume):
    """Round-5 regression: with price and volume "-" the assistant's quote stopped at Eastmoney with null; 40868ac
    asked the next source, which gave 12.06 (with volume 0 it gave Eastmoney's fake 0.00, the round-4 defect)."""
    payload = dict(SUSPENDED, f47=volume)
    assert asyncio.run(multi(offline_eastmoney(payload), Fallback()).get_quote('600001', Market.A_SHARE)).price == 12.06
    quotes = asyncio.run(multi(offline_eastmoney(payload), Fallback()).get_quotes(['600001'], Market.A_SHARE))
    assert [quote.price for quote in quotes] == [12.06]
    result = asyncio.run(get_stock_quote_tool(multi(offline_eastmoney(payload), Fallback())).function(symbol='600001'))
    assert (result['price'], result['prev_close'], result['status']) == (12.06, 11.8, 'time_unknown')


def test_r5_when_every_source_misses_the_answer_is_unavailable_with_what_was_given():
    """Round-5 regression: with the price missing and zero volume the tool also hid the previous close the source
    gave (40868ac showed 11.80, beside its fake 0.00 and -100%)."""
    for provider in (multi(offline_eastmoney(SUSPENDED)),
                     multi(offline_eastmoney(SUSPENDED), offline_eastmoney(dict(SUSPENDED, f60='-')))):
        result = asyncio.run(get_stock_quote_tool(provider).function(symbol='600001'))
        assert (result['prev_close'], result['volume']) == (11.8, 0), result
        assert '示例甲' in str(result['name'])
        assert (result['price'], result['change'], result['change_pct'], result['status']) == (
            None, None, None, 'unavailable')
        assert ungrounded_numbers('示例甲现价 0.00 元，跌幅 -100%。', [result]) == ['0.00元', '100%']
        assert ungrounded_numbers('示例甲昨收 11.80 元', [result]) == []
    quotes = asyncio.run(multi(offline_eastmoney(SUSPENDED)).get_quotes(['600001'], Market.A_SHARE))
    assert [(quote.price, quote.prev_close) for quote in quotes] == [(None, 11.8)]


def test_r5_a_zero_price_claim_needs_a_zero_price_even_with_the_holdings_context(tmp_path, monkeypatch):
    """C3.3: with the holdings context 「示例甲现价 0.00 元，跌幅 -100%。」 passed: 0.00 matched the fund's 0.00 and 100
    the day profit's 100.00 (40868ac passed it too, its tool itself giving 0.00 and -100%)."""
    from datetime import timedelta
    from core import agent_context
    from core.ledger import active_ledger
    from core.market.runtime import set_market_runtime
    from tests.fakes import FakeClock, ScriptedSource, build_ledger, fake_runtime, fx_rate, opening, position, quote
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    build_ledger(tmp_path, [opening('Main', cash={'CNY': '1000'}, date='2026-11-13',
                                    positions=[position('SHA:600001', '100', '10', '示例甲')])])
    clock = FakeClock('2026-11-17 10:00')
    now = clock.now()
    runtime = fake_runtime(clock, ScriptedSource(
        {'SHA:600001': quote('SHA:600001', '12', prev_close='11', at=now - timedelta(seconds=3), name='示例甲')},
        {'USD': fx_rate('USD', '7', at=now), 'HKD': fx_rate('HKD', '0.9', at=now)}))
    set_market_runtime(runtime)
    try:
        evidence = agent_context.build_live_context(active_ledger(), runtime).evidence
    finally:
        set_market_runtime(None)
    assert {'cash_cny': '1000.00', 'fund_cny': '0.00'} in evidence
    result = asyncio.run(get_stock_quote_tool(multi(offline_eastmoney(SUSPENDED))).function(symbol='600001'))
    claim = '示例甲现价 0.00 元，跌幅 -100%。'
    assert ungrounded_numbers(claim, [result], context_numbers=evidence) == ['0.00元', '100%']
    # both ways: a zero amount still grounds a zero amount, and a price claim that is not zero is as before
    assert ungrounded_numbers('基金 0.00 元，现价 12 元，当日盈亏 100.00 元', [result], context_numbers=evidence) == []
    assert ungrounded_numbers('现价 0.00 元', [{'fund_cny': '0.00'}]) == ['0.00元']
    assert ungrounded_numbers('现价为 ¥0', [{'cash': '0'}]) == ['0']
    assert ungrounded_numbers('当日盈亏 0.00 元，现金 0 元', [{'day_pnl': '0.00', 'cash': '0'}]) == []
    assert ungrounded_numbers('现价 12.00 元', [{'day_pnl': '12.00'}]) == []


# ---------------------------------------------------------------- round 6: the previous close across sources; one fallback

class PreviousCloseOnly:
    """A later legacy source without a price, which gives the previous close (AKShare's "-" for the price)."""
    supported_markets = [Market.A_SHARE]

    def __init__(self, prev_close, name='previous-close-example'):
        self.prev_close, self.name = prev_close, name

    async def get_quote(self, symbol, market):
        from datetime import datetime
        from core.models import Quote, Stock
        return Quote(stock=Stock(symbol=symbol, name='示例甲', market=Market.A_SHARE), price=None, open=None,
                     high=None, low=None, prev_close=self.prev_close, volume=0, amount=None, timestamp=datetime.now())

    async def get_quotes(self, symbols, market):
        return [await self.get_quote(symbol, market) for symbol in symbols]


def test_r6_the_first_previous_close_any_source_gives_is_kept():
    """Round-6 regression (cr_v6cs B9): the first source has neither price nor previous close, the second only a
    previous close of 11.80; 40868ac showed 11.80, round 5 null for both and footnoted a correct restatement."""
    neither = offline_eastmoney(dict(SUSPENDED, f60='-'))
    result = asyncio.run(get_stock_quote_tool(multi(neither, PreviousCloseOnly(11.8))).function(symbol='600001'))
    assert (result['price'], result['prev_close'], result['change'], result['status']) == (None, 11.8, None, 'unavailable')
    assert ungrounded_numbers('示例甲昨收 11.80 元，现价不可用', [result]) == []
    quotes = asyncio.run(multi(offline_eastmoney(dict(SUSPENDED, f60='-')), PreviousCloseOnly(11.8))
                         .get_quotes(['600001'], Market.A_SHARE))
    assert [(quote.price, quote.prev_close) for quote in quotes] == [(None, 11.8)]
    # both ways: the first source's own previous close stays first
    result = asyncio.run(get_stock_quote_tool(multi(offline_eastmoney(SUSPENDED), PreviousCloseOnly(11.5)))
                         .function(symbol='600001'))
    assert result['prev_close'] == 11.8


class Batch:
    """A fallback that answers a batch after ``delay`` seconds and records what it was asked."""
    supported_markets = [Market.A_SHARE]

    def __init__(self, name, delay=0.0, price=12.06):
        self.name, self.delay, self.price = name, delay, price
        self.batches, self.singles = [], []

    def _quote(self, symbol):
        from datetime import datetime
        from core.models import Quote, Stock
        return Quote(stock=Stock(symbol=symbol, name='示例', market=Market.A_SHARE), price=self.price, open=None,
                     high=None, low=None, prev_close=11.8, volume=1000, amount=None, timestamp=datetime.now())

    async def get_quote(self, symbol, market):
        self.singles.append(symbol)
        await asyncio.sleep(self.delay)
        return self._quote(symbol)

    async def get_quotes(self, symbols, market):
        self.batches.append(list(symbols))
        await asyncio.sleep(self.delay)
        return [self._quote(symbol) for symbol in symbols]


class Mixed(EastmoneyDirectProvider):
    """Eastmoney's own parse of a batch: the symbols in ``suspended`` have "-" for every price."""

    def __init__(self, suspended):
        super().__init__()
        self.suspended = suspended

    async def _fetch_quote_data(self, symbol):
        if symbol in self.suspended:
            return dict(SUSPENDED, f57=symbol)
        return dict(SUSPENDED, f43=10.5, f57=symbol)


def test_r6_the_symbols_eastmoney_cannot_price_go_to_the_fallback_in_one_batch():
    """Round-6 regression (cr_v6c B10): the three-index view without Eastmoney prices took 1.05 s at 40868ac with a
    one-second Yahoo (one batch), 3.06 s in round 5 (one request per symbol)."""
    import time
    from providers.market_data.multi_source_provider import MultiSourceProvider
    indices = ['000001', '399001', '399006']
    fallback = Batch('yahoo-example', delay=0.4)
    started = time.monotonic()
    quotes = asyncio.run(MultiSourceProvider([Mixed(set(indices)), fallback]).get_quotes(indices, Market.A_SHARE))
    elapsed = time.monotonic() - started
    assert [(quote.stock.symbol, quote.price) for quote in quotes] == [(symbol, 12.06) for symbol in indices]
    assert fallback.batches == [indices] and fallback.singles == [], 'one fallback batch, no request per symbol'
    assert elapsed < 1.0, elapsed


def test_r6_all_fallback_work_of_a_batch_fits_one_timeout():
    """Round-6 regression (cr_v6cs B10): two suspended stocks and one quote: 0.02 s at 40868ac, 12.03 s in round 5
    with a fallback that timed out (one timeout per symbol). Now one timeout in all, and the answered symbol is
    never sent to a fallback."""
    import time
    from providers.market_data.multi_source_provider import MultiSourceProvider
    hanging, never = Batch('hanging-example', delay=5), Batch('later-example')
    provider = MultiSourceProvider([Mixed({'600001', '600002'}), hanging, never], provider_timeout=0.6)
    started = time.monotonic()
    quotes = asyncio.run(provider.get_quotes(['600001', '600002', '600003'], Market.A_SHARE))
    elapsed = time.monotonic() - started
    assert [(quote.stock.symbol, quote.price, quote.prev_close) for quote in quotes] == [
        ('600001', None, 11.8), ('600002', None, 11.8), ('600003', 10.5, 11.8)]
    assert hanging.batches == [['600001', '600002']] and hanging.singles == [] and never.batches == []
    assert elapsed < 1.1, elapsed
    # both ways: a batch Eastmoney answers in full asks no fallback at all
    answered, idle = Mixed(set()), Batch('idle-example')
    quotes = asyncio.run(MultiSourceProvider([answered, idle]).get_quotes(['600001', '600002'], Market.A_SHARE))
    assert [quote.price for quote in quotes] == [10.5, 10.5] and idle.batches == [] and idle.singles == []
    # and Eastmoney's "-" for price and volume still gets the next source's 12.06 (round 5, cr_v5c B5)
    quotes = asyncio.run(MultiSourceProvider([offline_eastmoney(dict(SUSPENDED, f47='-')), Fallback()])
                         .get_quotes(['600001'], Market.A_SHARE))
    assert [quote.price for quote in quotes] == [12.06]
