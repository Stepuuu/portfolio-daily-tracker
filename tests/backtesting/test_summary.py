"""Golden tests: the headline and caveat texts are generated from the numbers by code."""
import pytest

from backtesting import summary
from backtesting.summary import data_caveats, headline, money, pct, span_label

PLAN_EXAMPLE = {
    "first_date": "2023-11-17", "last_date": "2026-11-16",
    "total_return": 0.182, "annualized_return": 0.057,
    "same_stock_return": -0.064, "index_name": "沪深300", "index_return": 0.098,
    "max_drawdown": -0.141, "max_drawdown_peak": "2024-09-13", "max_drawdown_trough": "2025-01-10",
    "closed_trades": 9, "winning_trades": 4, "open_at_end": 0, "fills": 18,
    "fees_total": 1234.0, "currency": "CNY",
}


def test_the_plan_example_sentence():
    text, missing = headline(PLAN_EXAMPLE)

    assert text == ("2023-11-17 至 2026-11-16（3 年），这个方法收益 +18.2%（年化 5.7%）；同期一直持有 −6.4%，"
                    "沪深300 +9.8%。最大回撤 −14.1%（2024-09-13→2025-01-10）。完成 9 笔交易，盈利 4 笔，费用合计 ¥1,234。")
    assert missing == []


def test_a_missing_index_is_left_out_and_its_reason_becomes_a_caveat():
    facts = {**PLAN_EXAMPLE, "index_return": None, "index_reason": "沪深300 行情无法获取（下载失败），没有和指数比较。"}

    text, missing = headline(facts)

    assert "；同期一直持有 −6.4%。" in text and "沪深300" not in text and "0.0%" not in text
    assert missing == [{"code": "index_unavailable", "level": "info",
                        "text": "沪深300 行情无法获取（下载失败），没有和指数比较。"}]


def test_no_index_chosen_leaves_it_out_without_a_caveat():
    text, missing = headline({**PLAN_EXAMPLE, "index_name": None, "index_return": None})
    assert "；同期一直持有 −6.4%。" in text and missing == []


def test_a_missing_buy_and_hold_comparison_is_explained():
    text, missing = headline({**PLAN_EXAMPLE, "same_stock_return": None})
    assert "；同期沪深300 +9.8%。" in text
    assert [c["code"] for c in missing] == ["same_stock_unavailable"]


def test_a_short_period_has_no_annualised_return_in_the_headline():
    facts = {**PLAN_EXAMPLE, "first_date": "2026-05-04", "last_date": "2026-11-16", "total_return": -0.031}

    text, missing = headline(facts)

    assert text.startswith("2026-05-04 至 2026-11-16（约 6 个月），这个方法收益 −3.1%；同期")
    assert "年化" not in text
    assert [c["code"] for c in missing] == ["short_period"]
    assert "196 天" in missing[0]["text"]


def test_an_open_position_at_the_end_and_hong_kong_dollars():
    facts = {**PLAN_EXAMPLE, "closed_trades": 0, "winning_trades": 0, "open_at_end": 1, "fills": 1,
             "fees_total": 12.345, "currency": "HKD", "index_name": "恒生指数", "index_return": -0.02}

    text, _ = headline(facts)

    assert text.endswith("恒生指数 −2.0%。最大回撤 −14.1%（2024-09-13→2025-01-10）。"
                         "没有完成的交易，期末仍持有 1 笔（按最后收盘价估值），费用合计 HK$12.35。")


def test_no_fills_and_no_drawdown():
    facts = {**PLAN_EXAMPLE, "total_return": 0.0, "fills": 0, "closed_trades": 0, "max_drawdown": 0.0,
             "annualized_return": 0.0}

    text, _ = headline(facts)

    assert text == ("2023-11-17 至 2026-11-16（3 年），这个方法收益 0.0%（年化 0.0%）；同期一直持有 −6.4%，"
                    "沪深300 +9.8%。期间净值没有回撤。期间没有成交。")


def test_a_run_that_never_traded_a_day_says_so():
    text, missing = headline({"first_date": None, "last_date": None})
    assert text == "所选区间内没有可以交易的日子，策略没有运行，没有结果。" and missing == []


@pytest.mark.parametrize("value,expected", [
    (0.182, "+18.2%"), (-0.064, "−6.4%"), (0.0, "0.0%"), (-0.0004, "0.0%"), (1.5, "+150.0%"),
])
def test_signed_percentages(value, expected):
    assert pct(value) == expected


@pytest.mark.parametrize("first,last,expected", [
    ("2023-11-17", "2026-11-16", "3 年"),
    ("2025-11-17", "2026-11-14", "1 年"),
    ("2024-05-01", "2026-11-16", "约 2.5 年"),
    ("2026-06-01", "2026-11-16", "约 6 个月"),
    ("2026-10-01", "2026-11-16", "46 天"),
])
def test_span_labels(first, last, expected):
    assert span_label(first, last) == expected


def test_money_formats():
    assert money(1234.4, "CNY") == "¥1,234"
    assert money(12.345, "USD") == "$12.35"
    assert money(1_000_000, "HKD") == "HK$1,000,000"


COVERAGE = {
    "ticker": "SHA:600519", "adjust": "qfq", "source": "akshare.stock_zh_a_hist",
    "requested_start": "2023-11-17", "requested_end": "2026-11-17", "first": "2023-08-10", "last": "2026-11-16",
    "rows": 790, "complete": True, "offline": False, "warmup_rows": 60,
    "suspected_corporate_actions": [], "warnings": [],
}


def by_code(items):
    return {item["code"]: item for item in items}


def test_adjusted_complete_data_only_states_the_adjustment():
    items = data_caveats(COVERAGE, adjust="qfq")
    assert [c["code"] for c in items] == ["adjusted_prices", "dividend_tax_ignored"]
    assert by_code(items)["adjusted_prices"]["text"].startswith("价格按前复权计算：分红、送转视为再投入")


def test_offline_partial_data():
    coverage = {**COVERAGE, "offline": True, "complete": False, "last": "2026-06-30",
                "warnings": ["离线: 只用本地缓存 ... 未完整覆盖; 下载失败 (ConnectionError: offline)"]}

    items = by_code(data_caveats(coverage, adjust="qfq"))

    assert items["offline_cache"] == {
        "code": "offline_cache", "level": "warning",
        "text": "未能取得完整行情（下载失败），只用了本地缓存的 2023-08-10~2026-06-30（请求 2023-11-17~2026-11-17）；"
                "结果按实际有数据的区间计算，没有补齐或外推。"}
    assert items["coverage_short"]["text"] == (
        "SHA:600519 的行情比请求的 2023-11-17~2026-11-17 短：2026-06-30 之后没有数据（停牌、退市或数据未更新）。"
        "收益和年化按实际有数据的区间计算。")


def test_a_late_listing_is_a_short_coverage_but_a_holiday_is_not():
    late = {**COVERAGE, "first": "2024-03-01", "warmup_rows": 0}
    assert "2024-03-01 之前没有数据" in by_code(data_caveats(late, adjust="qfq"))["coverage_short"]["text"]
    holiday = {**COVERAGE, "requested_start": "2024-10-01", "first": "2024-10-08", "warmup_rows": 0}
    assert "coverage_short" not in by_code(data_caveats(holiday, adjust="qfq"))


def test_unadjusted_data_with_gaps():
    coverage = {**COVERAGE, "adjust": "", "suspected_corporate_actions": ["2024-06-20"]}

    items = data_caveats(coverage, adjust="")

    assert [c["code"] for c in items] == ["unadjusted_gaps", "unadjusted_prices"]
    assert items[0]["text"] == ("不复权价格在 2024-06-20 出现疑似除权或拆合股的跳空，回测把它当成了真实涨跌，"
                                "结果可能严重失真；建议改用前复权。")


def test_demo_bars_are_labelled():
    items = data_caveats({**COVERAGE, "ticker": "SHA:DEMO", "source": "demo.synthetic"}, adjust="qfq")
    assert items[0]["code"] == "demo_data" and "不是真实行情" in items[0]["text"]


def test_commission_and_rerun_caveats():
    assert summary.commission_caveat("CN", 0.00025, 5.0, "CNY")["text"] == (
        "佣金按A股默认 0.025%（每笔最低 5 元）估算，与你券商的实际费率可能不同，可在高级设置里修改。")
    assert "0 估算" in summary.commission_caveat("US", 0.0, 0.0, "USD")["text"]
    changed = summary.data_changed_caveat([{"ticker": "SHA:600519", "before": "a" * 64, "after": "b" * 64}])
    assert changed["level"] == "warning" and "SHA:600519 aaaaaaaa→bbbbbbbb" in changed["text"]


def test_sort_puts_warnings_first_and_keeps_one_per_code():
    items = [summary.caveat("a", "info", "1"), summary.caveat("b", "warning", "2"), summary.caveat("a", "warning", "3")]
    assert summary.sort_caveats(items) == [summary.caveat("b", "warning", "2"), summary.caveat("a", "info", "1")]
