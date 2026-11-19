"""Every data tool returns {error, source} on failure; none raises, none fabricates."""
import asyncio
import json

import pytest

from core import agent_tools
from core.market.bars import BarError, FakeBarSource, set_bar_source
from core.tools import ToolExecutor
from providers.market_data.breadth_provider import FakeBreadthProvider, set_breadth_provider
from providers.news.cn_news_provider import FakeCnNewsProvider, set_cn_news_provider

TICKER = 'SHA:600519'

CALLS = {
    'get_price_history': {'ticker': TICKER, 'days': 10},
    'get_indicators': {'ticker': TICKER, 'names': ['rsi_14'], 'days': 3},
    'get_stock_news': {'ticker': TICKER, 'limit': 3},
    'get_announcements': {'ticker': TICKER, 'limit': 3},
    'get_market_flash': {'limit': 3},
    'get_market_breadth': {},
    'get_sector_board': {'kind': 'industry', 'limit': 3},
}
DATA_KEYS = {'rows', 'items', 'bars', 'counts', 'summary', 'values'}


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setenv('TRACKER_AGENT_CLS_FLASH', '1')
    monkeypatch.setenv('TRACKER_AGENT_BREADTH', '1')
    built = {tool.name: tool for tool in agent_tools.build_all()}
    yield built
    set_bar_source(None)
    set_cn_news_provider(None)
    set_breadth_provider(None)


def install(bars=None, news=None, breadth=None):
    set_bar_source(FakeBarSource(bars or {}, today='2026-11-17', label='fake-bars'))
    set_cn_news_provider(FakeCnNewsProvider(news or {}))
    set_breadth_provider(FakeBreadthProvider(breadth or {}))


def assert_failure(result):
    assert isinstance(result, dict) and set(result) == {'error', 'source'}, result
    assert isinstance(result['error'], str) and result['error'] and isinstance(result['source'], str) and result['source']
    json.dumps(result)


def test_all_seven_tools_exist_when_flags_are_on(tools):
    assert set(CALLS) <= set(tools)


@pytest.mark.parametrize('failure', [
    BarError('boom', 'fake-bars'), RuntimeError('boom'), None])
def test_sources_that_fail_or_know_nothing(tools, failure):
    bars = {TICKER: failure} if failure else {}
    news = {k: failure for k in ('news', 'announcements', 'flash')} if failure else {}
    breadth = {k: failure for k in ('activity', 'boards')} if failure else {}
    install(bars, news, breadth)
    for name, args in CALLS.items():
        result = run(tools[name].function(**args))
        assert_failure(result)


def test_provider_error_dicts_and_garbage_results(tools):
    err = {'error': '数据源超时（8 秒）', 'source': 'upstream'}
    for garbage in (err, 'text', 5, None, [], {'items': 'nope'}, {'counts': 'nope'}, {'items': None}):
        install({}, {'news': garbage, 'announcements': garbage, 'flash': garbage},
                {'activity': garbage, 'boards': garbage})
        for name in ('get_stock_news', 'get_announcements', 'get_market_flash', 'get_market_breadth', 'get_sector_board'):
            if garbage is None:
                continue
            assert_failure(run(tools[name].function(**CALLS[name])))
    install({}, {'news': err}, {})
    # a fixed provider message passes through; the provider's own idea of its "source" no longer does
    # (was: result == err, with source 'upstream'): only the tool's known source labels are accepted
    out = run(tools['get_stock_news'].function(**CALLS['get_stock_news']))
    assert out['error'] == err['error'] and out['source'] != 'upstream' and out['source'].startswith('akshare.')


@pytest.mark.parametrize('bad', [None, '', 'abc', 600519, '600519', 'SHA:', 'IDX:000001', 'HKG:700', ['SHA:600519'], {}])
def test_garbage_tickers_for_every_ticker_tool(tools, bad):
    install()
    for name in ('get_price_history', 'get_indicators', 'get_stock_news', 'get_announcements'):
        args = dict(CALLS[name], ticker=bad)
        assert_failure(run(tools[name].function(**args)))


def test_garbage_arguments_never_raise(tools):
    install({TICKER: BarError('x', 'fake-bars')}, {}, {})
    for name, tool in tools.items():
        if name not in CALLS:
            continue
        for kwargs in ({}, {'nonsense': 1}, {'ticker': None, 'days': None, 'limit': None, 'names': 5, 'kind': 3, 'order': []}):
            result = run(tool.function(**kwargs))
            assert isinstance(result, dict) and ('error' in result or 'source' in result), (name, kwargs)
            if 'error' in result:
                assert 'source' in result, (name, kwargs)


def test_executor_path_gives_the_same_shape(tools):
    install({TICKER: BarError('没有数据', 'fake-bars')})
    executor = ToolExecutor(list(tools.values()))
    result = run(executor.execute_tool('get_price_history', {'ticker': TICKER}))
    assert result == {'error': '没有数据', 'source': 'fake-bars'}
    wrong = run(executor.execute_tool('get_price_history', {'tickr': TICKER}))
    assert_failure(wrong)


def test_failures_carry_no_data_keys_and_no_exception_text(tools):
    install({TICKER: RuntimeError('token=SECRET-123 /home/user/file.py')},
            {'news': RuntimeError('token=SECRET-123')}, {})
    for name in ('get_price_history', 'get_indicators', 'get_stock_news'):
        result = run(tools[name].function(**CALLS[name]))
        assert_failure(result)
        assert not DATA_KEYS & set(result)
        assert 'SECRET' not in json.dumps(result) and '/home' not in json.dumps(result)


def test_indicators_never_return_values_when_bars_are_missing(tools):
    install({})
    result = run(tools['get_indicators'].function(ticker=TICKER, names=['sma_5'], days=3))
    assert_failure(result)


def test_tool_schemas_are_valid_for_the_model(tools):
    schemas = ToolExecutor(list(tools.values())).get_tool_schemas()
    assert {s['name'] for s in schemas} >= set(CALLS)
    by_name = {s['name']: s for s in schemas}
    names = by_name['get_indicators']['input_schema']['properties']['names']
    assert names['type'] == 'array' and 'rsi_14' in names['items']['enum']
    assert by_name['get_price_history']['input_schema']['required'] == ['ticker']
    json.dumps(schemas)
