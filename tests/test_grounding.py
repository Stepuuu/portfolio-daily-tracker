"""ungrounded_numbers / footnote: conservative, pure."""
import pytest

from core.agent_tools.grounding import footnote, ungrounded_numbers

RESULT = {
    'source': 'akshare', 'data_date': '2026-11-16',
    'summary': {'last_close': 1439.1, 'window_change_pct': 3.38, 'window_high': 1480.6, 'window_low': 1395.02},
    'rows': [['2026-11-16', 1410.0, 1480.6, 1395.02, 1439.1, 82691.0]],
    'items': [{'title': {'untrusted_text': '贵州茅台三季度营收增长 15.7%，净利润 8.2 亿元'}}],
}


def check(answer, results=(RESULT,), **kw):
    return ungrounded_numbers(answer, list(results), **kw)


def test_numbers_present_in_results_are_grounded():
    assert check('收盘价 1439.10 元，区间涨幅 3.38%，最高 1480.6，最低 1395.02。') == []


def test_rounding_and_truncation_are_accepted():
    assert check('收盘约 1439.1 元，涨 3.4%，另一处写 3.9%') == ['3.9%']
    assert check('最低 1395 元, 最低 1395.0 元, 最低 1395.02 元') == []
    assert check('成交 8.27万 手') == [] and check('成交 8.3万') == []


def test_percent_matches_in_either_scale_and_ignores_sign():
    assert check('比例 0.0338', [{'x': 0.0338}]) == []
    assert check('比例 0.0339 即 3.39%', [{'x': 0.0338}]) == ['0.0339', '3.39%']
    assert check('涨了 3.38%', [{'ratio': 0.0338}]) == [], 'a fraction field grounds the percentage'
    assert check('涨了 3.38%', [{'x': 0.0338}]) == ['3.38%'], 'a number of unknown unit only as written'
    assert check('涨了 3.38%', [{'x': 3.38}]) == []
    assert check('下跌 5.2%', [{'x': -5.2}]) == []
    assert check('上涨 +5.2%', [{'x': -5.2}]) == []


def test_chinese_units_scale():
    assert check('净利润 8.2 亿元') == []
    assert check('市值 1.2万亿元', [{'cap': 1_200_000_000_000}]) == []
    assert check('市值 1.5万亿元', [{'cap': 1_200_000_000_000}]) == ['1.5万亿元']


def test_invented_numbers_are_found_in_order_without_duplicates():
    answer = '目标价 1600.50 元，止损 1350.25 元；再说一次 1600.50 元。'
    assert check(answer) == ['1600.50元', '1350.25元']


def test_dates_times_codes_ordinals_and_list_numbers_are_ignored():
    answer = ('1. SHA:600519 在 2026-11-16 和 2026年11月17日 以及 11月17日、11/17 10:30 更新\n'
              '2) 代码 600519，港股 00700；第3点；(1) 第一名\n'
              '3、MA5 与 RSI14、T+1、Q3 业绩，访问 https://example.invalid/a/1.5/2.5')
    assert check(answer, []) == []


def test_counts_durations_and_ratios_are_ignored_but_prices_are_not():
    assert check('一手 100 股，3天内，PE 25.6倍，共 12 只，2.5 个月，下降 4.5周', []) == []
    # Changed in review round 3: a quantity the answer says the user holds needs evidence like any number.
    assert check('持有 100 股', []) == ['100股']
    assert check('报价 25.6', []) == ['25.6']
    assert check('报价 25 元', []) == ['25元']
    assert check('报价 ¥25.6 与 HK$12.5 与 $9.9', []) == ['25.6', '12.5', '9.9']


def test_plain_integers_are_not_checked():
    assert check('一共 5 个理由，指数在 3200 点附近', []) == []


def test_context_user_text_and_context_numbers_ground_numbers():
    context = '持仓 SHA:600519 成本价 1500.00 元，数量 200，现价 1439.10 元'
    assert check('你的成本价是 1500.00 元', [], context_numbers=[context]) == []
    assert check('你的成本价是 1500.00 元', [], context_numbers=[1500.0]) == []
    assert check('你的成本价是 1500.00 元', [], context_numbers=context) == []
    assert check('按你说的 9.5 元止损', [], user_text='我想在 9.5 元止损') == []
    assert check('按你说的 9.5 元止损', []) == ['9.5元']


def test_numbers_quoted_from_news_text_are_grounded_by_the_result_that_contained_them():
    assert check('新闻说营收增长 15.7%') == []
    assert check('新闻说营收增长 15.9%') == ['15.9%']


def test_zero_and_hundred_percent_are_grounded_like_any_other_value():
    # was: test_zero_and_hundred_percent_never_flag (an unconditional exemption, removed on purpose)
    assert check('0.00% 的变化，100% 确定', []) == ['0.00%', '100%']
    assert check('0.00% 的变化，100% 确定', [{'change_pct': 0.0, 'weight': 100}]) == []


def test_degenerate_inputs():
    assert ungrounded_numbers('', []) == [] and ungrounded_numbers(None, []) == []
    ungrounded_numbers('收盘 12.5 元', None)      # must not raise
    assert ungrounded_numbers('收盘 12.5 元', [None, 3, 'text', {'a': [float('nan'), float('inf')]}]) == ['12.5元']
    assert ungrounded_numbers('收盘 12.5 元', [{'a': True}]) == ['12.5元']


def test_nested_structures_and_numeric_strings_are_searched():
    result = {'a': [{'b': ('12.5',)}], 'c': {'d': '价格 3,456.70'}}
    assert check('12.5元，3456.7元', [result]) == []


def test_footnote_text_and_cap():
    assert footnote([]) == ''
    note = footnote(['1600.50元', '5.2%'])
    assert note == '以下数字未在本轮数据中找到，请核对：1600.50元、5.2%'
    many = footnote([f'{i}.5元' for i in range(1, 13)])
    assert many.count('、') == 7 and '等12项' in many
