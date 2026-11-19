"""A provider call that hangs must not take the others down with it."""
import asyncio
import sys
import threading
import time
import types
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from core.market.bars import BarError, LoaderBarSource
from providers.market_data.breadth_provider import BreadthProvider
from providers.news.cn_news_provider import ERR_BUSY, CnNewsProvider

NEWS = pd.DataFrame({'新闻标题': ['最新消息'], '新闻内容': ['内容'], '发布时间': ['2026-11-16 17:14:00'],
                     '文章来源': ['来源']})
BOARD = pd.DataFrame({'板块名称': ['板块甲'], '涨跌幅': [4.43]})


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def gates():
    """Events that release blocked provider threads, set at teardown whatever happens."""
    made = []

    def make():
        event = threading.Event()
        made.append(event)
        return event

    yield make
    for event in made:
        event.set()
    time.sleep(0.05)


def install(monkeypatch, **functions):
    module = types.ModuleType('akshare')
    for name, function in functions.items():
        setattr(module, name, function)
    monkeypatch.setitem(sys.modules, 'akshare', module)
    return module


def test_three_hung_news_calls_do_not_stop_a_healthy_one(monkeypatch, gates):
    """The reviewer's scenario: three calls block and time out, then the function is replaced."""
    gate = gates()
    started = []
    module = install(monkeypatch, stock_news_em=lambda symbol: started.append(symbol) or gate.wait(5) or NEWS)
    provider = CnNewsProvider(timeout=0.05)
    for code in ('600519', '000001', '600036'):
        out = run(provider.stock_news(code))
        assert set(out) == {'error', 'source'} and '超时' in out['error']
    assert len(started) == 3

    healthy = []
    module.stock_news_em = lambda symbol: healthy.append(symbol) or NEWS
    out = run(provider.stock_news('601318'))
    assert healthy == ['601318'], 'the healthy function must be invoked'
    assert 'error' not in out and out['items'][0]['title'] == '最新消息'


def test_at_the_thread_limit_calls_fail_fast_and_recover_when_stuck_calls_return(monkeypatch, gates):
    gate = gates()
    calls = []
    module = install(monkeypatch, stock_news_em=lambda symbol: calls.append(symbol) or gate.wait(5) or NEWS)
    provider = CnNewsProvider(timeout=0.02)
    limit = provider._pool('stock_news_em').max_threads
    for i in range(limit):
        assert '超时' in run(provider.stock_news(f'60000{i}'))['error']
    assert provider.stuck('stock_news_em') == limit

    module.stock_news_em = lambda symbol: calls.append('healthy') or NEWS
    started = time.monotonic()
    out = run(provider.stock_news('601318'))
    assert time.monotonic() - started < 0.5, 'must not queue'
    assert out == {'error': ERR_BUSY, 'source': out['source']} and 'healthy' not in calls

    gate.set()
    deadline = time.monotonic() + 2
    while provider.stuck('stock_news_em') and time.monotonic() < deadline:
        time.sleep(0.01)
    assert provider.stuck('stock_news_em') == 0
    assert 'error' not in run(provider.stock_news('601318')) and calls[-1] == 'healthy'


def test_functions_are_isolated_from_each_other(monkeypatch, gates):
    gate = gates()
    install(monkeypatch, stock_news_em=lambda symbol: gate.wait(5) or NEWS,
            stock_individual_notice_report=lambda **kw: pd.DataFrame(
                {'公告标题': ['公告'], '公告类型': ['其他'], '公告日期': ['2026-11-03']}))
    provider = CnNewsProvider(timeout=0.02)
    for i in range(provider._pool('stock_news_em').max_threads):
        run(provider.stock_news(f'60000{i}'))
    assert 'error' not in run(provider.announcements('600519'))


def test_breadth_provider_recovers_from_hung_calls_too(monkeypatch, gates):
    gate = gates()
    module = install(monkeypatch, stock_board_industry_name_em=lambda: gate.wait(5) or BOARD)
    provider = BreadthProvider(enabled=True, board_timeout=0.03)
    for _ in range(3):
        assert '超时' in run(provider.boards('industry'))['error']
        provider._cache.clear()
    assert provider.stuck('stock_board_industry_name_em') == 3
    module.stock_board_industry_name_em = lambda: BOARD
    out = run(provider.boards('industry'))
    assert 'error' not in out and out['items'][0]['name'] == '板块甲'


def test_a_timeout_argument_is_passed_when_the_library_function_accepts_one(monkeypatch):
    seen = {}

    def with_timeout(symbol, timeout=None):
        seen['with'] = timeout
        return NEWS

    install(monkeypatch, stock_news_em=with_timeout)
    run(CnNewsProvider(timeout=7).stock_news('600519'))
    assert seen['with'] == 7

    seen.clear()
    install(monkeypatch, stock_news_em=lambda symbol: seen.setdefault('plain', True) and NEWS)
    run(CnNewsProvider(timeout=7).stock_news('600519'))
    assert seen == {'plain': True}, 'a function without a timeout parameter is not given one'


def test_hung_threads_are_daemons_so_they_cannot_block_exit(monkeypatch, gates):
    gate = gates()
    install(monkeypatch, stock_news_em=lambda symbol: gate.wait(5) or NEWS)
    run(CnNewsProvider(timeout=0.02).stock_news('600519'))
    workers = [t for t in threading.enumerate() if t.name.startswith('isolated-stock_news_em')]
    assert workers and all(t.daemon for t in workers)


# ---------------------------------------------------------------- bars

class Blocking:
    def __init__(self, gate, frame):
        self.gate, self.frame, self.calls = gate, frame, []

    def get_daily(self, symbol, start, end, adjust='qfq'):
        self.calls.append(symbol)
        if self.gate is not None:
            self.gate.wait(5)
        return self.frame


def _frame():
    index = pd.bdate_range(end='2026-11-16', periods=40)
    return pd.DataFrame({'open': 10.0, 'high': 10.5, 'low': 9.5, 'close': 10.2, 'volume': 1000.0}, index=index)


def _clock():
    return datetime(2026, 11, 17, 9, 0, tzinfo=timezone(timedelta(hours=8)))


def test_bars_have_a_caller_side_timeout(gates):
    blocked = Blocking(gates(), _frame())
    source = LoaderBarSource(lambda: blocked, now=_clock, timeout=0.05)
    started = time.monotonic()
    with pytest.raises(BarError) as caught:
        run(source.get_daily('SHA:600519', 5))
    assert time.monotonic() - started < 0.5 and '超时' in str(caught.value)


def test_bars_recover_after_three_hung_loads_and_report_busy_at_the_limit(gates):
    gate = gates()
    blocked, healthy = Blocking(gate, _frame()), Blocking(None, _frame())
    current = {'loader': blocked}
    source = LoaderBarSource(lambda: current['loader'], now=_clock, timeout=0.02)
    for _ in range(3):
        with pytest.raises(BarError):
            run(source.get_daily('SHA:600519', 5))
    current['loader'] = healthy
    assert run(source.get_daily('SHA:600519', 5)).bars and healthy.calls, 'healthy loader must be invoked'

    current['loader'] = blocked
    for _ in range(source._pool_for("akshare.stock_zh_a_hist").max_threads - 3):
        with pytest.raises(BarError):
            run(source.get_daily('SHA:600519', 5))
    current['loader'] = healthy
    healthy.calls.clear()
    with pytest.raises(BarError) as caught:
        run(source.get_daily('SHA:600519', 5))
    assert str(caught.value) == ERR_BUSY and healthy.calls == []
    gate.set()
    deadline = time.monotonic() + 2
    while source._pool_for("akshare.stock_zh_a_hist").stuck and time.monotonic() < deadline:
        time.sleep(0.01)
    assert run(source.get_daily('SHA:600519', 5)).bars


class BySymbolLoader:
    """Blocks every symbol in ``hung`` until the gate opens; serves the rest at once."""

    def __init__(self, gate, hung):
        self.gate, self.hung, self.calls = gate, set(hung), []

    def get_daily(self, symbol, start, end, adjust='qfq'):
        self.calls.append(symbol)
        if symbol in self.hung:
            self.gate.wait(5)
        return _frame()


def test_a_hung_a_share_endpoint_does_not_starve_other_markets_or_endpoints(gates):
    gate = gates()
    loader = BySymbolLoader(gate, {'600519'})
    source = LoaderBarSource(lambda: loader, now=_clock, timeout=0.02)
    for _ in range(source._pool_for('akshare.stock_zh_a_hist').max_threads):
        with pytest.raises(BarError):
            run(source.get_daily('SHA:600519', 5))
    with pytest.raises(BarError) as caught:                 # the hung endpoint itself is busy
        run(source.get_daily('SHA:600519', 5))
    assert str(caught.value) == ERR_BUSY
    loader.calls.clear()
    assert run(source.get_daily('NASDAQ:AAPL', 5)).bars and loader.calls == ['AAPL']
    assert run(source.get_daily('HKG:700', 5)).bars
    assert run(source.get_daily('SHA:510300', 5)).bars, 'ETF endpoint is another function'
    gate.set()
    deadline = time.monotonic() + 2
    while source._pool_for('akshare.stock_zh_a_hist').stuck and time.monotonic() < deadline:
        time.sleep(0.01)
    assert run(source.get_daily('SHA:600519', 5)).bars


def test_breadth_functions_do_not_share_capacity(monkeypatch, gates):
    gate = gates()
    install(monkeypatch, stock_board_industry_name_em=lambda: gate.wait(5) or BOARD,
            stock_board_concept_name_em=lambda: BOARD)
    provider = BreadthProvider(enabled=True, board_timeout=0.02)
    for _ in range(provider._pool('stock_board_industry_name_em').max_threads):
        provider._cache.clear()
        assert '超时' in run(provider.boards('industry'))['error']
    assert 'error' not in run(provider.boards('concept'))
