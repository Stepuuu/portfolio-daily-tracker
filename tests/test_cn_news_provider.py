"""CnNewsProvider and BreadthProvider against a fake ``akshare`` module. No network."""
import asyncio
import sys
import time
import types

import pandas as pd
import pytest

from providers.market_data.breadth_provider import BreadthProvider
from providers.news.cn_news_provider import CnNewsProvider, FakeCnNewsProvider, map_columns


def run(coroutine):
    return asyncio.run(coroutine)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def install(monkeypatch, **functions):
    module = types.ModuleType('akshare')
    for name, function in functions.items():
        setattr(module, name, function)
    monkeypatch.setitem(sys.modules, 'akshare', module)
    return module


NEWS = pd.DataFrame({
    '关键词': ['600519', '600519', '600519'],
    '新闻标题': ['旧闻', '最新消息', '较早消息'],
    '新闻内容': ['内容甲', '内容乙', '内容丙'],
    '发布时间': ['2026-11-10 08:00:00', '2026-11-16 17:14:00', '2026-11-12 09:30:00'],
    '文章来源': ['来源甲', '来源乙', '来源丙'],
    '新闻链接': ['http://example.invalid/1', 'http://example.invalid/2', 'http://example.invalid/3']})


def test_news_happy_path_is_sorted_labelled_and_cached(monkeypatch):
    calls = []
    install(monkeypatch, stock_news_em=lambda symbol: calls.append(symbol) or NEWS)
    clock = Clock()
    provider = CnNewsProvider(clock=clock)
    first = run(provider.stock_news('600519', 2))
    assert [i['title'] for i in first['items']] == ['最新消息', '较早消息']
    assert first['total'] == 3 and first['cached'] is False
    assert first['data_date'] == '2026-11-16' and 'stock_news_em' in first['source']
    assert first['fetched_at'][:4] == '2026'
    assert calls == ['600519']
    second = run(provider.stock_news('600519', 3))
    assert second['cached'] is True and len(second['items']) == 3 and calls == ['600519'], 'served from cache'
    clock.t += 301
    assert run(provider.stock_news('600519', 1))['cached'] is False and len(calls) == 2
    assert 'link' not in str(first) and 'example.invalid' not in str(first), 'urls are not carried'


def test_renamed_title_column_is_a_fixed_error_that_does_not_echo_the_columns(monkeypatch):
    # was: the error listed the columns found; third-party text must not reach the model
    frame = NEWS.rename(columns={'新闻标题': '标题文字'})
    install(monkeypatch, stock_news_em=lambda symbol: frame)
    out = run(CnNewsProvider().stock_news('600519'))
    assert set(out) == {'error', 'source'} and '列' in out['error'] and '标题文字' not in out['error']


def test_alias_and_missing_optional_columns_still_work(monkeypatch):
    frame = NEWS.rename(columns={'新闻标题': '标题', '发布时间': '发布日期'}).drop(columns=['文章来源', '新闻内容'])
    install(monkeypatch, stock_news_em=lambda symbol: frame)
    out = run(CnNewsProvider().stock_news('600519'))
    assert 'error' not in out and out['items'][0]['title'] == '最新消息' and out['items'][0]['origin'] == ''


def test_missing_function_and_missing_package(monkeypatch):
    install(monkeypatch)       # akshare without stock_news_em
    out = run(CnNewsProvider().stock_news('600519'))
    assert set(out) == {'error', 'source'} and 'stock_news_em' in out['error']
    monkeypatch.setitem(sys.modules, 'akshare', None)       # import raises ImportError
    out = run(CnNewsProvider().stock_news('600519'))
    assert set(out) == {'error', 'source'} and 'akshare' in out['error']


def test_timeout_is_reported_not_raised(monkeypatch):
    install(monkeypatch, stock_news_em=lambda symbol: time.sleep(0.6) or NEWS)
    started = time.monotonic()
    out = run(CnNewsProvider(timeout=0.05).stock_news('600519'))
    assert time.monotonic() - started < 0.5
    assert set(out) == {'error', 'source'} and '超时' in out['error']


def test_empty_frame_is_a_valid_empty_answer(monkeypatch):
    install(monkeypatch, stock_news_em=lambda symbol: NEWS.iloc[0:0])
    out = run(CnNewsProvider().stock_news('600519'))
    assert 'error' not in out and out['items'] == [] and out['data_date'] is None and out['total'] == 0


def test_exceptions_and_junk_results_never_escape_and_are_not_cached(monkeypatch):
    state = {'n': 0}

    def flaky(symbol):
        state['n'] += 1
        if state['n'] == 1:
            raise ConnectionError('secret-proxy.invalid refused')
        if state['n'] == 2:
            return ['not', 'a', 'frame']
        return NEWS

    install(monkeypatch, stock_news_em=flaky)
    provider = CnNewsProvider()
    first = run(provider.stock_news('600519'))
    assert set(first) == {'error', 'source'} and first['error'] == '数据源连接失败' and 'secret' not in first['error']
    assert set(run(provider.stock_news('600519'))) == {'error', 'source'}
    assert 'error' not in run(provider.stock_news('600519')), 'a failure must not be remembered'


def test_announcements_ask_for_a_bounded_date_window(monkeypatch):
    seen = {}
    frame = pd.DataFrame({'代码': ['600519'] * 2, '名称': ['甲'] * 2, '公告标题': ['公告一', '公告二'],
                          '公告类型': ['其他', '年报'], '公告日期': ['2026-10-31', '2026-11-03'], '网址': ['u', 'v']})

    def notice(security, symbol, begin_date, end_date):
        seen.update(security=security, symbol=symbol, begin=begin_date, end=end_date)
        return frame

    install(monkeypatch, stock_individual_notice_report=notice)
    out = run(CnNewsProvider().announcements('600519', 5))
    assert seen['security'] == '600519' and len(seen['begin']) == 8 and seen['begin'] < seen['end']
    assert [i['title'] for i in out['items']] == ['公告二', '公告一'] and out['data_date'] == '2026-11-03'
    assert out['items'][0]['kind'] == '年报'


def test_flash_is_off_by_default_and_never_calls_akshare(monkeypatch):
    called = []
    install(monkeypatch, stock_info_global_cls=lambda symbol: called.append(1))
    monkeypatch.delenv('TRACKER_AGENT_CLS_FLASH', raising=False)
    out = run(CnNewsProvider().market_flash())
    assert set(out) == {'error', 'source'} and 'TRACKER_AGENT_CLS_FLASH' in out['error'] and called == []
    monkeypatch.setenv('TRACKER_AGENT_CLS_FLASH', '1')
    frame = pd.DataFrame({'标题': ['快讯'], '内容': ['正文'], '发布日期': ['2026-11-17'], '发布时间': ['09:31:00']})
    install(monkeypatch, stock_info_global_cls=lambda symbol: frame)
    out = run(CnNewsProvider().market_flash())
    assert out['items'][0]['published'] == '2026-11-17 09:31:00' and out['data_date'] == '2026-11-17'


def test_map_columns_helper():
    assert map_columns(['a', ' 标题 '], {'title': ('标题',), 'x': ('zzz',)}) == {'title': '标题', 'x': None}


def test_fake_twin_serves_scripts_and_never_invents():
    fake = FakeCnNewsProvider({'news': {'source': 's', 'items': []}})
    assert run(fake.stock_news('600519')) == {'source': 's', 'items': []}
    assert set(run(fake.announcements('600519'))) == {'error', 'source'}
    assert fake.calls[0][0] == 'news'


# ---------------------------------------------------------------- breadth

ACTIVITY = pd.DataFrame({'item': ['上涨', '涨停', '真实涨停', 'st st*涨停', '下跌', '跌停', '真实跌停', 'st st*跌停',
                                  '平盘', '停牌', '活跃度', '统计日期'],
                         'value': ['896.0', '36.0', '33.0', '0.0', '4245.0', '79.0', '65.0', '7.0', '76.0', '7.0',
                                   '17.15%', '2026-11-17 14:35:00']})
BOARD = pd.DataFrame({'排名': [1, 2, 3], '板块名称': ['板块甲', '板块乙', '板块丙'], '板块代码': ['a', 'b', 'c'],
                      '最新价': [1.0, 2.0, 3.0], '涨跌额': [0.1, -0.2, 0.3], '涨跌幅': [4.43, -2.5, 3.07],
                      '总市值': [1, 2, 3], '换手率': [2.45, 1.0, 4.5], '上涨家数': [8, 1, 7], '下跌家数': [0, 9, 4],
                      '领涨股票': ['甲股', '乙股', '丙股'], '领涨股票-涨跌幅': [11.97, 1.0, 10.02]})


def test_breadth_is_off_by_default(monkeypatch):
    called = []
    install(monkeypatch, stock_market_activity_legu=lambda: called.append(1) or ACTIVITY)
    monkeypatch.delenv('TRACKER_AGENT_BREADTH', raising=False)
    provider = BreadthProvider()
    assert 'TRACKER_AGENT_BREADTH' in run(provider.market_activity())['error']
    assert 'TRACKER_AGENT_BREADTH' in run(provider.boards())['error'] and called == []


def test_market_activity_parses_counts_and_stat_time(monkeypatch):
    install(monkeypatch, stock_market_activity_legu=lambda: ACTIVITY)
    out = run(BreadthProvider(enabled=True).market_activity())
    assert out['counts']['up'] == 896 and out['counts']['limit_down_real'] == 65 and out['counts']['flat'] == 76
    assert out['activity_pct'] == 17.15 and out['data_date'] == '2026-11-17'
    assert out['stat_time'] == '2026-11-17 14:35:00'


def test_market_activity_renamed_items_and_columns(monkeypatch):
    install(monkeypatch, stock_market_activity_legu=lambda: ACTIVITY.assign(item=lambda d: d['item'] + 'x'))
    out = run(BreadthProvider(enabled=True).market_activity())
    assert set(out) == {'error', 'source'}
    install(monkeypatch, stock_market_activity_legu=lambda: ACTIVITY.rename(columns={'value': '数值2'}))
    assert set(run(BreadthProvider(enabled=True).market_activity())) == {'error', 'source'}


def test_boards_sort_and_limit_on_the_whole_table(monkeypatch):
    install(monkeypatch, stock_board_industry_name_em=lambda: BOARD)
    provider = BreadthProvider(enabled=True)
    top = run(provider.boards('industry', 2, 'gainers'))
    assert [i['name'] for i in top['items']] == ['板块甲', '板块丙']
    bottom = run(provider.boards('industry', 1, 'losers'))
    assert [i['name'] for i in bottom['items']] == ['板块乙'] and bottom['items'][0]['change_pct'] == -2.5
    assert top['data_date_basis'] == 'fetch_time'
    assert set(run(provider.boards('nonsense'))) == {'error', 'source'}


def test_boards_missing_function_renamed_columns_and_timeout(monkeypatch):
    install(monkeypatch)
    assert 'stock_board_concept_name_em' in run(BreadthProvider(enabled=True).boards('concept'))['error']
    install(monkeypatch, stock_board_concept_name_em=lambda: BOARD.rename(columns={'涨跌幅': '幅度'}))
    assert '列' in run(BreadthProvider(enabled=True).boards('concept'))['error']
    install(monkeypatch, stock_board_concept_name_em=lambda: time.sleep(0.5) or BOARD)
    out = run(BreadthProvider(enabled=True, board_timeout=0.05).boards('concept'))
    assert '超时' in out['error']
