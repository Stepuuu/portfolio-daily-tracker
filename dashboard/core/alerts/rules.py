"""Alert rules and their firing state, in ``desk.sqlite3``.

A rule is ``{id, ticker, account, kind, threshold, enabled, cooldown_seconds,
note, source, created_at, updated_at}``:

ticker            exchange-qualified, ``SHA:600000``; indices as ``IDX:000300``
kind              stop_loss | target_price | day_change_pct | position_weight_max
                  (see ``core.alerts.engine`` for when each one fires)
threshold         a decimal string. A price for stop_loss and target_price
                  (0 < t <= 100000000); a signed percentage for day_change_pct
                  (``-5`` = down 5 % or more, ``5`` = up 5 % or more; not 0,
                  between -100 and 100); a percentage of the whole portfolio
                  for position_weight_max (0 < t <= 100). At most 32
                  characters and 8 decimal places, ASCII digits; exponent
                  forms are stored as plain numbers (``9.5E0`` -> ``9.5``)
account           optional. For position_weight_max it narrows the weight to
                  that account's position; for other kinds it is a label
cooldown_seconds  minimum time between two fires of the rule (default 300)
source            who created it: ``manual``, ``agent:chat`` (confirmed
                  proposal), ``plan:<id>`` ...; set by code, never by a request

Ids are never reused (``AUTOINCREMENT``), so the notification key of a
deleted rule can never collide with a new one. A rule keeps the spelling it
was given, but rules are compared by listing (``engine.listing``, the shared
``identity_key``): ``HKG:700`` and ``HKG:00700``, ``NYSE:BRK-B`` and
``NYSE:BRK.B`` are the same rule for duplicates, filters and position weights.

Rules stored before that comparison existed can be duplicates of each other
(``HKG:700`` and ``HKG:00700`` at the same threshold). The first writer after
the upgrade (normally the monitor, before its first cycle) reconciles them
once, in one transaction: per (listing, account, kind, threshold) the oldest
enabled rule stays enabled and takes over the most advanced firing state of
the group (the latest day, the most fires, disarmed), so a crossing that a
duplicate already reported is not reported again; the others are disabled
with ``disabled_reason`` saying which rule they repeat, and one notification
lists them. Their fired events stay. Enabling such a rule again is refused as
a duplicate (409) while the kept rule exists. While it stays disabled it is no
rule's duplicate: it never blocks editing, disabling or re-enabling the kept
rule (nor creating or proposing the rule once the kept one is deleted), and
its own note can be changed.

``disabled_reason`` is ``null`` for a rule disabled by a person (or a plan);
it is cleared when the rule is enabled again.

The state ``{armed, last_fired_at, last_trading_date, fire_count_today}``
belongs to the monitor; it also stores, internally, the observation behind its
last change (the exchange time of every input, see ``core.alerts.engine``).
Changing a rule's threshold or enabling it again re-arms it and forgets that, so the
current price is judged against the new setting at once; the day's fire count
is kept, so the daily cap still holds and a fire number is never reused
within a day.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from core import desk_store, notify
from core.market.instruments import InstrumentError, parse

from . import engine, messages
from .engine import DAY_CHANGE_PCT, KIND_LABELS, KINDS, POSITION_WEIGHT_MAX, PRICE_KINDS, Rule, RuleState

SCHEMA = 'alerts'
MIGRATIONS = [[
    """CREATE TABLE alert_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        account TEXT,
        kind TEXT NOT NULL,
        threshold TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        cooldown_seconds INTEGER NOT NULL DEFAULT 300,
        note TEXT,
        source TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""",
    "CREATE INDEX alert_rules_by_source ON alert_rules(source)",
    """CREATE TABLE alert_state (
        rule_id INTEGER PRIMARY KEY,
        armed INTEGER NOT NULL DEFAULT 1,
        last_fired_at TEXT,
        last_trading_date TEXT,
        fire_count_today INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL)""",
    # One row per fire. Written in the same transaction as the state change
    # and the notification; UNIQUE mirrors the notification's dedupe key.
    """CREATE TABLE alert_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        name TEXT,
        account TEXT,
        kind TEXT NOT NULL,
        threshold TEXT NOT NULL,
        trading_date TEXT NOT NULL,
        n INTEGER NOT NULL,
        value TEXT,
        price TEXT,
        prev_close TEXT,
        quote_source TEXT,
        quote_time TEXT,
        quote_status TEXT,
        fired_at TEXT NOT NULL,
        fired_ts REAL NOT NULL,
        delivery TEXT NOT NULL,
        notification_seq INTEGER,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        UNIQUE (rule_id, trading_date, n))""",
    "CREATE INDEX alert_events_by_time ON alert_events(fired_ts)",
    # The monitor's heartbeat and its outage / digest bookkeeping: one row.
    """CREATE TABLE alert_monitor (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL)""",
], [
    # The exchange time of the observation behind the last state change; an
    # observation that is not newer may not change the state again.
    "ALTER TABLE alert_state ADD COLUMN observed_at TEXT",
    # Which monitor evaluates the rules: one at a time across processes.
    """CREATE TABLE alert_monitor_lease (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        owner TEXT NOT NULL,
        pid INTEGER,
        expires_ts REAL NOT NULL,
        acquired_at TEXT NOT NULL,
        renewed_at TEXT NOT NULL)""",
], [
    # The observation behind the last state change, as JSON (``engine.Observation``):
    # the exchange time of every input and, for a weight rule, the ledger revision.
    "ALTER TABLE alert_state ADD COLUMN observation TEXT",
], [
    # The lease's own length (an expiry further ahead than that was written
    # before the clock went back) and a renewal counter, which a standby
    # watches with its monotonic clock.
    "ALTER TABLE alert_monitor_lease ADD COLUMN lease_seconds REAL",
    "ALTER TABLE alert_monitor_lease ADD COLUMN beat INTEGER NOT NULL DEFAULT 0",
], [
    # Why the application disabled a rule (a duplicate found when rules came to be
    # compared by listing), and the record that such one-time reconciliations ran.
    "ALTER TABLE alert_rules ADD COLUMN disabled_reason TEXT",
    """CREATE TABLE alert_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL)""",
]]

FIELDS = ('ticker', 'kind', 'threshold', 'account', 'enabled', 'cooldown_seconds', 'note')
EDITABLE = ('threshold', 'account', 'enabled', 'cooldown_seconds', 'note')
MAX_RULES = 500
DEFAULT_COOLDOWN = 300
MAX_COOLDOWN = 86400
MAX_NOTE = 200
MAX_ACCOUNT = 240
MAX_ID = 2 ** 53
_SOURCE = re.compile(r'[a-z][a-z0-9_.:\-]{0,63}')


class AlertRuleError(ValueError):
    """The request cannot be carried out; the message is shown to the user."""


class RuleNotFound(AlertRuleError):
    pass


class RuleConflict(AlertRuleError):
    pass


desk_store.register_schema(SCHEMA, MIGRATIONS)


# The one-time reconciliation of rules that are duplicates by listing (see the module docstring).
RECONCILED_KEY = 'listing_duplicates_reconciled'
RECONCILE_VERSION = '1'


def ensure() -> None:
    """For writers: create or upgrade the alert tables, and reconcile duplicates of an older release once."""
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)
    with desk_store.connect() as db:
        if _reconciled(db):
            return
    with desk_store.transaction() as db:
        if _reconciled(db):
            return
        disabled = _reconcile(db)
        db.execute("INSERT INTO alert_meta(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE "
                   "SET value=excluded.value, updated_at=excluded.updated_at",
                   (RECONCILED_KEY, RECONCILE_VERSION, desk_store.now()))
        if disabled:
            title, body = messages.duplicates_disabled(disabled)
            notify.publish('alert.monitor', 'warning', title, body,
                           data={'event': 'duplicates_disabled', 'rule_ids': [rule_id for rule_id, _kept in disabled],
                                 'kept': [kept for _rule_id, kept in disabled], 'link': '/alerts'},
                           dedupe_key='alert:duplicates:' + ','.join(str(rule_id) for rule_id, _kept in disabled))


def _reconciled(db) -> bool:
    row = db.execute("SELECT value FROM alert_meta WHERE key=?", (RECONCILED_KEY,)).fetchone()
    return row is not None and row['value'] == RECONCILE_VERSION


def _same_threshold(text) -> str:
    try:
        return engine.plain(Decimal(str(text)))
    except (InvalidOperation, ValueError, ArithmeticError):
        return str(text)


_STATE_COLUMNS = ('armed', 'last_fired_at', 'last_trading_date', 'fire_count_today', 'observed_at', 'observation')


def _advanced(row) -> tuple:
    """How far a stored state has got: the later day, more fires that day, disarmed, the later fire."""
    return (row['last_trading_date'] or '', int(row['fire_count_today'] or 0), not row['armed'],
            row['last_fired_at'] or '')


def _reconcile(db) -> List[Tuple[int, int]]:
    """Disable every enabled rule that repeats an older one by listing; returns ``[(disabled id, kept id)]``."""
    groups: Dict[tuple, List[int]] = {}
    for row in db.execute("SELECT id, ticker, account, kind, threshold FROM alert_rules WHERE enabled=1 ORDER BY id"):
        key = (engine.listing(row['ticker']), row['account'], row['kind'], _same_threshold(row['threshold']))
        groups.setdefault(key, []).append(row['id'])
    disabled: List[Tuple[int, int]] = []
    stamp = desk_store.now()
    for ids in groups.values():
        if len(ids) < 2:
            continue
        kept, repeats = ids[0], ids[1:]
        marks = ','.join('?' * len(ids))
        states = {row['rule_id']: row for row in db.execute(
            f"SELECT rule_id, {', '.join(_STATE_COLUMNS)} FROM alert_state WHERE rule_id IN ({marks})", ids)}
        best = max(states.values(), key=_advanced, default=None)
        if best is not None and best['rule_id'] != kept and (
                kept not in states or _advanced(best) > _advanced(states[kept])):
            db.execute(f"INSERT INTO alert_state(rule_id, {', '.join(_STATE_COLUMNS)}, updated_at) "
                       f"VALUES (?,{','.join('?' * len(_STATE_COLUMNS))},?) ON CONFLICT(rule_id) DO UPDATE SET "
                       + ', '.join(f'{column}=excluded.{column}' for column in _STATE_COLUMNS)
                       + ', updated_at=excluded.updated_at',
                       (kept, *[best[column] for column in _STATE_COLUMNS], stamp))
        for rule_id in repeats:
            db.execute("UPDATE alert_rules SET enabled=0, disabled_reason=?, updated_at=? WHERE id=?",
                       (messages.duplicate_reason(kept), stamp, rule_id))
            disabled.append((rule_id, kept))
    return disabled


def available() -> bool:
    """For readers: whether the alert tables exist. Never creates or migrates the store."""
    return desk_store.ready(SCHEMA, MIGRATIONS)


def stored() -> bool:
    """Whether the alert tables were ever created, at any version. Never creates or migrates the store."""
    return desk_store.schema_version(SCHEMA) > 0


# -- validation ---------------------------------------------------------------

def _ticker(value) -> str:
    try:
        return parse(value).ticker
    except InstrumentError:
        raise AlertRuleError('代码格式不正确。请使用“交易所:代码”，例如 SHA:600000、SHE:000001、HKG:00700、'
                             'NASDAQ:AAPL；指数用 IDX:000300。') from None


def _kind(value) -> str:
    if value not in KINDS:
        raise AlertRuleError('提醒类型必须是 stop_loss（止损）、target_price（目标价）、day_change_pct（涨跌幅）'
                             '或 position_weight_max（仓位上限）。')
    return value


def decimal_text(number: Decimal) -> str:
    """Plain text without exponent or trailing zeros; never raises (``engine.plain``)."""
    return engine.plain(number)


# Plain or exponent notation in ASCII digits only. ``Decimal`` also takes
# Unicode digits, underscores, NaN and Infinity, none of which is a threshold.
_NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')
_NOT_A_NUMBER = '阈值必须是数字，例如 "9.5"。'
_OUT_OF_RANGE = (f'阈值超出支持的范围：绝对值不超过 1 亿（{engine.MAX_THRESHOLD}），'
                 f'最多 {engine.MAX_THRESHOLD_PLACES} 位小数。')


def _threshold(kind: str, value) -> str:
    """The threshold as a plain decimal string, or ``AlertRuleError``.

    Length, magnitude and decimal places are checked on the exact value before
    anything normalises it, so no input can expand, overflow or round: at most
    ``MAX_THRESHOLD_TEXT`` characters, magnitude at most ``MAX_THRESHOLD`` (and
    the narrower range of each kind), at most ``MAX_THRESHOLD_PLACES`` places.
    Exponent forms within those bounds are stored as plain numbers
    (``9.5E0`` -> ``9.5``).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise AlertRuleError(_NOT_A_NUMBER)
    if isinstance(value, int) and abs(value) > engine.MAX_THRESHOLD:
        raise AlertRuleError(_OUT_OF_RANGE)   # before str(): a huge int is slow to print, or refused
    text = str(value).strip()
    if len(text) > engine.MAX_THRESHOLD_TEXT:
        raise AlertRuleError(f'阈值过长：最多 {engine.MAX_THRESHOLD_TEXT} 个字符，例如 "9.5"。')
    if not _NUMBER.fullmatch(text):
        raise AlertRuleError(_NOT_A_NUMBER)
    try:
        number = Decimal(text)   # exact: the syntax above has no context-dependent part
    except (InvalidOperation, ValueError, ArithmeticError):
        raise AlertRuleError(_OUT_OF_RANGE) from None   # an exponent beyond what Decimal can represent
    if kind in PRICE_KINDS and number <= 0:
        raise AlertRuleError('止损价和目标价必须大于 0。')
    if kind in PRICE_KINDS and number > engine.MAX_THRESHOLD:
        raise AlertRuleError(f'阈值超出支持的范围：止损价和目标价不能超过 1 亿（{engine.MAX_THRESHOLD}）。')
    if kind == DAY_CHANGE_PCT and (number == 0 or not Decimal(-100) < number <= Decimal(100)):
        raise AlertRuleError('涨跌幅阈值是带符号的百分数：负数表示下跌（-5 即跌幅达到 5%），正数表示上涨；'
                             '不能为 0，且须在 -100 到 100 之间。')
    if kind == POSITION_WEIGHT_MAX and not Decimal(0) < number <= Decimal(100):
        raise AlertRuleError('仓位上限是占组合的百分比，必须大于 0 且不超过 100。')
    if engine.decimal_places(number) > engine.MAX_THRESHOLD_PLACES:
        raise AlertRuleError(f'阈值最多保留 {engine.MAX_THRESHOLD_PLACES} 位小数。')
    if not engine.threshold_supported(number):
        raise AlertRuleError(_OUT_OF_RANGE)
    return engine.plain(number)


def _account(value) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AlertRuleError('账户名称必须是文字。')
    value = value.strip()
    if len(value) > MAX_ACCOUNT:
        raise AlertRuleError(f'账户名称最多 {MAX_ACCOUNT} 个字符。')
    return value or None


def _cooldown(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COOLDOWN:
        raise AlertRuleError(f'冷却时间必须是 0 到 {MAX_COOLDOWN} 之间的整数秒。')
    return value


def _note(value) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AlertRuleError('备注必须是文字。')
    value = value.strip()
    if len(value) > MAX_NOTE:
        raise AlertRuleError(f'备注最多 {MAX_NOTE} 个字符。')
    return value or None


def _enabled(value) -> bool:
    if not isinstance(value, bool):
        raise AlertRuleError('enabled 必须是 true 或 false。')
    return value


def _source(value) -> str:
    if not isinstance(value, str) or not _SOURCE.fullmatch(value):
        raise ValueError("rule source must be a short lowercase name such as 'manual' or 'plan:12'")
    return value


def _unknown(payload: Mapping, allowed: Iterable[str]) -> None:
    extra = sorted(str(key) for key in payload if key not in allowed)
    if extra:
        raise AlertRuleError('不支持的字段：' + '、'.join(extra) + '。')


def validate(payload: Any, *, stored: bool = False) -> dict:
    """Check and normalise a new rule; returns every field. Writes nothing.

    Accepts its own output unchanged, so it can run again when a proposal is
    confirmed. A stock code that no source can quote (one in an index range of
    its exchange, ``SHA:000001``: ``symbol_problem``) is refused, as the picker
    refuses it; ``stored``: the rule exists already (``update``), and its
    ticker, which cannot change, is not judged again, so it stays editable.
    """
    if not isinstance(payload, Mapping):
        raise AlertRuleError('提醒规则必须是一个 JSON 对象。')
    _unknown(payload, FIELDS)
    for name in ('ticker', 'kind', 'threshold'):
        if payload.get(name) in (None, ''):
            raise AlertRuleError({'ticker': '请填写代码。', 'kind': '请选择提醒类型。', 'threshold': '请填写阈值。'}[name])
    ticker, kind = _ticker(payload['ticker']), _kind(payload['kind'])
    unquotable = None if stored else parse(ticker).symbol_problem
    if unquotable:
        raise AlertRuleError(f'不能为这个代码设置提醒：{unquotable}。')
    if kind == POSITION_WEIGHT_MAX and ticker.startswith('IDX:'):
        raise AlertRuleError('指数不是持仓，不能设置仓位上限提醒。')
    return {'ticker': ticker, 'kind': kind, 'threshold': _threshold(kind, payload['threshold']),
            'account': _account(payload.get('account')),
            'enabled': _enabled(payload.get('enabled', True)),
            'cooldown_seconds': _cooldown(payload.get('cooldown_seconds', DEFAULT_COOLDOWN)),
            'note': _note(payload.get('note'))}


# -- rows ---------------------------------------------------------------------

_SELECT = ("SELECT r.*, s.armed AS s_armed, s.last_fired_at AS s_last_fired_at, "
           "s.last_trading_date AS s_last_trading_date, s.fire_count_today AS s_fire_count_today, "
           "s.observed_at AS s_observed_at, s.observation AS s_observation "
           "FROM alert_rules r LEFT JOIN alert_state s ON s.rule_id = r.id")


def _item(row) -> dict:
    armed = row['s_armed']
    return {
        'id': row['id'], 'ticker': row['ticker'], 'account': row['account'], 'kind': row['kind'],
        'kind_label': KIND_LABELS.get(row['kind'], row['kind']), 'threshold': row['threshold'],
        'condition': engine.describe_condition(row['kind'], Decimal(row['threshold'])),
        'enabled': bool(row['enabled']), 'cooldown_seconds': row['cooldown_seconds'], 'note': row['note'],
        'source': row['source'], 'created_at': row['created_at'], 'updated_at': row['updated_at'],
        'disabled_reason': row['disabled_reason'],
        'state': {'armed': True if armed is None else bool(armed), 'last_fired_at': row['s_last_fired_at'],
                  'last_trading_date': row['s_last_trading_date'],
                  'fire_count_today': row['s_fire_count_today'] or 0},
    }


def to_rule(item: Mapping) -> Rule:
    return Rule(id=item['id'], ticker=item['ticker'], kind=item['kind'], threshold=Decimal(item['threshold']),
                account=item.get('account'), enabled=bool(item['enabled']),
                cooldown_seconds=int(item['cooldown_seconds']), note=item.get('note'),
                source=item.get('source') or 'manual')


def to_state(item: Mapping) -> RuleState:
    state = item.get('state') or {}
    fired = state.get('last_fired_at')
    day = state.get('last_trading_date')
    return RuleState(armed=bool(state.get('armed', True)),
                     last_fired_at=datetime.fromisoformat(fired) if fired else None,
                     last_trading_date=date.fromisoformat(day) if day else None,
                     fire_count_today=int(state.get('fire_count_today') or 0))


def _row(db, rule_id) -> Optional[dict]:
    if isinstance(rule_id, bool) or not isinstance(rule_id, int) or not 0 < rule_id <= MAX_ID:
        return None
    row = db.execute(_SELECT + " WHERE r.id = ?", (rule_id,)).fetchone()
    return _item(row) if row is not None else None


def _require(db, rule_id) -> dict:
    item = _row(db, rule_id)
    if item is None:
        raise RuleNotFound('提醒规则不存在或已被删除。请刷新列表。')
    return item


def reconciled_duplicate(item: Mapping) -> bool:
    """Whether a rule is one the upgrade disabled as a repeat of another: only that sets ``disabled_reason``."""
    return not item['enabled'] and item['disabled_reason'] is not None


def _duplicate(db, rule: Mapping, exclude: Optional[int] = None) -> Optional[int]:
    """The id of a rule with the same listing (any spelling), kind, threshold and account, or ``None``.

    A rule the upgrade disabled as a repeat (``reconciled_duplicate``) is nobody's duplicate."""
    listing = engine.listing(rule['ticker'])
    rows = db.execute("SELECT id, ticker FROM alert_rules WHERE kind=? AND threshold=? AND account IS ? "
                      "AND id IS NOT ? AND NOT (enabled=0 AND disabled_reason IS NOT NULL) ORDER BY id",
                      (rule['kind'], rule['threshold'], rule['account'], exclude))
    return next((row['id'] for row in rows if engine.listing(row['ticker']) == listing), None)


# -- public functions ---------------------------------------------------------

def create(payload: Any, source: str = 'manual') -> dict:
    """Store a new rule (armed) and return it."""
    rule = validate(payload)
    source = _source(source)
    ensure()
    with desk_store.transaction() as db:
        if db.execute("SELECT COUNT(*) FROM alert_rules").fetchone()[0] >= MAX_RULES:
            raise AlertRuleError(f'提醒规则已达上限 {MAX_RULES} 条，请先删除不需要的规则。')
        same = _duplicate(db, rule)
        if same is not None:
            raise RuleConflict(f'已有相同的提醒规则（#{same}）。')
        stamp = desk_store.now()
        cursor = db.execute(
            "INSERT INTO alert_rules(ticker, account, kind, threshold, enabled, cooldown_seconds, note, source, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rule['ticker'], rule['account'], rule['kind'], rule['threshold'], int(rule['enabled']),
             rule['cooldown_seconds'], rule['note'], source, stamp, stamp))
        return _require(db, cursor.lastrowid)


def get(rule_id: int) -> dict:
    if not available():
        raise RuleNotFound('提醒规则不存在或已被删除。请刷新列表。')
    with desk_store.connect() as db:
        return _require(db, rule_id)


def list_rules(*, enabled: Optional[bool] = None, source: Optional[str] = None,
               ticker: Optional[str] = None) -> List[dict]:
    """Rules with their state, oldest first. ``ticker`` matches the listing in any spelling (``HKG:700``
    finds a rule stored as ``HKG:00700``, ``NYSE:BRK-B`` one stored as ``NYSE:BRK.B``)."""
    where, parameters = [], []
    listing = engine.listing(_ticker(ticker)) if ticker is not None else None
    if enabled is not None:
        where.append('r.enabled = ?')
        parameters.append(int(bool(enabled)))
    if source is not None:
        where.append('r.source = ?')
        parameters.append(source)
    if not available():
        return []
    with desk_store.connect() as db:
        query = _SELECT + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY r.id'
        return [_item(row) for row in db.execute(query, parameters)
                if listing is None or engine.listing(row['ticker']) == listing]


def update(rule_id: int, changes: Any) -> dict:
    """Change ``threshold``, ``account``, ``enabled``, ``cooldown_seconds`` or ``note``.

    A new threshold, or enabling a disabled rule, re-arms it. Enabling clears
    ``disabled_reason``.
    """
    if not isinstance(changes, Mapping):
        raise AlertRuleError('修改内容必须是一个 JSON 对象。')
    if 'ticker' in changes or 'kind' in changes:
        raise AlertRuleError('不能修改代码或提醒类型；请删除这条规则后重新创建。')
    _unknown(changes, EDITABLE)
    if not changes:
        raise AlertRuleError('没有要修改的内容。')
    ensure()
    with desk_store.transaction() as db:
        current = _require(db, rule_id)
        merged = validate({**{name: current[name] for name in FIELDS}, **changes}, stored=True)
        # A reconciled duplicate that stays disabled keeps its reason, so it stays nobody's duplicate.
        kept_aside = not merged['enabled'] and reconciled_duplicate(current)
        same = None if kept_aside else _duplicate(db, merged, exclude=rule_id)
        if same is not None:
            raise RuleConflict(f'已有相同的提醒规则（#{same}）。')
        db.execute("UPDATE alert_rules SET threshold=?, account=?, enabled=?, cooldown_seconds=?, note=?, "
                   "disabled_reason=?, updated_at=? WHERE id=?",
                   (merged['threshold'], merged['account'], int(merged['enabled']), merged['cooldown_seconds'],
                    merged['note'], None if merged['enabled'] else current['disabled_reason'], desk_store.now(),
                    rule_id))
        rearm = (merged['threshold'] != current['threshold'] or merged['account'] != current['account']
                 or (merged['enabled'] and not current['enabled']))
        if rearm:
            db.execute("UPDATE alert_state SET armed=1, observed_at=NULL, observation=NULL, updated_at=? "
                       "WHERE rule_id=?",
                       (desk_store.now(), rule_id))
        return _require(db, rule_id)


def delete(rule_id: int) -> dict:
    """Remove a rule and its state; returns what was removed. Its past events stay."""
    ensure()
    with desk_store.transaction() as db:
        item = _require(db, rule_id)
        db.execute("DELETE FROM alert_state WHERE rule_id=?", (rule_id,))
        db.execute("DELETE FROM alert_rules WHERE id=?", (rule_id,))
        return item


def disable_by_source(source: str) -> int:
    """Disable every enabled rule created by ``source`` (a trade plan closing); returns how many."""
    source = _source(source)
    ensure()
    with desk_store.transaction() as db:
        return db.execute("UPDATE alert_rules SET enabled=0, updated_at=? WHERE source=? AND enabled=1",
                          (desk_store.now(), source)).rowcount


# -- used by the monitor inside its own transaction ---------------------------

def _observed(text) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None
    return moment if moment is not None and moment.tzinfo is not None else None


def _observation(text) -> Optional[engine.Observation]:
    try:
        return engine.Observation.from_json(json.loads(text)) if isinstance(text, str) else None
    except ValueError:
        return None


def load_for_update(db, rule_id: int) -> Optional[Tuple[Rule, RuleState]]:
    """The rule and its state as stored now (with its observation), or ``None`` when it was deleted."""
    if isinstance(rule_id, bool) or not isinstance(rule_id, int) or not 0 < rule_id <= MAX_ID:
        return None
    row = db.execute(_SELECT + " WHERE r.id = ?", (rule_id,)).fetchone()
    if row is None:
        return None
    item = _item(row)
    return to_rule(item), replace(to_state(item), observed_at=_observed(row['s_observed_at']),
                                  observation=_observation(row['s_observation']))


def save_state(db, rule_id: int, state: RuleState) -> None:
    observed = state.observed_at.astimezone(timezone.utc).isoformat() if state.observed_at else None
    observation = desk_store.encode(state.observation.to_json()) if state.observation is not None else None
    db.execute(
        "INSERT INTO alert_state(rule_id, armed, last_fired_at, last_trading_date, fire_count_today, observed_at, "
        "observation, updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(rule_id) DO UPDATE SET armed=excluded.armed, "
        "last_fired_at=excluded.last_fired_at, last_trading_date=excluded.last_trading_date, "
        "fire_count_today=excluded.fire_count_today, observed_at=excluded.observed_at, "
        "observation=excluded.observation, updated_at=excluded.updated_at",
        (rule_id, int(state.armed), state.last_fired_at.isoformat(timespec='seconds') if state.last_fired_at else None,
         state.last_trading_date.isoformat() if state.last_trading_date else None, state.fire_count_today,
         observed, observation, desk_store.now()))


def counts() -> Dict[str, int]:
    if not available():
        return {'total': 0, 'enabled': 0}
    with desk_store.connect() as db:
        row = db.execute("SELECT COUNT(*) AS total, COALESCE(SUM(enabled), 0) AS enabled FROM alert_rules").fetchone()
        return {'total': row['total'], 'enabled': row['enabled']}


# ``rules.list`` is the public name; bound last so nothing here shadows the built-in.
list = list_rules
