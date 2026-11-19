"""get_price_history and get_indicators over the BarSource seam. Offline, fictional data."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from core.market import bars as bars_module
from core.market.bars import BarError, FakeBarSource, LoaderBarSource, set_bar_source
from core.agent_tools import market_tools

END = '2026-11-16'     # a Monday
TODAY = '2026-11-17'


def run(coroutine):
    return asyncio.run(coroutine)


def make_bars(count, end=END, base=10.0):
    dates = pd.bdate_range(end=end, periods=count)
    out = []
    for i, day in enumerate(dates):
        close = round(base + i * 0.1 + (0.3 if i % 3 == 0 else 0), 4)
        out.append({'date': day.strftime('%Y-%m-%d'), 'open': round(close - 0.05, 4), 'high': round(close + 0.2, 4),
                    'low': round(close - 0.2, 4), 'close': close, 'volume': 1000000.0 + i * 1000})
    return out


@pytest.fixture(autouse=True)
def reset_source():
    yield
    set_bar_source(None)


def use(script, **kw):
    source = FakeBarSource(script, today=TODAY, label='fake-bars', **kw)
    set_bar_source(source)
    return source


def test_price_history_envelope_and_coverage():
    use({'SHE:000001': make_bars(120)})
    result = run(market_tools.get_price_history('SHE:000001', 30))
    assert result['source'] == 'fake-bars'
    assert result['data_date'] == END
    assert result['adjust'] == 'qfq' and '前复权' in result['adjust_note']
    assert result['ticker'] == 'SHE:000001' and result['currency'] == 'CNY'
    assert result['fetched_at'].startswith('2026-11-17')
    coverage = result['coverage']
    assert coverage['rows'] == 30 and coverage['last'] == END and coverage['complete'] is True
    assert coverage['requested_end'] == TODAY and coverage['first'] == result['rows'][0][0]
    assert result['columns'] == ['date', 'open', 'high', 'low', 'close', 'volume']
    assert len(result['rows']) == 30 and all(isinstance(v, float) for v in result['rows'][0][1:])
    summary = result['summary']
    assert summary['last_close'] == result['rows'][-1][4]
    first_close, last_close = result['rows'][0][4], result['rows'][-1][4]
    assert summary['window_change_pct'] == pytest.approx((last_close / first_close - 1) * 100, abs=0.01)
    assert 'cite' in result
    json.dumps(result)


def test_short_history_is_flagged_incomplete_not_padded():
    use({'SHE:300001': make_bars(12)})
    result = run(market_tools.get_price_history('SHE:300001', 60))
    assert len(result['rows']) == 12
    assert result['coverage']['complete'] is False and result['coverage']['requested_days'] == 60
    assert any('不完整' in note for note in result['notes'])


def test_stale_last_bar_is_not_complete():
    use({'SHE:000001': make_bars(60, end='2026-10-01')})
    assert run(market_tools.get_price_history('SHE:000001', 30))['coverage']['complete'] is False


def test_days_are_clamped_and_validated():
    source = use({'SHE:000001': make_bars(300)})
    result = run(market_tools.get_price_history('SHE:000001', 9999))
    assert source.calls[-1][1] == 250 and len(result['rows']) == 250
    assert any('上限' in note for note in result['notes'])
    assert len(json.dumps(result, ensure_ascii=False)) <= market_tools.MAX_CHARS
    for bad in (0, -3, 'abc'):
        out = run(market_tools.get_price_history('SHE:000001', bad))
        assert 'error' in out and out['source'] == 'fake-bars' and 'rows' not in out
    assert run(market_tools.get_price_history('SHE:000001', 2.0))['coverage']['rows'] == 2


def test_size_cap_drops_oldest_rows():
    use({'SHE:000001': make_bars(250)})
    original = market_tools.MAX_CHARS
    market_tools.MAX_CHARS = 6000
    try:
        result = run(market_tools.get_price_history('SHE:000001', 250))
    finally:
        market_tools.MAX_CHARS = original
    assert result['truncated'] is True and result['count'] < 250
    assert result['rows'][-1][0] == END, 'the newest bar must survive truncation'


def test_failures_are_errors_with_source_and_no_data():
    use({'SHE:000001': BarError('数据源超时', 'fake-bars')})
    assert run(market_tools.get_price_history('SHE:000001')) == {'error': '数据源超时', 'source': 'fake-bars'}
    for ticker in ('000001', 'IDX:000001', 'garbage', '', None, 12):
        out = run(market_tools.get_price_history(ticker))
        assert set(out) == {'error', 'source'}, ticker
    out = run(market_tools.get_price_history('SHE:999999'))     # source has nothing
    assert set(out) == {'error', 'source'}


def test_unexpected_exception_inside_source_is_contained():
    use({'SHE:000001': RuntimeError('boom <secret>')})
    out = run(market_tools.get_price_history('SHE:000001'))
    assert set(out) == {'error', 'source'} and 'secret' not in json.dumps(out)


# ---------------------------------------------------------------- the real source over a fake loader

class FakeLoader:
    def __init__(self, frame=None, error=None):
        self.frame, self.error, self.calls = frame, error, []

    def get_daily(self, symbol, start, end, adjust='qfq'):
        self.calls.append((symbol, start, end, adjust))
        if self.error:
            raise self.error
        return self.frame


def frame_of(bars):
    frame = pd.DataFrame(bars)
    frame['date'] = pd.to_datetime(frame['date'])
    return frame.set_index('date')


def clock():
    return datetime(2026, 11, 17, 9, 0, tzinfo=timezone(timedelta(hours=8)))


def loader_source(loader, made=None):
    def factory():
        if made is not None:
            made.append(loader)
        return loader
    return LoaderBarSource(factory, now=clock)


def test_loader_source_resolves_the_code_from_the_exchange_prefix():
    loader = FakeLoader(frame_of(make_bars(40)))
    source = loader_source(loader)
    result = run(source.get_daily('SHE:000001', 30))
    assert loader.calls[0][0] == '000001' and loader.calls[0][3] == 'qfq'
    assert result.adjust == 'qfq' and result.currency == 'CNY' and 'stock_zh_a_hist' in result.source
    assert result.coverage['rows'] == 30 and result.coverage['complete'] is True
    assert result.coverage['requested_end'] == TODAY
    assert result.bars[-1]['date'] == END and result.volume_unit == 'shares'


@pytest.mark.parametrize('ticker', ['SHA:000001', 'SHE:600519', 'IDX:000001', '000001', 'SHA:60051', 'SHE:ABCDEF'])
def test_loader_source_refuses_what_it_cannot_map_unambiguously(ticker):
    loader = FakeLoader(frame_of(make_bars(40)))
    with pytest.raises(BarError):
        run(loader_source(loader).get_daily(ticker, 30))
    assert loader.calls == [], 'nothing may be fetched for an ambiguous ticker'


def test_loader_source_maps_hk_and_us_to_the_symbols_the_loader_routes():
    # was: HK was asserted to be Yahoo with 'yahoo_auto_adjust'. The loader routes HK to akshare.stock_hk_hist (qfq);
    # the labels now follow the endpoint (see test_bar_provenance.py for the end-to-end checks).
    loader = FakeLoader(frame_of(make_bars(40)))
    source = loader_source(loader)
    hk = run(source.get_daily('HKG:700', 10))
    assert loader.calls[-1][0] == '0700.HK' and hk.adjust == 'qfq' and hk.currency == 'HKD'
    assert hk.provider == 'akshare.stock_hk_hist' and hk.market == 'HK'
    us = run(source.get_daily('NASDAQ:AAPL', 10))
    assert loader.calls[-1][0] == 'AAPL' and us.currency == 'USD'
    assert us.provider == 'yfinance' and us.adjust == 'yahoo_auto_adjust'


def test_loader_source_failures_become_bar_errors_without_internals():
    for error in (RuntimeError('secret token abc'), ValueError('x')):
        with pytest.raises(BarError) as caught:
            run(loader_source(FakeLoader(error=error)).get_daily('SHA:600519', 10))
        assert 'secret' not in str(caught.value) and caught.value.source
    with pytest.raises(BarError):
        run(loader_source(FakeLoader(frame_of(make_bars(3)).iloc[0:0])).get_daily('SHA:600519', 10))


def test_loader_source_uses_a_fresh_loader_per_call_so_no_cache_is_shared():
    made = []
    loaders = [FakeLoader(frame_of(make_bars(40))), FakeLoader(frame_of(make_bars(40)))]
    source = LoaderBarSource(lambda: (made.append(1), loaders[len(made) - 1])[1], now=clock)
    run(source.get_daily('SHA:600519', 5))
    run(source.get_daily('SHA:600519', 5))
    assert len(made) == 2


def test_loader_call_runs_off_the_event_loop_thread():
    import threading
    seen = {}

    class Recording(FakeLoader):
        def get_daily(self, *args, **kwargs):
            seen['thread'] = threading.current_thread()
            return super().get_daily(*args, **kwargs)

    source = loader_source(Recording(frame_of(make_bars(40))))

    async def go():
        seen['loop_thread'] = threading.current_thread()
        return await source.get_daily('SHA:600519', 5)

    run(go())
    assert seen['thread'] is not seen['loop_thread']


# ---------------------------------------------------------------- indicators

def test_indicators_match_technical_factors_and_state_units():
    from backtesting.factors.technical import TechnicalFactors
    bars = make_bars(250)
    use({'SHE:000001': bars})
    result = run(market_tools.get_indicators('SHE:000001', ['sma_5', 'rsi_14', 'macd'], 3))
    assert result['columns'] == ['date', 'sma_5', 'rsi_14', 'macd', 'macd_signal', 'macd_hist']
    assert len(result['rows']) == 3 and result['rows'][-1][0] == END and result['data_date'] == END
    close = pd.Series([b['close'] for b in bars])
    assert result['rows'][-1][1] == pytest.approx(TechnicalFactors.sma(close, 5).iloc[-1], abs=1e-3)
    assert result['rows'][-1][2] == pytest.approx(TechnicalFactors.rsi(close, 14).iloc[-1], abs=1e-3)
    assert result['units']['rsi_14'] == '0-100' and '前复权' in result['adjust_note']
    assert result['adjust'] == 'qfq' and result['coverage']['last'] == END
    assert 'cite' in result
    json.dumps(result)


def test_indicators_warmup_is_requested_and_insufficient_history_is_null():
    source = use({'SHE:000001': make_bars(20)})
    result = run(market_tools.get_indicators('SHE:000001', 'sma_60, sma_5', 5))
    assert source.calls[-1][1] == 5 + market_tools.INDICATOR_WARMUP
    column = result['columns'].index('sma_60')
    assert all(row[column] is None for row in result['rows']), 'never a made-up value'
    assert all(row[result['columns'].index('sma_5')] is not None for row in result['rows'])
    assert any('只取到' in note for note in result['notes'])


def test_indicator_names_are_an_allow_list():
    use({'SHE:000001': make_bars(200)})
    for bad in (['sma_5', '__import__("os")'], ['rsi_99'], ['close'], 'eval'):
        out = run(market_tools.get_indicators('SHE:000001', bad))
        assert set(out) == {'error', 'source'} and '不支持' in out['error']
    assert set(run(market_tools.get_indicators('SHE:000001', []))) == {'error', 'source'}
    assert set(run(market_tools.get_indicators('SHE:000001', None))) == {'error', 'source'}
    too_many = [f'sma_{n}' for n in (5, 10, 20, 60)] + [f'ema_{n}' for n in (12, 26)] + \
        ['rsi_6', 'rsi_14', 'macd', 'kdj', 'boll', 'atr_14', 'obv']
    assert 'error' in run(market_tools.get_indicators('SHE:000001', too_many))
    assert all(name in market_tools.INDICATOR_NAMES for name in ('macd', 'kdj', 'boll', 'rsi_14'))


def test_indicator_failure_is_an_error_not_a_guess():
    use({'SHE:000001': BarError('没有该代码的日线', 'fake-bars')})
    assert run(market_tools.get_indicators('SHE:000001', ['rsi_14'])) == {'error': '没有该代码的日线', 'source': 'fake-bars'}


def test_tools_are_registered_with_chinese_descriptions_naming_source_and_date():
    from core import agent_tools
    tools = {tool.name: tool for tool in agent_tools.build_all()}
    for name in ('get_price_history', 'get_indicators'):
        description = tools[name].description
        assert 'source' in description and ('data_date' in description or '日期' in description)
        assert '前复权' in description
    assert 'rsi_14' in tools['get_indicators'].description


def test_default_loader_is_the_backtest_dataloader():
    from backtesting.data.loader import DataLoader
    first, second = LoaderBarSource()._make_loader(), LoaderBarSource()._make_loader()
    assert isinstance(first, DataLoader) and first is not second


def test_a_todays_bar_before_the_close_is_marked_provisional():
    source = FakeBarSource({'SHE:000001': make_bars(40, end=TODAY)}, today=TODAY)
    set_bar_source(source)
    result = run(market_tools.get_price_history('SHE:000001', 5))
    assert result['data_date'] == TODAY and result['last_bar_provisional'] is False   # fake stamps 15:30
    from core.market.bars import BarResult
    early = BarResult('SHE:000001', 's', 'qfq', 'CNY', make_bars(3, end=TODAY), {}, TODAY + 'T10:30:00+08:00')
    assert market_tools._provisional(early) is True
