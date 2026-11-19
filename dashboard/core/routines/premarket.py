"""Routine ``premarket``: the pre-market brief, due 08:45 Asia/Shanghai on A-share trading days.

Sections and where each number comes from (unknown values are null with the
reason; see ``core.routines.briefs`` for the fact format):

overnight   US (纳斯达克综合、标普500、道琼斯), Hong Kong (恒生、恒生科技) and
            A-share (上证、深证成指、创业板指、沪深300) index quotes from the market
            runtime (``IDX:`` tickers, the same ``QuoteService`` as ``GET /api/quotes``):
            close, previous close and change, each with its source, exchange time,
            session date and status. A market whose latest quote is older than
            its latest weekday session says so.
levels      every holding (ledger, as of the trading day, per account) and watch item:
            distance from the latest price (the last close before the open; at a
            catch-up run after a market opened, its intraday price, and the note
            says which) to each active stop / target: active alert rules of kind
            stop_loss / target_price and active plans' stop_price / target_price,
            one row per price. A level
            is matched to a holding by listing (``HKG:700`` = ``HKG:00700``) and by
            the account it covers (none: every account).
reviews     plans (draft or active) and research-journal entries whose review date
            is due: on or before the trading day, at most 30 days back.
news        announcements and news headlines of A-share holdings since the
            previous close, from the akshare provider behind the agent's news
            tools (isolated calls with timeouts). When the provider cannot be
            reached, or is not queried (scripted quotes, demo), the section says
            数据源不可用 and why; never an empty list as if nothing happened.
            Headlines are third-party text and stay inside ``untrusted_text``.
alerts      最近触发的提醒: every alert fired on the previous trading day and since
            (newest first; all are counted, the newest are listed), and every stop,
            target and weight rule still in its fired state (the monitor has not
            re-armed it), each saying whether its notification has been read.
            Reading a notification is not handling the alert.
pending     proposals waiting for confirmation (generic proposals and ledger
            previews made against the current ledger revision, the first opening
            balance at revision zero included, a manual preview only within its
            two-hour lifetime as the ledger lists it), all of them counted.

The ledger is read once (``brief_inputs.read_snapshot``) and replayed as of the
trading day; when it cannot be read the brief still comes, with the ledger's
facts 不可用 and the run failed so that it is retried.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from core import watchlist
from core.agent_tools._envelope import untrusted, valid_date
from core.provider_errors import log_failure
from core.alerts.engine import KIND_LABELS
from core.market.calendar import CN, FIRST_PRICE, HK, SHANGHAI, US, ZONES
from core.market.instruments import InstrumentError, parse
from core.market.quotes import DELAYED, LIVE, LiveQuote
from core.market.search import INDEX_NAMES
from core.market.today import quote_object
from core.routines import briefs, scheduler
from core.routines import brief_inputs as inputs
from core.routines.brief_inputs import (InputUnavailable, day_text, grouped, pct_text, plain, price_phrase,
                                        quote_status, quote_time, short, two)

logger = logging.getLogger(__name__)

KIND = briefs.PREMARKET
TITLE = briefs.TITLES[KIND]
OVERNIGHT = ((US, ('IDX:IXIC', 'IDX:SPX', 'IDX:DJIA')),
             (HK, ('IDX:HSI', 'IDX:HSTECH')),
             (CN, ('IDX:000001', 'IDX:399001', 'IDX:399006', 'IDX:000300')))
REVIEW_WINDOW_DAYS = 30
NEWS_LIMIT = 10
NEWS_SHOWN = 5
NEWS_TICKERS = 20
NEWS_BUDGET_SECONDS = 40.0
FAKE_NEWS_REASON = '模拟行情模式（TRACKER_QUOTE_SOURCE=fake）不联网查询公告与新闻'


def due(clock):
    return briefs.due_at(KIND, clock)


async def run(trading_date: str):
    """The scheduler's entry: the brief is stored by the outcome's ``commit`` while the run owns its claim."""
    return await briefs.produce(KIND, trading_date, build, store=False)


def _guarded(reader, what: str):
    """``(value, problem)``: a reader's result, or the (fixed) reason it could not be read."""
    try:
        return reader(), None
    except InputUnavailable as exc:
        return None, str(exc)
    except Exception as exc:
        logger.warning('premarket brief: %s could not be read: %s', what, type(exc).__name__)
        return None, f'{what}暂时无法读取'


# -- collecting (blocking; runs in a worker thread) ------------------------------

def read_ledger_view(ledger, trading_date: str):
    """``(view, problem)``: the ledger read once and replayed as of ``trading_date``, or why it could not be."""
    snapshot, problem = _guarded(lambda: inputs.read_snapshot(ledger), '账本')
    if problem is not None:
        return None, problem
    return _guarded(lambda: inputs.ledger_view(snapshot, trading_date), '账本')


def _collect(trading_date: str, runtime, ledger, now: datetime) -> dict:
    view, ledger_problem = read_ledger_view(ledger, trading_date)
    watched, watch_problem = _guarded(watchlist.list_items, '自选')
    names: Dict[str, str] = dict(view.names) if view else {}
    for item in watched or []:
        if item.get('name'):
            names.setdefault(item['ticker'], item['name'])
    held_tickers = list(dict.fromkeys(ticker for _, ticker, _ in view.held())) if view else []
    held_listings = {inputs.listing(ticker) for ticker in held_tickers}
    watched_only = [item['ticker'] for item in watched or [] if inputs.listing(item['ticker']) not in held_listings]
    subjects = list(dict.fromkeys(held_tickers + watched_only))
    levels, levels_problem = _guarded(lambda: inputs.active_levels(subjects), '止损和目标')
    index_tickers = [ticker for _, group in OVERNIGHT for ticker in group]
    tickers = list(dict.fromkeys(index_tickers + subjects))
    quotes: Dict[str, LiveQuote] = runtime.quotes.get(tickers) if tickers else {}
    for ticker, quote in quotes.items():
        if quote.name:
            names.setdefault(ticker, quote.name)
    held = view.held(names) if view else []
    plans_due, plans_problem = _guarded(lambda: _plans_due(trading_date), '交易计划')
    journal_due, journal_problem = _guarded(lambda: _journal_due(trading_date), '研究笔记')
    first = runtime.clock.previous_trading_day(CN, inputs.as_date(trading_date)).isoformat()
    alerts, alerts_problem = _guarded(lambda: recent_alerts(first, trading_date), '提醒记录')
    pending, pending_problem = _guarded(lambda: inputs.pending_proposals(now), '待确认方案')
    ledger_pending, ledger_pending_problem = None, ledger_problem
    if ledger_problem is None:
        ledger_pending, ledger_pending_problem = _guarded(lambda: inputs.pending_ledger_previews(view, now),
                                                          '账本待确认记录')
        if ledger_pending is None and ledger_pending_problem is None:
            ledger_pending_problem = '账本的待确认记录暂时无法读取'
    return {'view': view, 'ledger_problem': ledger_problem, 'watched': watched, 'watch_problem': watch_problem,
            'watched_only': watched_only, 'held': held, 'names': names, 'levels': levels,
            'levels_problem': levels_problem, 'quotes': quotes, 'plans_due': plans_due, 'plans_problem': plans_problem,
            'journal_due': journal_due, 'journal_problem': journal_problem, 'alerts': alerts,
            'alerts_first': first, 'alerts_problem': alerts_problem, 'pending': pending,
            'pending_problem': pending_problem, 'ledger_pending': ledger_pending,
            'ledger_pending_problem': ledger_pending_problem}


def recent_alerts(first: str, last: str) -> dict:
    """What the alert section needs: every alert fired in ``[first, last]`` counted, the newest listed, and the
    rules still in their fired state."""
    total, unread = inputs.alert_count(first, last)
    return {'total': total, 'unread': unread, 'events': inputs.alert_events(first, last, inputs.MAX_ITEMS),
            'rules': inputs.fired_rules()}


def _window(trading_date: str) -> Tuple[str, str]:
    return (inputs.as_date(trading_date) - timedelta(days=REVIEW_WINDOW_DAYS)).isoformat(), trading_date


def _plans_due(trading_date: str) -> List[dict]:
    from core import plans
    first, last = _window(trading_date)
    return [plan for plan in plans.list_plans() if plan['status'] in (plans.DRAFT, plans.ACTIVE)
            and plan.get('review_on') and first <= plan['review_on'] <= last]


def _journal_due(trading_date: str) -> List[dict]:
    first, last = _window(trading_date)
    return inputs.journal_entries(match=lambda entry: isinstance(entry.get('review_on'), str)
                                  and first <= entry['review_on'] <= last)


# -- sections ---------------------------------------------------------------------

def closed_weekday(clock, market: str):
    """The latest weekday whose session would be the current one, when the calendar says it was a holiday."""
    local = clock.now().astimezone(ZONES[market])
    day = local.date() if local.time() >= FIRST_PRICE[market] else local.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    if not clock.is_trading_day(market, day) and clock.confidence(market, day) == 'calendar':
        return day
    return None


def _overnight(quotes: Dict[str, LiveQuote], clock, now: datetime) -> dict:
    facts, notes = [], []
    for market, tickers in OVERNIGHT:
        expected = clock.session_of(market)
        holiday = closed_weekday(clock, market)
        if holiday is not None:
            notes.append(f'{inputs.MARKET_NAMES[market]} {holiday.isoformat()[5:]} 休市')
        sessions = []
        for ticker in tickers:
            quote = quotes.get(ticker)
            code = ticker.split(':', 1)[1]
            label = INDEX_NAMES.get(code, ticker)
            item = quote_object(ticker, quote)
            close, change = item['price'], item['change_pct']
            status = quote_status(quote)
            if close is None:
                reason = (quote.reason if quote is not None else None) or '没有行情'
                facts.append(briefs.fact(label, None, f'不可用：{reason}', source=quote.source if quote else None,
                                         as_of=quote_time(quote), unit='pct', status=status, ref={'ticker': ticker},
                                         metric='index_change_pct'))
                continue
            phrase = price_phrase(quote, market)
            text = f'{grouped(close)} 点 {pct_text(change)}（{phrase}）' if change is not None else \
                f'{grouped(close)} 点（{phrase}；缺少前收盘价，涨跌幅不可用）'
            if quote.session_date is not None:
                sessions.append(quote.session_date)
                if quote.session_date < expected:
                    text += f'；{expected.isoformat()[5:]} 没有新的收盘价（可能休市或数据未更新）'
            facts.append(briefs.fact(label, change, text, source=quote.source, as_of=quote_time(quote), unit='pct',
                                     status=status, ref={'ticker': ticker}, metric='index_change_pct', signed=True,
                                     figures={'close': close, 'prev_close': item['prev_close']}))
        group = facts[-len(tickers):]
        reasons = {item['text'] for item in group if item['value'] is None}
        if len(reasons) == 1 and all(item['value'] is None and not item.get('figures') for item in group):
            # One outage for the whole market: one row that says so, not one per index.
            del facts[-len(tickers):]
            names = '、'.join(item['label'] for item in group)
            facts.append(briefs.fact(f'{inputs.MARKET_NAMES[market]}指数', None, f'{reasons.pop()}（{names}）',
                                     source=group[0]['source'], as_of=group[0]['as_of'], unit='pct',
                                     status=group[0]['status'], metric='index_change_pct'))
        if sessions:
            latest = max(sessions)
            place = {US: '纽约', HK: '香港', CN: ''}[market]
            if latest < expected:
                notes.append(f'{inputs.MARKET_NAMES[market]}最近一个交易时段是{place} {latest.isoformat()[5:]}，'
                             f'{expected.isoformat()[5:]} 没有新数据（可能休市）')
            else:
                notes.append(f'{inputs.MARKET_NAMES[market]}：{place}{latest.isoformat()[5:]} 交易时段')
    unusable = all(item['value'] is None for item in facts)
    return briefs.section('overnight', '隔夜与上一交易日行情', facts, note='；'.join(notes) or None,
                          status=briefs.unavailable_status('指数行情全部不可用') if unusable else None)


Subject = Tuple[str, str, Optional[str]]   # (ticker, name as shown, account or None for a watched listing)


def level_facts(subjects: Sequence[Subject], levels: Dict[str, List[inputs.Level]],
                quotes: Dict[str, LiveQuote], *, only_reached: bool = False) -> Tuple[List[dict], List[Subject]]:
    """Distance facts for ``(ticker, name, account)`` subjects; also returns the subjects without any level.

    A subject's levels are those of its listing that cover its account (``brief_inputs.levels_for``).
    """
    facts, bare = [], []
    for ticker, name, account in subjects:
        found = inputs.levels_for(levels, ticker, account)
        if not found:
            bare.append((ticker, name, account))
            continue
        quote = quotes.get(ticker)
        market = inputs.market_of(ticker)
        for level in found:
            label = f'{name} {level.label} {grouped(plain(level.threshold))}'
            ref = {'ticker': ticker, 'account': account or level.account,
                   'plan_id': level.plan_ids[0] if level.plan_ids else None,
                   'alert_rule_id': level.rule_ids[0] if level.rule_ids else None}
            origin = f'（{level.origin()}）' if level.origin() else ''
            if quote is None or not quote.usable:
                if only_reached:
                    continue
                reason = (quote.reason if quote is not None else None) or '没有行情'
                facts.append(briefs.fact(label, None, f'不可用：{reason}', source=quote.source if quote else None,
                                         as_of=quote_time(quote), unit='pct', status=quote_status(quote), ref=ref,
                                         metric='distance_pct'))
                continue
            gap, pct, reached = inputs.distance(level, quote.price)
            if only_reached and not reached:
                continue
            phrase, price = price_phrase(quote, market), grouped(plain(quote.price))
            if not reached:
                text = f'{phrase} {price}，距{level.label}还有 {two(pct)}%{origin}'
            elif level.kind == 'stop':
                text = f'{phrase} {price}，已低于止损（{two(pct)}%）{origin}'
            else:
                text = f'{phrase} {price}，已达到或高于目标（{two(pct)}%）{origin}'
            facts.append(briefs.fact(label, two(pct), text, source=quote.source, as_of=quote_time(quote), unit='pct',
                                     status=quote_status(quote), ref=ref, metric='distance_pct',
                                     figures={'price': plain(quote.price), 'threshold': plain(level.threshold),
                                              'gap': plain(gap)},
                                     level='warning' if reached else None))
    return facts, bare


def held_subjects(view, held) -> List[Subject]:
    """``(ticker, name, account)`` per holding; the name names the account when the ledger has several."""
    several = view is not None and len(view.accounts()) > 1
    return list(dict.fromkeys((ticker, f'{name}（{account}）' if several else name, account)
                              for account, ticker, name in held))


def _levels(data: dict, now: datetime) -> dict:
    stamp = briefs.stamp(now)
    held = held_subjects(data['view'], data['held'])   # names resolved in _collect
    watched = [(ticker, data['names'].get(ticker, ticker), None) for ticker in data['watched_only']]
    problems = [problem for problem in (data['ledger_problem'], data['watch_problem'], data['levels_problem']) if problem]
    if data['levels'] is None:
        return briefs.section('levels', '止损与目标距离', [briefs.fact(
            '止损与目标', None, f'不可用：{data["levels_problem"]}', source=inputs.SOURCE_PLANS, as_of=stamp,
            unit='pct', status=briefs.unavailable_status(data['levels_problem']), metric='distance_pct')],
            status=briefs.unavailable_status(data['levels_problem']))
    facts, bare = level_facts(held + watched, data['levels'], data['quotes'])
    if bare:
        facts.append(briefs.fact('未设止损或目标', len(bare), '、'.join(name for _, name, _ in bare[:inputs.MAX_ITEMS]),
                                 source=inputs.SOURCE_PLANS, as_of=stamp, unit='count', metric='without_levels_count',
                                 items=[{'text': f'{name}（{ticker}）', 'ref': {'ticker': ticker, 'account': account}}
                                        for ticker, name, account in bare[:inputs.MAX_ITEMS]]))
    if not held and not watched and not problems:
        facts.append(briefs.fact('持仓与自选', 0, '没有持仓，也没有自选', source=inputs.SOURCE_LEDGER, as_of=stamp,
                                 unit='count', metric='subject_count'))
    for problem in problems:
        if problem != data['levels_problem']:
            facts.append(briefs.fact('数据缺失', None, f'不可用：{problem}', source=None, as_of=stamp, unit='count',
                                     status=briefs.unavailable_status(problem), metric='subject_count'))
    # What the rows' prices are: a catch-up run after an opening has intraday prices for that market.
    codes = [(item['status'] or {}).get('code') for item in facts
             if item['metric'] == 'distance_pct' and item['value'] is not None]
    trading = sum(1 for code in codes if code in (LIVE, DELAYED))
    if not trading:
        prices = '盘前的最新价是上一交易时段的收盘价。'
    elif trading == len(codes):
        prices = '生成时市场已开盘，最新价是盘中价，不是收盘价。'
    else:
        prices = '生成时部分市场已开盘：这些品种的最新价是盘中价，其余是上一交易时段的收盘价；每行写明价格的性质。'
    note = ('来源：启用中的止损/目标提醒和启用中的交易计划，按上市品种（不论代码写法）和它们适用的账户对应到持仓，'
            '同一账户同一价格只列一次。百分比 = 价格到止损或目标的距离 ÷ 最新价；负数表示已越过。' + prices)
    return briefs.section('levels', '止损与目标距离', facts, note=note)


def _reviews(data: dict, trading_date: str, now: datetime) -> dict:
    stamp = briefs.stamp(now)
    facts, problems = [], [problem for problem in (data['plans_problem'], data['journal_problem']) if problem]
    plans_due, journal_due = data['plans_due'] or [], data['journal_due'] or []
    total = len(plans_due) + len(journal_due)
    if problems:
        facts.append(briefs.fact('到期复查', None, '不可用：' + '；'.join(problems), source=None, as_of=stamp,
                                 unit='count', status=briefs.unavailable_status('；'.join(problems)),
                                 metric='review_due_count'))
    else:
        facts.append(briefs.fact('到期复查', total, f'{total} 项' if total else '没有到期的复查', source='交易计划、研究笔记',
                                 as_of=stamp, unit='count', metric='review_due_count'))

    def overdue(day: str) -> str:
        days = (inputs.as_date(trading_date) - inputs.as_date(day)).days
        return '今天到期' if days == 0 else f'已过 {days} 天'

    names = data['names']
    for plan in plans_due:
        name = names.get(plan['ticker'], plan['ticker'])
        facts.append(briefs.fact(f'计划 #{plan["id"]} {name}', plan['review_on'],
                                 f'复查日 {plan["review_on"]}（{overdue(plan["review_on"])}，{plan["status_label"]}）：'
                                 f'{short(plan["thesis"])}', source=inputs.SOURCE_PLANS, as_of=stamp, unit='date',
                                 ref={'plan_id': plan['id'], 'ticker': plan['ticker']}, metric='review_due'))
    for entry in journal_due:
        ticker = entry.get('ticker') or ''
        facts.append(briefs.fact(f'研究笔记 {names.get(ticker, ticker)}', entry['review_on'],
                                 f'复查日 {entry["review_on"]}（{overdue(entry["review_on"])}）：{short(entry.get("thesis"))}',
                                 source=inputs.SOURCE_JOURNAL, as_of=stamp, unit='date',
                                 ref={'journal_id': entry['id'], 'ticker': ticker or None}, metric='review_due'))
    return briefs.section('reviews', '到期复查', facts,
                          note=f'启用中或草稿状态的交易计划和研究笔记，复查日在最近 {REVIEW_WINDOW_DAYS} 天内（含今天）。')


def _alerts(data: dict, now: datetime, names: Dict[str, str]) -> dict:
    stamp = briefs.stamp(now)
    title = '最近触发的提醒'
    first = data['alerts_first']
    if data['alerts'] is None:
        problem = data['alerts_problem']
        return briefs.section('alerts', title, [briefs.fact(
            '最近触发的提醒', None, f'不可用：{problem}', source=inputs.SOURCE_ALERTS, as_of=stamp, unit='count',
            status=briefs.unavailable_status(problem), metric='recent_alert_count')],
            status=briefs.unavailable_status(problem))
    found = data['alerts']
    fired_ids = {rule['id'] for rule in found['rules']}
    # Rules still fired whose latest alert is older than the window: listed once more, after the window's.
    older = [rule for rule in found['rules']
             if rule['event'] is None or not first <= rule['event']['trading_date'] <= data['trading_date']]
    count = found['total'] + len(older)
    unread = found['unread']
    for rule in older:
        read = rule['event']['read'] if rule['event'] is not None else False
        unread = None if unread is None or read is None else unread + (0 if read else 1)
    parts = [f'上一交易日（{day_text(first)}）以来触发 {found["total"]} 条']
    if older:
        parts.append(f'更早触发、仍处于触发状态 {len(older)} 条')
    read_text = '通知已读状态未知' if unread is None else (f'其中 {unread} 条通知未读' if unread else '通知都已读')
    text = (f'{count} 条：' + '，'.join(parts) + f'；{read_text}') if count else '上一交易日以来没有触发提醒，也没有仍处于触发状态的提醒'
    facts = [briefs.fact('最近触发的提醒', count, text, source=inputs.SOURCE_ALERTS, as_of=stamp, unit='count',
                         metric='recent_alert_count', level='warning' if count else None)]
    facts += [alert_fact(event, names, still=event['rule_id'] in fired_ids) for event in found['events']]
    for rule in older:
        facts.append(alert_fact(rule['event'], names, still=True) if rule['event'] is not None
                     else fired_rule_fact(rule, names))
    note = (f'上一交易日（{first}）以来触发的提醒（最新的在前，最多列 {inputs.MAX_ITEMS} 条），以及更早触发、'
            '规则仍处于触发状态（价格或仓位尚未回到止损、目标或上限之内）的提醒。看过通知不等于处理了提醒：每条都标明通知是否已读。')
    if found['total'] > len(found['events']):
        note += f'共 {found["total"]} 条，只列出最新的 {len(found["events"])} 条。'
    return briefs.section('alerts', title, facts, note=note)


READ_TEXT = {True: '通知已读', False: '通知未读', None: '通知已读状态未知'}


def alert_fact(event: dict, names: Dict[str, str], *, still: bool = False) -> dict:
    """One fired alert, as the alert log recorded it (price, threshold and quote at the moment it fired),
    with whether its notification was read and whether its rule is still in its fired state."""
    name = event.get('name') or names.get(event['ticker'], event['ticker'])
    kind = KIND_LABELS.get(event['kind'], event['kind'])
    fired = event['fired_at']
    when = ''
    try:
        when = datetime.fromisoformat(fired).astimezone(SHANGHAI).strftime('%m-%d %H:%M')
    except (TypeError, ValueError):
        pass
    status = {'code': event.get('quote_status') or 'unavailable', 'reason': None,
              'estimated': event.get('quote_status') not in ('live', 'delayed', 'closed')}
    tail = '；'.join(part for part in (READ_TEXT.get(event.get('read')) if 'read' in event else None,
                                       '仍处于触发状态' if still else None) if part)
    body = f'{when} {event.get("body") or event.get("title") or ""}'.strip()
    return briefs.fact(f'{name} {kind}', event.get('value'), f'{body}（{tail}）' if tail else body,
                       source=event.get('quote_source') or inputs.SOURCE_ALERTS,
                       as_of=event.get('quote_time') or fired, unit=inputs.alert_value_unit(event), status=status,
                       ref={'alert_event_id': event['id'], 'alert_rule_id': event['rule_id'], 'ticker': event['ticker'],
                            'account': event.get('account')},
                       metric='alert_value', figures={'threshold': event.get('threshold'), 'price': event.get('price')},
                       level='warning')


def fired_rule_fact(rule: dict, names: Dict[str, str]) -> dict:
    """A rule still in its fired state whose alert the log no longer has: its threshold, and since when."""
    name = names.get(rule['ticker'], rule['ticker'])
    since = ''
    try:
        since = datetime.fromisoformat(rule['state']['last_fired_at']).astimezone(SHANGHAI).strftime('%m-%d %H:%M')
    except (TypeError, ValueError, KeyError):
        pass
    unit = 'pct' if rule['kind'] == 'position_weight_max' else inputs.currency_of(rule['ticker'])
    return briefs.fact(f'{name} {KIND_LABELS.get(rule["kind"], rule["kind"])}', rule['threshold'],
                       f'仍处于触发状态（{since + " " if since else ""}触发；提醒记录中已没有这条触发记录）',
                       source=inputs.SOURCE_ALERTS, as_of=rule['state'].get('last_fired_at'), unit=unit,
                       ref={'alert_rule_id': rule['id'], 'ticker': rule['ticker'], 'account': rule.get('account')},
                       metric='alert_threshold', level='warning')


def pending_section(data: dict, now: datetime, lessons_only: bool = False) -> dict:
    stamp = briefs.stamp(now)
    facts = []
    if data['pending'] is None:
        problem = data['pending_problem']
        facts.append(briefs.fact('待确认方案', None, f'不可用：{problem}', source=inputs.SOURCE_PROPOSALS, as_of=stamp,
                                 unit='count', status=briefs.unavailable_status(problem), metric='pending_count'))
    else:
        by_kind: Dict[str, List[dict]] = {}
        order = list(inputs.PROPOSAL_LABELS)
        for item in sorted(data['pending'], key=lambda item: (order.index(item['kind']) if item['kind'] in order
                                                              else len(order), item['kind'])):
            by_kind.setdefault(item['kind'], []).append(item)
        parts = '、'.join(f'{inputs.PROPOSAL_LABELS.get(kind, kind)} {len(items)}' for kind, items in by_kind.items())
        total = len(data['pending'])
        facts.append(briefs.fact('待确认方案', total, f'{total} 项（{parts}）' if total else '没有待确认的方案',
                                 source=inputs.SOURCE_PROPOSALS, as_of=stamp, unit='count', metric='pending_count',
                                 ref={'proposal_ids': [item['id'] for item in data['pending']][:inputs.MAX_ITEMS]}))
    if data['ledger_pending'] is None:
        problem = data['ledger_pending_problem'] or data['ledger_problem'] or inputs.LEDGER_UNREADABLE
        facts.append(briefs.fact('待确认账本记录', None, f'不可用：{problem}', source=inputs.SOURCE_LEDGER, as_of=stamp,
                                 unit='count', status=briefs.unavailable_status(problem), metric='ledger_pending_count'))
    else:
        count = data['ledger_pending']
        facts.append(briefs.fact('待确认账本记录', count, f'{count} 个预览尚未确认入账' if count else '没有',
                                 source=inputs.SOURCE_LEDGER, as_of=stamp, unit='count', metric='ledger_pending_count'))
    return briefs.section('pending', '待确认', facts, note='只算生成时已经存在、仍可确认的方案（全部计入，'
                                                            '包括账本还没有任何记录时的期初余额预览）。')


# -- news (async: the provider calls are isolated, with timeouts) ------------------

def _news_subjects(held: Sequence[Tuple[str, str, str]]) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    a_shares, others = [], []
    for _, ticker, name in dict.fromkeys(held):
        try:
            instrument = parse(ticker)
        except InstrumentError:
            continue
        (a_shares if instrument.market == CN and instrument.kind == 'equity' else others).append((ticker, name))
    return list(dict.fromkeys(a_shares)), list(dict.fromkeys(others))


def _since(clock, trading_date: str) -> Tuple[str, str]:
    """``(previous trading day, its close as 'YYYY-MM-DD 15:00')``."""
    previous = clock.previous_trading_day(CN, inputs.as_date(trading_date)).isoformat()
    return previous, f'{previous} 15:00'


def _recent(items, previous_day: str, close: str) -> List[dict]:
    found = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        published = valid_date(item.get('published')) or ''
        if len(published) > 10:
            if published.replace('T', ' ')[:16] >= close:
                found.append(item)
        elif published and published >= previous_day:
            found.append(item)
    return found


def _resolve_news_provider(news_provider, runtime):
    """``(provider, reason not queried)``."""
    if news_provider is False:
        return None, '未查询公告与新闻'
    if isinstance(news_provider, str):
        return None, news_provider
    if news_provider is not None:
        return news_provider, None
    if briefs.demo_mode():
        return None, '演示模式不联网查询公告与新闻'
    if getattr(runtime, 'mode', None) == 'fake':
        return None, FAKE_NEWS_REASON
    from providers.news.cn_news_provider import get_cn_news_provider
    return get_cn_news_provider(), None


async def _news(held, clock, trading_date: str, now: datetime, news_provider, runtime,
                ledger_problem: Optional[str] = None) -> dict:
    from core.agent_tools._envelope import provider_error
    from providers.news.cn_news_provider import SRC_NEWS, SRC_NOTICE
    stamp = briefs.stamp(now)
    title = '公告与新闻'
    a_shares, others = _news_subjects(held)
    provider, off = _resolve_news_provider(news_provider, runtime)
    facts: List[dict] = []
    if ledger_problem is not None:
        # The holdings are unknown, not none.
        return briefs.section('news', title, [briefs.fact(
            '持仓', None, f'不可用：{ledger_problem}，不知道要查询哪些持仓', source=inputs.SOURCE_LEDGER, as_of=stamp,
            unit='count', status=briefs.unavailable_status(ledger_problem), metric='news_subject_count')],
            status=briefs.unavailable_status(ledger_problem))
    if not a_shares and not others:
        return briefs.section('news', title, [briefs.fact('持仓', 0, '没有持仓，不查询公告与新闻', source=inputs.SOURCE_LEDGER,
                                                          as_of=stamp, unit='count', metric='news_subject_count')])
    if others:
        reason = '公告与新闻数据源只支持 A 股'
        facts.append(briefs.fact('港股、美股持仓', None, f'未查询：{reason}（{"、".join(name for _, name in others[:10])}）',
                                 source=None, as_of=stamp, unit='count',
                                 status={'code': 'not_queried', 'reason': reason, 'estimated': False},
                                 metric='announcement_count'))
    if not a_shares:
        return briefs.section('news', title, facts)
    if provider is None:
        reason = f'数据源不可用：{off}'
        facts.insert(0, briefs.fact('公告与新闻', None, reason, source=None, as_of=stamp, unit='count',
                                    status={'code': 'not_queried', 'reason': off, 'estimated': False},
                                    metric='announcement_count'))
        return briefs.section('news', title, facts, status={'code': 'not_queried', 'reason': reason})

    previous_day, close = _since(clock, trading_date)
    subjects = a_shares[:NEWS_TICKERS]

    async def one(kind: str, ticker: str):
        code = ticker.split(':', 1)[1]
        call = provider.announcements(code, NEWS_LIMIT) if kind == 'notice' else provider.stock_news(code, NEWS_LIMIT)
        try:
            return await call
        except Exception as exc:   # the real provider never raises; a fake may. Its text is not trusted.
            return {'error': log_failure(logger, f'premarket brief news ({kind})', exc),
                    'source': SRC_NOTICE if kind == 'notice' else SRC_NEWS}

    calls = [(kind, ticker) for ticker, _ in subjects for kind in ('notice', 'news')]
    try:
        results = await asyncio.wait_for(asyncio.gather(*(one(kind, ticker) for kind, ticker in calls)),
                                         NEWS_BUDGET_SECONDS)
    except asyncio.TimeoutError:
        results = [{'error': f'数据源超时（{NEWS_BUDGET_SECONDS:g} 秒）', 'source': SRC_NEWS}] * len(calls)
    answers = dict(zip(calls, results))
    failures = []
    for ticker, name in subjects:
        for kind, label, source, limit in (('notice', '公告', SRC_NOTICE, 150), ('news', '新闻', SRC_NEWS, 120)):
            result = answers[(kind, ticker)]
            if not isinstance(result, dict) or 'error' in result or not isinstance(result.get('items'), list):
                problem = provider_error(result, source, (SRC_NEWS, SRC_NOTICE))
                failures.append(problem['error'])
                facts.append(briefs.fact(f'{name} {label}', None, f'数据源不可用：{problem["error"]}',
                                         source=problem['source'], as_of=stamp, unit='count',
                                         status=briefs.unavailable_status(problem['error']), ref={'ticker': ticker},
                                         metric='announcement_count' if kind == 'notice' else 'news_count'))
                continue
            recent = _recent(result['items'], previous_day, close)
            items = [{'published': valid_date(item.get('published')), 'title': untrusted(item.get('title'), limit),
                      **({'kind': untrusted(item.get('kind'), 20)} if kind == 'notice' else {})}
                     for item in recent[:NEWS_SHOWN]]
            text = (f'{len(recent)} 条（自 {day_text(previous_day)} 收盘）' if recent
                    else f'自 {day_text(previous_day)} 收盘以来数据源没有返回{label}')
            facts.append(briefs.fact(f'{name} {label}', len(recent), text, source=result.get('source') or source,
                                     as_of=result.get('fetched_at') or stamp, unit='count', ref={'ticker': ticker},
                                     metric='announcement_count' if kind == 'notice' else 'news_count', items=items))
    note = (f'A 股持仓自上一交易日（{previous_day}）收盘以来的公告和新闻标题；公告按发布日期（含 {day_text(previous_day)} 当天）。'
            '标题是第三方网站的原文，仅供查阅。数据源没有返回不代表没有发生相关事件。')
    if len(a_shares) > NEWS_TICKERS:
        note += f'只查询了前 {NEWS_TICKERS} 只持仓。'
    status = None
    if failures and len(failures) == 2 * len(subjects):
        status = briefs.unavailable_status('数据源不可用：' + failures[0])
    return briefs.section('news', title, facts, note=note, status=status)


# -- the brief --------------------------------------------------------------------

async def build(trading_date: str, *, runtime, ledger=Ellipsis, news_provider=None):
    """``(brief, blockers)`` for ``trading_date`` from the data as it is now (``runtime.clock``).

    ``ledger``: ``...`` reads the portfolio's ledger file, ``None`` means none
    (see ``brief_inputs.read_snapshot``). It is read once, as of ``trading_date``.
    """
    clock = runtime.clock
    now = clock.now()
    data = await asyncio.to_thread(_collect, trading_date, runtime, ledger, now)
    data['trading_date'] = trading_date
    news = await _news(data['held'], clock, trading_date, now, news_provider, runtime, data['ledger_problem'])
    sections = [_overnight(data['quotes'], clock, now), _levels(data, now), _reviews(data, trading_date, now), news,
                _alerts(data, now, data['names']), pending_section(data, now)]
    notices = ledger_notices(data['view'], data['ledger_problem'], trading_date)
    calendar = briefs.calendar_notice(clock, trading_date)
    if calendar is not None:
        notices.insert(0, calendar)
    return briefs.assemble(KIND, trading_date, now, sections, notices), blockers_of(data['ledger_problem'],
                                                                                    data['quotes'].values())


def ledger_notices(view, problem: Optional[str], trading_date: str) -> List[dict]:
    """What to know about the ledger before reading: unreadable, empty, corrected, later records left out."""
    notices = []
    if problem:
        notices.append({'kind': 'ledger', 'text': f'账本不可用：{problem}。持仓、成交和盈亏部分为不可用，稍后自动重试。'})
        return notices
    if view is not None and view.state is None:
        notices.append({'kind': 'ledger', 'text': f'账本在 {trading_date} 还没有已确认的数据，持仓相关部分为空。'})
    if view is not None and view.corrected:
        notices.append({'kind': 'corrected_view', 'text': f'账户 {"、".join(view.corrected)} 按日期回放不成立，'
                                                          '已提前计入日期较晚的冲销记录（与今日页相同）。'})
    if view is not None and view.later:
        notices.append({'kind': 'future_events', 'text': f'账本中有 {view.later} 笔日期晚于 {trading_date} 的记录，'
                                                         '本简报不计入。'})
    return notices


def blockers_of(ledger_problem: Optional[str], quotes) -> List[str]:
    """Why the run fails (and is retried): fixed texts, never a provider's words or an amount."""
    blockers = []
    if ledger_problem:
        blockers.append('账本不可用')
    quotes = list(quotes)
    if quotes and not any(quote is not None and quote.usable for quote in quotes):
        blockers.append('行情全部不可用')
    return blockers


scheduler.register(KIND, due, run, title=TITLE, retry_until=briefs.retry_until,
                   refuse=lambda: briefs.manual_refusal(KIND))
