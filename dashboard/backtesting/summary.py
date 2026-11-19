"""The headline and the caveat texts of a backtest, generated from the numbers.

No LLM is involved: every sentence below is a template filled with facts the
run recorded. A part of the headline whose input is missing is left out and the
reason becomes a caveat instead, so the headline never shows a made-up value
(a missing index is "not available", never 0%).

All functions are pure: dicts in, strings and caveat dicts out.

Caveats are ``{"code", "level", "text"}`` as in analyzer/caveats.py. Codes
produced here:

==================== ======= ===========================================================
code                 level   when
==================== ======= ===========================================================
demo_data            info    the bars come from the demo's synthetic generator
offline_cache        warning a download was needed but failed or was not allowed; only
                             the local cache was used
coverage_short       warning the bars start more than 10 days after the requested start
                             or end more than 10 days before the requested end
adjusted_prices      info    qfq / hfq prices (dividends reinvested, share counts on
                             adjusted prices)
dividend_tax_ignored info    qfq / hfq prices: dividends counted before tax
unadjusted_prices    info    unadjusted prices: cash dividends are not counted
unadjusted_gaps      warning unadjusted prices with suspected corporate-action gaps (the
                             run was allowed explicitly)
commission_assumed   info    the commission is the market default, not the user's own
legacy_fees_translated info  a legacy request's sell rate includes stamp duty (v3.2 meaning):
                             a sale is charged that rate and no stamp duty on top, plus
                             the fees v3.2 did not know (see service.translate_legacy_fees)
in_sample            info    always: the parameters were judged on this same history
short_period         info    fewer than 365 days measured: no annualised return in the
                             headline
same_stock_unavailable info  no buy-and-hold comparison: holding the stock under the run's
                             own capital, lots, fees and execution never bought (the
                             reason is facts.same_stock_reason), or there were no bars
index_unavailable    info    the market index could not be loaded, does not cover the run's
                             dates, misses sessions it cannot stand in for (a closure is
                             accepted only where the holiday table lists it), or has bars
                             on days its exchange was closed (see
                             service.index_session_gaps); the value is null
data_changed         warning a rerun used different bars than the saved run, or the same
                             bars with different derived features (the code that computes
                             them changed)
data_unverified      warning a rerun of a record whose data was hashed by an older rule
                             (bars without turnover, or without the derived features):
                             whether the strategy saw the same data is unknown (same_data null)
engine_changed       info    a rerun used another engine version
strategy_changed     warning a rerun ran a strategy whose class, source or parameter set
                     / info  differs from the saved definition (warning); or replayed the
                             saved preset rule although today's preset differs (info)
strategy_unverified  warning a rerun of a record saved without a strategy definition, or
                             without the fingerprints of the code it imports (or one
                             of them unreadable): whether it is the same strategy is unknown
settings_changed     warning a rerun's resolved settings (lot rule, commission defaults,
                             broker rules, fee table, same-stock rule) differ from the
                             saved manifest.resolved (or, without it, the fee table version)
settings_unverified  warning a rerun of a record saved without manifest.resolved (or whose
                             "auto" index is not recorded): those settings were resolved
                             as today and could not be compared
runtime_changed      info    a rerun ran on other python / pandas / numpy versions
==================== ======= ===========================================================
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

INFO, WARNING = "info", "warning"
MINUS = "−"     # the typographic minus used in the headline: −6.4%

CURRENCY_SYMBOLS = {"CNY": "¥", "HKD": "HK$", "USD": "$"}
CURRENCY_UNITS = {"CNY": "元", "HKD": "港元", "USD": "美元"}
MARKET_LABELS = {"CN": "A股", "HK": "港股", "US": "美股"}
ADJUST_LABELS = {"qfq": "前复权", "hfq": "后复权", "": "不复权"}

EXIT_REASON_ZH = {
    "signal": "信号", "stop": "止损", "target": "止盈", "trailing": "回落",
    "max_hold": "到期", "end_mark": "期末估值",
}

# The hypothetical end-of-data liquidation (end_mark): a valuation, never a real sale.
END_MARK_ZH = "期末估值（仍持有，按最后收盘价估值，不是真实卖出）"
END_MARK_BLOCKED = ("suspended", "limit_down", "t_plus_one")


def end_mark_blocked_text(blocked: Optional[str], quantity, blocked_quantity) -> Optional[str]:
    """Why an end-marked position could not really be sold on its last day (the engine's
    end_mark.positions[].blocked: suspended, limit_down or t_plus_one, in that order of
    precedence; blocked_quantity the shares it applies to); None when it could."""
    if not blocked:
        return None
    if blocked == "suspended":
        return "最后一天停牌或没有行情，实际卖不出，不能按这个价格成交"
    if blocked == "limit_down":
        return "最后一天一字跌停，实际卖不出，不能按这个价格成交"
    if blocked == "t_plus_one":
        part = float(blocked_quantity or 0)
        whole = part <= 0 or part >= float(quantity or 0)
        who = "这些股票是最后一天买入的" if whole else f"其中 {part:g} 股是最后一天买入的"
        return who + "（A 股 T+1，当天不能卖出），实际卖不出，不能按这个价格成交"
    return f"最后一天实际卖不出（{blocked}），不能按这个价格成交"


# 更多指标: one line each, for the page.
METRIC_NOTES = {
    "total_return": "期末资产相对本金的涨跌，已扣除全部费用和滑点。",
    "annualized_return": "把总收益按实际日历天数折算成每年的复合收益；不足一年时偶然性很大。",
    "max_drawdown": "净值从此前最高点下跌的最大幅度。",
    "max_drawdown_duration": "净值低于此前最高点的最长连续交易日数。",
    "volatility": "日收益的标准差乘以 √252，衡量净值波动有多大。",
    "sharpe_ratio": "每承担一份波动换来多少超额收益（无风险利率按 3%）；少于 60 个交易日不计算。",
    "sortino_ratio": "与夏普相同，但只把下跌的波动算作风险；少于 60 个交易日不计算。",
    "calmar_ratio": "年化收益除以最大回撤的绝对值。",
    "var_95": "历史上最差的 5% 交易日里，单日亏损至少有这么多（日收益的 5% 分位数）。",
    "win_rate": "完整交易（买入到全部卖出）中盈利的比例；持平和期末仍持有的不计。",
    "payoff_ratio": "平均每笔盈利除以平均每笔亏损（盈亏比）。",
    "profit_factor": "全部盈利之和除以全部亏损之和（利润因子），大于 1 才是整体赚钱。",
    "avg_holding_days": "每股从买入到卖出平均持有的日历天数。",
    "closed_trades": "已经完成的交易笔数（从空仓买入到全部卖出算一笔）。",
    "total_trades": "交易笔数，含期末仍持有、按收盘价估值的一笔。",
    "benchmark_return": "同一只股票按同样的本金、费用、滑点、整手和次日开盘成交一直持有的收益；买不进时不可用。",
    "alpha": "这个方法的总收益减去一直持有的收益。",
}

SHORTFALL_DAYS = 10
SHORT_PERIOD_DAYS = 365


def caveat(code: str, level: str, text: str) -> Dict[str, str]:
    return {"code": code, "level": level, "text": text}


# ------------------------------------------------------------------ #
#  Formatting
# ------------------------------------------------------------------ #

def _finite(value) -> bool:
    return value is not None and not isinstance(value, bool) and isinstance(value, (int, float)) \
        and math.isfinite(float(value))


def pct(value: float, digits: int = 1, signed: bool = True) -> str:
    """0.182 -> '+18.2%', -0.064 -> '−6.4%'; a value that rounds to zero has no sign."""
    number = round(float(value) * 100, digits)
    if number == 0:
        return f"{0:.{digits}f}%"
    text = f"{abs(number):.{digits}f}%"
    if number < 0:
        return MINUS + text
    return ("+" + text) if signed else text


def money(value: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency, "")
    unit = "" if symbol else f" {currency}"
    amount = abs(float(value))
    text = f"{amount:,.0f}" if amount >= 100 else f"{amount:,.2f}"
    return f"{MINUS if value < 0 else ''}{symbol}{text}{unit}"


def span_label(first: str, last: str) -> str:
    """'3 年' when the span is within 10 days of whole years, else '约 2.4 年' / '约 5 个月' / '20 天'."""
    days = (pd.Timestamp(last) - pd.Timestamp(first)).days
    years = days / 365.25
    whole = round(years)
    if whole >= 1 and abs(days - whole * 365.25) <= 10:
        return f"{whole} 年"
    if years >= 1:
        return f"约 {years:.1f} 年"
    if days >= 60:
        return f"约 {round(days / 30.44)} 个月"
    return f"{days} 天"


# ------------------------------------------------------------------ #
#  Headline
# ------------------------------------------------------------------ #

def headline(facts: Dict[str, Any]) -> Tuple[str, List[Dict[str, str]]]:
    """(headline_zh, caveats for the parts left out).

    Facts read (all optional except the period): first_date, last_date,
    total_return, annualized_return, same_stock_return (holding the stock under the
    run's own assumptions), same_stock_reason, index_name,
    index_return, index_reason, max_drawdown, max_drawdown_peak,
    max_drawdown_trough, closed_trades, winning_trades, open_at_end, fills,
    fees_total, currency.
    """
    first, last = facts.get("first_date"), facts.get("last_date")
    if not first or not last:
        return "所选区间内没有可以交易的日子，策略没有运行，没有结果。", []
    missing: List[Dict[str, str]] = []
    days = (pd.Timestamp(last) - pd.Timestamp(first)).days

    result = f"{first} 至 {last}（{span_label(first, last)}），这个方法收益 {pct(facts.get('total_return', 0.0))}"
    annual = facts.get("annualized_return")
    if _finite(annual) and days >= SHORT_PERIOD_DAYS:
        result += f"（年化 {pct(annual, signed=False)}）"
    elif _finite(annual):
        missing.append(caveat(
            "short_period", INFO,
            f"回测区间只有 {days} 天，不足一年；折算成年化会放大偶然性，摘要里没有给出年化收益（指标表里仍有）。",
        ))

    comparisons = []
    same = facts.get("same_stock_return")
    if _finite(same):
        comparisons.append(f"一直持有 {pct(same)}")
    else:
        missing.append(caveat(
            "same_stock_unavailable", INFO,
            facts.get("same_stock_reason") or "这段区间不足两个交易日的收盘价，算不出“一直持有”的对照收益。",
        ))
    index_return, index_name = facts.get("index_return"), facts.get("index_name")
    if _finite(index_return) and index_name:
        comparisons.append(f"{index_name} {pct(index_return)}")
    elif facts.get("index_reason"):
        missing.append(caveat("index_unavailable", INFO, facts["index_reason"]))
    if comparisons:
        result += "；同期" + "，".join(comparisons)
    sentences = [result + "。"]

    drawdown = facts.get("max_drawdown")
    if _finite(drawdown) and drawdown < 0:
        # However small, a measured drawdown is shown (with the digits it needs), never as none.
        digits = next((d for d in (1, 2, 3, 4) if round(drawdown * 100, d) != 0), None)
        text = f"最大回撤 {pct(drawdown, digits)}" if digits is not None else "最大回撤不到 0.0001%"
        peak, trough = facts.get("max_drawdown_peak"), facts.get("max_drawdown_trough")
        if peak and trough:
            text += f"（{peak}→{trough}）"
        sentences.append(text + "。")
    elif _finite(drawdown) and drawdown == 0:
        sentences.append("期间净值没有回撤。")

    fills = int(facts.get("fills") or 0)
    closed = int(facts.get("closed_trades") or 0)
    open_at_end = int(facts.get("open_at_end") or 0)
    if fills == 0:
        sentences.append("期间没有成交。")
    else:
        if closed:
            trades = f"完成 {closed} 笔交易，盈利 {int(facts.get('winning_trades') or 0)} 笔"
        else:
            trades = "没有完成的交易"
        if open_at_end:
            blocked = [p for p in ((facts.get("end_mark") or {}).get("positions") or []) if p.get("blocked")]
            trades += (f"，期末仍持有 {open_at_end} 笔（按最后收盘价估值" +
                       (f"，其中 {len(blocked)} 笔最后一天实际卖不出" if blocked else "") + "）")
        fees = facts.get("fees_total")
        if _finite(fees):
            trades += f"，费用合计 {money(fees, facts.get('currency', 'CNY'))}"
        sentences.append(trades + "。")
    return "".join(sentences), missing


# ------------------------------------------------------------------ #
#  Data caveats (from the Coverage of the traded symbol)
# ------------------------------------------------------------------ #

def _offline_reason(warnings: Iterable[str]) -> str:
    text = " ".join(warnings)
    if "下载失败" in text:
        return "下载失败"
    if "未允许下载" in text:
        return "未允许下载"
    return "未能下载"


def data_caveats(coverage: Optional[Dict[str, Any]], *, adjust: str, kind: str = "stock") -> List[Dict[str, str]]:
    """Caveats about the bars one run traded on (a ``Coverage.to_dict()``)."""
    out: List[Dict[str, str]] = []
    if not coverage:
        return out
    ticker = coverage.get("ticker", "")
    source = coverage.get("source") or ""
    from_repository = source != "dataframe"
    if source.startswith("demo"):
        out.append(caveat(
            "demo_data", INFO,
            f"演示数据：{ticker} 的 K 线由程序按固定种子生成，不是真实行情，结果只用于演示。",
        ))
    if from_repository and coverage.get("offline"):
        out.append(caveat(
            "offline_cache", WARNING,
            f"未能取得完整行情（{_offline_reason(coverage.get('warnings') or [])}），只用了本地缓存的 "
            f"{coverage.get('first')}~{coverage.get('last')}（请求 {coverage.get('requested_start')}~"
            f"{coverage.get('requested_end')}）；结果按实际有数据的区间计算，没有补齐或外推。",
        ))
    if from_repository and coverage.get("rows"):
        short = shortfall(coverage)
        if short:
            out.append(caveat("coverage_short", WARNING, short))
    if adjust in ("qfq", "hfq") and kind != "index" and from_repository:
        out.append(caveat(
            "adjusted_prices", INFO,
            f"价格按{ADJUST_LABELS[adjust]}计算：分红、送转视为再投入，买卖股数按复权价换算，与当时实际成交的"
            f"价格和股数不同；用绝对价位（如“跌破 10 元”）判断的规则会受影响。",
        ))
        out.append(caveat(
            "dividend_tax_ignored", INFO,
            "未计分红的税：复权把分红按税前全额再投入，实际到账要扣税（税率因市场和持有时间而异），结果会略偏高。",
        ))
    if adjust == "" and kind != "index":
        gaps = list(coverage.get("suspected_corporate_actions") or [])
        if gaps:
            shown = "、".join(gaps[:5]) + (f" 等 {len(gaps)} 天" if len(gaps) > 5 else "")
            out.append(caveat(
                "unadjusted_gaps", WARNING,
                f"不复权价格在 {shown} 出现疑似除权或拆合股的跳空，回测把它当成了真实涨跌，结果可能严重失真；"
                f"建议改用前复权。",
            ))
        if from_repository or gaps:
            out.append(caveat(
                "unadjusted_prices", INFO,
                "价格不复权：现金分红不计入收益，送转股会显示成股价下跌。",
            ))
    return out


def shortfall(coverage: Dict[str, Any]) -> Optional[str]:
    """The coverage_short text, or None when the bars span the requested window."""
    start, end = coverage.get("requested_start"), coverage.get("requested_end")
    first, last = coverage.get("first"), coverage.get("last")
    if not (start and end and first and last):
        return None
    parts = []
    # With warm-up rows the data reaches before the start.
    if not coverage.get("warmup_rows") and (pd.Timestamp(first) - pd.Timestamp(start)).days > SHORTFALL_DAYS:
        parts.append(f"{first} 之前没有数据（上市较晚或数据缺失）")
    if (pd.Timestamp(end) - pd.Timestamp(last)).days > SHORTFALL_DAYS:
        parts.append(f"{last} 之后没有数据（停牌、退市或数据未更新）")
    if not parts:
        return None
    return (f"{coverage.get('ticker', '')} 的行情比请求的 {start}~{end} 短：{'；'.join(parts)}。"
            f"收益和年化按实际有数据的区间计算。")


# ------------------------------------------------------------------ #
#  Run-level caveats
# ------------------------------------------------------------------ #

def in_sample_caveat() -> Dict[str, str]:
    return caveat(
        "in_sample", INFO,
        "参数是在这同一段历史上看的结果，没有在别的时间段或别的股票上检验；在同一段历史上反复调参，"
        "结果会越来越好看，但不代表未来。",
    )


def commission_caveat(market: str, rate: float, minimum: float, currency: str) -> Dict[str, str]:
    label = MARKET_LABELS.get(market, market)
    if rate == 0 and minimum == 0:
        text = f"佣金按{label}默认 0 估算（多数美股券商零佣金），与你券商的实际收费可能不同，可在高级设置里修改。"
    else:
        text = (f"佣金按{label}默认 {rate * 100:.3g}%（每笔最低 {minimum:g} {CURRENCY_UNITS.get(currency, currency)}）"
                f"估算，与你券商的实际费率可能不同，可在高级设置里修改。")
    return caveat("commission_assumed", INFO, text)


FEE_NAMES = {"stamp_duty": "印花税", "transfer_fee": "过户费", "trading_fee": "交易费", "sfc_levy": "证监会征费",
             "afrc_levy": "会财局征费", "sec_fee": "SEC 费"}


def legacy_fees_caveat(record: Dict[str, Any]) -> Dict[str, str]:
    """The legacy_fees_translated caveat for a manifest's spec.legacy_fees record."""
    sent = float(record.get("commission_sell_sent") or 0.0)
    included = record.get("included_in_sell_rate")
    if not included:
        # Saved before 2026-11 (the rate was cut to the buy commission and stamp duty charged on top).
        used = float(record.get("commission_sell_used") or 0.0)
        return caveat(
            "legacy_fees_translated", INFO,
            f"旧版请求的卖出佣金率 {sent * 100:.3g}% 按“含印花税”理解：卖出佣金按 {used * 100:.3g}% 计算，"
            f"印花税、过户费等按成交日的法定费率另计。",
        )
    names = "、".join(FEE_NAMES.get(name, name) for name in included)
    extra = "、".join(FEE_NAMES.get(name, name) for name in record.get("charged_on_top") or [])
    on_top = f"v3.2 没有计算的{extra}按成交日的费率另计。" if extra else ""
    return caveat(
        "legacy_fees_translated", INFO,
        f"旧版请求（没有 fee_basis）的卖出佣金率 {sent * 100:.3g}% 按 v3.2 的含义理解为已含{names}："
        f"卖出时按 {sent * 100:.3g}% 收取，不再另收{names}；{on_top}"
        f"要让卖出佣金只是佣金、{names}另计，请求里加 fee_basis: \"commission_only\"。",
    )


def data_changed_caveat(changes: List[Dict[str, Any]]) -> Dict[str, str]:
    """changes: [{ticker, before, after, features?}]: features=True means the same bars with
    other derived features (before/after are then the features' hashes)."""
    def one(change):
        before, after = change.get("before"), change.get("after")
        if change.get("features"):
            return f"{change['ticker']} 由行情算出的指标列 {(before or '未记录')[:8]}→{(after or '未记录')[:8]}（K 线相同）"
        if before and after:
            return f"{change['ticker']} {before[:8]}→{after[:8]}"
        return f"{change['ticker']} {'新增' if after else '缺失'}"

    detail = "；".join(one(change) for change in changes)
    if all(change.get("features") for change in changes):
        why = "K 线相同，是计算均线、RSI 等指标的代码改过"
    elif any(change.get("features") for change in changes):
        why = "可能是复权锚点变化、数据补齐或修正，也有计算指标的代码改过"
    else:
        why = "可能是复权锚点变化、数据补齐或修正"
    return caveat(
        "data_changed", WARNING,
        f"策略这次拿到的数据与保存时不同（{detail}），{why}；这次结果与原记录不能直接比较。",
    )


DATA_RULES = {1: "只含开高低收、成交量和成交额，不含涨跌幅、换手率和引擎算出的指标列",
              2: "含全部 K 线列，但不含引擎由 K 线算出的指标列（均线、RSI 等）"}


def data_unverified_caveat(before_rule, after_rule) -> Dict[str, str]:
    rule = DATA_RULES.get(before_rule, "规则不明")
    return caveat(
        "data_unverified", WARNING,
        f"保存这条记录时的数据哈希按旧规则计算（第 {before_rule} 版，{rule}），"
        f"与现在的哈希（第 {after_rule} 版）不能比较；无法确认这次策略拿到的数据与原记录相同。",
    )


def strategy_changed_caveat(name: str, *, changes: Optional[List[str]] = None, replayed: bool = False) -> Dict[str, str]:
    """changes: the strategy that ran differs from the saved one (warning). replayed: the
    saved definition was replayed, but today's definition of the method differs (info)."""
    if changes:
        return caveat(
            "strategy_changed", WARNING,
            f"策略「{name}」与保存时不同（{'；'.join(changes)}）：这次运行的是现在的代码，"
            f"结果与原记录不能直接比较。",
        )
    return caveat(
        "strategy_changed", INFO,
        f"方法「{name}」现在的定义与保存时不同；这次按保存时的规则和参数原样重放，结果可以和原记录比较，"
        f"但用现在的方法新跑一次会不同。",
    )


def strategy_unverified_caveat(name: str, missing: Optional[List[str]] = None) -> Dict[str, str]:
    """missing: what of the saved definition could not be compared (None: no definition at all)."""
    if missing:
        return caveat(
            "strategy_unverified", WARNING,
            f"保存这条记录时没有记下（或现在读不到）策略「{name}」所依赖的{'、'.join(missing)}的指纹；"
            f"这次用现在的代码重跑，如果这些代码之后改过，结果会与原记录不同，无法核对。",
        )
    return caveat(
        "strategy_unverified", WARNING,
        f"这条记录是旧版本保存的，没有记下策略「{name}」的完整定义（参数默认值、规则或源码）；这次按现在的定义重跑，"
        f"如果方法或策略代码之后改过，结果会与原记录不同，无法核对。",
    )


def settings_changed_caveat(changed: List[str]) -> Dict[str, str]:
    return caveat(
        "settings_changed", WARNING,
        f"这次重跑时{'、'.join(changed)}与保存时不同（例如市场默认值或规则更新了），即使行情相同，结果也可能不同。",
    )


def settings_unverified_caveat(items: List[str]) -> Dict[str, str]:
    return caveat(
        "settings_unverified", WARNING,
        f"这条记录保存时没有记下设置的解析结果，这次按现在的规则解析：{'、'.join(items)}无法与保存时核对，"
        f"即使行情相同，结果也可能不同。",
    )


def runtime_changed_caveat(before, after) -> Dict[str, str]:
    def text(runtime):
        return "，".join(f"{k} {v}" for k, v in sorted((runtime or {}).items())) or "未记录"
    return caveat(
        "runtime_changed", INFO,
        f"运行环境的版本不同（保存时 {text(before)}；现在 {text(after)}），数值可能有细微差别。",
    )


def engine_changed_caveat(before: str, after: str) -> Dict[str, str]:
    return caveat(
        "engine_changed", INFO,
        f"回测引擎版本不同（保存时 {before or '未记录'}，现在 {after}），即使行情相同，结果也可能不同。",
    )


def sort_caveats(items: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    """Warnings first; one caveat per code (the first one wins)."""
    seen, out = set(), []
    for item in items:
        if item["code"] in seen:
            continue
        seen.add(item["code"])
        out.append(item)
    order = {WARNING: 0, INFO: 1}
    return sorted(out, key=lambda item: order.get(item["level"], 2))
