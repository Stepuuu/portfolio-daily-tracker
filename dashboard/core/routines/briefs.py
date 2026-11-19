"""Pre- and post-market briefs: the shared model, their storage, settings and the run that makes one.

Every number in a brief is computed by code from the ledger, the desk store and
labelled quotes; the optional model commentary is kept apart (``commentary``)
and never supplies a fact. Unknown is ``null`` with the reason, never zero.

A brief (``premarket.build`` / ``postmarket.build``)::

    kind            'premarket' | 'postmarket'
    title           '盘前简报' | '盘后简报'
    trading_date    YYYY-MM-DD, the A-share trading day it is for
    generated_at    ISO-8601, Asia/Shanghai
    sections        [{id, title, facts: [fact], note?, status?}]
                    ``status`` is null, or {code: 'unavailable' | 'not_queried', reason} when the
                    whole section's source could not be used (the facts then say why, too)
    quality         {level: 'complete' | 'estimated' | 'incomplete', estimated: n, provisional: n,
                    unavailable: n, text}: 'complete' only when no fact is estimated, provisional
                    (an intraday price, not a close) or unavailable
    notices         [{kind, text}]: things to know before reading (the close snapshot's state, a
                    trading calendar that is only the weekday fallback ...)
    commentary      null, or what ``brief_commentary.comment`` returned (separate, labelled)
    version         1

A fact::

    label    short Chinese label ("上证指数", "示例宽基 止损 54.00")
    value    a decimal string (money, prices, percentages), an int (counts), a YYYY-MM-DD
             string (unit 'date'), a status word (unit 'state'), or null when unknown
    unit     'CNY' | 'HKD' | 'USD' | 'pct' | 'points' | 'count' | 'date' | 'state' | null
    text     the Chinese display string of the value; for a null value it says why
    source   where the value came from: a quote source ('eastmoney', 'yahoo', 'fake'), or
             '账本', '提醒记录', '交易计划', '研究笔记', '待确认方案', '例行任务记录', a news source
    as_of    when the value was true: a quote's exchange time, else the time it was read
    status   null, or {code, reason, estimated, provisional?}: ``code`` is a quote status (live,
             delayed, closed, stale, prev_close, time_unknown, unavailable) or 'estimated' (a
             total built on non-verified inputs) or 'unavailable' (no value); ``estimated`` is
             true when the value is computed from an input that is not verified;
             ``provisional`` is true when it is computed from an intraday price of a session
             still trading (not a close): it will change
    ref      null, or {ticker?, account?, plan_id?, alert_rule_id?, alert_event_id?, ledger_event_id?,
             journal_id?, proposal_ids?}: what the value is about, and what the UI links to
    metric   machine name of what ``value`` is (``index_change_pct``, ``distance_pct`` ...)
    figures  {name: decimal string}: the other numbers that ``text`` shows
    fx       optional, for an amount converted to CNY: {currency, rate, source, as_of, status,
             reason, estimated}: the rate used, where and when it was quoted
    signed   true when the sign of ``value`` means up / down (red / green)
    items    optional sub-rows [{text, ref?, level?, published?, title?}]; third-party text
             (headlines) appears only as ``title: {"untrusted_text": ...}``
    level    optional 'warning' | 'info'

Storage: table ``briefs`` in ``desk.sqlite3``, one row per kind and trading
date, replaced when that day is generated again (a retry or a manual run), so
the page shows exactly what was generated and when. Readers use ``ready()``
and never create the store.

Settings (environment, read at every use, like ``TRACKER_CLOSE_SNAPSHOT``)::

    TRACKER_PREMARKET=0         switch the pre-market brief off   (routines.premarket.enabled)
    TRACKER_PREMARKET_TIME      HH:MM, default 08:45 Asia/Shanghai (routines.premarket.time)
    TRACKER_POSTMARKET=0        switch the post-market brief off  (routines.postmarket.enabled)
    TRACKER_POSTMARKET_TIME     HH:MM, default 15:45               (routines.postmarket.time)
    TRACKER_BRIEF_COMMENTARY=1  model commentary on (default off)  (routines.commentary.enabled)

A run (``produce``) returns ``ok`` when the brief was stored; ``failed`` (the
scheduler retries after 15 and 30 minutes) when the ledger could not be read
or no quote at all could be had: the brief is stored anyway, so the page shows
what is missing, and a later attempt replaces it; ``skipped`` when switched
off. On ``ok`` one notification without amounts is published per kind and day.
A failed run's summary is a fixed text (no provider's words, no amounts) and
promises nothing: whether and when it is retried is the run row's
``next_attempt_at``, which the page reads.

The trading day is pinned when the run starts (the market runtime's session
clock, the one the scheduler and the page use): every builder reads the
ledger as of that day, and when the clock has passed midnight before the
brief is stored, nothing is stored and the attempt fails (``expired``). Run by
the scheduler, the brief is stored by the outcome's ``commit``, inside the
transaction that records the attempt and only while that run still owns its
claim: a late worker whose claim was taken over never overwrites the brief of
the run that won.

A day whose mainland calendar is only the weekday fallback (a year missing
from ``holidays.json``) is still generated; its brief says so (notice
``calendar``) and the page marks the day.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from core import desk_store, notify
from core.market.calendar import CN, SHANGHAI, SessionClock

logger = logging.getLogger(__name__)

PREMARKET, POSTMARKET = 'premarket', 'postmarket'
KINDS = (PREMARKET, POSTMARKET)
TITLES = {PREMARKET: '盘前简报', POSTMARKET: '盘后简报'}
VERSION = 1
MAX_PAYLOAD = 400_000

SCHEMA = 'briefs'
MIGRATIONS = [[
    """CREATE TABLE briefs (
        kind TEXT NOT NULL,
        trading_date TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (kind, trading_date))""",
]]
desk_store.register_schema(SCHEMA, MIGRATIONS)

# kind -> (switch variable, time variable, default time)
SETTINGS = {
    PREMARKET: ('TRACKER_PREMARKET', 'TRACKER_PREMARKET_TIME', time(8, 45)),
    POSTMARKET: ('TRACKER_POSTMARKET', 'TRACKER_POSTMARKET_TIME', time(15, 45)),
}
COMMENTARY_ENV = 'TRACKER_BRIEF_COMMENTARY'
_OFF = {'0', 'off', 'false', 'no'}
_ON = {'1', 'on', 'true', 'yes'}
_TIME = re.compile(r'([01]\d|2[0-3]):([0-5]\d)')
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')


class BriefError(ValueError):
    """A request about briefs cannot be answered; the message is for the user."""


# -- settings -------------------------------------------------------------------

def enabled(kind: str) -> bool:
    return os.environ.get(SETTINGS[kind][0], '').strip().lower() not in _OFF


def configured_time(kind: str) -> time:
    """The due time of ``kind``; a malformed setting falls back to the default (``time_problem`` says so)."""
    text = os.environ.get(SETTINGS[kind][1], '').strip()
    match = _TIME.fullmatch(text)
    return time(int(match.group(1)), int(match.group(2))) if match else SETTINGS[kind][2]


def time_problem(kind: str) -> Optional[str]:
    text = os.environ.get(SETTINGS[kind][1], '').strip()
    if text and not _TIME.fullmatch(text):
        return f'{SETTINGS[kind][1]} 应为 HH:MM（24 小时制），已按默认 {SETTINGS[kind][2]:%H:%M} 运行'
    return None


def commentary_enabled() -> bool:
    return os.environ.get(COMMENTARY_ENV, '').strip().lower() in _ON


def settings() -> dict:
    """What ``GET /api/routines/status`` shows under ``settings``."""
    result = {}
    for kind, (switch, clock_env, default) in SETTINGS.items():
        result[kind] = {'enabled': enabled(kind), 'time': f'{configured_time(kind):%H:%M}',
                        'default_time': f'{default:%H:%M}', 'problem': time_problem(kind),
                        'env': {'enabled': switch, 'time': clock_env},
                        'key': {'enabled': f'routines.{kind}.enabled', 'time': f'routines.{kind}.time'}}
    result['commentary'] = {'enabled': commentary_enabled(), 'env': {'enabled': COMMENTARY_ENV},
                            'key': {'enabled': 'routines.commentary.enabled'}}
    return result


def due_at(kind: str, clock: SessionClock) -> Optional[datetime]:
    """The configured time on an A-share trading day (Shanghai), otherwise None."""
    day = clock.local_date()
    if not clock.is_trading_day(day=day):
        return None
    return datetime.combine(day, configured_time(kind), tzinfo=SHANGHAI)


def manual_refusal(kind: str) -> Optional[str]:
    """Why a manual run of ``kind`` cannot be accepted (the scheduler asks before queuing one), or None."""
    if not enabled(kind):
        return f'{TITLES[kind]}已关闭（{SETTINGS[kind][0]}=0），不能生成。'
    return None


CALENDAR_UNVERIFIED = '交易日历未核实（按工作日推算）'


def calendar_notice(clock: SessionClock, trading_date: str) -> Optional[dict]:
    """The notice for a day whose mainland calendar is only the weekday fallback, else None."""
    day = date.fromisoformat(trading_date)
    if clock.confidence(CN, day) == 'calendar':
        return None
    return {'kind': 'calendar', 'text': f'{CALENDAR_UNVERIFIED}：{day.year} 年的 A 股节假日表未收录，'
                                        f'{trading_date} 按工作日当作交易日，可能是休市日。'}


def retry_until(trading_date: str) -> datetime:
    """Midnight after the trading day: a brief is built from that day's data or not at all."""
    return datetime.combine(date.fromisoformat(trading_date) + timedelta(days=1), time(0, 0), tzinfo=SHANGHAI)


# -- facts ----------------------------------------------------------------------

def fact(label: str, value: Any, text: str, *, source: Optional[str], as_of: Optional[str],
         unit: Optional[str] = None, status: Optional[dict] = None, ref: Optional[dict] = None,
         metric: Optional[str] = None, figures: Optional[Mapping[str, Optional[str]]] = None,
         signed: bool = False, items: Optional[List[dict]] = None, level: Optional[str] = None,
         fx: Optional[dict] = None) -> dict:
    """One fact (see the module docstring). ``None`` figures and empty refs are dropped."""
    item = {'label': label, 'value': value, 'unit': unit, 'text': text, 'source': source, 'as_of': as_of,
            'status': status, 'ref': {key: val for key, val in (ref or {}).items() if val not in (None, [], '')} or None,
            'metric': metric}
    if figures:
        kept = {key: val for key, val in figures.items() if val is not None}
        if kept:
            item['figures'] = kept
    if signed:
        item['signed'] = True
    if items:
        item['items'] = items
    if level:
        item['level'] = level
    if fx:
        item['fx'] = fx
    return item


def unavailable_status(reason: str) -> dict:
    return {'code': 'unavailable', 'reason': reason, 'estimated': False}


def section(section_id: str, title: str, facts: Sequence[dict], note: Optional[str] = None,
            status: Optional[dict] = None) -> dict:
    item = {'id': section_id, 'title': title, 'facts': list(facts)}
    if note:
        item['note'] = note
    if status:
        item['status'] = status
    return item


def _is_unavailable(item: Mapping) -> bool:
    status = item.get('status') or {}
    return item.get('value') is None and (status.get('code') in ('unavailable', 'not_queried')
                                          or item.get('unit') not in (None, 'state'))


def quality(sections: Sequence[Mapping]) -> dict:
    """How complete the brief is: counts of estimated, provisional and unavailable facts (and unusable sections).

    ``complete`` only when no fact is any of them.
    """
    estimated = provisional = unavailable = 0
    for part in sections:
        if part.get('status'):
            unavailable += 1 if not part.get('facts') else 0
        for item in part.get('facts') or []:
            status = item.get('status') or {}
            if _is_unavailable(item):
                unavailable += 1
            elif status.get('estimated'):
                estimated += 1
            elif status.get('provisional'):
                provisional += 1
    level = 'incomplete' if unavailable else 'estimated' if estimated or provisional else 'complete'
    pieces = [f'{unavailable} 项不可用' if unavailable else '', f'{estimated} 项为估算' if estimated else '',
              f'{provisional} 项为盘中价（非收盘价）' if provisional else '']
    text = '；'.join(piece for piece in pieces if piece) or '所有数字都有来源且已核实'
    return {'level': level, 'estimated': estimated, 'provisional': provisional, 'unavailable': unavailable,
            'text': text}


def assemble(kind: str, trading_date: str, generated_at: datetime, sections: Sequence[dict],
             notices: Sequence[dict] = ()) -> dict:
    return {'kind': kind, 'title': TITLES[kind], 'trading_date': trading_date,
            'generated_at': stamp(generated_at), 'sections': list(sections), 'quality': quality(sections),
            'notices': list(notices), 'commentary': None, 'version': VERSION}


def stamp(moment: Optional[datetime]) -> Optional[str]:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=SHANGHAI)
    return moment.astimezone(SHANGHAI).isoformat(timespec='seconds')


def count_facts(brief: Mapping) -> int:
    return sum(len(part.get('facts') or []) for part in brief.get('sections') or [])


# -- storage --------------------------------------------------------------------

def _ensure() -> None:
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)


def stored() -> bool:
    """For readers: whether briefs can be read. Never creates or migrates the store."""
    return desk_store.ready(SCHEMA, MIGRATIONS)


def save(brief: Mapping) -> None:
    """Store ``brief``, replacing that kind's brief of the same trading day."""
    if brief.get('kind') not in KINDS or not _DATE.fullmatch(str(brief.get('trading_date') or '')):
        raise ValueError('a brief needs its kind and trading_date')
    payload = desk_store.encode(brief)
    if len(payload) > MAX_PAYLOAD:
        raise ValueError('brief is too large to store')
    _ensure()
    stamp_now = desk_store.now()
    with desk_store.transaction() as db:
        db.execute("INSERT INTO briefs(kind, trading_date, generated_at, payload, created_at, updated_at) "
                   "VALUES (?,?,?,?,?,?) ON CONFLICT(kind, trading_date) DO UPDATE SET "
                   "generated_at=excluded.generated_at, payload=excluded.payload, updated_at=excluded.updated_at",
                   (brief['kind'], brief['trading_date'], brief['generated_at'], payload, stamp_now, stamp_now))


def load(kind: str, trading_date: str) -> Optional[dict]:
    """The stored brief, or None. Never creates the store."""
    if kind not in KINDS or not stored():
        return None
    with desk_store.connect() as db:
        row = db.execute("SELECT payload FROM briefs WHERE kind=? AND trading_date=?", (kind, trading_date)).fetchone()
    return json.loads(row['payload']) if row is not None else None


def latest_date(kind: Optional[str] = None) -> Optional[str]:
    """The newest trading day with a stored brief (of ``kind``, or of either kind)."""
    if not stored():
        return None
    with desk_store.connect() as db:
        if kind is None:
            row = db.execute("SELECT MAX(trading_date) AS day FROM briefs").fetchone()
        else:
            row = db.execute("SELECT MAX(trading_date) AS day FROM briefs WHERE kind=?", (kind,)).fetchone()
    return row['day'] if row is not None else None


# -- the run --------------------------------------------------------------------

Builder = Callable[..., Any]


def demo_mode() -> bool:
    return os.environ.get('TRACKER_DEMO_MODE') == '1'


def summary(brief: Mapping) -> str:
    """The run summary: counts only, never amounts."""
    sections = brief.get('sections') or []
    return f'已生成：{len(sections)} 个部分，{count_facts(brief)} 项；{brief["quality"]["text"]}'


def session_day(runtime) -> str:
    """The Shanghai date on the market runtime's clock: the trading day a run is for."""
    return runtime.clock.now().astimezone(SHANGHAI).date().isoformat()


def _alone(commit):
    """Run ``commit`` in a transaction of its own (a run outside the scheduler)."""
    with desk_store.transaction():
        return commit()


async def produce(kind: str, trading_date: str, build: Builder, *, runtime=None, ledger=Ellipsis,
                  news_provider=None, llm=None, publish: bool = True, store: bool = True):
    """Build, comment (when switched on), store and announce ``kind`` for ``trading_date``.

    The outcome carries ``commit``: it stores the brief (and announces an
    ``ok`` one) unless the trading day has passed meanwhile, in which case it
    stores nothing and returns the ``expired`` failure to record instead. The
    scheduler (``store=False``, see ``run`` in the kind modules) calls it inside
    the transaction that records the attempt, only while the run still owns its
    claim; with ``store`` (a direct call) it is called here.

    ``runtime`` (market runtime), ``ledger`` (``None`` for none, ``...`` for the
    portfolio's), ``news_provider`` and ``llm`` are for tests; by default the
    process's own are used.
    """
    from core.routines import scheduler
    if demo_mode():
        return scheduler.skipped('演示模式', 'demo')
    if not enabled(kind):
        return scheduler.skipped(f'已关闭（{SETTINGS[kind][0]}=0）', 'disabled')
    if runtime is None:
        from core.market.runtime import get_market_runtime
        runtime = get_market_runtime()
    if session_day(runtime) != trading_date:
        return scheduler.failed(f'已过 {trading_date} 当日，不能再用当时的数据生成', 'expired')
    brief, blockers = await build(trading_date, runtime=runtime, ledger=ledger, news_provider=news_provider)
    if commentary_enabled():
        from core.routines import brief_commentary
        brief['commentary'] = await brief_commentary.comment(brief, llm)
    moved = f'生成期间已过 {trading_date} 当日，结果未保存（简报只用当日的数据）'
    if session_day(runtime) != trading_date:
        return scheduler.failed(moved, 'expired')
    detail = {'sections': len(brief['sections']), 'facts': count_facts(brief),
              'unavailable': brief['quality']['unavailable'], 'estimated': brief['quality']['estimated'],
              'provisional': brief['quality'].get('provisional', 0)}
    if blockers:
        # Fixed texts only: the provider's words and any amount stay in the brief's facts.
        outcome = scheduler.failed('；'.join(blockers) + '。已保存当前结果', 'source_unavailable', detail)
    else:
        outcome = scheduler.ok(summary(brief), detail)

    def commit():
        if session_day(runtime) != trading_date:
            return scheduler.failed(moved, 'expired')
        save(brief)
        if outcome.status == 'ok' and publish:
            try:
                notify.publish(f'routine.{kind}', 'info', TITLES[kind],
                               f'{trading_date} 的{TITLES[kind]}已生成，点击查看。',
                               data={'kind': kind, 'trading_date': trading_date,
                                     'href': f'/review?date={trading_date}'},
                               dedupe_key=f'brief:{kind}:{trading_date}')
            except Exception as exc:   # the brief is stored; a missing notification must not fail it
                logger.warning('brief %s %s: notification failed: %s', kind, trading_date, type(exc).__name__)
        return None

    outcome = replace(outcome, commit=commit)
    if not store:
        return outcome
    replaced = await asyncio.to_thread(_alone, commit)
    return replace(replaced or outcome, commit=None)


# -- what the page reads --------------------------------------------------------

STATE_TEXT = {
    'ok': '已生成', 'failed': '失败', 'running': '生成中', 'pending': '等待运行', 'skipped': '已跳过',
    'unavailable': '不可用', 'scheduled': '尚未生成', 'due': '尚未生成', 'missing': '未生成',
    'not_trading_day': '非交易日', 'disabled': '已关闭', 'demo': '演示数据',
}


def parse_day(text: Optional[str]) -> Optional[date]:
    if text in (None, ''):
        return None
    if not isinstance(text, str) or not _DATE.fullmatch(text):
        raise BriefError('日期格式应为 YYYY-MM-DD。')
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise BriefError('日期不存在。') from None


def _scheduler_state():
    from core.routines import scheduler
    running = scheduler.current()
    return running, bool(running is not None and running.alive()), scheduler.mode()


def kind_view(kind: str, day: date, clock: SessionClock) -> dict:
    """One kind's brief for ``day`` with what the scheduler says about it."""
    from core.routines import scheduler
    if kind not in KINDS:
        raise BriefError('没有这种简报。')
    trading_date = day.isoformat()
    now = clock.now()
    today = now.astimezone(SHANGHAI).date()
    trading = clock.is_trading_day(CN, day)
    due = datetime.combine(day, configured_time(kind), tzinfo=SHANGHAI) if trading else None
    run = scheduler.run_for(kind, trading_date)
    if run is not None and isinstance(run.get('detail'), dict):
        run['detail'] = {key: value for key, value in run['detail'].items() if key != 'log_tail'}
    brief = load(kind, trading_date)
    _, alive, mode = _scheduler_state()
    switched_on = enabled(kind)

    if run is not None:
        state, reason = run['status'], run.get('summary') or None
        if state == 'failed':
            # Whether it is tried again is the run row's, not the summary's, to say.
            retry = run.get('next_attempt_at')
            when = _clock_text(retry, trading_date) if retry else None
            reason = (reason or '生成失败') + (f'；将于 {when} 自动重试' if when else '；不会再自动重试')
    elif brief is not None:
        state, reason = ('demo' if mode == 'demo' else 'ok'), None
    elif not trading:
        state, reason = 'not_trading_day', f'{trading_date} 不是 A 股交易日，不生成{TITLES[kind]}。'
    elif mode == 'demo':
        state, reason = 'demo', '演示模式不生成简报，只显示演示数据中已有的简报。'
    elif not switched_on:
        state, reason = 'disabled', f'{TITLES[kind]}已关闭（{SETTINGS[kind][0]}=0）。'
    elif day > today or (day == today and now < due):
        state, reason = 'scheduled', f'{due:%H:%M} 自动生成。'
    elif day == today:
        state, reason = 'due', (f'已到 {due:%H:%M}，等待后台调度器生成。' if alive
                                else '后台调度器未运行：例行任务只在后端运行期间自动执行。')
    else:
        state, reason = 'missing', '当日没有生成（后端当时可能未运行）。'

    refusal = None
    if mode == 'demo':
        refusal = '演示模式只读，不能生成简报。'
    elif not switched_on:
        refusal = manual_refusal(kind)
    elif not alive:
        refusal = '后台调度器未运行，不能生成。'
    elif not trading or day != today:
        refusal = '只能生成当天（交易日）的简报。'
    elif now < due:
        refusal = f'{due:%H:%M} 之后才能生成。'
    elif run is not None and run['status'] in ('running', 'pending'):
        refusal = '正在生成。'
    elif run is not None and run['status'] == 'ok':
        refusal = '今天的简报已经生成。'
    return {'kind': kind, 'title': TITLES[kind], 'trading_date': trading_date, 'enabled': switched_on,
            'time': f'{configured_time(kind):%H:%M}', 'due_at': stamp(due), 'state': state,
            'state_text': STATE_TEXT.get(state, state), 'reason': reason, 'run': run, 'brief': brief,
            'can_run': refusal is None, 'run_refusal': refusal}


def _clock_text(text: Optional[str], day: str) -> Optional[str]:
    """'16:00', or '11-18 00:15' when not on ``day``; None for a time that does not parse."""
    try:
        moment = datetime.fromisoformat(text).astimezone(SHANGHAI)
    except (TypeError, ValueError):
        return None
    return f'{moment:%H:%M}' if moment.date().isoformat() == day else f'{moment:%m-%d %H:%M}'


def day_view(text: Optional[str] = None, clock: Optional[SessionClock] = None) -> dict:
    """``GET /api/briefs``: both kinds for a date (default: the current A-share trading day).

    ``calendar`` maps the day, the previous and next ones and the current
    trading day to ``'calendar'`` (the mainland holiday table knows the year)
    or ``'weekday_only'`` (the weekday fallback: the day may be a holiday).
    """
    if clock is None:
        from core.market.runtime import get_market_runtime
        clock = get_market_runtime().clock
    current = clock.current_trading_day(CN)
    day = parse_day(text) or current
    previous = clock.previous_trading_day(CN, day)
    following = None
    probe = day
    for _ in range(40):
        probe += timedelta(days=1)
        if probe > current:
            break
        if clock.is_trading_day(CN, probe):
            following = probe
            break
    _, alive, mode = _scheduler_state()
    calendar = {probe.isoformat(): clock.confidence(CN, probe)
                for probe in (day, previous, following, current) if probe is not None}
    return {'date': day.isoformat(), 'current_trading_date': current.isoformat(), 'calendar': calendar,
            'is_trading_day': clock.is_trading_day(CN, day), 'previous_date': previous.isoformat(),
            'next_date': following.isoformat() if following else None, 'latest_date': latest_date(),
            'now': stamp(clock.now()), 'mode': mode, 'scheduler_alive': alive,
            'items': {kind: kind_view(kind, day, clock) for kind in KINDS}}
