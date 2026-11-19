"""Grounding of realistic Chinese assistant sentences, one documented expectation each.

``ungrounded_numbers`` is conservative: it flags what it can show is missing
from this turn's data and stays quiet when unsure. Cases marked LIMIT document
what it cannot do (the expectation is today's behaviour, not the ideal).
Evidence keeps its unit (re-review G5): a price grounds amounts, a
percentage field grounds percentages, and the difference and the change in
percent between two prices of the data are evidence too.
"""
import pytest

from core.agent_tools.grounding import ungrounded_numbers

PRICE = {
    'source': 'akshare.stock_zh_a_hist（东方财富）', 'fetched_at': '2026-11-17T09:00:00+08:00',
    'data_date': '2026-11-16', 'ticker': 'SHA:600519', 'currency': 'CNY', 'adjust': 'qfq',
    'coverage': {'first': '2026-10-19', 'last': '2026-11-16', 'rows': 20, 'requested_days': 20,
                 'requested_start': '2026-10-01', 'requested_end': '2026-11-17', 'complete': True},
    'units': {'open/high/low/close': 'CNY/股（qfq）', 'volume': '股'},
    'summary': {'first_date': '2026-10-19', 'last_date': '2026-11-16', 'last_close': 1439.1,
                'window_change_pct': 3.38, 'window_high': 1480.6, 'window_low': 1395.02},
    'columns': ['date', 'open', 'high', 'low', 'close', 'volume'],
    'rows': [['2026-11-13', 1405.0, 1420.0, 1398.0, 1410.0, 70123400.0],
             ['2026-11-16', 1410.0, 1480.6, 1395.02, 1439.1, 82691000.0]],
    'count': 2, 'last_bar_provisional': False,
    'notes': ['days 超过上限，已按 250 返回。', '日线截至 data_date，15:05 之后才算收盘。'],
}
NEWS = {
    'source': 'akshare.stock_news_em（东方财富个股新闻）', 'fetched_at': '2026-11-17T09:00:00+08:00',
    'data_date': '2026-11-16', 'count': 1, 'total_available': 40,
    'items': [{'published': '2026-11-16 17:14:00',
               'title': {'untrusted_text': '贵州茅台前三季度营收增长 15.7%，净利润 8.2 亿元，拟每10股派息 276.7 元'}}],
}
BREADTH = {
    'source': 'akshare.stock_market_activity_legu', 'fetched_at': '2026-11-17T15:10:00+08:00',
    'data_date': '2026-11-17', 'stat_time': '2026-11-17 14:35:00',
    'counts': {'up': 896, 'down': 4245, 'limit_up': 36, 'limit_down': 79}, 'activity_pct': 17.15,
}
RESULTS = [PRICE, NEWS, BREADTH]

# (answer, expected ungrounded list, why)
CASES = [
    # --- prices that are in the data
    ('贵州茅台最新收盘价 1439.10 元，数据日期 2026-11-16。', [], 'close, written with two decimals'),
    ('收盘价是 1,439.1 元。', [], 'thousands separator'),
    ('区间最高 1480.60 元，最低 1395.02 元。', [], 'window high and low'),
    ('近期在 1395 元到 1480.6 元之间波动。', [], 'truncated low, exact high'),
    ('近20个交易日区间涨幅 3.38%。', [], 'window_change_pct; "20" is a count of days'),
    ('今天涨了 +3.38%。', [], 'sign is ignored'),
    ('成交量约 8269.1 万股。', [], '82691000 shares written in 万'),
    ('新闻称前三季度营收增长 15.7%，净利润 8.2 亿元。', [], 'quoted from the news result'),
    ('公告拟每10股派息 276.7 元。', [], 'quoted from the news result; "10股" is a count'),
    ('市场上涨 896 家，下跌 4245 家，涨停 36 只。', [], 'plain integers are never checked'),
    ('市场赚钱效应（活跃度）为 17.15%。', [], 'activity_pct'),
    # --- invented numbers
    ('建议止损位 1350.25 元。', ['1350.25元'], 'invented price'),
    ('目标价 1600 元。', ['1600元'], 'invented integer price with a unit'),
    ('净利润 9.2 亿元。', ['9.2亿元'], 'amount differs from the news'),
    ('成交额约 12.5 亿元。', ['12.5亿元'], 'amount in no result'),
    ('市值 2.3 万亿元。', ['2.3万亿元'], 'amount with 万亿'),
    ('活跃度升到 21.3%。', ['21.3%'], 'percentage in no result'),
    ('RSI14 为 62.5。', ['62.5'], 'a bare decimal in no result'),
    ('每10股派息 300 元。', ['300元'], 'dividend differs from the news'),
    # --- date and time metadata is not evidence (the review's cases)
    ('当前股价 2026 元。', ['2026元'], 'year from data_date must not ground a price'),
    ('当前股价 11.00 元。', ['11.00元'], 'month from fetched_at must not ground a price'),
    ('当前股价 16.00 元。', ['16.00元'], 'day from data_date must not ground a price'),
    ('当前股价 0.00 元。', ['0.00元'], 'no unconditional exemption for zero'),
    ('2026年11月16日收盘价 1439.1 元，数据时间 2026-11-17 09:00，14:35 统计。', [], 'dates and times are masked in the answer'),
    ('2025年全年营收增长 18.2%。', ['18.2%'], 'a year in the text does not make the percentage true'),
    # --- not financial
    ('Python 3.10 的格式和 v2.1 版本都可以。', [], 'version numbers'),
    ('1. 先看趋势\n2. 再看成交量\n3. 最后看消息面', [], 'list numbering'),
    ('我列了 3 点原因，其中第 2 点最重要，共 5 个理由。', [], 'counts of what the answer enumerates'),
    ('SHA:600519 的代码是 600519，港股 00700。', [], 'codes'),
    # --- simple arithmetic between two prices of the data (re-review G5)
    ('比前一日涨了 29.10 元。', [], 'the difference of two consecutive closes (1439.1-1410)'),
    ('日涨幅约 2.06%。', [], 'the change from the previous close ((1439.1-1410)/1410)'),
    ('当日振幅 85.58 元。', [], 'high minus low of one bar'),
    ('比前一日涨了 29.20 元。', ['29.20元'], 'a difference that is not the data\'s'),
    ('今日上涨 143910%。', ['143910%'], 'a price never grounds a percentage at another scale'),
    ('当前股价 15.7 元。', ['15.7元'], 'a percentage in the news never grounds a price'),
    ('当前股价 2.06 元。', ['2.06元'], 'a computed change in percent never grounds a price'),
    # --- known limitations: LIMIT
    ('两天收盘价合计 2849.10 元。', ['2849.10元'],
     'LIMIT: only differences and percentage changes are computed; a sum (1410+1439.1) is flagged'),
    ('当前市盈率 25.6 倍。', [], 'LIMIT: "倍" figures are treated as counts and never checked'),
    ('价格区间 1300-1500 元。', ['1500元'], 'LIMIT: only the figure carrying the unit is checked; the bare 1300 is not'),
    ('今天下跌 3.38%。', [], 'LIMIT: the sign/direction is not compared, only the magnitude'),
    ('我们 100% 确定。', ['100%'], 'LIMIT: no special case for 100%, so rhetoric is flagged and the reader is asked to check'),
]


@pytest.mark.parametrize('answer,expected,why', CASES, ids=[f'{i:02d}' for i in range(len(CASES))])
def test_realistic_sentences(answer, expected, why):
    assert ungrounded_numbers(answer, RESULTS) == expected, why


def test_the_table_is_large_enough_and_covers_every_kind():
    assert len(CASES) >= 25
    assert sum(1 for *_, why in CASES if why.startswith('LIMIT')) >= 4


# ---------------------------------------------------------------- the review's three cases, minimal data

REVIEW = [{'data_date': '2026-11-17', 'close': 10.5}]


def test_review_case_date_components_do_not_ground_prices():
    assert ungrounded_numbers('当前股价 2026 元', REVIEW) == ['2026元']
    assert ungrounded_numbers('当前股价 11.00 元', REVIEW) == ['11.00元']
    assert ungrounded_numbers('当前股价 17.00 元', REVIEW) == ['17.00元']
    assert ungrounded_numbers('当前股价 10.50 元', REVIEW) == []


def test_review_case_zero_is_grounded_only_by_a_zero_in_the_data():
    assert ungrounded_numbers('当前股价 0.00 元', REVIEW) == ['0.00元']
    assert ungrounded_numbers('当前股价 0.00 元', [{'close': 0.0}]) == []
    assert ungrounded_numbers('涨跌幅 0.00%', [{'change_pct': 0}]) == []
    assert ungrounded_numbers('涨跌幅 0.00%', REVIEW) == ['0.00%']


def test_review_case_version_numbers_are_not_flagged():
    assert ungrounded_numbers('Python 3.10 的格式', []) == []
    assert ungrounded_numbers('需要 Python 3.10 以上，pandas 2.2 版', []) == []
    assert ungrounded_numbers('报价 3.10', []) == ['3.10']


@pytest.mark.parametrize('key', ['data_date', 'fetched_at', 'quote_time', 'stat_time', 'published', 'first', 'last',
                                 'requested_end', 'as_of_date'])
def test_date_and_time_fields_are_not_evidence(key):
    value = '2026-11-17 14:35:00'
    assert ungrounded_numbers('现价 14.35 元', [{key: value}]) == ['14.35元']
    assert ungrounded_numbers('现价 2026 元', [{key: value}]) == ['2026元']
    assert ungrounded_numbers('现价 1435.00 元', [{key: 1435}]) == ['1435.00元'], 'a numeric date field too'


def test_tool_boilerplate_is_not_evidence():
    result = {'close': 10.5, 'notes': ['已按 250 返回，15:05 后收盘'], 'units': {'rsi_14': '0-100'},
              'cite': '数据来源 12.5', 'coverage': {'rows': 30, 'requested_days': 33}, 'count': 7}
    assert ungrounded_numbers('现价 250 元，100 元，12.5 元，30 元，7 元，0 元', [result]) == [
        '250元', '100元', '12.5元', '30元', '7元', '0元']


# ---------------------------------------------------------------- re-review G5: evidence keeps its unit and scale

def test_g5_a_version_inside_third_party_text_is_not_price_evidence():
    result = {'close': 10.5, 'title': {'untrusted_text': '需要 Python 3.10'}}
    assert ungrounded_numbers('当前股价3.10元', [result]) == ['3.10元']


def test_g5_b_a_price_does_not_ground_a_percentage_a_hundred_times_larger():
    assert ungrounded_numbers('今日上涨1050%', [{'close': 10.5}]) == ['1050%']


def test_g5_c_difference_and_change_between_two_prices_are_accepted():
    assert ungrounded_numbers('上涨1.5元，涨幅15%', [{'previous_close': 10, 'close': 11.5}]) == []


def test_g5_third_party_text_grounds_only_numbers_written_with_their_unit():
    news = {'title': {'untrusted_text': '公司股价 3.10 元，另有 12.5 和 Python 3.10'}}
    assert ungrounded_numbers('股价 3.10 元', [news]) == []
    assert ungrounded_numbers('现价 12.50 元', [news]) == ['12.50元'], 'a bare figure in a headline'


def test_g5_arithmetic_only_within_one_record_or_one_instruments_series():
    holdings = [{'ticker': 'SHA:600001', 'price': 10}, {'ticker': 'SHA:600002', 'price': 12}]
    assert ungrounded_numbers('两者相差 2.00 元', [holdings]) == ['2.00元'], 'two instruments are no series'
    bars = [{'date': '2026-11-13', 'close': 10}, {'date': '2026-11-16', 'close': 12}]
    assert ungrounded_numbers('涨了 2.00 元，涨幅 20.00%', [bars]) == []
    row = {'price': '12', 'cost_price': '10', 'market_value': '1200'}
    assert ungrounded_numbers('现价 12 元，成本价 10 元，每股赚 2.00 元（20.00%）', [row]) == []
    assert ungrounded_numbers('市值比现价多 1188.00 元', [row]) == ['1188.00元'], 'value is no price'


# ---------------------------------------------------------------- review round 3: metadata is never evidence

@pytest.fixture
def no_ledger(tmp_path, monkeypatch):
    from core.market import search
    from core.market.runtime import set_market_runtime
    from tests.fakes import FakeClock, ScriptedSource, fake_runtime
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    set_market_runtime(fake_runtime(FakeClock('2026-11-17 10:00'), ScriptedSource()))
    search.set_symbol_index(search.SymbolIndex(fetchers={}))
    yield tmp_path
    set_market_runtime(None)
    search.set_symbol_index(None)


def test_r3_an_empty_portfolio_result_grounds_no_price(no_ledger):
    """Review finding: an empty get_portfolio grounded 「现价0.00元」 through ledger_revision 0 and its note 「不是 0」."""
    import asyncio
    from core.tools import get_portfolio_tool
    result = asyncio.run(get_portfolio_tool(None).function())
    assert result['accounts'] == [] and result['ledger_revision'] == 0 and '不是 0' in str(result)
    assert ungrounded_numbers('现价0.00元', [result]) == ['0.00元']


def test_r3_revisions_ids_counts_versions_and_tool_text_are_not_evidence():
    """Review finding: revision 37 grounded 「37元」."""
    result = {'ledger_revision': 37, 'revision': 12, 'id': 41, 'rule_id': 23, 'seq': 88, 'version': '1.25',
              'kind_version': '3', 'count': 9, 'trade_count': 7, 'rows': 31, 'n': 6,
              'page': {'offset': 40, 'limit': 50, 'returned': 45},
              'note': '金额是十进制字符串；null 表示不可用，不是 0。最多 64 项。',
              'message': '已提交 5.5 元的方案', 'error': '一次最多 12.5 个', 'reason': '行情延迟 15.5 分钟',
              'notice': '账本有 2.5 条提示'}
    answer = ('现价 37 元、12 元、41 元、23 元、88 元、1.25 元、3 元、9 元、7 元、31 元、6 元、40 元、50 元、45 元、'
              '0.00 元、64 元、5.5 元、12.5 元、15.5 元、2.5 元。')
    assert ungrounded_numbers(answer, [result]) == [
        '37元', '12元', '41元', '23元', '88元', '1.25元', '3元', '9元', '7元', '31元', '6元', '40元', '50元', '45元',
        '0.00元', '64元', '5.5元', '12.5元', '15.5元', '2.5元']


def test_r3_an_error_result_grounds_nothing():
    """A tool that refuses (say, no ledger for a legacy snapshot) proves no number, even one quoted in its error."""
    refusal = {'error': '没有已确认的账本，旧快照（99 股，42.50 美元）不能作为当前持仓', 'source': 'legacy_snapshot'}
    assert ungrounded_numbers('持有 99 股，现价 42.50 美元', [refusal]) == ['99股', '42.50美元']


# ---------------------------------------------------------------- review round 3: the producer's declared unit

def test_r3_a_brief_fact_keeps_its_declared_unit():
    """Review finding: a brief's ``value=7, unit=count, metric=trade_count`` grounded 「7元」."""
    trades = {'label': '今日成交', 'value': 7, 'unit': 'count', 'text': '7 笔（买入 4，卖出 3）', 'metric': 'trade_count'}
    pnl = {'label': '今日盈亏合计', 'value': '1234.50', 'unit': 'CNY', 'text': '1,234.50 元', 'metric': 'day_pnl',
           'figures': {'day_pnl_pct': '2.35'}}
    change = {'label': '上证指数', 'value': '0.85', 'unit': 'pct', 'text': '+0.85%', 'metric': 'index_change_pct'}
    level = {'label': '上证指数', 'value': '3250.12', 'unit': 'points', 'text': '3,250.12 点'}
    facts = [{'facts': [trades, pnl, change, level]}]
    assert ungrounded_numbers('今日成交 7 笔，盈亏 1234.50 元（2.35%），上证涨 0.85%，报 3250.12 点。', facts) == []
    assert ungrounded_numbers('成交额 7 元，买入 4.00 元，盈亏 2.35 元，指数 0.85 元，点位 3250.12 元。', facts) == [
        '7元', '4.00元', '2.35元', '0.85元', '3250.12元']


INDICATORS = {
    'source': 'akshare + TechnicalFactors（本机计算）', 'data_date': '2026-11-16', 'ticker': 'SHA:600001',
    'units': {'sma_5': '价格（前复权，与K线同币种）', 'rsi_14': '0-100', 'close_vs_ma_20': '%（收盘价相对20日均线的偏离）',
              'momentum_20': '%（20日涨跌幅）', 'volume_ma_20': '股（20日均量）', 'obv': '股（累计，仅看方向）'},
    'columns': ['date', 'sma_5', 'rsi_14', 'close_vs_ma_20', 'momentum_20', 'volume_ma_20', 'obv'],
    'rows': [['2026-11-16', 10.5, 62.5, 4.25, 8.13, 1200000.0, 98765.0]], 'count': 1,
}


def test_r3_indicators_keep_the_unit_their_tool_declares():
    """Review finding: ``close_vs_ma_20=4.25`` flagged the correct 4.25% and accepted 4.25元; ``momentum_20`` grounded a price."""
    assert ungrounded_numbers('收盘价偏离20日均线 4.25%，20日涨幅 8.13%，RSI 62.5，5日均线 10.50 元。', [INDICATORS]) == []
    assert ungrounded_numbers('现价 4.25 元，目标 8.13 元，止损 62.5 元，成本 98765.0 元。', [INDICATORS]) == [
        '4.25元', '8.13元', '62.5元', '98765.0元']


def test_r3_indicator_fields_without_a_declared_unit_follow_the_table():
    record = [{'date': '2026-11-16', 'close_vs_ma_20': 4.25, 'momentum_20': 8.13, 'sma_20': 10.2, 'rsi_6': 55.5,
               'high_low_ratio': 1.85, 'boll_width': 0.12, 'mystery_factor_10': 3.3}]
    assert ungrounded_numbers('偏离 4.25%，动量 8.13%，20日均线 10.20 元，RSI 55.5。', record) == []
    assert ungrounded_numbers('现价 4.25 元，8.13 元，55.5 元。', record) == ['4.25元', '8.13元', '55.5元']
    # names that do not say their scale are no evidence at all (high_low_ratio is a price range, not a ratio)
    assert ungrounded_numbers('区间 1.85 元，比例 185%，带宽 12%，因子 3.3。', record) == ['1.85元', '185%', '12%', '3.3']


# ---------------------------------------------------------------- review round 3: arithmetic with the right denominator

def test_r3_a_change_in_percent_uses_the_earlier_price_as_denominator():
    """Review finding: previous close 80 and close 100 grounded both 「收盘涨幅20%」 and 「25%」."""
    quote = [{'prev_close': 80, 'close': 100}]
    assert ungrounded_numbers('收盘涨幅 25%，涨了 20 元。', quote) == []
    assert ungrounded_numbers('收盘涨幅 20%。', quote) == ['20%']
    holding = [{'cost_price': 80, 'price': 100}]
    assert ungrounded_numbers('持仓收益 25%，每股赚 20.00 元。', holding) == []
    assert ungrounded_numbers('持仓收益 20%。', holding) == ['20%']
    newest_first = [[{'date': '2026-11-16', 'close': 100}, {'date': '2026-11-13', 'close': 80}]]
    assert ungrounded_numbers('两天涨了 25%。', newest_first) == []
    assert ungrounded_numbers('两天涨了 20%。', newest_first) == ['20%']
    bar = [{'high': 100, 'low': 80}]
    assert ungrounded_numbers('振幅 20 元。', bar) == []
    assert ungrounded_numbers('振幅 25%，或 20%。', bar) == ['25%', '20%'], 'no denominator is declared for high/low'


# ---------------------------------------------------------------- review round 3: share quantities of a holding

def test_r3_a_holding_quantity_needs_evidence():
    """Review finding: with no holdings evidence 「持有123.45股」 passed because share counts were skipped."""
    assert ungrounded_numbers('你持有123.45股。', []) == ['123.45股']
    assert ungrounded_numbers('你持有 SHA:600001 300 股，昨天买入 2 手。', []) == ['300股', '2手']
    assert ungrounded_numbers('你持有 SHA:600001 300 股，昨天买入 2 手。', [{'quantity': '300'}, {'quantity': '200'}]) == []
    assert ungrounded_numbers('每10股派息 2.5 元，一手是 100 股。', [{'dividend': 2.5}]) == [], 'a rate and a lot size'


def test_r3_a_brief_s_model_commentary_is_not_evidence():
    """Sibling: the post-market brief's model commentary is 「模型点评，不是事实」; its numbers ground nothing."""
    report = {'kind': 'postmarket', 'sections': [{'id': 'pnl', 'facts': [
        {'label': {'untrusted_text': '今日盈亏合计'}, 'value': '1234.50', 'unit': 'CNY',
         'text': {'untrusted_text': '1,234.50 元'}}]}],
        'commentary': {'model': 'example', 'status': 'ok', 'note': '模型点评，不是事实；数字以 sections 为准。',
                       'text': {'untrusted_text': '今天赚了 1,300.00 元，涨幅 3.5%'}}}
    assert ungrounded_numbers('今日盈亏 1234.50 元。', [report]) == []
    assert ungrounded_numbers('今天赚了 1300.00 元，涨幅 3.5%。', [report]) == ['1300.00元', '3.5%']


# ---------------------------------------------------------------- release gate: a number a tool returned is never footnoted

@pytest.fixture
def weighted(tmp_path, monkeypatch):
    """示例甲 100 x 12 beside 1,000 cash: weight 54.55% (fictional)."""
    from datetime import timedelta
    from core.market import search
    from core.market.runtime import set_market_runtime
    from tests.fakes import FakeClock, ScriptedSource, build_ledger, fake_runtime, fx_rate, opening, position, quote
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    build_ledger(tmp_path, [opening('Main', cash={'CNY': '1000'}, date='2026-11-13',
                                    positions=[position('SHA:600001', '100', '10', '示例甲')])])
    clock = FakeClock('2026-11-17 10:00')
    now = clock.now()
    set_market_runtime(fake_runtime(clock, ScriptedSource(
        {'SHA:600001': quote('SHA:600001', '12', prev_close='11', at=now - timedelta(seconds=3), name='示例甲')},
        {'USD': fx_rate('USD', '7', at=now), 'HKD': fx_rate('HKD', '0.9', at=now)})))
    search.set_symbol_index(search.SymbolIndex(fetchers={}))
    yield tmp_path
    set_market_runtime(None)
    search.set_symbol_index(None)


def test_gate_an_alert_distance_in_percentage_points_is_grounded(weighted):
    """Release gate: list_alert_rules returned weight 54.55% and 5.45 points to a 60% cap; the exact answer
    「示例甲仓位 54.55%，距离上限还差 5.45 个百分点。」 got the footnote 「5.45个百分点」."""
    import asyncio
    from core import agent_tools
    from core.alerts import rules
    rules.create({'ticker': 'SHA:600001', 'kind': 'position_weight_max', 'threshold': '60'}, source='manual')
    tools = {tool.name: tool for tool in agent_tools.build_all(None)}
    result = asyncio.run(tools['list_alert_rules'].function())
    market = result['rules'][0]['market']
    assert (market['weight_pct'], market['distance'], market['unit']) == ('54.55', '5.45', 'pct_points'), market
    # Changed in round 5: 「上限 60%」 is gone from the second answer; the rule's threshold is a price or an
    # amount by its name, and 40868ac footnoted 60% from it, as it does again.
    for answer in ('示例甲仓位 54.55%，距离上限还差 5.45 个百分点。', '距离上限还差约 5.5 个百分点。',
                   '还差 5.45 个点', '还差 5.45 pct', '还差5.45%'):
        assert ungrounded_numbers(answer, [result]) == [], answer


DIGITS = str.maketrans('0123456789.,', '０１２３４５６７８９．，')
# (written number, unit word after it, a tool result containing that number exactly)
# Changed in round 5: the rows that grounded a percentage from ``value`` or ``threshold`` (a price or an
# amount by name) now take it from a distance or a percentage field; ``test_r5_...`` has the other way.
SUFFIXES = [
    ('%', {'distance': '5.45', 'unit': 'pct_points'}), ('％', {'weight_pct': '5.45'}),
    ('个百分点', {'distance': '5.45', 'unit': 'pct_points'}), ('百分点', {'distance': '5.45'}),
    ('个点', {'distance': '5.45'}), (' pct', {'threshold': '5.45'}),
    ('元', {'price': '5.45'}), (' 元', {'amount': '5.45'}), ('港元', {'price': '5.45'}), ('美元', {'value': '5.45'}),
    ('块', {'cost_price': '5.45'}), ('股', {'quantity': '5.45'}), ('手', {'quantity': '5.45'}),
    ('份', {'quantity': '5.45'}), ('倍', {'pe': '5.45'}), ('天', {'days': '5.45'}), ('', {'value': '5.45'}),
    # a distance holds percentage points or a price gap: its number grounds any unit word
    ('个百分点', {'distance': '5.45'}), ('%', {'distance_to_stop': '5.45'}), ('%', {'change_pct': '5.45'}),
    ('元', {'change': '5.45'}),
]


@pytest.mark.parametrize('sign', ['', '+', '-', '−'])
@pytest.mark.parametrize('suffix, result', SUFFIXES, ids=[f'{i:02d}' for i in range(len(SUFFIXES))])
def test_gate_a_number_a_tool_returned_is_grounded_whatever_unit_word_follows(sign, suffix, result):
    held = '持有' if suffix.strip() in ('股', '手', '份') else ''     # a share count is checked as a holding
    assert ungrounded_numbers(f'{held}{sign}5.45{suffix}', [result]) == []
    assert ungrounded_numbers(f'{held}{sign}5.45{suffix}', [{k: (v.replace('5.45', '-5.45') if k != 'unit' else v)
                                                              for k, v in result.items()}]) == [], 'sign in the data'


# Changed in round 5: the two percentages come from a percentage field, not from ``value`` (an amount).
@pytest.mark.parametrize('written, data, field', [
    ('1,234.50 元', '1234.50', 'value'), ('1234.50 元', '1,234.50', 'value'), ('１２３４.５０ 元', '1234.50', 'value'),
    ('1234.50 元', '１２３４.５０', 'value'), ('−1,234.50 元', '-1234.50', 'value'), ('1234.50 元', '−1234.50', 'value'),
    ('＋5.45%', '5.45', 'change_pct'), ('５.４５ 个百分点', '5.45', 'distance'),
])
def test_gate_separators_full_width_digits_and_signs(written, data, field):
    assert ungrounded_numbers(f'金额 {written}', [{field: data}]) == [], (written, data)


def test_gate_the_exact_number_rule_keeps_the_unit_a_producer_declared():
    """What the release gate keeps from round 3: a declared unit, metadata and computed figures stay strict."""
    assert ungrounded_numbers('现价 4.25 元', [{'units': {'x': '%'}, 'x': '4.25'}]) == ['4.25元']
    assert ungrounded_numbers('成交 7 元', [{'value': 7, 'unit': 'count'}]) == ['7元']
    assert ungrounded_numbers('现价 37 元', [{'ledger_revision': 37}]) == ['37元']
    assert ungrounded_numbers('现价 15.7 元', [{'title': {'untrusted_text': '营收增长 15.7%'}}]) == ['15.7元']


# ---------------------------------------------------------------- round 5: only a distance grounds any unit word

R5_QUOTE = {'quotes': [{'ticker': 'SHA:600001', 'price': '8.88', 'prev_close': '8.50', 'change_pct': '4.47',
                        'quote': {'source': 'scripted', 'status': 'live', 'reason': None}}]}
R5_HOLDING = {'accounts': [{'label': 'A1', 'positions': [{'ticker': 'SHA:600001', 'quantity': '100', 'cost_price': '10',
                                                          'price': '12', 'market_value': '1200.00', 'day_pnl': '100.00'}]}]}
R5_RULE = {'rules': [{'ticker': 'SHA:600001', 'kind': 'position_weight_max', 'threshold': '60',
                      'market': {'weight_pct': '54.55', 'value': '54.55', 'distance': '5.45', 'unit': 'pct_points'}}]}
R5_PLAN = {'plans': [{'ticker': 'SHA:600001', 'stop_price': '10.50', 'market': {
    'price': '12', 'distance_to_stop': '1.50', 'distance_to_stop_pct': '12.50', 'weight_pct': '54.55'}}]}


@pytest.mark.parametrize('answer, data, footnoted', [
    # the gate's case: a rule's or a plan's distance carries no unit of its own (40868ac: both footnoted)
    ('示例甲仓位 54.55%，距离上限还差 5.45 个百分点。', R5_RULE, []),
    ('距离上限还差 5.45%', R5_RULE, []),
    ('距离止损还差 1.50 元，即 12.50%', R5_PLAN, []),
    # a price or an amount grounds money and bare numbers only, never a percentage or a share count
    # (40868ac footnoted these; round 4 grounded them)
    ('涨了 8.88%', R5_QUOTE, ['8.88%']),
    ('持有 12 股', R5_HOLDING, ['12股']),
    ('涨了 1200.00%', R5_HOLDING, ['1200.00%']),
    ('当日涨了 100.00%', R5_HOLDING, ['100.00%']),
    ('上限 60%。', R5_RULE, ['60%']),
    # and still grounds what it is (40868ac and round 4 alike)
    ('现价 8.88 元，涨了 4.47%', R5_QUOTE, []),
    ('现价 8.88', R5_QUOTE, []),
    ('持有 100 股，现价 12 元，当日盈亏 100.00 元', R5_HOLDING, []),
])
def test_r5_only_a_distance_grounds_a_number_with_any_unit_word(answer, data, footnoted):
    """Round-5 regression: a ¥8.88 quote grounded a claimed 8.88% gain and a ¥12 price grounded 「持有12股」."""
    assert ungrounded_numbers(answer, [data]) == footnoted


# ---------------------------------------------------------------- round 6: a zero price the user stated grounds its restatement

@pytest.mark.parametrize('user, answer, display', [
    ('我的软件上显示现价 0.00 元，这是什么意思？', '你看到的现价 0.00 元，通常表示今天还没有成交。', '0.00元'),
    ('假设股价是 0.00 元，我会亏多少？', '如果股价是 0.00 元，你的持仓会全部亏掉。', '0.00元'),
    ('如果收盘价为 ¥0 呢？', '收盘价为 ¥0 时，市值为 0。', '0'),
])
def test_r6_a_zero_price_the_user_stated_grounds_its_restatement(user, answer, display):
    """Round-6 regression (cr_v6c B8, cr_v6cs B7): the user's own 「现价 0.00 元」/「股价是 0.00 元」/「收盘价为 ¥0」,
    restated by the assistant, got no footnote at 40868ac and one in round 5."""
    assert ungrounded_numbers(answer, [], user_text=user) == []
    # both ways (C3.3 as round 5 left it): without the user's figure the zero price is footnoted
    assert ungrounded_numbers(answer, []) == [display]
    assert ungrounded_numbers(answer, [{'fund_cny': '0.00'}], user_text='我的基金是 0 元') == [display]
