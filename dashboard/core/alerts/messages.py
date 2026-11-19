"""The Chinese text of alert notifications and proposals. Built by code from numbers, never by a model."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping, Optional, Sequence, Tuple

from core.market.calendar import SHANGHAI
from core.market.quotes import DELAYED, LiveQuote
from core.market.valuation import quantized

from .engine import (DAY_CHANGE_PCT, KIND_LABELS, MAX_FIRES_PER_DAY, POSITION_WEIGHT_MAX, STOP_LOSS, TARGET_PRICE,
                     Decision, Rule, describe_condition, priority, scientific)

NOT_AN_ORDER = '这只是提醒，不是委托单，系统不会替你下单。'
CENT = Decimal('0.01')
MARKET_NAMES = {'CN': 'A 股', 'HK': '港股', 'US': '美股'}


def _too_large(value: Decimal) -> bool:
    return value.is_finite() and value.adjusted() >= 16


def number(value: Optional[Decimal], places: int = 6) -> str:
    """A price or rate without padding: 12.3 stays 12.3. Never raises; absurd values in a short E form."""
    if value is None:
        return '—'
    try:
        if not value.is_finite():
            return '—'
        if _too_large(value):
            return scientific(value)
        text = format(quantized(value, Decimal(1).scaleb(-places)), 'f')
    except (ArithmeticError, ValueError, TypeError, AttributeError):
        return scientific(value) if isinstance(value, Decimal) else '—'
    text = text.rstrip('0').rstrip('.') if '.' in text else text
    return '0' if text in ('-0', '') else text


def percent(value: Optional[Decimal]) -> str:
    """Two places, half up. Never raises."""
    if value is None:
        return '—'
    try:
        if not value.is_finite():
            return '—'
        if _too_large(value):
            return scientific(value)
        return format(quantized(value, CENT), 'f')
    except (ArithmeticError, ValueError, TypeError, AttributeError):
        return scientific(value) if isinstance(value, Decimal) else '—'


def clock_text(moment: Optional[datetime]) -> str:
    if moment is None:
        return '未知'
    return moment.astimezone(SHANGHAI).strftime('%Y-%m-%d %H:%M:%S')


def label(ticker: str, name: Optional[str]) -> str:
    return f'{name}（{ticker}）' if name and name != ticker else ticker


def _condition_sentence(rule: Rule, decision: Decision, quote: Optional[LiveQuote]) -> str:
    price = number(quote.price if quote else None)
    threshold = number(rule.threshold, 8)
    if rule.kind == STOP_LOSS:
        return f'现价 {price}，已跌到止损价 {threshold} 或以下。'
    if rule.kind == TARGET_PRICE:
        return f'现价 {price}，已涨到目标价 {threshold} 或以上。'
    if rule.kind == DAY_CHANGE_PCT:
        direction = '跌幅' if rule.threshold < 0 else '涨幅'
        prev = number(quote.prev_close if quote else None)
        moved = decision.value.copy_abs() if decision.value is not None else None
        return (f'今日{direction} {percent(moved)}%（现价 {price}，昨收 {prev}），'
                f'已达到设定的 {number(rule.threshold.copy_abs(), 8)}%。')
    if rule.kind == POSITION_WEIGHT_MAX:
        where = f'在账户“{rule.account}”中的持仓' if rule.account else '持仓'
        return (f'{where}约占组合 {percent(decision.value)}%（现价 {price}），已达到或超过上限 {threshold}%。'
                '仓位按全部账户的人民币总资产（含现金和基金）计算。')
    return describe_condition(rule.kind, rule.threshold)


def alert(rule: Rule, decision: Decision, quote: Optional[LiveQuote], name: Optional[str]) -> Tuple[str, str, str]:
    """``(level, title, body)`` of a fired rule."""
    kind = KIND_LABELS.get(rule.kind, rule.kind)
    title = f'{kind}提醒：{name} {rule.ticker}' if name and name != rule.ticker else f'{kind}提醒：{rule.ticker}'
    lines = [f'{label(rule.ticker, name)}{_condition_sentence(rule, decision, quote)}']
    if quote is not None:
        delayed = f'，{quote.reason}' if quote.status == DELAYED and quote.reason else ''
        lines.append(f'行情来源：{quote.source}，交易所时间 {clock_text(quote.quote_time)}（北京时间）{delayed}。')
    lines.append(f'这是该规则本交易日第 {decision.fire_number} 次提醒（每条规则每个交易日最多 {MAX_FIRES_PER_DAY} 次）。')
    if rule.note:
        lines.append(f'备注：{rule.note}')
    lines.append(NOT_AN_ORDER)
    return ('critical' if rule.kind == STOP_LOSS else 'warning'), title, '\n'.join(lines)


def digest(events: Sequence[Mapping], cap: int, window_minutes: int) -> Tuple[str, str]:
    """``(title, body)`` for alerts that were recorded but not pushed one by one.

    Lists stop-losses first, then by ``engine.PRIORITY``, then in the order they fired.
    """
    ordered = sorted(events, key=lambda event: (priority(event.get('kind')), event.get('id') or 0))
    shown = ordered[:10]
    stops = sum(1 for event in events if event.get('kind') == STOP_LOSS)
    lines = [f'{window_minutes} 分钟内提醒已超过 {cap} 条。为避免刷屏，以下 {len(events)} 条提醒没有单独推送，'
             '只在这里汇总（提醒记录页可查看全部）' + (f'，其中 {stops} 条止损，请优先查看：' if stops else '：')]
    for event in shown:
        lines.append(f'· {event["title"]}，{event["fired_at"][11:19]}')
    if len(events) > len(shown):
        lines.append(f'· 另有 {len(events) - len(shown)} 条，请在提醒记录页查看。')
    lines.append(NOT_AN_ORDER)
    return f'提醒汇总：{len(events)} 条提醒未单独推送', '\n'.join(lines)


def paused(failures: int, reason: str, since: Optional[str]) -> Tuple[str, str]:
    return '行情中断，提醒暂停', (
        f'提醒监控已连续 {failures} 次没有取得可用的行情（{reason}）。'
        f'{"自 " + since[11:19] + " 起，" if since else ""}在行情恢复之前价格提醒不会触发，请自行留意行情。'
        '可能的原因：数据源中断、网络问题，或当地休市而节假日表未收录。恢复后会再通知一次。')


def resumed(since: Optional[str]) -> Tuple[str, str]:
    return '行情已恢复，提醒已恢复', (
        f'行情{"自 " + since[11:19] + " 起" if since else ""}中断，现已恢复，价格提醒继续检查。'
        '中断期间若价格越过阈值后又回到阈值以内，不会补发提醒；仍在阈值以外的规则会在本次检查中提醒。')


def calendar_warning(market: str, day: str) -> Tuple[str, str]:
    name = MARKET_NAMES.get(market, market)
    return f'{name}交易日历与行情不一致', (
        f'节假日表显示 {name} {day} 休市，但行情的交易所时间仍在更新。为避免漏掉提醒，今天仍按交易时段检查提醒。'
        '请核对并更新节假日表（core/market/holidays.json）。')


def describe_proposal(payload: Mapping) -> str:
    """The one line a person reads before confirming an alert rule proposal."""
    kind = KIND_LABELS.get(payload.get('kind'), payload.get('kind'))
    condition = describe_condition(payload['kind'], Decimal(payload['threshold']))
    account = f'，账户“{payload["account"]}”' if payload.get('account') else ''
    note = f'。备注：{payload["note"]}' if payload.get('note') else ''
    return (f'新增{kind}提醒：{payload["ticker"]}{account}，{condition}时提醒（每个交易日最多 {MAX_FIRES_PER_DAY} 次）'
            f'{note}。只提醒，不会下单。')


def weight_rules_held(entries) -> Tuple[str, str]:
    """The once-a-day notice of weight rules held back for long; ``entries`` are ``rule_skips`` entries."""
    since = min(entry['since'] for entry in entries)
    tickers = '、'.join(dict.fromkeys(str(entry.get('ticker')) for entry in entries))
    reason = entries[0].get('message') or '组合估值未经核实'
    body = (f'{len(entries)} 条仓位上限提醒（{tickers}）自 {since[:16].replace("T", " ")} 起无法检查：{reason}。'
            '原因消除前（例如停牌的持仓复牌、行情或汇率恢复）不会按仓位提醒；其他提醒照常检查。'
            '这条通知每天最多一次，提醒页显示当前情况。')
    return '仓位上限提醒暂停', body


def duplicate_reason(kept: int) -> str:
    """``disabled_reason`` of a rule disabled as a repeat of rule ``kept``."""
    return (f'与提醒规则 #{kept} 重复（同一证券的不同写法，例如 HKG:700 与 HKG:00700），已自动停用，'
            f'避免同一次触发提醒两次；它的提醒记录保留。')


def duplicates_disabled(pairs: Sequence[Tuple[int, int]]) -> Tuple[str, str]:
    """The notice of the one-time reconciliation; ``pairs`` are ``(disabled id, kept id)``."""
    shown = '、'.join(f'#{rule_id}（与 #{kept} 重复）' for rule_id, kept in pairs[:10])
    more = f'等 {len(pairs)} 条' if len(pairs) > 10 else ''
    return '已停用重复的提醒规则', (
        f'以下提醒规则与另一条规则是同一证券的不同写法（例如 HKG:700 与 HKG:00700），条件相同，'
        f'会对同一次触发重复提醒，已自动停用：{shown}{more}。保留的规则照常检查，停用规则的提醒记录保留。')
