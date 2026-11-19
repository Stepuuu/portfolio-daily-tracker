"""The background task that checks alert rules while their market trades.

One cycle (every ``interval`` seconds, 5 by default):

1. read the enabled rules;
2. decide, per market, whether to look at all: only in continuous trading by
   the calendar. Outside it (nights, weekends, lunch break, call auction)
   nothing is fetched. On a weekday the holiday table calls a holiday, the
   monitor *fails open*: during normal session hours it probes the quotes
   once every ``probe_interval`` seconds, and when their exchange time is
   from today and still advancing it treats the session as open for the
   rest of the day, says so once in a notification and in the status;
3. fetch every needed quote in one batched ``QuoteService.aget`` call (plus
   the currency rates when a position-weight rule needs a valuation). A
   weight rule values the confirmed ledger as of today, read-only, exactly as
   the Today page does (``core.alerts.holdings``): an event dated tomorrow
   does not count yet;
4. record the cycle: three failed cycles in a row (the request failed, or
   no rule had a usable quote) publish one "行情中断，提醒暂停" notice, and
   the first good cycle after it one "提醒已恢复" notice. Pause notices are
   at least ``PAUSE_NOTICE_SPACING`` apart; a shorter repeat outage shows in
   the status only;
5. read the rules and their states again (one read, after the quotes came
   back: a rule edited while they were fetched is judged as edited, on the
   quotes just fetched, in this cycle) and decide each one on that; for each
   rule whose state would change, most important kind first
   (``engine.PRIORITY``: stop_loss, position_weight_max, target_price,
   day_change_pct), in its own ``desk_store.transaction()``: re-read the rule
   and its state once more, decide again on what is stored then, store the
   new state and, when it fires, the event row and the notification
   (``dedupe_key=alert:{rule_id}:{trading_date}:{n}``). State and
   notification commit together, so a restart can never fire twice. A fire
   is pushed on its own only within the global cap: ``GLOBAL_CAP``
   notifications per ``GLOBAL_WINDOW``, of which ``STOP_LOSS_RESERVE`` are
   kept for stop-losses; beyond it the fire is recorded as ``suppressed``;
6. if fires were held back, publish one digest, at most one per window. It
   lists stop-losses first and is ``critical`` when it holds one.

The heartbeat (``last_cycle_at``, ``last_ok_at``, ``rules_active``,
``status`` ...) and the outage and digest bookkeeping live in one row of
``alert_monitor``, so they survive a restart. ``status()`` reads it.
``last_cycle_at`` and ``last_ok_at`` are times of the session clock, like
every other time the monitor records (fires, skips, outages); ``beat_at``,
the wall-clock time of the write, is what the health check and the lease
compare with the wall clock.

One monitor at a time. Two backends on the same portfolio (or a restart that
overlaps the old process) would each evaluate the rules. Every cycle first
takes or renews a lease, one row of ``alert_monitor_lease`` (owner id, pid,
its length ``max(30 s, 6 x interval)``, the expiry that far ahead of the wall
clock, and a renewal counter). A monitor that finds a valid lease of another
owner stands by: it fetches nothing, writes nothing (not the heartbeat
either) and tries again next cycle; ``status()`` of its process reports
``standby``. Every write a cycle makes (the outage bookkeeping, each rule's
state, the digest) checks inside its transaction that the lease still names
this monitor, so a monitor that stalled past its expiry while another took
over writes nothing when it resumes. Stopping releases the lease, so a
standby monitor takes over at its next cycle. A lease row of the release
before lease lengths (``lease_seconds`` empty) is as long as its expiry is
after its renewal (``renewed_at``), at least ``LEASE_MIN_SECONDS``.

Another owner's lease is valid only while all three hold: its expiry is
after the wall clock's now; it is not more than one lease length (plus a
second) after now (a later expiry was written before the clock went back);
and this monitor has seen it renewed within the last lease length of its own
``time.monotonic()`` clock. What that guarantees between processes on one
machine, whatever the wall clock does (steps back or forward, stands still):

* at most one monitor writes at any moment: the lease row is the arbiter
  and it is checked inside every write transaction (SQLite serialises them),
  so no clock is involved in that;
* a standby takes over at most one lease length plus one interval after the
  last renewal it saw (a backward step makes the old expiry invalid at once;
  otherwise the monotonic watch decides);
* a forward step (or a holder that stalls past its lease) can hand the lease
  to a standby early while the holder is alive; the holder's next write is
  refused and it stands by. A hand-over never makes two writers.

The heartbeat is healthy only when ``beat_at`` lies within
``max(3 x interval, 30 s)`` before now; one dated in the future is not
(the clock went back after it was written), unless the monitor that holds
the lease runs in this very process and its own monotonic clock says it
wrote within that window. ``clock_warning`` in the status says so.

Cycles are numbered (``cycle_seq`` in the heartbeat row, taken with the
lease, before anything is read), and the first write of a cycle records its
number as ``applied_cycle``. A cycle whose number is below ``applied_cycle``
read its rules, ledger and quotes before a newer cycle began writing: it is
*superseded* and writes nothing from then on. Every write of a cycle (the
heartbeat included) checks inside its transaction that the lease still names
this monitor, that no newer cycle has begun, and that the cycle's data is
still current: the session date of every market it looks at is the one of
the cycle's start, and no quote (rate) that was live or delayed when it was
fetched has become older than ``DELAYED_MAX_AGE`` (``FX_DELAYED_MAX_AGE``)
since; a cycle without such a quote may write for one lease length
(``lease_seconds``, at least 30 s) after it began. A cycle that stalled
longer, or over a change of session date (a process paused overnight), is
*stale*: it writes nothing, not even its heartbeat, and the next cycle
fetches anew. A write that finds the lease taken over writes nothing either.
Independently, each
rule's state stores the observation behind its last change, and the engine
drops a change whose inputs are older than that (see ``core.alerts.engine``).

Decision table: what one cycle does with one enabled rule, the first row that
matches decides. ``action`` is what ``CycleReport.actions`` says.

===  ==========================================================  ===================================
row  when                                                        then
===  ==========================================================  ===================================
1    another monitor holds a valid lease                         standby: nothing fetched, evaluated
                                                                 or written (no action)
2    the rule's market is not in continuous trading (and has     not evaluated (no action; status
     no fail-open today)                                         ``waiting``)
3    the quote request failed, or every evaluated rule hit a     failed cycle: nothing evaluated (no
     quote failure (``engine.QUOTE_FAILURES``)                   action); three in a row pause
4    a newer cycle has begun writing (``applied_cycle`` above    superseded: this cycle writes
     this cycle's number), or this cycle's data is out of date   nothing more; the rule it was
     (a market's session date changed since the cycle began,     about to write gets action
     or a quote that was live or delayed then is now too old)    ``superseded`` / ``stale_cycle``
                                                                 (cycle status the same), later
                                                                 rules none
5    the rule was deleted or disabled since the cycle began      ``deleted`` / ``disabled``
     (it is read again after the quotes came back, and once
     more in the write transaction; an edited threshold or
     account is judged as edited, on this cycle's quotes)
6    its stored threshold is out of range                        ``skip`` invalid_threshold
                                                                 (also ``errored_rule_ids``)
7    its quote cannot be used: none, unavailable, stale, no      ``skip`` with the quote's reason
     exchange time, too old; for price kinds, no trade today
8    weight rule: the valuation is unavailable or not verified   ``skip`` weight_unavailable /
                                                                 weight_estimated
9    the session date is earlier than the one stored             ``skip`` date_regressed
10   the result changes nothing: not met, already fired,         ``none`` / ``wait``; nothing written
     inside the re-arm band, daily cap, cooldown
11   it would change the state, but one of its inputs is older  ``skip`` stale_observation
     than the same input behind the last change: an earlier
     exchange time, or for a verified close an earlier session
     (a stored time beyond now + 2 minutes is ignored; the
     release conditions are in ``core.alerts.engine``)
12   otherwise                                                   ``fire`` / ``rearm`` / ``none`` (a
                                                                 new day re-arms), written with its
                                                                 observation in one transaction
===  ==========================================================  ===================================

Every ``skip`` (rows 6-9 and 11) is kept in the heartbeat row (``rule_skips``,
with the time it began) and shown by ``GET /api/alerts/status``
(``skipped_rules``) and the rule view (``skip_code``, ``skip_reason``,
``skipped_since``). ``since`` is when the current reason began,
``held_since`` when the rule began to be skipped at all, whatever the reasons
in between. An entry stays while its market is closed, so both span the lunch
break and the night: a weight rule paused for weeks by one suspended holding
shows since when. A weight rule held back for 30 minutes or more in a row,
for any reasons (``held_since``), is also announced in one ``alert.monitor``
notification (``event: weight_rules_held``), at most one a day: such a pause
hides behind every other rule working normally, and the status page is not
something one watches.

A weight rule's observation covers everything its weight depends on and
nothing else: the quote of every holding with a quantity, the rate of every
currency other than CNY that is held (as cash other than zero, or as the
currency of a holding) and the ledger revision; so a cash withdrawal or a
rate change under an unchanged quote is evaluated like a new price, and a
currency without cash cannot hold a crossing back. A verified close is one
observation per session (``engine.quote_observed``).

Nothing here can take the application down: every cycle is guarded, errors
are logged by type only (never prices, amounts or tickers), and a cycle that
raises counts as a failed one. What reaches the status and the notifications
about an error is a fixed text (``core.provider_errors``), never an
exception's class name or message. No order is ever placed: a fire only
stores a notification.

Environment: ``TRACKER_ALERTS_ENABLED=0`` keeps the monitor from starting;
``TRACKER_ALERT_INTERVAL_SECONDS`` sets the cycle interval (1-300).
The read-only demo (``TRACKER_DEMO_MODE=1``) never starts it: it would write its heartbeat and could
fire alerts into data that must not change. Its status says ``demo`` and ``running: false``, whatever
heartbeat another process left in the directory.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from core import desk_store, notify, provider_errors
from core.market.calendar import SESSIONS, SHANGHAI, ZONES, SessionClock
from core.market.instruments import MARKETS
from core.market.quotes import (DELAYED, DELAYED_MAX_AGE, FUTURE_TOLERANCE, FX_DELAYED_MAX_AGE, LIVE, PREV_CLOSE,
                                LiveQuote)
from core.market.runtime import MarketRuntime, get_market_runtime
from core.market.valuation import value_portfolio

from . import engine, events, holdings, messages, rules
from .engine import POSITION_WEIGHT_MAX, QUOTE_FAILURES, SKIP, STOP_LOSS, Context, Observation, Rule

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 5.0
MIN_INTERVAL, MAX_INTERVAL = 1.0, 300.0
PROBE_INTERVAL = 60.0
# A lease may expire at most this long after one lease length from now (rounding).
LEASE_CLOCK_SLACK = 1.0
# A heartbeat this far ahead of the clock is dated in the future: the clock went back.
HEARTBEAT_FUTURE_SLACK = timedelta(seconds=1)
FAIL_OPEN_FRESHNESS = timedelta(minutes=5)
FAILURES_BEFORE_PAUSE = 3
PAUSE_NOTICE_SPACING = timedelta(minutes=30)
GLOBAL_CAP = 20
GLOBAL_WINDOW = timedelta(minutes=10)
# Of the GLOBAL_CAP individual notifications per window, this many are kept
# for stop-losses: other kinds are pushed one by one only while fewer than
# GLOBAL_CAP - STOP_LOSS_RESERVE (15) alerts of any kind went out in the
# window, stop-losses while fewer than GLOBAL_CAP (20). A quarter of the
# budget: a storm of target prices or daily moves can never take the last
# five slots, and a storm of stop-losses still gets every slot.
STOP_LOSS_RESERVE = 5
# A weight rule held back this long (unverified or unavailable valuation) is
# reported in one notification, at most one a day (``_held_notice``).
HELD_NOTICE_AFTER = timedelta(minutes=30)
HELD_CODES = frozenset({engine.WEIGHT_ESTIMATED, engine.WEIGHT_UNAVAILABLE})
LEASE_MIN_SECONDS = 30.0
ENV_ENABLED = 'TRACKER_ALERTS_ENABLED'
ENV_INTERVAL = 'TRACKER_ALERT_INTERVAL_SECONDS'

# Per-market modes of one cycle.
SESSION, FAIL_OPEN, PROBE, CLOSED = 'session', 'fail_open', 'probe', 'closed'
# Heartbeat statuses.
OK, DEGRADED, PAUSED, WAITING, NO_RULES, ERROR, STOPPED = 'ok', 'degraded', 'paused', 'waiting', 'no_rules', 'error', 'stopped'
NOT_STARTED, DISABLED, UNRESPONSIVE = 'not_started', 'disabled', 'unresponsive'
# A monitor without the lease (cycle report and the status of its process).
STANDBY = 'standby'
# Quotes were fine, but some rules could not be evaluated (``errored_rule_ids``).
RULE_ERRORS = 'rule_errors'
# Roles of a monitor.
ACTIVE = 'active'
# ``_apply_one`` found that another monitor holds the lease now.
LEASE_LOST = 'lease_lost'
# A newer cycle has begun writing: this one writes nothing more (cycle report status and rule action).
SUPERSEDED = 'superseded'
# This cycle's data is out of date (decision table row 4): it writes nothing more.
STALE_CYCLE = 'stale_cycle'
# The status of a process in the read-only demo, which never runs the monitor.
DEMO = 'demo'
# Who holds a live lease (``status()['lease_holder']``).
THIS_PROCESS, ANOTHER_PROCESS = 'this_process', 'another_process'

# Fixed texts for errors (``core.provider_errors`` for the quote request): never an exception's text.
QUOTE_REQUEST_FAILED = '行情请求出错：{}'
VALUATION_FAILED = '估值出错，暂不按仓位提醒（详情见服务器日志）'
CYCLE_FAILED = '提醒检查出错（详情见服务器日志）'

GUARANTEE_NOTICE = ('提醒只在本机后端运行、且相关市场处于交易时段时检查，依赖行情源及时更新；'
                    '后端关闭或行情中断期间不会提醒。它不是止损委托，不会下单，也不保证一定送达。')

_instances: 'weakref.WeakSet[AlertMonitor]' = weakref.WeakSet()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: Optional[datetime]) -> Optional[str]:
    return moment.astimezone(SHANGHAI).isoformat(timespec='seconds') if moment is not None else None


def _parse(text) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


def enabled() -> bool:
    return os.environ.get(ENV_ENABLED, '1').strip().lower() not in ('0', 'false', 'no', 'off')


def demo_mode() -> bool:
    """The read-only demo: the monitor neither fires alerts nor writes its heartbeat there."""
    return os.environ.get('TRACKER_DEMO_MODE') == '1'


def configured_interval() -> float:
    raw = os.environ.get(ENV_INTERVAL, '').strip()
    if not raw:
        return DEFAULT_INTERVAL
    try:
        value = float(raw)
    except ValueError:
        logger.warning('%s is not a number; using %s seconds', ENV_INTERVAL, DEFAULT_INTERVAL)
        return DEFAULT_INTERVAL
    return min(max(value, MIN_INTERVAL), MAX_INTERVAL)


def in_session_hours(market: str, moment: datetime) -> bool:
    """Weekday and inside continuous-trading hours, whatever the holiday table says."""
    local = moment.astimezone(ZONES[market])
    return local.weekday() < 5 and any(start <= local.time() < end for start, end, _ in SESSIONS[market])


# -- the persisted heartbeat row ----------------------------------------------

def _load(db) -> dict:
    row = db.execute("SELECT data FROM alert_monitor WHERE id=1").fetchone()
    if row is None:
        return {}
    try:
        data = json.loads(row['data'])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _save(db, data: dict) -> None:
    db.execute("INSERT INTO alert_monitor(id, data, updated_at) VALUES (1, ?, ?) "
               "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
               (desk_store.encode(data), desk_store.now()))


# The heartbeat of a monitor that has nothing stored yet. A monitor with no rules must not
# create the store just to say that it is idle, so until the first rule is written the
# heartbeat lives here, in the process that runs the monitor, keyed by the store it stands for
# (the store follows PORTFOLIO_DIR, which can change between calls).
_idle_heartbeats: Dict[str, dict] = {}


def _idle_heartbeat() -> dict:
    return _idle_heartbeats.setdefault(str(desk_store.path()), {})


def heartbeat() -> dict:
    """The stored heartbeat row (empty before the first cycle). Never creates the store."""
    if not rules.available():
        return dict(_idle_heartbeat())
    with desk_store.connect() as db:
        return _load(db)


def _lease_row(db):
    return db.execute("SELECT owner, pid, expires_ts, acquired_at, renewed_at, lease_seconds, beat "
                      "FROM alert_monitor_lease WHERE id=1").fetchone()


def lease_length(row: Mapping) -> float:
    """The length of a stored lease.

    A row of the release before lease lengths has none: its length is how far
    its expiry lies after its renewal (both written by the same renewal, so a
    later step of the clock does not change it), between ``LEASE_MIN_SECONDS``
    and the longest a lease of that release could be.
    """
    length = row['lease_seconds']
    if length:
        return float(length)
    renewed = _parse(row['renewed_at'])
    if renewed is None or renewed.tzinfo is None:
        return LEASE_MIN_SECONDS
    derived = float(row['expires_ts']) - renewed.timestamp()
    return min(max(derived, LEASE_MIN_SECONDS), max(LEASE_MIN_SECONDS, 6 * MAX_INTERVAL))


def lease_dated_ahead(row: Mapping, now_ts: float) -> bool:
    """The expiry lies more than one lease length after now: written before the clock went back."""
    return float(row['expires_ts']) > now_ts + lease_length(row) + LEASE_CLOCK_SLACK


def lease_valid(row: Mapping, now_ts: float) -> bool:
    """By the wall clock: the expiry is after now, and not more than one lease length after it."""
    return now_ts < float(row['expires_ts']) and not lease_dated_ahead(row, now_ts)


def lease() -> Optional[dict]:
    """The stored lease ``{owner, pid, expires_ts, acquired_at, renewed_at, lease_seconds, beat}`` or ``None``.

    Never creates the store."""
    if not rules.available():
        return None
    with desk_store.connect() as db:
        row = _lease_row(db)
    return dict(row) if row is not None else None


def session_open(clock: SessionClock, market: str, data: Optional[Mapping] = None) -> bool:
    """Whether rules of ``market`` are checked now (calendar session, or today's fail-open)."""
    if clock.is_trading(market):
        return True
    fail_open = (data or {}).get('fail_open') or {}
    return (fail_open.get(market) == clock.local_date(market).isoformat()
            and in_session_hours(market, clock.now()))


def weight_observation(valuation, rates: Optional[Mapping], revision: Optional[int]) -> Observation:
    """Everything a weight depends on, and nothing else: the quote of each holding with a quantity, the
    rate of each currency held (cash other than zero, or a holding's currency), the ledger revision."""
    times: Dict[str, Optional[datetime]] = {}
    currencies = set()
    for account in (valuation.accounts if valuation is not None else ()):
        currencies.update(currency for currency, balance in account.cash.items() if balance)
        for held in account.positions:
            if not held.quantity:
                continue   # nothing held: its price changes no weight
            currencies.add(held.currency)
            observed = engine.quote_observed(held.ticker, held.quote)
            if observed is not None:
                times[observed[0]] = observed[1]
    for currency in sorted(currencies - {'CNY', None}):
        rate = (rates or {}).get(currency)
        if rate is not None:
            times[engine.fx_input(currency)] = getattr(rate, 'quote_time', None)
    return Observation.of(times, revision)


def observations(active: Sequence[Rule], quotes: Mapping[str, LiveQuote], valuation=None,
                 rates: Optional[Mapping] = None, revision: Optional[int] = None) -> Dict[int, Observation]:
    """What each rule's decision rests on, by rule id (see ``engine.Observation``).

    A price or daily-move rule looks at its own quote; a weight rule at the
    whole valuation (``weight_observation``).
    """
    weights = weight_observation(valuation, rates, revision)
    observed: Dict[int, Observation] = {}
    for rule in active:
        if rule.kind == POSITION_WEIGHT_MAX:
            observed[rule.id] = weights
            continue
        own = engine.quote_observed(rule.ticker, quotes.get(rule.ticker))
        if own is not None:
            observed[rule.id] = Observation.of({own[0]: own[1]})
    return observed


# Skip codes that only the monitor can tell (they depend on the stored state
# and the cycle); the rule view computes the others from fresh quotes itself.
MONITOR_SKIPS = frozenset({engine.STALE_OBSERVATION, 'date_regressed'})


def recent_skips(data: Mapping, now: datetime) -> Dict[int, dict]:
    """The skips of the latest cycle by rule id, when that cycle is recent by the session clock ``now``; else ``{}``."""
    return _skips(data) if _fresh(data, now, CYCLE_FIELD) else {}


def _skips(data: Mapping) -> Dict[int, dict]:
    skips = data.get('rule_skips')
    result = {}
    for key, entry in (skips.items() if isinstance(skips, dict) else ()):
        if isinstance(entry, dict) and str(key).isdigit():
            result[int(key)] = entry
    return result


def _window(data: Mapping) -> timedelta:
    return timedelta(seconds=max(3 * float(data.get('interval_seconds') or DEFAULT_INTERVAL), 30))


# The heartbeat's times: when the latest cycle ran by the session clock, and when the row was written by the
# wall clock (the health check's). A row of the previous release has only ``last_cycle_at``, a wall time.
CYCLE_FIELD, BEAT_FIELD = 'last_cycle_at', 'beat_at'


def _stamp(data: Mapping, name: str) -> Optional[datetime]:
    moment = _parse(data.get(name))
    if moment is None and name == BEAT_FIELD:
        moment = _parse(data.get(CYCLE_FIELD))
    return moment


def _dated_ahead(data: Mapping, now: datetime, name: str = BEAT_FIELD) -> bool:
    moment = _stamp(data, name)
    return moment is not None and moment - now > HEARTBEAT_FUTURE_SLACK


def _fresh(data: Mapping, now: datetime, name: str = BEAT_FIELD) -> bool:
    """Written within the window before ``now`` (of the clock ``name`` was taken with); never when dated ahead."""
    moment = _stamp(data, name)
    return (moment is not None and not _dated_ahead(data, now, name)
            and now - moment <= _window(data))


@dataclass
class CycleReport:
    """What one cycle did; for tests and logs (no prices)."""
    status: str = ''
    modes: Dict[str, str] = field(default_factory=dict)
    fetched: List[str] = field(default_factory=list)
    failed: bool = False
    actions: Dict[int, str] = field(default_factory=dict)


class AlertMonitor:
    def __init__(self, runtime: Optional[MarketRuntime] = None, *, interval: float = DEFAULT_INTERVAL,
                 probe_interval: float = PROBE_INTERVAL, ledger: Optional[Callable] = None,
                 wall: Optional[Callable[[], datetime]] = None, owner: Optional[str] = None,
                 lease_seconds: Optional[float] = None, monotonic: Optional[Callable[[], float]] = None):
        """``runtime`` defaults to ``get_market_runtime()`` on every cycle; ``wall`` is the
        clock of the heartbeat's health and of the lease (real time by default). ``ledger``,
        when given, returns a ``core.ledger.Ledger`` (or ``None``) to value instead of the
        portfolio's own ledger, which is read read-only (``core.alerts.holdings``); either
        is replayed as of today. ``owner`` names this monitor in the lease (a random id by
        default); ``lease_seconds`` overrides ``max(30, 6 x interval)``; ``monotonic`` is the
        clock of the lease watch and of this process's own heartbeat (``time.monotonic``)."""
        self._runtime = runtime
        self.interval = min(max(float(interval), 0.01), MAX_INTERVAL)
        self.probe_interval = probe_interval
        self._ledger = ledger
        self._wall = wall or _utc_now
        self.owner = owner or uuid.uuid4().hex
        self._lease_seconds = lease_seconds
        self._monotonic = monotonic or time.monotonic
        # Another owner's lease as last seen: ((owner, renewal, expiry), monotonic time first seen).
        self._watch: Optional[Tuple[tuple, float]] = None
        # Monotonic time of this monitor's latest heartbeat write.
        self._beat_mono: Optional[float] = None
        # ACTIVE while this monitor holds the lease (or nothing is stored), STANDBY without it.
        self.role: Optional[str] = None
        # The number of the cycle under way (``cycle_seq``), taken with the lease.
        self._cycle_no: Optional[int] = None
        # The cycle under way: its session clock, its start (session time), the markets it looks at, and
        # the quotes and rates it fetched (``None`` before it fetched any). See ``_out_of_date``.
        self._cycle_clock: Optional[SessionClock] = None
        self._cycle_at: Optional[datetime] = None
        self._cycle_markets: Tuple[str, ...] = ()
        self._cycle_quotes: Optional[List[LiveQuote]] = None
        self._cycle_rates: List = []
        self._task: Optional[asyncio.Task] = None
        self._stop: Optional[asyncio.Event] = None
        self._last_probe: Dict[str, datetime] = {}
        self._probe_seen: Dict[str, datetime] = {}
        self._fail_open: Dict[str, date] = {}

    @property
    def lease_seconds(self) -> float:
        return float(self._lease_seconds) if self._lease_seconds else max(LEASE_MIN_SECONDS, 6 * self.interval)

    # -- the lease -------------------------------------------------------------

    def _holds(self, db) -> bool:
        """Inside a transaction: the lease names this monitor (expired or not: nobody took it over)."""
        row = _lease_row(db)
        return row is not None and row['owner'] == self.owner

    def _writes(self, db) -> bool:
        """Inside a transaction: this cycle may still write (the lease is ours, no newer cycle began writing
        and its data is current)."""
        return (self._holds(db) and self._cycle_no is not None
                and int(_load(db).get('applied_cycle') or 0) == self._cycle_no and not self._out_of_date())

    def _current(self, db, data: Mapping) -> bool:
        """Inside a transaction, for a write that is not the cycle's first: the lease is ours and no newer
        cycle has begun (by anyone), nor written."""
        return (self._holds(db) and self._cycle_no is not None
                and int(data.get('cycle_seq') or 0) == self._cycle_no
                and int(data.get('applied_cycle') or 0) <= self._cycle_no)

    def _begin_cycle(self, clock: SessionClock, at: datetime) -> None:
        self._cycle_clock, self._cycle_at, self._cycle_markets = clock, at, ()
        self._cycle_quotes, self._cycle_rates = None, []

    def _out_of_date(self) -> bool:
        """The cycle under way may no longer write (decision table row 4).

        Its data is out of date when the session date of one of its markets
        has changed since it began (a process paused overnight), or, once it
        has fetched quotes, when one of them that was live or delayed then has
        meanwhile become too old to be used at all (``DELAYED_MAX_AGE``; a
        rate, ``FX_DELAYED_MAX_AGE``). A cycle without such a quote (waiting,
        no rules, a failed request) is out of date one lease length after it
        began.
        """
        clock, began = self._cycle_clock, self._cycle_at
        if clock is None or began is None:
            return False
        now = clock.now()
        if any(clock.local_date(market) != clock.local_date(market, at=began) for market in self._cycle_markets):
            return True
        ages = [(item.quote_time, limit)
                for items, limit in ((self._cycle_quotes or (), DELAYED_MAX_AGE), (self._cycle_rates, FX_DELAYED_MAX_AGE))
                for item in items
                if getattr(item, 'status', None) in (LIVE, DELAYED) and getattr(item, 'quote_time', None) is not None]
        if not ages:
            return now - began > timedelta(seconds=self.lease_seconds)
        return any(now - moment > limit for moment, limit in ages)

    def _held_by_another(self, row) -> bool:
        """``row`` (another owner's lease) is valid: by the wall clock, and renewed within one lease length
        of this monitor's monotonic clock (see the module docstring)."""
        if not lease_valid(row, self._wall().timestamp()):
            return False
        key, seen = (row['owner'], row['beat'], row['expires_ts']), self._monotonic()
        if self._watch is None or self._watch[0] != key:
            self._watch = (key, seen)
        return seen - self._watch[1] < lease_length(row)

    def beat_age(self) -> Optional[float]:
        """Seconds since this monitor last wrote its heartbeat, by its monotonic clock; ``None`` before."""
        return None if self._beat_mono is None else self._monotonic() - self._beat_mono

    def _renew(self, db) -> None:
        row, stamp = _lease_row(db), desk_store.now()
        ours = row is not None and row['owner'] == self.owner
        acquired = row['acquired_at'] if ours else stamp
        beat = int(row['beat'] or 0) + 1 if ours else 1
        db.execute("INSERT INTO alert_monitor_lease(id, owner, pid, expires_ts, acquired_at, renewed_at, "
                   "lease_seconds, beat) VALUES (1,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET owner=excluded.owner, "
                   "pid=excluded.pid, expires_ts=excluded.expires_ts, acquired_at=excluded.acquired_at, "
                   "renewed_at=excluded.renewed_at, lease_seconds=excluded.lease_seconds, beat=excluded.beat",
                   (self.owner, os.getpid(), self._wall().timestamp() + self.lease_seconds, acquired, stamp,
                    self.lease_seconds, beat))

    def _take_lease(self) -> bool:
        """Take or renew the lease; False (and STANDBY) while another monitor holds a valid one."""
        if not rules.stored():
            # Nothing stored, so no rules: nothing to evaluate and nobody to share it with.
            self.role = ACTIVE
            self._cycle_no = None
            return True
        rules.ensure()
        with desk_store.transaction() as db:
            row = _lease_row(db)
            if row is not None and row['owner'] != self.owner and self._held_by_another(row):
                if self.role != STANDBY:
                    logger.info('alert monitor stands by: another monitor (pid %s) holds the lease', row['pid'])
                self.role = STANDBY
                return False
            self._renew(db)
            # Number the cycle before it reads anything (see the module docstring).
            data = _load(db)
            self._cycle_no = int(data.get('cycle_seq') or 0) + 1
            data['cycle_seq'] = self._cycle_no
            _save(db, data)
        if self.role != ACTIVE:
            logger.info('alert monitor holds the lease')
        self.role = ACTIVE
        return True

    # -- lifecycle -------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name='alert-monitor')
        _instances.add(self)

    async def stop(self, timeout: float = 10.0) -> None:
        """Stop after the current cycle (cancelled after ``timeout`` seconds) and record it."""
        task = self._task
        if task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        except Exception:
            pass
        _instances.discard(self)
        try:
            await asyncio.to_thread(self._record_stop)
        except Exception as exc:
            logger.warning('alert monitor could not record its stop: %s', type(exc).__name__)

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            started = loop.time()
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('alert monitor cycle failed: %s', type(exc).__name__)
                try:
                    await asyncio.to_thread(self._record_error, exc)
                except Exception as again:
                    logger.warning('alert monitor could not record a failed cycle: %s', type(again).__name__)
            delay = max(self.interval * 0.2, self.interval - (loop.time() - started))
            try:
                await asyncio.wait_for(self._stop.wait(), delay)
            except asyncio.TimeoutError:
                pass

    # -- one cycle -------------------------------------------------------------

    async def run_cycle(self) -> CycleReport:
        runtime = self._runtime or get_market_runtime()
        report = await self._cycle(runtime)
        # Every cycle, also after the close: fires held back by the global cap
        # must not wait for the next session to be summarised.
        if report.status not in (STANDBY, SUPERSEDED, STALE_CYCLE):
            await asyncio.to_thread(self._digest_if_due, runtime.clock.now())
        return report

    async def _cycle(self, runtime: MarketRuntime) -> CycleReport:
        clock = runtime.clock
        report = CycleReport()
        if not await asyncio.to_thread(self._take_lease):
            report.status = STANDBY
            return report
        now = clock.now()
        self._begin_cycle(clock, now)
        items = await asyncio.to_thread(rules.list_rules, enabled=True)
        enabled_rules = [rules.to_rule(item) for item in items]
        if not enabled_rules:
            report.status = NO_RULES
            await asyncio.to_thread(self._heartbeat_only, NO_RULES, now, {}, 0, 0)
            return report

        modes = self._plan(clock, now, enabled_rules)
        report.modes = modes
        self._cycle_markets = tuple(modes)
        checked = [rule for rule in enabled_rules if modes.get(rule.market) in (SESSION, FAIL_OPEN, PROBE)]
        if not checked:
            report.status = WAITING
            await asyncio.to_thread(self._heartbeat_only, WAITING, now, modes, len(enabled_rules), 0)
            return report

        state, ledger_problem = None, None
        if any(rule.kind == POSITION_WEIGHT_MAX for rule in checked):
            state, ledger_problem = await asyncio.to_thread(self._ledger_state, clock)
        held = [ticker for account in (state or {}).get('accounts', {}).values() for ticker in account['positions']]
        tickers = list(dict.fromkeys([rule.ticker for rule in checked] + held))
        error = None
        try:
            quotes = await runtime.quotes.aget(tickers)
        except Exception as exc:   # QuoteService should not raise; never trust that blindly
            logger.warning('alert monitor quote request failed: %s', type(exc).__name__)
            quotes, error = {}, QUOTE_REQUEST_FAILED.format(provider_errors.classify(exc))
        self._cycle_quotes = list(quotes.values())
        report.fetched = tickers

        newly_open = []
        for market in sorted({rule.market for rule in checked if modes.get(rule.market) == PROBE}):
            self._last_probe[market] = now
            if self._advancing(clock, market, [rule.ticker for rule in checked if rule.market == market], quotes, now):
                self._fail_open[market] = clock.local_date(market)
                modes[market] = FAIL_OPEN
                newly_open.append(market)
        active = [rule for rule in checked if modes.get(rule.market) in (SESSION, FAIL_OPEN)]
        if not active:
            report.status = WAITING
            await asyncio.to_thread(self._heartbeat_only, WAITING, now, modes, len(enabled_rules), 0)
            return report

        valuation, weight_problem, rates = None, ledger_problem, None
        if error is None and state is not None and any(rule.kind == POSITION_WEIGHT_MAX for rule in active):
            try:
                rates = await runtime.quotes.afx()
                self._cycle_rates = list((rates or {}).values())
                valuation = value_portfolio(state, quotes, rates, None, [])
            except Exception as exc:
                weight_problem = VALUATION_FAILED
                logger.warning('alert monitor valuation failed: %s', type(exc).__name__)

        contexts = {rule.id: self._context(clock, now, rule, valuation, weight_problem) for rule in active}
        problems = [engine.quote_problem(rule, quotes.get(rule.ticker), contexts[rule.id]) for rule in active]
        failed = error is not None or all(problem is not None and problem[0] in QUOTE_FAILURES for problem in problems)
        reason = error or next((problem[1] for problem in problems if problem is not None), None)
        report.failed = failed
        report.status = await asyncio.to_thread(self._record_cycle, clock, now, modes, len(enabled_rules),
                                                len(active), failed, reason, newly_open)
        if report.status in (STANDBY, SUPERSEDED, STALE_CYCLE):
            # Another monitor took the lease while this one waited for quotes, a newer cycle began
            # writing, or this one's data is no longer current: it writes nothing more.
            report.failed = False
            return report
        if not failed:
            observed = observations(active, quotes, valuation, rates, (state or {}).get('revision'))
            report.actions, broken, skips = await asyncio.to_thread(
                self._apply_all, clock, now, active, quotes, valuation, weight_problem, observed)
            stopped = {SUPERSEDED, STALE_CYCLE} & set(report.actions.values())
            if stopped:
                report.status = stopped.pop()
            elif self.role == ACTIVE:
                report.status = await asyncio.to_thread(
                    self._record_rule_results, broken, skips, active, now,
                    {rule.id for rule in enabled_rules}) or report.status
        return report

    def _plan(self, clock: SessionClock, now: datetime, enabled_rules: Sequence[Rule]) -> Dict[str, str]:
        modes: Dict[str, str] = {}
        for market in sorted({rule.market for rule in enabled_rules if rule.market}):
            if clock.is_trading(market):
                modes[market] = SESSION
            elif not in_session_hours(market, now) or clock.is_trading_day(market):
                modes[market] = CLOSED
            elif self._fail_open.get(market) == clock.local_date(market):
                modes[market] = FAIL_OPEN
            else:
                # The holiday table says closed on a weekday in session hours.
                last = self._last_probe.get(market)
                due = last is None or not timedelta(0) <= now - last < timedelta(seconds=self.probe_interval)
                modes[market] = PROBE if due else CLOSED
        return modes

    def _advancing(self, clock: SessionClock, market: str, tickers: Sequence[str],
                   quotes: Mapping[str, LiveQuote], now: datetime) -> bool:
        """Today's exchange time, recent and newer than the previous probe saw."""
        today, zone, found = clock.local_date(market), ZONES[market], False
        for ticker in tickers:
            quote = quotes.get(ticker)
            if quote is None or quote.price is None or quote.quote_time is None or quote.price_basis == PREV_CLOSE:
                continue
            seen = self._probe_seen.get(ticker)
            self._probe_seen[ticker] = quote.quote_time
            age = now - quote.quote_time
            if quote.quote_time.astimezone(zone).date() != today or not -FUTURE_TOLERANCE <= age <= FAIL_OPEN_FRESHNESS:
                continue
            if seen is None or quote.quote_time > seen:
                found = True
        return found

    def _ledger_state(self, clock: SessionClock) -> Tuple[Optional[dict], Optional[str]]:
        """The confirmed holdings as of today, read-only (``core.alerts.holdings``), or why there are none."""
        if self._ledger is None:
            return holdings.confirmed_holdings(clock)
        try:
            ledger = self._ledger()
        except Exception as exc:
            logger.warning('alert monitor could not open the ledger: %s', type(exc).__name__)
            return None, holdings.LEDGER_UNREADABLE
        if ledger is None:
            return None, holdings.NO_HOLDINGS
        return holdings.confirmed_holdings(clock, ledger)

    @staticmethod
    def _context(clock: SessionClock, now: datetime, rule: Rule, valuation, weight_problem,
                 observation: Optional[Observation] = None) -> Context:
        """The context of ``rule`` in the cycle that began at ``now``: its session date is the one of ``now``."""
        weight, code, reason = None, None, None
        if rule.kind == POSITION_WEIGHT_MAX:
            weight, code, reason = (engine.weight_check(rule, valuation) if valuation is not None
                                    else (None, engine.WEIGHT_UNAVAILABLE, weight_problem))
        return Context(now=now, session_open=True, trading_date=clock.local_date(rule.market, at=now),
                       weight_pct=weight, weight_reason=reason, weight_code=code, observation=observation)

    # -- writes (worker thread; no awaiting inside a transaction) --------------

    def _apply_all(self, clock, now, active: Sequence[Rule], quotes, valuation, weight_problem,
                   observed: Optional[Mapping[int, Observation]] = None
                   ) -> Tuple[Dict[int, str], List[int], Dict[int, Tuple[str, str]]]:
        """``(actions by rule id, ids of the rules that could not be evaluated, skips by rule id)``.

        A skip is ``(code, message)``: rows 6-9 and 11 of the decision table.
        The rules and their states are read again first, after the quotes came
        back: a rule edited, disabled or deleted while they were fetched is
        decided as it is stored now, on the quotes of this cycle.
        """
        actions: Dict[int, str] = {}
        broken: List[int] = []
        skips: Dict[int, Tuple[str, str]] = {}
        observed = observed or {}
        stored = {item['id']: item for item in rules.list_rules()}
        # The most important kinds first: they take the notification budget first.
        for listed in sorted(active, key=lambda each: (engine.priority(each.kind), each.id)):
            item = stored.get(listed.id)
            if item is None or not item['enabled']:
                actions[listed.id] = 'deleted' if item is None else 'disabled'
                continue
            rule = rules.to_rule(item)
            quote = quotes.get(rule.ticker)
            seen = observed.get(rule.id)
            try:
                # Most cycles change nothing: decide on the rule and state just read and take
                # the write lock only when there is a change. The transaction decides again on
                # what is stored by then.
                decision = engine.evaluate(rule, rules.to_state(item), quote,
                                           self._context(clock, now, rule, valuation, weight_problem, seen))
                if decision.reason == 'invalid_threshold':
                    broken.append(rule.id)
                if decision.changed:
                    action, decision = self._apply_one(clock, now, rule.id, quote, valuation, weight_problem, seen)
                else:
                    action = decision.action
                actions[rule.id] = action
                if decision is not None and decision.action == SKIP:
                    skips[rule.id] = (decision.reason, decision.message)
            except Exception as exc:   # one rule must not stop the others
                actions[rule.id] = 'error'
                broken.append(rule.id)
                logger.warning('alert rule %s could not be evaluated: %s', rule.id, type(exc).__name__)
                continue
            if actions[rule.id] == LEASE_LOST:
                # Another monitor took over while this one stalled: it evaluates from now on.
                logger.warning('alert monitor lost its lease during a cycle; it stands by')
                self.role = STANDBY
                break
            if actions[rule.id] in (SUPERSEDED, STALE_CYCLE):
                logger.warning('alert monitor cycle %s is %s; it writes nothing more', self._cycle_no,
                               actions[rule.id])
                break
        return actions, broken, skips

    def _record_rule_results(self, broken: Sequence[int], skips: Mapping[int, Tuple[str, str]],
                             active: Sequence[Rule], now: datetime,
                             enabled_ids: Optional[Sequence[int]] = None) -> Optional[str]:
        """Keep in the heartbeat the rules this cycle could not handle and the ones it skipped; returns the status.

        ``errored_rule_ids`` (and the status ``rule_errors``) as before; ``rule_skips``
        ``{rule id: {code, message, since, held_since, ticker, kind}}``, ``since`` being
        when the same skip began and ``held_since`` when the rule began to be skipped,
        whatever the reasons since. A rule this cycle did not evaluate (its market is
        closed) keeps its entry while it stays enabled, so both span closed hours: a
        weight rule paused by a long suspension shows since when. Writes only when
        something changed; publishes ``_held_notice`` when due. ``None`` when this
        cycle may not write any more (``stale_cycle`` when its data is out of date).
        """
        ids = sorted(set(broken))
        by_id = {rule.id: rule for rule in active}
        keep = set(enabled_ids if enabled_ids is not None else by_id) - set(by_id)
        with desk_store.transaction() as db:
            if not self._holds(db) or self._cycle_no is None or int(_load(db).get('applied_cycle') or 0) != self._cycle_no:
                return None
            if self._out_of_date():
                return STALE_CYCLE
            data = _load(db)
            status = data.get('status')
            wanted = RULE_ERRORS if ids and status == OK else OK if not ids and status == RULE_ERRORS else status
            before = data.get('rule_skips') if isinstance(data.get('rule_skips'), dict) else {}
            recorded = {key: entry for key, entry in before.items() if str(key).isdigit() and int(key) in keep}
            for rule_id, (code, message) in sorted(skips.items()):
                prior = before.get(str(rule_id)) if isinstance(before.get(str(rule_id)), dict) else None
                # A start dated after the clock (it went back) starts again now.
                since = _parse(prior.get('since')) if prior else None
                held = _parse(prior.get('held_since') or prior.get('since')) if prior else None
                same = prior is not None and prior.get('code') == code and since is not None and since <= now + FUTURE_TOLERANCE
                still = prior is not None and held is not None and held <= now + FUTURE_TOLERANCE
                rule = by_id.get(rule_id)
                recorded[str(rule_id)] = {'code': code, 'message': message,
                                          'since': prior.get('since') if same else _iso(now),
                                          'held_since': (prior.get('held_since') or prior.get('since')) if still
                                          else _iso(now),
                                          'ticker': rule.ticker if rule else None, 'kind': rule.kind if rule else None}
            noticed = self._held_notice(data, recorded, skips, now)
            if data.get('errored_rule_ids', []) == ids and wanted == status and before == recorded and not noticed:
                return status
            if ids != data.get('errored_rule_ids', []):
                logger.warning('alert monitor: %s rule(s) could not be evaluated: %s', len(ids), ids)
            data.update(errored_rule_ids=ids, rules_errored=len(ids), status=wanted, rule_skips=recorded)
            _save(db, data)
            return wanted

    @staticmethod
    def _held_notice(data: dict, recorded: Mapping[str, dict], skips: Mapping[int, Tuple[str, str]],
                     now: datetime) -> bool:
        """One notification a day at most while weight rules have been held back for ``HELD_NOTICE_AFTER``.

        Held back means skipped in every cycle since ``held_since``, for any
        reasons, one after the other (an unverified valuation, then a holding
        without a price, then an input older than the one behind the last
        change ...): such a pause can last for weeks without the rule ever
        saying so, and the status page is not watched. The status shows the
        hold all along. Returns whether ``data`` was changed.
        """
        today = now.astimezone(SHANGHAI).date().isoformat()
        if data.get('held_notice_date') == today:
            return False
        held = []
        for rule_id in sorted(skips):
            entry = recorded.get(str(rule_id)) or {}
            since = _parse(entry.get('held_since'))
            if entry.get('kind') == POSITION_WEIGHT_MAX and since is not None and now - since >= HELD_NOTICE_AFTER:
                held.append((rule_id, {**entry, 'since': entry['held_since']}))
        if not held:
            return False
        title, body = messages.weight_rules_held([entry for _rule_id, entry in held])
        since = min(entry['since'] for _rule_id, entry in held)
        notify.publish('alert.monitor', 'warning', title, body,
                       data={'event': 'weight_rules_held', 'rule_ids': [rule_id for rule_id, _entry in held],
                             'since': since, 'link': '/alerts'},
                       dedupe_key=f'alert:held:{today}')
        data['held_notice_date'] = today
        return True

    def _apply_one(self, clock, now, rule_id: int, quote: Optional[LiveQuote], valuation, weight_problem,
                   observation: Optional[Observation] = None) -> Tuple[str, Optional[engine.Decision]]:
        """``(action, decision)``; the decision is ``None`` when none was made.

        Decided inside the write transaction on the rule and state stored then.
        """
        with desk_store.transaction() as db:
            if not self._holds(db):
                return LEASE_LOST, None
            if self._cycle_no is None or int(_load(db).get('applied_cycle') or 0) != self._cycle_no:
                return SUPERSEDED, None
            if self._out_of_date():
                return STALE_CYCLE, None
            loaded = rules.load_for_update(db, rule_id)
            if loaded is None:
                return 'deleted', None
            rule, state = loaded
            if not rule.enabled:
                return 'disabled', None
            decision = engine.evaluate(rule, state, quote, self._context(clock, now, rule, valuation, weight_problem,
                                                                         observation))
            if decision.changed:
                rules.save_state(db, rule.id, decision.state)
            if decision.fired:
                self._record_fire(db, rule, decision, quote, now)
            return decision.action, decision

    @staticmethod
    def _record_fire(db, rule: Rule, decision, quote: Optional[LiveQuote], now: datetime) -> None:
        name = quote.name if quote is not None and quote.name else None
        level, title, body = messages.alert(rule, decision, quote, name)
        day = decision.trading_date.isoformat()
        event = {
            'rule_id': rule.id, 'ticker': rule.ticker, 'name': name, 'account': rule.account, 'kind': rule.kind,
            'threshold': rules.decimal_text(rule.threshold), 'trading_date': day, 'n': decision.fire_number,
            'value': messages.number(decision.value, 6), 'price': messages.number(quote.price) if quote else None,
            'prev_close': messages.number(quote.prev_close) if quote and quote.prev_close else None,
            'quote_source': quote.source if quote else None, 'quote_time': _iso(quote.quote_time) if quote else None,
            'quote_status': quote.status if quote else None, 'fired_at': _iso(now), 'fired_ts': now.timestamp(),
            'title': title, 'body': body, 'notification_seq': None}
        budget = GLOBAL_CAP if rule.kind == STOP_LOSS else GLOBAL_CAP - STOP_LOSS_RESERVE
        if events.notified_since(db, now.timestamp() - GLOBAL_WINDOW.total_seconds(),
                                 now.timestamp() + FUTURE_TOLERANCE.total_seconds()) >= budget:
            event['delivery'] = 'suppressed'
        else:
            data = {key: event[key] for key in ('rule_id', 'ticker', 'name', 'account', 'kind', 'threshold',
                                                'trading_date', 'n', 'value', 'price', 'prev_close', 'quote_source',
                                                'quote_time', 'quote_status')}
            data.update(rule_source=rule.source, link='/alerts')
            seq = notify.publish('alert', level, title, body, data=data,
                                 dedupe_key=f'alert:{rule.id}:{day}:{decision.fire_number}')
            event['delivery'] = 'notified' if seq is not None else 'duplicate'
            event['notification_seq'] = seq
            if seq is None:
                logger.warning('alert rule %s fire %s was already notified', rule.id, decision.fire_number)
        events.record(db, event)

    def _beat(self, data: dict, status: str, now: Optional[datetime], modes: Mapping[str, str],
              enabled_count: int, active_count: int) -> None:
        """Write the heartbeat into ``data``: ``beat_at`` by the wall clock (health), ``last_cycle_at`` by the
        session clock (``now``, the cycle's start; left as it is by a stop, which is not a cycle)."""
        self._beat_mono = self._monotonic()
        data.update(status=status, beat_at=_iso(self._wall()), interval_seconds=self.interval,
                    rules_enabled=enabled_count, rules_active=active_count, modes=dict(modes),
                    fail_open={market: day.isoformat() for market, day in self._fail_open.items()},
                    pid=os.getpid())
        if now is not None:
            data.update(last_cycle_at=_iso(now), session_time=_iso(now))

    def _heartbeat_only(self, status: str, now, modes, enabled_count: int, active_count: int) -> None:
        """The heartbeat of a cycle that evaluates nothing; only while the cycle may still write."""
        if not rules.available():
            self._beat(_idle_heartbeat(), status, now, modes, enabled_count, active_count)
            return
        with desk_store.transaction() as db:
            data = _load(db)
            if not self._current(db, data) or self._out_of_date():
                return
            data['applied_cycle'] = self._cycle_no
            self._beat(data, status, now, modes, enabled_count, active_count)
            if status == NO_RULES:
                data['rule_skips'] = {}   # no rule is enabled: nothing is being skipped
            _save(db, data)

    def _record_stop(self) -> None:
        """Release the lease and record the stop.

        Recorded also by a monitor that holds no lease, but only while no other
        monitor holds a valid one: ``stopped`` can only make the status say that
        nothing checks, never that something does. A stop is not a cycle:
        ``last_cycle_at`` stays the time of the last cycle.
        """
        if not rules.available():
            self._beat(_idle_heartbeat(), STOPPED, None, {}, 0, 0)
            self.role = None
            return
        with desk_store.transaction() as db:
            row = _lease_row(db)
            if row is not None and row['owner'] == self.owner:
                db.execute("DELETE FROM alert_monitor_lease WHERE id=1 AND owner=?", (self.owner,))
            elif row is not None and lease_valid(row, self._wall().timestamp()):
                self.role = None
                return   # another monitor checks the rules: its heartbeat is not ours to touch
            data = _load(db)
            self._beat(data, STOPPED, None, {}, 0, 0)
            _save(db, data)
        self.role = None

    def _record_cycle(self, clock: SessionClock, now: datetime, modes, enabled_count: int, active_count: int,
                      failed: bool, reason: Optional[str], newly_open: Sequence[str]) -> str:
        rules.ensure()
        with desk_store.transaction() as db:
            if not self._holds(db):
                self.role = STANDBY
                return STANDBY
            data = _load(db)
            if self._cycle_no is None or int(data.get('applied_cycle') or 0) > self._cycle_no:
                return SUPERSEDED   # a newer cycle began writing while this one read its data
            if self._out_of_date():
                logger.warning('alert monitor cycle %s is out of date (stalled); it writes nothing', self._cycle_no)
                return STALE_CYCLE
            self._renew(db)   # the quote request may have taken a while
            data['applied_cycle'] = self._cycle_no
            status = self._outage(data, now, failed, reason)
            if status == OK and data.get('errored_rule_ids'):
                status = RULE_ERRORS   # until this cycle's evaluation says otherwise
            for market in newly_open:
                day = clock.local_date(market, at=now).isoformat()
                title, body = messages.calendar_warning(market, day)
                notify.publish('alert.monitor', 'warning', title, body,
                               data={'event': 'calendar_mismatch', 'market': market, 'date': day, 'link': '/alerts'},
                               dedupe_key=f'alert:calendar:{market}:{day}')
                data['calendar_warning'] = {'market': market, 'date': day, 'at': _iso(now), 'message': body}
            self._beat(data, status, now, modes, enabled_count, active_count)
            _save(db, data)
        return status

    def _outage(self, data: dict, now: datetime, failed: bool, reason: Optional[str]) -> str:
        """Count the cycle; publish the pause / recovery notice on the transitions.

        A pause notice follows the previous one by at least ``PAUSE_NOTICE_SPACING``,
        so quotes that keep dropping out and coming back cannot flood the
        inbox; an outage inside that spacing shows in the status only, and
        its end is not announced either.
        """
        if failed:
            failures = int(data.get('consecutive_failures') or 0) + 1
            since = data.get('outage_since') or _iso(now)
            data.update(consecutive_failures=failures, outage_since=since,
                        outage_reason=(reason or '未知原因')[:300])
            last_notice = data.get('last_pause_notice_ts')
            spaced = (last_notice is None
                      or not 0 <= now.timestamp() - last_notice < PAUSE_NOTICE_SPACING.total_seconds())
            if failures >= FAILURES_BEFORE_PAUSE and not data.get('outage_notified') and spaced:
                title, body = messages.paused(failures, data['outage_reason'], since)
                notify.publish('alert.monitor', 'critical', title, body,
                               data={'event': 'paused', 'since': since, 'link': '/alerts'},
                               dedupe_key=f'alert:monitor:{since}:paused')
                data.update(outage_notified=True, last_pause_notice_ts=now.timestamp())
            return PAUSED if failures >= FAILURES_BEFORE_PAUSE else DEGRADED
        if data.get('outage_notified'):
            since = data.get('outage_since')
            title, body = messages.resumed(since)
            notify.publish('alert.monitor', 'info', title, body,
                           data={'event': 'resumed', 'since': since, 'link': '/alerts'},
                           dedupe_key=f'alert:monitor:{since}:resumed')
        data.update(consecutive_failures=0, outage_since=None, outage_reason=None, outage_notified=False,
                    last_ok_at=_iso(now))
        return OK

    def _record_error(self, exc: Exception) -> None:
        runtime = self._runtime or get_market_runtime()
        now = runtime.clock.now()
        if not rules.available():
            self._beat(_idle_heartbeat(), ERROR, now, {}, 0, 0)
            return
        with desk_store.transaction() as db:
            data = _load(db)
            if not self._current(db, data) or self._out_of_date():
                return
            data['applied_cycle'] = self._cycle_no
            status = self._outage(data, now, True, CYCLE_FAILED)
            self._beat(data, ERROR if status != PAUSED else PAUSED, now, data.get('modes') or {},
                       int(data.get('rules_enabled') or 0), 0)
            _save(db, data)

    def _digest_if_due(self, now: datetime) -> None:
        if not rules.available():
            return
        with desk_store.connect() as db:
            covered = int(_load(db).get('digest_covered_event_id') or 0)
            if not events.suppressed_after(db, covered):
                return
        with desk_store.transaction() as db:
            data = _load(db)
            if not self._current(db, data):
                return
            pending = events.suppressed_after(db, int(data.get('digest_covered_event_id') or 0))
            last = data.get('last_digest_ts')
            if not pending or (last is not None and 0 <= now.timestamp() - last < GLOBAL_WINDOW.total_seconds()):
                return
            title, body = messages.digest(pending, GLOBAL_CAP, int(GLOBAL_WINDOW.total_seconds() // 60))
            stops = sum(1 for event in pending if event['kind'] == STOP_LOSS)
            notify.publish('alert.digest', 'critical' if stops else 'warning', title, body,
                           data={'event_ids': [event['id'] for event in pending], 'count': len(pending),
                                 'stop_loss_count': stops, 'link': '/alerts'},
                           dedupe_key=f'alert:digest:{pending[-1]["id"]}')
            data.update(digest_covered_event_id=pending[-1]['id'], last_digest_ts=now.timestamp(),
                        last_digest_at=_iso(now))
            _save(db, data)


def record_fire(db, rule: Rule, decision, quote: Optional[LiveQuote], now: datetime) -> None:
    """Store the log row and the notification of a fire, inside the caller's transaction.

    What the monitor does for a ``decision`` whose ``fired`` is true; public so
    that history can be seeded (``core.demo_seed``) with the alerts the engine
    really decides, instead of hand-written rows. The caller saves
    ``decision.state`` with ``rules.save_state`` in the same transaction.
    """
    AlertMonitor._record_fire(db, rule, decision, quote, now)


# -- status -------------------------------------------------------------------

def _message(status: str, data: Mapping, markets: Mapping, held: Optional[Mapping] = None,
             wall_now: Optional[datetime] = None) -> str:
    if status == DISABLED:
        return f'提醒监控已关闭（{ENV_ENABLED}=0）。'
    if status == DEMO:
        return '演示模式只读：本进程不运行提醒监控，不检查提醒，也不会发出提醒。'
    if status == STANDBY:
        holder = f'（pid {held["pid"]}）' if held and held.get('pid') else ''
        return (f'本进程的提醒监控处于待命状态：另一个后端进程{holder}持有检查权，由它检查提醒；'
                '它停止或长时间没有续期后，本进程会自动接管。')
    if status == NOT_STARTED:
        return '提醒监控还没有运行过（后端未启动后台任务，或刚刚启动）。'
    if status == STOPPED:
        return '提醒监控已停止（后端已关闭或正在关闭）。'
    if status == UNRESPONSIVE and wall_now is not None and _dated_ahead(data, wall_now):
        return ('提醒监控的心跳时间晚于本机当前时间（系统时钟可能被调回），无法确认它仍在运行；'
                '在恢复之前不会提醒。正常运行的监控会在下一次检查时恢复心跳。')
    if status == UNRESPONSIVE:
        return '提醒监控长时间没有心跳，可能已停止或卡在行情请求上；在恢复之前不会提醒。'
    if status == NO_RULES:
        return '没有启用的提醒规则。'
    if status == WAITING:
        upcoming = sorted(entry['next_open'] for entry in markets.values() if entry['rules'] and entry['next_open'])
        return '当前不在相关市场的交易时段，暂不检查提醒' + (f'；下次开盘 {upcoming[0][:16].replace("T", " ")}。' if upcoming else '。')
    if status == PAUSED:
        follow_up = ('恢复后会通知。' if data.get('outage_notified')
                     else '30 分钟内已推送过一次中断通知，这次中断及其恢复不再单独推送。')
        return f'行情中断，提醒暂停：{data.get("outage_reason") or "未知原因"}。{follow_up}'
    if status == DEGRADED:
        return (f'最近 {data.get("consecutive_failures")} 次检查没有取得可用行情（{data.get("outage_reason") or "未知原因"}）；'
                f'连续 {FAILURES_BEFORE_PAUSE} 次失败会发出中断通知。')
    if status == ERROR:
        return f'提醒检查出错：{data.get("outage_reason") or "未知原因"}。'
    if status == RULE_ERRORS:
        ids = list(data.get('errored_rule_ids') or [])
        shown = '、'.join(f'#{rule_id}' for rule_id in ids[:10]) + ('等' if len(ids) > 10 else '')
        return (f'提醒正在运行，但有 {len(ids)} 条规则无法检查（{shown}），这些规则在修正前不会提醒；'
                '请查看规则的阈值和提示。')
    return '提醒正在运行。'


def _lease_view(held: Optional[Mapping], here: Sequence['AlertMonitor'], wall_now: datetime,
                live: bool) -> Optional[dict]:
    if held is None:
        return None
    expires = datetime.fromtimestamp(float(held['expires_ts']), timezone.utc)
    return {'owner_pid': held.get('pid'), 'expires_at': _iso(expires),
            'valid': lease_valid(held, wall_now.timestamp()), 'live': live,
            'this_process': any(monitor.owner == held['owner'] for monitor in here)}


CLOCK_WARNING = ('提醒监控记录的时间晚于本机当前时间，系统时钟可能被调回。监控不会因此停止：'
                 '它会在下一次检查时恢复，失去续期的检查权最迟一个租约周期后被接管。')


def status(runtime: Optional[MarketRuntime] = None, wall: Optional[Callable[[], datetime]] = None) -> dict:
    """The status payload of ``GET /api/alerts/status``.

    ``status`` is the evaluating monitor's state, read from the heartbeat it
    writes, except in a process whose own monitor stands by: there it is
    ``standby`` and ``active_status`` carries the evaluating monitor's state;
    and in the read-only demo, which runs no monitor: there it is ``demo``.
    ``role`` is what this process's monitor is (``active``, ``standby`` or
    ``None`` when none runs here); ``in_process`` whether one runs here.

    Who checks the rules: ``lease_holder`` is ``this_process``,
    ``another_process`` or ``None`` when no monitor holds a live lease;
    ``lease_holder_pid`` that process; ``lease`` describes the stored lease
    (``live`` as ``lease_holder``). A lease is live while it is valid by the
    wall clock, or while it names the monitor of this process and that
    monitor's own monotonic clock says it beat recently (a monitor with no
    store yet holds no stored lease: it is live the same way). ``running`` is
    true only outside the demo, with the monitor enabled, a live lease and a
    healthy heartbeat (``status`` not ``disabled``, ``not_started``,
    ``stopped`` or ``unresponsive``). ``demo`` says whether this process is
    the read-only demo.

    ``last_cycle_at`` and ``last_ok_at`` are session-clock times, like
    ``as_of`` of the other pages; the health check uses ``beat_at`` (wall
    clock), which the payload leaves out.
    """
    runtime = runtime or get_market_runtime()
    clock = runtime.clock
    data = heartbeat()
    held = lease()
    enabled_rules = [rules.to_rule(item) for item in rules.list_rules(enabled=True)]
    wall_now = (wall or _utc_now)()
    interval = float(data.get('interval_seconds') or DEFAULT_INTERVAL)
    here = [monitor for monitor in list(_instances) if monitor.running]
    # The monitor holding the lease in this process vouches for itself by its own monotonic clock.
    beating = [monitor for monitor in here if monitor.role == ACTIVE and monitor.beat_age() is not None
               and monitor.beat_age() <= _window(data).total_seconds()]
    beating_here = bool(beating)
    fresh = _fresh(data, wall_now) or beating_here
    clock_warning = (CLOCK_WARNING if _dated_ahead(data, wall_now)
                     or (held is not None and lease_dated_ahead(held, wall_now.timestamp())) else None)
    if held is not None:
        holder_here = any(monitor.owner == held['owner'] for monitor in here)
        live = lease_valid(held, wall_now.timestamp()) or any(monitor.owner == held['owner'] for monitor in beating)
        holder = (THIS_PROCESS if holder_here else ANOTHER_PROCESS) if live else None
        holder_pid = held.get('pid') if live else None
    else:
        # Nothing stored yet: a monitor of this process that has nothing to share holds no stored lease.
        live = beating_here and not rules.stored()
        holder, holder_pid = (THIS_PROCESS, os.getpid()) if live else (None, None)
    stored = data.get('status') or NOT_STARTED
    if not enabled():
        effective = DISABLED
    elif stored in (STOPPED, NOT_STARTED) or fresh:
        effective = stored
    else:
        effective = UNRESPONSIVE
    roles = {monitor.role for monitor in here}
    role = ACTIVE if ACTIVE in roles else STANDBY if STANDBY in roles else None
    demo = demo_mode()
    if demo:
        shown = DEMO
    else:
        shown = STANDBY if role == STANDBY and effective != DISABLED else effective

    markets = {}
    for market in MARKETS:
        count = sum(1 for rule in enabled_rules if rule.market == market)
        is_open = session_open(clock, market, data)
        markets[market] = {
            'phase': clock.phase(market), 'trading_day': clock.current_trading_day(market).isoformat(),
            'is_trading_day': clock.is_trading_day(market), 'calendar_confidence': clock.confidence(market),
            'next_open': _iso(clock.next_open(market)), 'rules': count, 'monitoring': bool(count) and is_open,
            'fail_open': is_open and not clock.is_trading(market)}
    paused = int(data.get('consecutive_failures') or 0) >= FAILURES_BEFORE_PAUSE
    # Rules of the markets checked now; a closed market's rules keep their entry (and its ``since``) for later.
    checked_now = {rule.id for rule in enabled_rules if markets.get(rule.market, {}).get('monitoring')}
    skipped = [{'rule_id': rule_id, **{key: entry.get(key) for key in ('ticker', 'kind', 'code', 'message', 'since')},
                'held_since': entry.get('held_since') or entry.get('since')}
               for rule_id, entry in sorted(_skips(data).items()) if fresh and rule_id in checked_now]
    warning = data.get('calendar_warning')
    if isinstance(warning, dict) and warning.get('date') != clock.local_date(warning.get('market', 'CN')).isoformat():
        warning = None
    return {
        'running': (not demo and live and effective not in (DISABLED, NOT_STARTED, STOPPED, UNRESPONSIVE)),
        'in_process': bool(here), 'demo': demo,
        'enabled': enabled(), 'status': shown, 'message': _message(shown, data, markets, held, wall_now),
        'active_status': effective, 'role': role, 'lease': _lease_view(held, here, wall_now, live),
        'lease_holder': holder, 'lease_holder_pid': holder_pid,
        'last_cycle_at': data.get('last_cycle_at'), 'last_ok_at': data.get('last_ok_at'),
        'rules_enabled': len(enabled_rules), 'rules_active': int(data.get('rules_active') or 0) if fresh else 0,
        'interval_seconds': interval, 'consecutive_failures': int(data.get('consecutive_failures') or 0),
        'paused': paused, 'pause_reason': data.get('outage_reason') if paused else None,
        'pause_notified': bool(data.get('outage_notified')),
        'outage_since': data.get('outage_since'), 'calendar_warning': warning, 'markets': markets,
        'rules_errored': int(data.get('rules_errored') or 0),
        'errored_rule_ids': [int(rule_id) for rule_id in data.get('errored_rule_ids') or []],
        'rules_skipped': len(skipped), 'skipped_rules': skipped, 'clock_warning': clock_warning,
        'quote_source': '+'.join(runtime.quotes.source_names) or 'none', 'quote_mode': runtime.mode,
        'notice': GUARANTEE_NOTICE}


# -- the application's background task ----------------------------------------

@asynccontextmanager
async def background(app):
    """Run one ``AlertMonitor`` for the application's lifetime (``backend.features``)."""
    monitor: Optional[AlertMonitor] = None
    if enabled() and not demo_mode():
        try:
            monitor = AlertMonitor(interval=configured_interval())
            await monitor.start()
        except Exception as exc:
            logger.warning('alert monitor could not start: %s', type(exc).__name__)
            monitor = None
    try:
        app.state.alert_monitor = monitor
    except Exception:
        pass
    try:
        yield monitor
    finally:
        if monitor is not None:
            try:
                await monitor.stop()
            except Exception as exc:
                logger.warning('alert monitor did not stop cleanly: %s', type(exc).__name__)
