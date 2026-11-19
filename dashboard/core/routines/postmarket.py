"""Routine ``postmarket``: the post-market brief, due 15:45 Asia/Shanghai on A-share trading days.

It runs after the close snapshot (15:40) but does not depend on it: the
snapshot's state of the day is only mentioned (``notices``).

One snapshot. The ledger is read once (``brief_inputs.read_snapshot``) and
replayed as of the trading day: events dated later are not applied (a
reversal dated tomorrow does not count today), and for an account the dated
replay had to correct, the later reversals are applied as the corrected
replay applies them (``today.events_in_force``, the Today page's rule). That
one view is valued once, with one quote fetch and one rate fetch, and
every section is derived from it: the trades, their plan links, the
positions sold out today, the profit and the stop / target check.

Sections and their sources (see ``core.routines.briefs`` for the fact format):

trades      the effective events dated the trading day: buys and sells with
            quantity, price, fee and amount, and the other records (dividends,
            fees, cash).
plan_check  ``core.plans.deviations_for_date`` for the day's trades: every linked
            buy and sell with its relation and deviations, grouped by deviation
            code with the plan module's own messages. A sale without a plan is
            not "按计划" but its own group; trades without a link (booked before
            any plan existed) are counted as not checked.
pnl         day profit per position (``valuation.value_portfolio``, the Today
            page's algorithm): price moves and trade fees, not dividends,
            standalone fees, currency moves or fund revaluations. Each row says
            which session its price is from, in words true when it was made: a
            close says its date ("纽约 11-16 收盘"); a price of a session still
            trading says so ("香港 11-17 盘中价 15:44，非收盘价，16:00 收盘") and
            makes the row and the total provisional. An amount converted to CNY
            carries the rate, its source and time (``fx``), and is estimated when
            the rate is not verified.
alerts      alerts fired on the trading day (the alert log): all counted, the
            newest listed, each saying whether its notification was read.
levels      holdings at or beyond an active stop or target at the latest price,
            matched by listing and account. The count is 不可用 when a holding
            with a stop or target has no usable price, and an estimate that
            names the holdings it could not check when one of them rests on a
            price that is not verified (stale, a New York close not yet final),
            a zero included.
lessons     ``lesson`` proposals waiting for confirmation.

The run fails (and is retried, the partial brief stored) when the ledger
cannot be read or when not one of the positions the brief needs a price for
(open ones and those sold out today) has a usable quote.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Dict, List, Optional, Sequence, Tuple

from core.market.calendar import CN, HK, SHANGHAI, US
from core.market.quotes import CLOSED, DELAYED, LIVE, PREV_CLOSE_ONLY, STALE, TIME_UNKNOWN, FxRate, LiveQuote
from core.market.today import (DAY_PNL_EXCLUDES, US_DATES_HINT, WORKING_DIGITS, day_basis, traded_recently,
                               us_trades_recently)
from core.market.valuation import PositionValue, quote_estimate, rate_estimate, value_portfolio
from core.routines import briefs, premarket, scheduler
from core.routines import brief_inputs as inputs
from core.routines.brief_inputs import (InputUnavailable, day_text, dec, exact, grouped, local_clock, money_text,
                                        plain, quote_time, short, two)
from core.routines.premarket import alert_fact, level_facts

logger = logging.getLogger(__name__)

KIND = briefs.POSTMARKET
TITLE = briefs.TITLES[KIND]
SNAPSHOT_KIND = 'close_snapshot'
PLACE = {CN: 'A股', HK: '香港', US: '纽约'}

LEDGER_KIND_LABEL = {
    'opening': '期初余额', 'buy': '买入', 'sell': '卖出', 'deposit': '入金', 'withdrawal': '出金', 'dividend': '分红',
    'fee': '费用', 'transfer': '账户转账', 'fx': '换汇', 'split': '拆股', 'reverse': '冲销', 'fund_value': '基金估值调整',
    'fund_buy': '基金申购', 'fund_sell': '基金赎回', 'interest': '利息',
}
DEVIATION_LABELS = {
    'no_plan': '没有交易计划', 'after_invalidation': '计划结束后仍买入', 'outside_entry_range': '超出计划入场区间',
    'below_stop_buy': '在止损价以下买入', 'over_planned_quantity': '超过计划股数', 'over_planned_weight': '超过计划仓位',
    'weight_unknown': '仓位无法核对', 'sold_below_stop': '低于止损价卖出', 'sold_before_target': '未到目标价卖出',
    'price_unknown': '成交价无法对照计划价格', 'plan_conflict': '多个计划同时启用',
}
RELATION_LABELS = {'entry': '建仓', 'add': '加仓', 'reduce': '减仓', 'exit': '清仓', 'none': '无计划'}
EXCLUDES = {'dividend': '分红', 'fee': '单独费用'}


def due(clock):
    return briefs.due_at(KIND, clock)


async def run(trading_date: str):
    """The scheduler's entry: the brief is stored by the outcome's ``commit`` while the run owns its claim."""
    return await briefs.produce(KIND, trading_date, build, store=False)


def _guarded(reader, what: str):
    try:
        return reader(), None
    except InputUnavailable as exc:
        return None, str(exc)
    except Exception as exc:
        logger.warning('postmarket brief: %s could not be read: %s', what, type(exc).__name__)
        return None, f'{what}暂时无法读取'


def _event_name(event: dict, names: Dict[str, str]) -> str:
    ticker = event.get('ticker')
    own = event.get('name') if event.get('name') != ticker else None
    return own or (names.get(ticker) if ticker else None) or ticker or ''


def _ref(**keys) -> dict:
    return {key: value for key, value in keys.items() if value is not None}


def _currency(event: dict) -> str:
    return event.get('currency') or (inputs.currency_of(event['ticker']) if event.get('ticker') else None) or 'CNY'


# -- reading ----------------------------------------------------------------------

class _NoLaterReversals:
    """For ``plans.deviations_for_date``: no event counts as reversed here. The brief keeps only the links of
    trades in force on the day of its own snapshot, so a reversal dated later does not drop a trade of today."""

    @staticmethod
    def history() -> List[dict]:
        return []


def _links(view: inputs.LedgerView, trading_date: str) -> List[dict]:
    from core import plans
    trades = {event.get('id') for event in view.on(trading_date, ('buy', 'sell'))}
    return [link for link in plans.deviations_for_date(trading_date, _NoLaterReversals())
            if link['event_id'] in trades]


def _value(view: inputs.LedgerView, runtime, extra: Sequence[str]):
    """``(valuation, quotes, rates)`` of the view: one quote fetch, one rate fetch."""
    clock = runtime.clock
    held = list(dict.fromkeys(ticker for account in view.state['accounts'].values() for ticker in account['positions']))
    tickers = list(dict.fromkeys(held + traded_recently(view.events, clock) + list(extra)))
    quotes = runtime.quotes.get(tickers) if tickers else {}
    rates = runtime.quotes.fx()
    with localcontext() as context:
        context.prec = WORKING_DIGITS
        prev_state, todays, reasons = day_basis(view.events, held, quotes, clock)
        valuation = value_portfolio(view.state, quotes, rates, prev_state, todays, day_reasons=reasons)
    return valuation, quotes, rates


# -- sections ---------------------------------------------------------------------

def _trades(view, problem: Optional[str], trading_date: str, stamp: str, names: Dict[str, str]) -> dict:
    title = '今日成交'
    if view is None:
        return briefs.section('trades', title, [briefs.fact('今日成交', None, f'不可用：{problem}', source=inputs.SOURCE_LEDGER,
                                                           as_of=stamp, unit='count', metric='trade_count',
                                                           status=briefs.unavailable_status(problem))],
                              status=briefs.unavailable_status(problem))
    source = inputs.ledger_source(view)
    todays = view.on(trading_date)
    trades = [event for event in todays if event['kind'] in ('buy', 'sell')]
    others = [event for event in todays if event['kind'] not in ('buy', 'sell')]
    buys = sum(1 for event in trades if event['kind'] == 'buy')
    text = (f'{len(trades)} 笔（买入 {buys}，卖出 {len(trades) - buys}）' if trades
            else ('今日没有已确认的成交' if view.state is not None else '账本还没有已确认的数据'))
    facts = [briefs.fact('今日成交', len(trades), text, source=source, as_of=stamp, unit='count', metric='trade_count')]
    for event in trades:
        quantity, price, fee = dec(event.get('quantity')), dec(event.get('price')), dec(event.get('fee')) or Decimal(0)
        currency = _currency(event)
        amount = quantity * price if quantity is not None and price is not None else None
        label = f'{LEDGER_KIND_LABEL[event["kind"]]} {_event_name(event, names)}'
        accounts = f'，{event.get("account")}' if len(view.accounts()) > 1 and event.get('account') else ''
        facts.append(briefs.fact(
            label, two(amount),
            f'{exact(quantity)} 股 × {grouped(exact(price))} = {money_text(two(amount), currency)}，'
            f'费用 {money_text(two(fee), currency)}{accounts}',
            source=source, as_of=stamp, unit=currency, metric='trade_amount',
            figures={'quantity': exact(quantity), 'price': exact(price), 'fee': exact(fee)},
            ref={'ledger_event_id': event.get('id'), 'ticker': event.get('ticker'), 'account': event.get('account')}))
    if others:
        items = []
        for event in others[:inputs.MAX_ITEMS]:
            amount = event.get('amount')
            what = LEDGER_KIND_LABEL.get(event['kind'], event['kind'])
            subject = _event_name(event, names)
            items.append({'text': ' '.join(part for part in (what, subject, money_text(amount, _currency(event))
                                                              if amount is not None else '') if part),
                          'ref': {'ledger_event_id': event.get('id'), 'ticker': event.get('ticker')}})
        facts.append(briefs.fact('其他账本记录', len(others), '、'.join(item['text'] for item in items[:5]),
                                 source=source, as_of=stamp, unit='count', metric='other_event_count', items=items))
    return briefs.section('trades', title, facts,
                          note=f'账本（版本 {view.revision}）中日期为 {trading_date}、已确认且当日仍有效的记录'
                               '（日期更晚的记录和冲销不计入）；美股按纽约当地日期记账。')


def _plan_check(view, links, links_problem: Optional[str], trading_date: str, stamp: str,
                names: Dict[str, str]) -> dict:
    title = '计划核对'
    if view is None or links is None:
        problem = links_problem or '账本不可用'
        return briefs.section('plan_check', title, [briefs.fact(
            '计划核对', None, f'不可用：{problem}', source=inputs.SOURCE_PLANS, as_of=stamp, unit='count',
            status=briefs.unavailable_status(problem), metric='deviation_trade_count')],
            status=briefs.unavailable_status(problem))
    trades = view.on(trading_date, ('buy', 'sell'))
    by_id = {event.get('id'): event for event in trades}
    linked = {link['event_id']: link for link in links}
    deviated = [link for link in links if link['deviations']]
    followed = [link for link in links if not link['deviations'] and link.get('plan_id') is not None]
    planless = [link for link in links if not link['deviations'] and link.get('plan_id') is None]
    unchecked = [event for event in trades if event.get('id') not in linked]
    if not trades:
        return briefs.section('plan_check', title, [briefs.fact(
            '计划核对', 0, '今日没有成交，无需核对', source=inputs.SOURCE_PLANS, as_of=stamp, unit='count',
            metric='deviation_trade_count')])
    text = f'成交 {len(trades)} 笔：偏离计划 {len(deviated)} 笔，按计划 {len(followed)} 笔'
    if planless:
        text += f'，没有计划的卖出 {len(planless)} 笔'
    if unchecked:
        text += f'，未核对 {len(unchecked)} 笔'
    facts = [briefs.fact('偏离计划的成交', len(deviated), text, source=inputs.SOURCE_PLANS, as_of=stamp, unit='count',
                         metric='deviation_trade_count', level='warning' if deviated else None)]

    def describe(link: dict) -> str:
        event = by_id.get(link['event_id']) or link
        name = _event_name(event, names) or link['ticker']
        kind = LEDGER_KIND_LABEL.get(link['kind'], link['kind'])
        return f'{kind} {name} {exact(dec(link.get("quantity")))} 股 @ {grouped(link.get("price"))}'

    groups: Dict[str, List[dict]] = {}
    for link in deviated:
        for deviation in link['deviations']:
            groups.setdefault(deviation['code'], []).append({'link': link, 'deviation': deviation})
    for code, entries in groups.items():
        warning = any(entry['deviation'].get('level') == 'warning' for entry in entries)
        items = [{'text': f'{describe(entry["link"])}：{entry["deviation"]["message"]}',
                  'level': entry['deviation'].get('level'),
                  'ref': _ref(plan_id=entry['link'].get('plan_id'), ledger_event_id=entry['link']['event_id'],
                              ticker=entry['link']['ticker'])} for entry in entries]
        facts.append(briefs.fact(DEVIATION_LABELS.get(code, code), len(entries), entries[0]['deviation']['message'],
                                 source=inputs.SOURCE_PLANS, as_of=stamp, unit='count', metric='deviation_count',
                                 items=items, level='warning' if warning else 'info',
                                 ref={'plan_id': entries[0]['link'].get('plan_id'), 'ticker': entries[0]['link']['ticker']}))
    if followed:
        items = [{'text': f'{describe(link)}：{RELATION_LABELS.get(link["relation"], link["relation"])}，计划 #{link["plan_id"]}',
                  'ref': {'plan_id': link['plan_id'], 'ledger_event_id': link['event_id'], 'ticker': link['ticker']}}
                 for link in followed]
        facts.append(briefs.fact('按计划执行', len(followed), '、'.join(item['text'] for item in items[:3]),
                                 source=inputs.SOURCE_PLANS, as_of=stamp, unit='count', metric='followed_count',
                                 items=items))
    if planless:
        items = [{'text': describe(link), 'ref': {'ledger_event_id': link['event_id'], 'ticker': link['ticker']}}
                 for link in planless]
        facts.append(briefs.fact('没有计划的卖出', len(planless), '卖出时这只股票没有启用中的交易计划（不算偏离，也不算按计划）',
                                 source=inputs.SOURCE_PLANS, as_of=stamp, unit='count', metric='planless_count',
                                 items=items))
    if unchecked:
        items = [{'text': describe({'event_id': event.get('id'), 'kind': event['kind'], 'ticker': event['ticker'],
                                    'quantity': event.get('quantity'), 'price': event.get('price')}),
                  'ref': {'ledger_event_id': event.get('id'), 'ticker': event['ticker']}} for event in unchecked]
        facts.append(briefs.fact('未核对', len(unchecked), '确认这些成交时还没有任何交易计划，或计划检查没有运行',
                                 source=inputs.SOURCE_PLANS, as_of=stamp, unit='count', metric='unchecked_count',
                                 items=items))
    return briefs.section('plan_check', title, facts,
                          note='成交确认入账时与当时的交易计划对照的结果；偏离只是提醒，不影响账本。')


def session_words(quote: Optional[LiveQuote], market: Optional[str], clock) -> Tuple[str, bool]:
    """``(words, provisional)``: which session the price is from, true at generation time.

    ``provisional`` when the price is from a session still trading (not its close).
    """
    if quote is None or not quote.usable or market not in PLACE:
        return '', False
    place = PLACE[market]
    session = day_text(quote.session_date.isoformat()) if quote.session_date else ''
    when = local_clock(quote, market)
    local = '纽约时间 ' if market == US else ''
    if quote.status == CLOSED:
        if quote.close_pending:   # seen before the close settled: not the official close yet (estimated)
            return ' '.join(part for part in (place, session, '收盘价尚未最终确定') if part), False
        return ' '.join(part for part in (place, session, '收盘') if part), False
    if quote.status in (LIVE, DELAYED):
        end = clock.session_end(market, quote.session_date) if quote.session_date else None
        close = f'，{end.astimezone(inputs.ZONES[market]):%H:%M} 收盘' if end is not None else ''
        delayed = '延迟，' if quote.status == DELAYED else ''
        head = ' '.join(part for part in (place, session, '盘中价', f'{local}{when}'.strip()) if part)
        return f'{head}，{delayed}非收盘价{close}', True
    if quote.status == STALE:
        head = ' '.join(part for part in (f'{place}最后价格', session, f'{local}{when}'.strip()) if part)
        return f'{head}，已过期，非收盘价', False
    if quote.status == PREV_CLOSE_ONLY:
        return f'{place}昨收价，{session or "本交易日"} 尚无成交', False
    if quote.status == TIME_UNKNOWN:
        return f'{place}价格时间未知', False
    return '', False


def fx_of(rate: Optional[FxRate]) -> Optional[dict]:
    """The ``fx`` of a fact converted at ``rate``."""
    if rate is None:
        return None
    flagged = rate_estimate(rate)
    return {'currency': rate.currency, 'rate': plain(rate.rate), 'source': rate.source,
            'as_of': inputs.moment(rate.quote_time or rate.fetched_at), 'status': rate.status,
            'reason': flagged[1] if flagged else rate.reason, 'estimated': flagged is not None}


def _fx_words(fx: Optional[dict]) -> str:
    if not fx or fx.get('rate') is None:
        return ''
    when = ''
    try:
        when = datetime.fromisoformat(fx['as_of']).astimezone(SHANGHAI).strftime('%m-%d %H:%M')
    except (TypeError, ValueError):
        pass
    # The rate's source is in ``fx`` (and on the page's source line); the sentence says the rate and its time.
    return '，'.join(part for part in (f'汇率 {fx["rate"]}', when, '未经核实' if fx.get('estimated') else '') if part)


def _first_reason(entries, ticker: Optional[str] = None, day_only: bool = False) -> Optional[str]:
    for entry in entries or []:
        if ticker is not None and entry.get('ticker') != ticker:
            continue
        if day_only and entry.get('field') not in (None, 'day_pnl'):
            continue
        return entry.get('reason')
    return None


def _pnl(valuation, quotes, rates, problem: Optional[str], runtime, view, now: datetime, stamp: str) -> dict:
    title = '盈亏归因'
    clock = runtime.clock
    source = '+'.join(runtime.quotes.source_names) or None
    if valuation is None:
        reason = problem or '账本在该日还没有已确认的持仓'
        return briefs.section('pnl', title, [briefs.fact('今日盈亏', None, f'不可用：{reason}', source=None, as_of=stamp,
                                                        unit='CNY', status=briefs.unavailable_status(reason),
                                                        metric='day_pnl_total', signed=True)],
                              status=briefs.unavailable_status(reason) if problem else None)
    excludes = '、'.join(EXCLUDES.get(kind, kind) for kind in DAY_PNL_EXCLUDES)
    unavailable = list(valuation.unavailable)
    several = len(valuation.accounts) > 1
    rows: List[dict] = []
    provisional_markets: Dict[str, List[str]] = {}
    closes: Dict[str, set] = {}
    unsettled: Dict[str, set] = {}      # closes seen before they settled (New York, quotes.CLOSE_NOT_FINAL)
    for account in valuation.accounts:
        entries: List[Tuple[PositionValue, bool]] = ([(row, False) for row in account.positions]
                                                     + [(row, True) for row in account.closed_today])
        for row, closed in entries:
            quote = row.quote
            market = inputs.market_of(row.ticker)
            label = row.name + (f'（{account.name}）' if several else '') + ('（今日清仓）' if closed else '')
            value = two(row.day_pnl_cny)
            words, provisional = session_words(quote, market, clock)
            fx = fx_of(rates.get(row.currency)) if row.currency != 'CNY' else None
            if value is None:
                reason = _first_reason(unavailable, row.ticker, day_only=True)
                if reason is None and row.day_pnl is not None:
                    # Known in its own currency; the CNY rate is what is missing.
                    rate = next((entry.get('reason') for entry in unavailable
                                 if entry.get('currency') == row.currency), None) or '汇率不可用'
                    reason = f'缺少 {row.currency} 汇率（{rate}）'
                reason = reason or (quote.reason if quote is not None else None) or '当日盈亏不可用'
                status = briefs.unavailable_status(reason)
                native = ('' if row.day_pnl is None or row.currency == 'CNY'
                          else f'{money_text(two(row.day_pnl), row.currency, signed=True)}；人民币金额')
                text = f'{native}不可用：{reason}'
            else:
                reason = row.estimated_reason or (quote.reason if quote is not None else None)
                if provisional:
                    provisional_markets.setdefault(market, []).append(row.ticker)
                    reason = '；'.join(part for part in (reason, f'{words}，收盘后会变') if part)
                elif quote is not None and quote.status == CLOSED and quote.session_date is not None:
                    (unsettled if quote.close_pending else closes).setdefault(market, set()).add(
                        quote.session_date.isoformat())
                status = {'code': quote.status if quote is not None else 'unavailable', 'reason': reason,
                          'estimated': bool(row.estimated)}
                if provisional:
                    status['provisional'] = True
                native = '' if row.currency == 'CNY' else f'{money_text(two(row.day_pnl), row.currency, signed=True)} ≈ '
                detail = '；'.join(part for part in (words, _fx_words(fx)) if part)
                text = f'{native}{money_text(value, "CNY", signed=True)}' + (f'（{detail}）' if detail else '')
            rows.append(briefs.fact(label, value, text, source=quote.source if quote is not None else None,
                                    as_of=quote_time(quote) or stamp, unit='CNY', status=status,
                                    ref={'ticker': row.ticker, 'account': account.name}, metric='day_pnl_cny',
                                    signed=True,
                                    figures={'day_pnl': two(row.day_pnl), 'price': plain(row.price),
                                             'prev_close': plain(row.prev_close), 'quantity': exact(row.quantity),
                                             'fx_rate': fx['rate'] if fx else None},
                                    **({'fx': fx} if fx else {})))
    total = two(valuation.day_pnl_cny)
    if total is None:
        reason = _first_reason(unavailable) or '部分持仓的当日盈亏不可用'
        total_status = briefs.unavailable_status(reason)
        total_text = f'不可用：{reason}'
    else:
        estimated = valuation.quality == 'estimated' or any((row['status'] or {}).get('estimated') for row in rows)
        provisional = bool(provisional_markets)
        reasons = [entry.get('reason') or '' for entry in valuation.estimated]
        if provisional:
            reasons.append('含' + '、'.join(inputs.MARKET_NAMES[market] for market in provisional_markets)
                           + '盘中价（非收盘价）')
        total_status = None
        if estimated or provisional:
            total_status = {'code': 'estimated' if estimated else 'provisional',
                            'reason': '；'.join(dict.fromkeys(reason for reason in reasons if reason)) or None,
                            'estimated': estimated}
            if provisional:
                total_status['provisional'] = True
        pct = two(valuation.day_pnl_pct)
        total_text = money_text(total, 'CNY', signed=True) + (f'（{grouped(pct, signed=True)}%）' if pct is not None else '')
        if provisional:
            total_text += '，含盘中价，收盘后会变'
    pct = two(valuation.day_pnl_pct)
    facts = [briefs.fact('今日盈亏合计', total, total_text, source=source, as_of=stamp, unit='CNY',
                         status=total_status, metric='day_pnl_total', signed=True, figures={'day_pnl_pct': pct})] + rows
    notes = [f'与今日页同一算法：价格变动和交易费用，不含{excludes}、汇率变动和基金估值。']
    for market, tickers in provisional_markets.items():
        notes.append(f'{inputs.MARKET_NAMES[market]}在生成时（北京时间 {now.astimezone(SHANGHAI):%H:%M}）仍在交易，'
                     f'这些行是盘中价，不是收盘价，合计因此为估算。')
    for market, days in closes.items():
        if market != CN:
            notes.append(f'{inputs.MARKET_NAMES[market]}按{PLACE[market]} {"、".join(day_text(day) for day in sorted(days))} '
                         '的收盘价计算。')
    for market, days in unsettled.items():
        notes.append(f'{inputs.MARKET_NAMES[market]}的收盘价在生成时尚未最终确定（{PLACE[market]} '
                     f'{"、".join(day_text(day) for day in sorted(days))}，交易所在收盘后数分钟才发布官方收盘价），'
                     '这些行按当时看到的价格计算，为估算。')
    if valuation.quality == 'estimated':
        notes.append('合计含未经核实的行情或汇率，为估算。')
    if any(row.get('fx') for row in rows):
        notes.append('换算成人民币用的汇率写在每一行。')
    return briefs.section('pnl', title, facts, note=''.join(notes))


def _alerts_today(found, problem: Optional[str], names: Dict[str, str], stamp: str) -> dict:
    title = '今日触发的提醒'
    if found is None:
        return briefs.section('alerts', title, [briefs.fact('今日触发', None, f'不可用：{problem}', source=inputs.SOURCE_ALERTS,
                                                           as_of=stamp, unit='count', metric='alert_count',
                                                           status=briefs.unavailable_status(problem))],
                              status=briefs.unavailable_status(problem))
    total, unread, events = found['total'], found['unread'], found['events']
    read_text = '通知已读状态未知' if unread is None else (f'其中 {unread} 条通知未读' if unread else '通知都已读')
    facts = [briefs.fact('今日触发', total, f'{total} 条；{read_text}' if total else '今日没有触发提醒',
                         source=inputs.SOURCE_ALERTS, as_of=stamp, unit='count', metric='alert_count')]
    facts += [alert_fact(event, names) for event in events]
    note = '最新的在前。看过通知不等于处理了提醒。'
    if total > len(events):
        note += f'共 {total} 条，只列出最新的 {len(events)} 条。'
    return briefs.section('alerts', title, facts, note=note)


def _unverified_words(found: Sequence[LiveQuote]) -> str:
    """What is wrong with the prices of ``found`` (each one not verified, ``quote_estimate``)."""
    if all(quote.status == STALE for quote in found):
        return '价格已过期'
    if all(quote.close_pending for quote in found):
        return '收盘价尚未最终确定'
    return '价格未经核实'


def _levels_at_close(view, subjects, levels, problem: Optional[str], quotes, stamp: str) -> dict:
    title = '越过止损或目标的持仓'
    if view is None or levels is None:
        return briefs.section('levels', title, [briefs.fact('越过止损或目标', None, f'不可用：{problem}',
                                                           source=inputs.SOURCE_PLANS, as_of=stamp, unit='count',
                                                           status=briefs.unavailable_status(problem),
                                                           metric='levels_reached_count')],
                              status=briefs.unavailable_status(problem))
    reached, _ = level_facts(subjects, levels, quotes, only_reached=True)
    missing = [(ticker, name) for ticker, name, account in subjects
               if inputs.levels_for(levels, ticker, account) and not (quotes.get(ticker) and quotes[ticker].usable)]
    if missing:
        # One holding with a stop or target that could not be checked makes the count unknown, never zero.
        names = '、'.join(ticker if name == ticker else f'{name}（{ticker}）' for ticker, name in missing)
        reason = f'{names} 没有可用行情，无法判断'
        known = f'；其余持仓中已越过 {len(reached)} 项' if reached else ''
        count = briefs.fact('越过止损或目标', None, f'不可用：{reason}{known}', source=inputs.SOURCE_PLANS, as_of=stamp,
                            unit='count', status=briefs.unavailable_status(reason), metric='levels_reached_count',
                            level='warning')
    else:
        # A count that rests on a price that is not verified (stale, a close not yet final) is an estimate,
        # a zero as much as any other: it names the holdings it could not check.
        unverified = list(dict.fromkeys((ticker, name) for ticker, name, account in subjects
                                        if inputs.levels_for(levels, ticker, account)
                                        and quote_estimate(quotes[ticker]) is not None))
        status, text = None, (f'{len(reached)} 项' if reached else '没有持仓越过止损或目标')
        if unverified:
            words = _unverified_words([quotes[ticker] for ticker, _ in unverified])
            reason = f'{len(unverified)} 只持仓{words}，未能核对'
            names = '、'.join(ticker if name == ticker else f'{name}（{ticker}）' for ticker, name in unverified)
            status = {'code': 'estimated', 'reason': reason, 'estimated': True}
            text = f'{len(reached)} 项（{reason}：{names}）'
        count = briefs.fact('越过止损或目标', len(reached), text, source=inputs.SOURCE_PLANS,
                            as_of=stamp, unit='count', metric='levels_reached_count', status=status,
                            level='warning' if reached else None)
    return briefs.section('levels', title, [count] + reached,
                          note='启用中的止损/目标提醒和交易计划，按上市品种和适用账户对应到持仓，按最新价判断。')


def _lessons(pending, problem: Optional[str], stamp: str) -> dict:
    title = '待确认的教训'
    if pending is None:
        return briefs.section('lessons', title, [briefs.fact('待确认教训', None, f'不可用：{problem}',
                                                            source=inputs.SOURCE_PROPOSALS, as_of=stamp, unit='count',
                                                            status=briefs.unavailable_status(problem),
                                                            metric='lesson_count')],
                              status=briefs.unavailable_status(problem))
    lessons = [item for item in pending if item['kind'] == 'lesson']
    return briefs.section('lessons', title, [briefs.fact(
        '待确认教训', len(lessons), f'{len(lessons)} 条等你核对' if lessons else '没有',
        source=inputs.SOURCE_PROPOSALS, as_of=stamp, unit='count', metric='lesson_count',
        ref={'proposal_ids': [item['id'] for item in lessons][:inputs.MAX_ITEMS]})])


def _snapshot_notice(trading_date: str) -> dict:
    try:
        run = scheduler.run_for(SNAPSHOT_KIND, trading_date)
    except Exception as exc:
        logger.warning('postmarket brief: the close snapshot run could not be read: %s', type(exc).__name__)
        return {'kind': 'close_snapshot', 'text': '收盘快照：状态暂时无法读取'}
    if run is None:
        return {'kind': 'close_snapshot', 'text': '收盘快照：今天没有运行记录'}
    text = f'收盘快照：{run["status_text"]}'
    if run.get('summary'):
        text += f'（{short(run["summary"], 120)}）'
    return {'kind': 'close_snapshot', 'text': text, 'status': run['status']}


def _alerts_of(trading_date: str) -> dict:
    total, unread = inputs.alert_count(trading_date, trading_date)
    return {'total': total, 'unread': unread, 'events': inputs.alert_events(trading_date, trading_date, inputs.MAX_ITEMS)}


# -- the brief --------------------------------------------------------------------

def _build(trading_date: str, runtime, ledger) -> tuple:
    clock = runtime.clock
    now = clock.now()
    stamp = briefs.stamp(now)
    view, ledger_problem = premarket.read_ledger_view(ledger, trading_date)
    names = dict(view.names) if view else {}
    links, links_problem = (None, ledger_problem) if view is None else _guarded(lambda: _links(view, trading_date),
                                                                                 '交易计划核对')
    held_tickers = list(dict.fromkeys(ticker for _, ticker, _ in view.held())) if view else []
    traded = [event['ticker'] for event in view.on(trading_date) if event.get('ticker')] if view else []
    levels, levels_problem = _guarded(lambda: inputs.active_levels(held_tickers), '止损和目标')
    valuation, quotes, rates, value_problem = None, {}, {}, ledger_problem
    if view is not None and view.state is not None:
        try:
            valuation, quotes, rates = _value(view, runtime, traded)
        except Exception as exc:
            logger.warning('postmarket brief: valuation failed: %s', type(exc).__name__)
            value_problem = '今日估值暂时无法计算'
    for ticker, quote in quotes.items():
        if quote.name:
            names.setdefault(ticker, quote.name)
    held = view.held(names) if view else []
    subjects = premarket.held_subjects(view, held)
    alerts, alerts_problem = _guarded(lambda: _alerts_of(trading_date), '提醒记录')
    pending, pending_problem = _guarded(lambda: inputs.pending_proposals(now), '待确认方案')
    sections = [
        _trades(view, ledger_problem, trading_date, stamp, names),
        _plan_check(view, links, links_problem or ledger_problem, trading_date, stamp, names),
        _pnl(valuation, quotes, rates, value_problem, runtime, view, now, stamp),
        _alerts_today(alerts, alerts_problem, names, stamp),
        _levels_at_close(view, subjects, levels, levels_problem or ledger_problem, quotes, stamp),
        _lessons(pending, pending_problem, stamp),
    ]
    notices = [_snapshot_notice(trading_date)]
    calendar = briefs.calendar_notice(clock, trading_date)
    if calendar is not None:
        notices.insert(0, calendar)
    notices += premarket.ledger_notices(view, ledger_problem, trading_date)
    if view is not None and us_trades_recently(view.events, clock):
        notices.append({'kind': 'exchange_local_dates', 'text': US_DATES_HINT})
    # A total outage is judged over every position the brief needs a price for, sold-out ones included.
    needed = [quotes.get(row.ticker) for account in (valuation.accounts if valuation else ())
              for row in (*account.positions, *account.closed_today)]
    return briefs.assemble(KIND, trading_date, now, sections, notices), premarket.blockers_of(ledger_problem, needed)


async def build(trading_date: str, *, runtime, ledger=Ellipsis, news_provider=None):
    """``(brief, blockers)`` for ``trading_date`` from the data as it is now (``runtime.clock``).

    ``ledger``: ``...`` reads the portfolio's ledger file, ``None`` means none
    (see ``brief_inputs.read_snapshot``). It is read once, as of ``trading_date``.
    """
    return await asyncio.to_thread(_build, trading_date, runtime, ledger)


scheduler.register(KIND, due, run, title=TITLE, retry_until=briefs.retry_until,
                   refuse=lambda: briefs.manual_refusal(KIND))
