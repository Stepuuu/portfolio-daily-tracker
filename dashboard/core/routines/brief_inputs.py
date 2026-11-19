"""What the briefs read, and the small formatting rules they share.

Every reader here only reads: the desk store through ``desk_store.ready`` /
``connect`` (never created), the ledger file read-only. Readers return
``None`` (or raise ``InputUnavailable`` with a fixed reason) when their source
cannot be read, so the brief can say 不可用 instead of showing an empty list
as if nothing had happened.

The ledger is read once per brief (``read_snapshot``): one read transaction
gives the revision, every event and the unconfirmed previews, and everything
the brief says about the ledger (positions, trades, plan links, sold-out
positions, profit, stops and targets) is derived from that one snapshot as of
the brief's trading day (``ledger_view``).

Some queries are written here on top of other modules' tables because those
modules have no read function for them (they are not edited from here):

* ``alert_events`` joined to ``notifications`` (has the person read the alert?);
* the research journal in ``ledger.sqlite3`` (``backend/api/ledger.py`` creates
  it on first use and has only an HTTP route).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core import desk_store, notify, proposals
from core.ledger import LedgerError, ReadOnlyOpen, portfolio_directory, replay, replay_as_of
from core.market.calendar import CN, HK, SHANGHAI, US, ZONES
from core.market.instruments import InstrumentError, parse
from core.market.quotes import CLOSED, DELAYED, LIVE, PREV_CLOSE_ONLY, STALE, TIME_UNKNOWN, UNAVAILABLE, LiveQuote
from core.market.search import identity_key
from core.market.today import effective_events, events_in_force, moment, read_ledger
from core.market.valuation import quantized, quote_estimate

logger = logging.getLogger(__name__)

CENT = Decimal('0.01')
HUNDRED = Decimal(100)
MARKET_NAMES = {CN: 'A股', HK: '港股', US: '美股'}
PLACE = {CN: '', HK: '香港', US: '纽约'}
SOURCE_LEDGER = '账本'
SOURCE_ALERTS = '提醒记录'
SOURCE_PLANS = '交易计划'
SOURCE_JOURNAL = '研究笔记'
SOURCE_PROPOSALS = '待确认方案'
SOURCE_RUNS = '例行任务记录'
MAX_ITEMS = 30
LEDGER_FILE = 'ledger.sqlite3'
LEDGER_WAIT_SECONDS = 5.0
# Fixed reasons: what SQLite or the file said goes to the log only.
LEDGER_UNREADABLE = '账本暂时无法读取'
LEDGER_DAMAGED = '账本文件已损坏或结构不完整，无法读取'
LEDGER_UNREPLAYABLE = '账本无法按日期回放'
JOURNAL_UNREADABLE = '研究笔记暂时无法读取'
MAX_JOURNAL = 2000


class InputUnavailable(RuntimeError):
    """A source of the brief cannot be read; the message (Chinese, fixed) says why."""


# -- numbers --------------------------------------------------------------------

def dec(value) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def two(value: Optional[Decimal]) -> Optional[str]:
    """Two decimal places, as a string; None stays None."""
    rounded = quantized(value, CENT)
    if rounded is None:
        return None
    return format(rounded if rounded != 0 else Decimal('0.00'), 'f')


def plain(value: Optional[Decimal], places: int = 6) -> Optional[str]:
    rounded = quantized(value, Decimal(1).scaleb(-places))
    if rounded is None:
        return None
    if rounded == 0:
        return '0'
    text = format(rounded, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def exact(value: Optional[Decimal]) -> Optional[str]:
    """``value`` as the ledger holds it, never rounded (trailing zeros dropped): '0.12345678', '100'."""
    if value is None:
        return None
    if value == 0:
        return '0'
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def grouped(text: Optional[str], signed: bool = False, min_places: int = 2) -> str:
    """'12345.6' -> '12,345.60'; ``signed`` adds '+' to positive values. Display only."""
    number = dec(text)
    if number is None:
        return '不可用'
    places = max(min_places, -number.as_tuple().exponent if number.as_tuple().exponent < 0 else 0)
    body = f'{abs(number):,.{places}f}'
    sign = '-' if number < 0 else ('+' if signed and number > 0 else '')
    return sign + body


def pct_text(text: Optional[str], signed: bool = True) -> str:
    return '不可用' if dec(text) is None else grouped(text, signed=signed) + '%'


def percent_of(part: Optional[Decimal], whole: Optional[Decimal]) -> Optional[Decimal]:
    if part is None or whole is None or whole == 0:
        return None
    return part / whole * HUNDRED


CURRENCY_SYMBOL = {'CNY': '¥', 'HKD': 'HK$', 'USD': '$'}


def money_text(text: Optional[str], currency: str, signed: bool = False) -> str:
    """'¥1,234.50' / '+HK$12.00'; 不可用 for None."""
    number = dec(text)
    if number is None:
        return '不可用'
    body = grouped(text, signed=False)
    sign = '-' if number < 0 else ('+' if signed and number > 0 else '')
    return f'{sign}{CURRENCY_SYMBOL.get(currency, currency + " ")}{body.lstrip("-")}'


# -- quotes ---------------------------------------------------------------------

def quote_status(quote: Optional[LiveQuote]) -> dict:
    """``{code, reason, estimated}`` for a fact computed from ``quote``."""
    if quote is None:
        return {'code': UNAVAILABLE, 'reason': '没有行情', 'estimated': False}
    flagged = quote_estimate(quote)
    reason = quote.reason
    if flagged is not None:
        reason = flagged[1]
    return {'code': quote.status, 'reason': reason, 'estimated': flagged is not None}


def quote_time(quote: Optional[LiveQuote]) -> Optional[str]:
    if quote is None:
        return None
    return moment(quote.quote_time or quote.fetched_at)


def market_of(ticker: str) -> Optional[str]:
    try:
        return parse(ticker).market
    except InstrumentError:
        return None


def currency_of(ticker: str) -> Optional[str]:
    try:
        return parse(ticker).currency
    except InstrumentError:
        return None


def listing(ticker: Optional[str]) -> Optional[str]:
    """The listing a ticker names, whatever its spelling (``HKG:700`` = ``HKG:00700``): ``identity_key``."""
    return identity_key(ticker) if ticker else None


def local_clock(quote: Optional[LiveQuote], market: Optional[str], seconds: bool = False) -> str:
    """'15:00' in the exchange's own time, '' when unknown."""
    if quote is None or quote.quote_time is None or market not in ZONES:
        return ''
    return quote.quote_time.astimezone(ZONES[market]).strftime('%H:%M:%S' if seconds else '%H:%M')


def price_phrase(quote: Optional[LiveQuote], market: Optional[str]) -> str:
    """What the price of ``quote`` is: '11-16 收盘价', '盘中价 10:32', '已过期的价格（11-14 15:00）' ..."""
    if quote is None or quote.price is None:
        return '没有价格'
    session = quote.session_date.isoformat()[5:] if quote.session_date else ''
    place = PLACE.get(market or '', '')
    when = local_clock(quote, market)
    if quote.status == CLOSED:
        # A New York close seen before it settles is not the official close yet (quotes.CLOSE_NOT_FINAL).
        return f'{place}{session} 收盘价' + ('（尚未最终确定）' if quote.close_pending else '')
    if quote.status in (LIVE, DELAYED):
        return f'{place}盘中价 {when}'.strip() + ('（延迟）' if quote.status == DELAYED else '')
    if quote.status == PREV_CLOSE_ONLY:
        return '昨收价（今日尚无成交）'
    if quote.status == STALE:
        return f'最后价格（{session} {when}，已过期）'.replace('（ ', '（')
    if quote.status == TIME_UNKNOWN:
        return '价格（时间未知）'
    return '价格'


# -- the ledger: one read --------------------------------------------------------

@dataclass
class LedgerSnapshot:
    """``ledger.sqlite3`` as one read transaction saw it.

    ``events`` are every stored event with its ``_sequence`` (as
    ``Ledger.history()``), ``previews`` the unconfirmed proposals
    ``{id, revision, created_at, events, expires_at}`` (``events``: how many
    it would book; ``expires_at``: the end of a manual preview's lifetime,
    ``proposal_expiry`` as the ledger API writes it, ``None`` for the others).
    """
    revision: int
    events: List[dict]
    previews: Optional[List[dict]] = field(default_factory=list)   # None: they could not be read

    def history(self) -> List[dict]:
        return list(self.events)

    def state(self, as_of: Optional[str] = None) -> dict:
        return {'revision': self.revision, **(replay(self.events) if as_of is None else replay_as_of(self.events, as_of))}


def _read_file(path: Path, timeout: float) -> Optional[LedgerSnapshot]:
    """The snapshot of the ledger file at ``path``; ``None`` without one. Never creates or changes a file."""
    if not path.is_file():
        return None
    try:
        # Read-only, and no -wal / -shm file is left beside a WAL ledger (core.ledger.ReadOnlyOpen).
        target = ReadOnlyOpen(path)
        db = target.connect(timeout)
    except (sqlite3.Error, OSError) as exc:
        logger.warning('brief: the ledger could not be opened: %s', type(exc).__name__)
        raise InputUnavailable(LEDGER_UNREADABLE) from None
    try:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'ledger_meta', 'events'} & tables:
            return None                               # an empty file, or only a journal: no ledger yet
        if not {'ledger_meta', 'events'} <= tables:
            raise InputUnavailable(LEDGER_DAMAGED)
        meta = db.execute('SELECT revision FROM ledger_meta WHERE id=1').fetchone()
        if meta is None:
            raise InputUnavailable(LEDGER_DAMAGED)
        events = [{**json.loads(row['payload']), '_sequence': row['sequence']}
                  for row in db.execute('SELECT sequence, payload FROM events ORDER BY sequence')]
        previews = []
        expiry = ({row[0]: row[1] for row in db.execute('SELECT proposal_id, expires_at FROM proposal_expiry')}
                  if 'proposal_expiry' in tables else {})
        if 'proposals' in tables:
            for row in db.execute('SELECT id, revision, created_at, payload FROM proposals WHERE receipt IS NULL '
                                  'ORDER BY created_at DESC'):
                try:
                    booked = len(json.loads(row['payload']).get('events') or [])
                except (ValueError, AttributeError):
                    booked = 0
                previews.append({'id': row['id'], 'revision': row['revision'], 'created_at': row['created_at'],
                                 'events': booked, 'expires_at': expiry.get(row['id'])})
        if target.moved():                            # a writer checkpointed meanwhile: not one version
            raise InputUnavailable(LEDGER_UNREADABLE)
        return LedgerSnapshot(int(meta[0]), events, previews)
    except InputUnavailable:
        raise
    except sqlite3.DatabaseError as exc:          # locked past the wait, not a database, malformed
        logger.warning('brief: the ledger could not be read: %s', type(exc).__name__)
        busy = isinstance(exc, sqlite3.OperationalError) and ('locked' in str(exc) or 'busy' in str(exc))
        raise InputUnavailable(LEDGER_UNREADABLE if busy else LEDGER_DAMAGED) from None
    except (ValueError, TypeError, KeyError) as exc:   # a stored event that is not JSON
        logger.warning('brief: a ledger event could not be decoded: %s', type(exc).__name__)
        raise InputUnavailable(LEDGER_DAMAGED) from None
    finally:
        db.close()


def read_snapshot(source=Ellipsis, *, timeout: float = LEDGER_WAIT_SECONDS) -> Optional[LedgerSnapshot]:
    """One read of the ledger; ``None`` when there is none. ``InputUnavailable`` when it cannot be read.

    ``source``: ``...`` (the portfolio's ``ledger.sqlite3``), ``None`` (no
    ledger), a path, a ``Ledger`` (its file is read read-only), a snapshot, or
    another object with ``state()`` and ``history()`` (read through
    ``read_ledger``, for tests).
    """
    if source is None or isinstance(source, LedgerSnapshot):
        return source
    if source is Ellipsis:
        return _read_file(portfolio_directory() / LEDGER_FILE, timeout)
    if isinstance(source, (str, Path)):
        return _read_file(Path(source), timeout)
    path = getattr(source, 'path', None)
    if isinstance(path, Path):
        return _read_file(path, timeout)
    try:
        read = read_ledger(source)
    except (LedgerError, sqlite3.Error) as exc:
        logger.warning('brief: the ledger could not be read: %s', type(exc).__name__)
        raise InputUnavailable(LEDGER_UNREADABLE) from None
    if read is None:
        raise InputUnavailable('账本正在写入，未能读到同一版本')
    revision, events = read
    previews: Optional[List[dict]]
    try:
        previews = [{'id': item['id'], 'revision': item['revision'], 'created_at': item['created_at'],
                     'events': len(item.get('events') or []), 'expires_at': item.get('expires_at')}
                    for item in source.proposals()]
    except Exception as exc:   # an object without previews: the count says 不可用
        logger.warning('brief: ledger previews could not be read: %s', type(exc).__name__)
        previews = None
    return LedgerSnapshot(revision, events, previews)


@dataclass
class LedgerView:
    """The ledger snapshot as of a trading day: the state, the events in force, the names.

    ``events`` are the events in force on the day (``today.events_in_force``):
    nothing dated later, reversals and their targets dropped, and for an
    account the dated replay had to correct, the targets of later reversals
    dropped too, as the corrected replay drops them. ``state`` is ``None``
    when nothing is confirmed as of the day.
    """
    revision: int
    events: List[dict]
    state: Optional[dict]
    names: Dict[str, str] = field(default_factory=dict)
    corrected: List[str] = field(default_factory=list)
    later: int = 0                          # events dated after the day (not applied)
    previews: Optional[List[dict]] = field(default_factory=list)

    def held(self, names: Optional[Mapping[str, str]] = None) -> List[Tuple[str, str, str]]:
        """``(account, ticker, name)`` of every open position; the name falls back to ``names``, then the ticker.

        The ledger stores the ticker as the name when none was entered.
        """
        found = []
        for account, value in ((self.state or {}).get('accounts') or {}).items():
            for ticker, position in (value.get('positions') or {}).items():
                name = position.get('name')
                if not name or name == ticker:
                    name = (names or {}).get(ticker) or self.names.get(ticker) or ticker
                found.append((account, ticker, name))
        return found

    def accounts(self) -> List[str]:
        return list(((self.state or {}).get('accounts') or {}).keys())

    def on(self, day: str, kinds: Optional[Sequence[str]] = None) -> List[dict]:
        """The events in force dated ``day`` (of ``kinds``)."""
        return [event for event in self.events if event['date'] == day and (kinds is None or event['kind'] in kinds)]


def ledger_view(snapshot: Optional[LedgerSnapshot], as_of: str) -> LedgerView:
    """``snapshot`` as of ``as_of`` (YYYY-MM-DD). ``InputUnavailable`` when it does not replay."""
    if snapshot is None:
        return LedgerView(0, [], None)
    events = snapshot.events
    if not snapshot.revision or not events:
        return LedgerView(snapshot.revision, [], None, previews=snapshot.previews)
    try:
        state = replay_as_of(events, as_of)
    except (LedgerError, KeyError, TypeError, ValueError) as exc:
        logger.warning('brief: the ledger does not replay as of %s: %s', as_of, type(exc).__name__)
        raise InputUnavailable(LEDGER_UNREPLAYABLE) from None
    corrected = list(state.get('as_of_corrected_accounts') or [])
    in_force = events_in_force(events, as_of, corrected)
    names: Dict[str, str] = {}
    for event in events:
        if event.get('ticker') and event.get('name') and event['name'] != event['ticker']:
            names[event['ticker']] = event['name']
        for position in event.get('positions') or []:
            if position.get('ticker') and position.get('name') and position['name'] != position['ticker']:
                names[position['ticker']] = position['name']
    # Later-dated events are not applied, except the reversals the corrected replay applied.
    dated = {event.get('id') for event in effective_events([event for event in events if event['date'] <= as_of])}
    applied = dated - {event.get('id') for event in in_force}
    later = sum(1 for event in events if event['date'] > as_of
                and not (event['kind'] == 'reverse' and event.get('target_id') in applied))
    return LedgerView(snapshot.revision, in_force, state if state.get('accounts') else None, names, corrected, later,
                      snapshot.previews)


def ledger_source(view: LedgerView) -> str:
    return f'{SOURCE_LEDGER}（版本 {view.revision}）'


def pending_ledger_previews(view: Optional[LedgerView], as_of: datetime) -> Optional[int]:
    """Ledger previews that can still be confirmed: made against the snapshot's revision (zero for the first
    opening balance) before ``as_of``, with something to book, and not a manual preview whose lifetime had
    ended by ``as_of`` (the ledger API's rule: it lists none of those and refuses to confirm them). All of
    them are counted. ``None`` when the previews could not be read."""
    if view is None:
        return 0
    if view.previews is None:
        return None
    limit = as_of.astimezone(SHANGHAI)
    count = 0
    for item in view.previews:
        try:
            created = datetime.fromisoformat(item['created_at'])
        except (TypeError, ValueError):
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=SHANGHAI)
        if item.get('expires_at') is not None:
            try:
                ends = datetime.fromisoformat(item['expires_at'])
            except (TypeError, ValueError):
                continue
            if (ends if ends.tzinfo is not None else ends.replace(tzinfo=timezone.utc)) <= limit:
                continue                     # expired: the ledger no longer lists it
        if item.get('revision') == view.revision and item.get('events') and created <= limit:
            count += 1
    return count


# -- stop and target levels -----------------------------------------------------

@dataclass
class Level:
    ticker: str                   # as the rule or plan spells it
    kind: str                     # 'stop' | 'target'
    threshold: Decimal
    account: Optional[str] = None  # the account the rule or plan covers; None: every account
    plan_ids: List[int] = field(default_factory=list)
    rule_ids: List[int] = field(default_factory=list)

    @property
    def label(self) -> str:
        return '止损' if self.kind == 'stop' else '目标'

    def origin(self) -> str:
        parts = [f'计划 #{plan_id}' for plan_id in self.plan_ids] + [f'提醒 #{rule_id}' for rule_id in self.rule_ids]
        return '、'.join(parts)

    def covers(self, account: Optional[str]) -> bool:
        """Whether this level is about the holding in ``account`` (``None``: a subject in no account)."""
        return self.account is None or account is None or self.account == account


def active_levels(tickers: Iterable[str]) -> Dict[str, List[Level]]:
    """Stops and targets of active alert rules and active plans for the listings of ``tickers``.

    Keyed by listing (``listing()``: ``HKG:700`` and ``HKG:00700`` are one).
    One level per listing, account, kind and price: an active plan creates
    its own stop and target rules, so a plan and its rule at the same price
    and account are one level that names both. A level with an account covers
    only that account's holding (``Level.covers``).
    """
    from core import plans
    from core.alerts import rules
    wanted = {listing(ticker) for ticker in tickers}
    found: Dict[Tuple[str, Optional[str], str, Decimal], Level] = {}

    def add(ticker: str, kind: str, threshold, account=None, plan_id=None, rule_id=None) -> None:
        number = dec(threshold)
        key = listing(ticker)
        if number is None or key not in wanted:
            return
        level = found.setdefault((key, account, kind, number), Level(ticker, kind, number, account))
        if plan_id is not None and plan_id not in level.plan_ids:
            level.plan_ids.append(plan_id)
        if rule_id is not None and rule_id not in level.rule_ids:
            level.rule_ids.append(rule_id)

    for plan in plans.list_plans(status=plans.ACTIVE):
        add(plan['ticker'], 'stop', plan.get('stop_price'), plan.get('account'), plan_id=plan['id'])
        add(plan['ticker'], 'target', plan.get('target_price'), plan.get('account'), plan_id=plan['id'])
    for rule in rules.list_rules(enabled=True):
        kind = {'stop_loss': 'stop', 'target_price': 'target'}.get(rule['kind'])
        if kind:
            add(rule['ticker'], kind, rule['threshold'], rule.get('account'), rule_id=rule['id'])
    result: Dict[str, List[Level]] = {}
    for (key, *_), level in found.items():
        result.setdefault(key, []).append(level)
    for levels in result.values():
        levels.sort(key=lambda level: (level.kind != 'stop', level.threshold, level.account or ''))
    return result


def levels_for(levels: Mapping[str, List[Level]], ticker: str, account: Optional[str]) -> List[Level]:
    """The levels about ``ticker`` held in ``account`` (``None``: a watched listing, every level).

    One per kind and price: a rule for every account and this account's plan
    at the same stop are one level the holding can cross, naming both.
    """
    merged: Dict[Tuple[str, Decimal], Level] = {}
    for level in levels.get(listing(ticker)) or []:
        if not level.covers(account):
            continue
        same = merged.setdefault((level.kind, level.threshold), level)
        if same is not level:
            merged[(level.kind, level.threshold)] = Level(
                same.ticker, same.kind, same.threshold, same.account if same.account == level.account else account,
                plan_ids=same.plan_ids + [number for number in level.plan_ids if number not in same.plan_ids],
                rule_ids=same.rule_ids + [number for number in level.rule_ids if number not in same.rule_ids])
    return list(merged.values())


def distance(level: Level, price: Decimal) -> Tuple[Decimal, Decimal, bool]:
    """``(gap, gap % of price, reached)``: gap > 0 while the level is not reached."""
    gap = price - level.threshold if level.kind == 'stop' else level.threshold - price
    reached = price <= level.threshold if level.kind == 'stop' else price >= level.threshold
    return gap, gap / price * HUNDRED, reached


# -- alerts ---------------------------------------------------------------------

ALERT_COLUMNS = ('id', 'rule_id', 'ticker', 'name', 'account', 'kind', 'threshold', 'trading_date', 'n', 'value',
                 'price', 'prev_close', 'quote_source', 'quote_time', 'quote_status', 'fired_at', 'delivery',
                 'notification_seq', 'title', 'body')
# Rules whose stored state says whether their condition still holds.
STATEFUL_KINDS = ('stop_loss', 'target_price', 'position_weight_max')


def _with_read(rows: List[dict]) -> List[dict]:
    """Each row gains ``read``: True when its notification was read, False when it is unread or the alert
    had none of its own (summarised in a digest), ``None`` when the notifications cannot be read."""
    seqs = [row['notification_seq'] for row in rows if row['notification_seq'] is not None]
    read: Optional[Dict[int, Optional[str]]] = {}
    if seqs:
        if desk_store.ready(notify.SCHEMA, notify.MIGRATIONS):
            with desk_store.connect() as db:
                read = {row['seq']: row['read_at'] for row in db.execute(
                    f"SELECT seq, read_at FROM notifications WHERE seq IN ({','.join('?' * len(seqs))})", seqs)}
        else:
            read = None
    for row in rows:
        seq = row['notification_seq']
        if read is None:
            row['read'] = None
        elif seq is None:
            row['read'] = False
        else:
            row['read'] = read.get(seq) is not None
    return rows


def alert_events(first: str, last: str, limit: int = MAX_ITEMS) -> List[dict]:
    """The newest ``limit`` fired alerts with ``trading_date`` in ``[first, last]``, newest first, with ``read``.

    The newest are selected first: a long day never pushes the latest alerts
    out. ``alert_count`` counts all of them.
    """
    from core.alerts import rules
    if not rules.available():
        return []
    with desk_store.connect() as db:
        rows = [dict(row) for row in db.execute(
            f"SELECT {', '.join(ALERT_COLUMNS)} FROM alert_events WHERE trading_date >= ? AND trading_date <= ? "
            "ORDER BY fired_ts DESC, id DESC LIMIT ?", (first, last, max(int(limit), 0)))]
    return _with_read(rows)


def alert_count(first: str, last: str) -> Tuple[int, Optional[int]]:
    """``(fired, unread)``: every alert fired with ``trading_date`` in ``[first, last]``, and how many of them
    have no read notification (``None`` when the notifications cannot be read)."""
    from core.alerts import rules
    if not rules.available():
        return 0, 0
    with desk_store.connect() as db:
        total = db.execute("SELECT COUNT(*) FROM alert_events WHERE trading_date >= ? AND trading_date <= ?",
                           (first, last)).fetchone()[0]
        if not total:
            return 0, 0
        if not desk_store.ready(notify.SCHEMA, notify.MIGRATIONS):
            return total, None
        unread = db.execute(
            "SELECT COUNT(*) FROM alert_events e LEFT JOIN notifications n ON n.seq = e.notification_seq "
            "WHERE e.trading_date >= ? AND e.trading_date <= ? AND n.read_at IS NULL", (first, last)).fetchone()[0]
    return total, unread


def fired_rules() -> List[dict]:
    """Enabled stop, target and weight rules still in their fired state (the monitor has not re-armed them),
    each with ``event``: its latest fired alert (with ``read``), or ``None`` when the log has none."""
    from core.alerts import rules
    if not rules.available():
        return []
    found = [rule for rule in rules.list_rules(enabled=True)
             if rule['kind'] in STATEFUL_KINDS and not (rule.get('state') or {}).get('armed', True)]
    if not found:
        return []
    with desk_store.connect() as db:
        for rule in found:
            row = db.execute(f"SELECT {', '.join(ALERT_COLUMNS)} FROM alert_events WHERE rule_id=? "
                             "ORDER BY fired_ts DESC, id DESC LIMIT 1", (rule['id'],)).fetchone()
            rule['event'] = dict(row) if row is not None else None
    events = _with_read([rule['event'] for rule in found if rule['event'] is not None])
    by_id = {event['id']: event for event in events}
    for rule in found:
        if rule['event'] is not None:
            rule['event'] = by_id[rule['event']['id']]
    return found


def alert_value_unit(event: Mapping) -> Optional[str]:
    if event['kind'] in ('day_change_pct', 'position_weight_max'):
        return 'pct'
    return currency_of(event['ticker'])


# -- journal --------------------------------------------------------------------

def journal_entries(directory=None, *, ticker: Optional[str] = None, match: Optional[Callable[[dict], bool]] = None,
                    limit: int = MAX_JOURNAL) -> List[dict]:
    """Research-journal entries of ``ledger.sqlite3``, newest first; ``[]`` when it has none.

    ``ticker`` (any spelling of the listing) and ``match`` filter before
    ``limit`` is applied, so an older entry that matches is never crowded out
    by newer ones that do not. Opened read-only, so a read never creates the
    ledger file or the table. ``InputUnavailable`` when the file cannot be read.
    """
    path = Path(directory or portfolio_directory()) / LEDGER_FILE
    if not path.is_file():
        return []
    wanted = listing(ticker) if ticker else None
    try:
        target = ReadOnlyOpen(path)          # no -wal / -shm file is left beside a WAL ledger
        db = target.connect(5)
    except (sqlite3.Error, OSError) as exc:
        logger.warning('brief: the journal could not be opened: %s', type(exc).__name__)
        raise InputUnavailable(JOURNAL_UNREADABLE) from None
    try:
        db.row_factory = sqlite3.Row
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='journal'").fetchone() is None:
            return []
        entries = []
        for row in db.execute('SELECT id, created_at, payload FROM journal ORDER BY created_at DESC, rowid DESC'):
            try:
                payload = json.loads(row['payload'])
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            entry = {'id': row['id'], 'created_at': row['created_at'],
                     **{key: payload.get(key) for key in ('ticker', 'thesis', 'invalidation', 'review_on')}}
            if wanted is not None and listing(entry.get('ticker') if isinstance(entry.get('ticker'), str) else None) != wanted:
                continue
            if match is not None and not match(entry):
                continue
            entries.append(entry)
            if len(entries) >= limit:
                break
        if target.moved():                            # a writer checkpointed meanwhile: not one version
            raise InputUnavailable(JOURNAL_UNREADABLE)
        return entries
    except sqlite3.Error as exc:
        logger.warning('brief: the journal could not be read: %s', type(exc).__name__)
        raise InputUnavailable(JOURNAL_UNREADABLE) from None
    finally:
        db.close()


# -- proposals ------------------------------------------------------------------

PROPOSAL_LABELS = {'alert_rule': '提醒规则', 'trade_plan': '交易计划', 'watchlist_add': '自选', 'lesson': '教训'}


def pending_proposals(as_of: datetime) -> List[dict]:
    """Every pending generic proposal created at or before ``as_of`` (``{id, kind, created_at}``), newest first.

    All of them: the count is taken before any list is shortened for display.
    """
    if not desk_store.ready(proposals.SCHEMA, proposals.MIGRATIONS):
        return []
    limit = as_of.astimezone(SHANGHAI)
    with desk_store.connect() as db:
        rows = db.execute("SELECT id, kind, created_at FROM proposals WHERE status='pending' AND expires_ts > ? "
                          "ORDER BY seq DESC", (time.time(),)).fetchall()
    found = []
    for row in rows:
        try:
            created = datetime.fromisoformat(row['created_at'])
        except (TypeError, ValueError):
            continue
        if created.tzinfo is None or created <= limit:
            found.append({'id': row['id'], 'kind': row['kind'], 'created_at': row['created_at']})
    return found


def short(text: Optional[str], limit: int = 60) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def day_text(day: str) -> str:
    return day[5:] if isinstance(day, str) and len(day) == 10 else str(day)


def as_date(text: str) -> date:
    return date.fromisoformat(text)
