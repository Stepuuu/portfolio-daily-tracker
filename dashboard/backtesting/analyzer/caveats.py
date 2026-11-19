"""Rule-generated caveats for a backtest result.

Each caveat is ``{"code", "level", "text"}``: ``code`` is stable for code that
reads the result, ``level`` is ``info`` or ``warning``, ``text`` is the Chinese
sentence shown to the user. Rules only read facts the run recorded; nothing is
inferred from the strategy's code.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

from backtesting.broker import market_rules
from backtesting.broker.fees import NOT_MODELLED, not_modelled_hk

INFO, WARNING = "info", "warning"

REASON_TEXT = {
    "lot_unaffordable": "一手超过可用资金",
    "insufficient_cash": "资金不足",
    "limit_up": "涨停买不进",
    "limit_down": "跌停卖不出",
    "suspended": "停牌或缺少当日行情",
    "t_plus_one": "T+1 当日不能卖出",
    "volume_cap": "超过当日成交量的 10%",
    "limit_queue": "涨跌停排队只成交一部分",
    "below_min_lot": "数量不足一手",
    "no_position": "没有可卖持仓",
    "price_not_reached": "限价未触及",
    "lot_rounding": "数量不是整手",
    "end_of_data": "数据结束时仍未成交",
    "position_closed": "持仓已被同一根K线上更早的卖出卖完",
    "odd_lot": "卖出数量不符合整手规则（不足一手的零股须一次性全部卖出，不能卖零碎股数）",
}

FINAL_MISS = ("expired", "open_at_end", "rejected", "cancelled")


def caveat(code: str, level: str, text: str) -> Dict[str, str]:
    return {"code": code, "level": level, "text": text}


def _money(value) -> str:
    return f"{float(value):,.0f}"


def lot_caveats(unfilled: Iterable[dict], symbols: Iterable[str], config) -> List[Dict[str, str]]:
    out = []
    unaffordable = [entry for entry in unfilled if entry.get("reason") == "lot_unaffordable"]
    if unaffordable:
        first = unaffordable[0]
        out.append(caveat(
            "lot_unaffordable", WARNING,
            f"有 {len(unaffordable)} 次买入信号没有下单：一手（{first.get('lot')} 股，约 {_money(first.get('lot_cost', 0))}）"
            f"超过该信号可用的资金（约 {_money(first.get('budget', 0))}）。交易少或为零是资金不够一手，不是没有信号；"
            f"可提高本金或仓位比例，或核对每手股数。",
        ))
    assumed = [symbol for symbol in symbols if config.lot_rule_for(symbol).assumed]
    if assumed:
        out.append(caveat(
            "lot_assumed", INFO,
            f"{'、'.join(assumed)}：{market_rules.HK_LOT_NOTE}。",
        ))
    return out


def unfilled_caveats(unfilled: Iterable[dict]) -> List[Dict[str, str]]:
    entries = [entry for entry in unfilled if entry.get("reason") != "lot_unaffordable"]
    if not entries:
        return []
    counts = Counter(entry.get("reason") for entry in entries)
    reasons = "，".join(f"{REASON_TEXT.get(reason, reason)} {count} 次" for reason, count in counts.most_common())
    missed = sum(1 for entry in entries if entry.get("outcome") in FINAL_MISS)
    late = sum(1 for entry in entries if entry.get("outcome") == "filled_late")
    partial = sum(1 for entry in entries if entry.get("outcome") == "partial")
    outcomes = "，".join(text for text in (
        f"{missed} 个最终没有成交" if missed else "",
        f"{late} 个推迟成交" if late else "",
        f"{partial} 个只成交了一部分" if partial else "",
    ) if text)
    return [caveat(
        "unfilled_signals", WARNING if missed else INFO,
        f"有 {len(entries)} 个交易信号没有按时全部成交（{reasons}）：{outcomes}。"
        f"买入信号未成交只保留 1 个交易日，卖出最多重试 20 个交易日；结果按实际能成交的情况计算，明细见未成交记录。",
    )]


def stop_carried_caveats(unfilled: Iterable[dict], config=None) -> List[Dict[str, str]]:
    """Stops that triggered but could not sell in full on the trigger bar."""
    carried = [entry for entry in unfilled if entry.get("carried_by")]
    if not carried:
        return []
    counts = Counter((entry.get("reasons") or [entry.get("reason")])[0] for entry in carried)
    reasons = "，".join(f"{REASON_TEXT.get(reason, reason)} {count} 次" for reason, count in counts.most_common())
    missed = sum(1 for entry in carried if entry.get("outcome") in FINAL_MISS)
    retry = getattr(config, "sell_retry_bars", 20)
    return [caveat(
        "stop_carried", WARNING if missed else INFO,
        f"有 {len(carried)} 个止损触发后当天没能全部卖出（{reasons}）：止损一旦触发就不再撤回，未卖出的部分转为市价卖单，"
        f"从下一个交易日起按开盘价卖出（与普通卖单一样最多重试 {retry} 个交易日），成交价可能明显低于止损价"
        + (f"；其中 {missed} 个最终仍未卖出，之后这部分持仓没有止损保护" if missed else "")
        + "。明细见未成交记录（carried_by 为转成的市价卖单）。",
    )]


def warmup_caveats(warmup_info: Dict[str, Dict[str, Any]], bar_counts: Dict[str, int]) -> List[Dict[str, str]]:
    out = []
    for symbol, info in warmup_info.items():
        needed, available = info.get("bars", 0), info.get("available", 0)
        if not needed:
            continue
        if info.get("first_date") is None:
            out.append(caveat(
                "warmup_short", WARNING,
                f"{symbol} 只有 {bar_counts.get(symbol, 0)} 根K线，不超过预热期需要的 {needed} 根，策略一次也没有运行。",
            ))
        elif info.get("requested_start") and available < needed:
            out.append(caveat(
                "warmup_short", WARNING,
                f"{symbol} 开始日 {info['requested_start']} 之前只有 {available} 根K线，少于预热期需要的 {needed} 根："
                f"策略从 {info['first_date']} 指标形成后才开始运行，此前不交易，也不计入收益和风险统计。",
            ))
    return out


FEW_TRADES = 10


def few_trades_caveats(closed_trades: Optional[int]) -> List[Dict[str, str]]:
    if closed_trades is None or closed_trades >= FEW_TRADES:
        return []
    return [caveat(
        "few_trades", WARNING,
        f"只有 {closed_trades} 笔完整交易（少于 {FEW_TRADES} 笔）：胜率、盈亏比、利润因子等交易统计偶然性很大，"
        f"不足以判断这个方法好不好。",
    )]


def end_mark_caveats(end_mark: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    if not end_mark:
        return []
    symbols = "、".join(position["symbol"] for position in end_mark["positions"])
    text = (f"期末仍持有 {symbols}，按最后一个交易日（{end_mark['date']}）的收盘价估值并扣除卖出费用，未计滑点；"
            f"这笔估值盈亏 {end_mark['pnl']:+,.2f} 已计入总收益，但不计入胜率等交易统计。")
    blocked = [p for p in end_mark["positions"] if p.get("blocked")]
    if blocked:
        detail = "；".join(f"{p['symbol']} {_blocked_text(p)}" for p in blocked)
        return [caveat("end_mark", WARNING, text + f"其中{detail}，实际无法按这个价格卖出，结果可能偏乐观。")]
    return [caveat("end_mark", INFO, text)]


def _blocked_text(position: Dict[str, Any]) -> str:
    """Why an end-marked position could not really be sold on its last day."""
    if position["blocked"] == "limit_down":
        return "当日一字跌停"
    if position["blocked"] == "t_plus_one":
        quantity = position.get("blocked_quantity") or 0
        whole = not quantity or quantity >= position.get("quantity", quantity)
        return ("当日买入" if whole else f"有 {quantity:g} 股是当日买入") + "（A 股 T+1，当天不能卖出）"
    return "当日停牌或没有行情"


def adjustment_caveats(adjustments: Optional[Dict[str, str]]) -> List[Dict[str, str]]:
    """Unadjusted (adjust="") symbols: dividends and corporate actions are not in the prices."""
    raw = [symbol for symbol, adjust in (adjustments or {}).items() if adjust == ""]
    if not raw:
        return []
    indices = [symbol for symbol in raw if str(symbol).upper().startswith("IDX:")]
    stocks = [symbol for symbol in raw if symbol not in indices]
    out = []
    if stocks:
        out.append(caveat(
            "unadjusted_prices", WARNING,
            f"{'、'.join(stocks)} 用的是不复权价格：分红、送转、拆合股都没有计入，除权日的价格下跳被当成亏损，"
            f"分红现金也没有到账，收益被低估或扭曲。疑似除权日扫描只能发现大幅跳空，发现不了现金分红，"
            f"没有发现不代表结果可靠；要得到可靠的收益请改用前复权。",
        ))
    if indices:
        out.append(caveat(
            "unadjusted_prices", INFO,
            f"{'、'.join(indices)} 是价格指数，不含成分股分红：按它计算的收益低于跟踪该指数的全收益投资。",
        ))
    return out


MARKET_NAMES = {"a_share": "A股", "hk": "港股", "us": "美股"}


def fee_caveats(fee_assumptions: Iterable) -> List[Dict[str, str]]:
    assumptions = list(fee_assumptions)
    if not assumptions:
        return []
    markets = sorted({market for market, _ in assumptions})
    first = min(day for _, day in assumptions)
    names = "、".join(MARKET_NAMES.get(market, market) for market in markets)
    return [caveat(
        "fee_assumed", WARNING,
        f"有 {len(assumptions)} 个交易日的成交早于费率表收录的起始日期（最早 {first}，{names}）："
        f"这些成交的税费按最早收录的费率估算（早期费率简化），与当时实际费率可能不同。",
    )]


def fee_limit_caveats(symbols: Iterable[str], config) -> List[Dict[str, str]]:
    """One fee_not_modelled caveat per market the run touches whose fees are not
    fully modelled (fee_schedules.json `ignored`, worded in fees.NOT_MODELLED; for HK
    the ETF stamp-duty relief only where it can apply, fees.not_modelled_hk)."""
    markets: Dict[str, List[str]] = {}
    for symbol in symbols:
        try:
            market = config.market_for(symbol)
        except ValueError:
            continue
        markets.setdefault(market, []).append(symbol)
    out = []
    for market in sorted(markets, key=lambda m: list(MARKET_NAMES).index(m) if m in MARKET_NAMES else 9):
        level, text = not_modelled_hk(markets[market]) if market == "hk" else NOT_MODELLED.get(market, ("", ""))
        if text:
            out.append(caveat("fee_not_modelled", WARNING if level == "warning" else INFO,
                              f"{MARKET_NAMES.get(market, market)}费用没有完全建模：{text}"))
    return out


def build_caveats(
    *,
    unfilled: List[dict],
    symbols: List[str],
    config,
    warmup_info: Optional[Dict[str, Dict[str, Any]]] = None,
    bar_counts: Optional[Dict[str, int]] = None,
    end_mark: Optional[Dict[str, Any]] = None,
    closed_trades: Optional[int] = None,
    fee_assumptions: Iterable = (),
    extra: Optional[List[Dict[str, Any]]] = None,
    adjustments: Optional[Dict[str, str]] = None,
    single_entry: bool = False,
) -> List[Dict[str, str]]:
    """All caveats of a run, warnings first.

    Codes: warmup_short, lot_unaffordable, lot_assumed, unfilled_signals,
    stop_carried, few_trades, end_mark, fee_assumed, fee_not_modelled,
    unadjusted_prices.

    adjustments: symbol -> adjust ("qfq" / "hfq" / "") of the bars each symbol
    ran on; an unadjusted symbol always gets the unadjusted_prices caveat.
    single_entry: the strategy buys at most once by design (一直持有, a buy date):
    one round trip at most, so there is no few_trades caveat (nothing to act on).
    """
    out: List[Dict[str, str]] = []
    out += adjustment_caveats(adjustments)
    out += warmup_caveats(warmup_info or {}, bar_counts or {})
    out += lot_caveats(unfilled, symbols, config)
    out += unfilled_caveats(unfilled)
    out += stop_carried_caveats(unfilled, config)
    out += [] if single_entry else few_trades_caveats(closed_trades)
    out += end_mark_caveats(end_mark)
    out += fee_caveats(fee_assumptions)
    out += fee_limit_caveats(symbols, config)
    out += list(extra or [])
    order = {WARNING: 0, INFO: 1}
    return sorted(out, key=lambda item: order.get(item["level"], 2))


def reason_counts(unfilled: Iterable[dict]) -> Counter:
    return Counter(entry.get("reason") for entry in unfilled)
