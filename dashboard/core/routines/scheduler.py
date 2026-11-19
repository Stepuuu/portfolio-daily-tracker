"""Daily routines: jobs that run at most once per trading day, keyed by ``(kind, trading_date)``.

Registering a kind, in a module listed in ``core.routines.KIND_MODULES``::

    from datetime import datetime, time
    from core.market.calendar import SHANGHAI
    from core.routines import scheduler

    def due(clock):
        # ``clock`` is a SessionClock whose now() is the moment asked about.
        # Return when the run for that trading day becomes due, or None when
        # this kind does not run that day.
        day = clock.local_date()
        if not clock.is_trading_day(day=day):
            return None
        return datetime.combine(day, time(8, 30), tzinfo=SHANGHAI)

    async def run(trading_date):      # 'YYYY-MM-DD'; a plain function runs in a worker thread
        ...
        return scheduler.ok("盘前简报已生成")

    scheduler.register("premarket", due, run, title="盘前简报")

``run`` returns ``ok(...)``, ``failed(...)``, ``skipped(...)``,
``unavailable(...)`` or ``waiting(...)``; an exception counts as ``failed``. Summaries are shown to
the user and go into notifications: short plain text, never amounts or other
personal data. ``detail`` is a small JSON object for the run row (a log tail,
a documentation link).

How runs happen (one row per kind and trading day in ``routine_runs`` in
``desk.sqlite3``):

* The loop wakes about once a minute. Once ``due`` has passed and the day has
  no row, it claims the day by inserting a ``running`` row and runs the kind.
  Only today is caught up after a start: a day the backend was not running is
  not run later.
* The claim is atomic (``UNIQUE(kind, trading_date)`` inside one write
  transaction) and holds a lease that the running scheduler renews, so two
  schedulers, or a restarted one, never run the same day of a kind at the
  same time. A ``running`` row whose lease has run out was left by a process
  that stopped; it counts as an interrupted attempt and is run again. A lease
  more than one lease length ahead of the clock was renewed before the clock
  went back and has run out too, and a retry time further ahead than the
  longest retry delay is due now: a clock step never postpones a day by its
  size. (A forward step can end a live claim early; the portfolio lock and
  the pipeline's checkpoint keep a second run from writing or pushing twice.)
* ``failed`` is retried after each of ``retry_delays`` (by default 15 minutes
  after the first failure and 30 minutes after the second). After the last
  retry the row stays ``failed`` and one notification is published (dedupe key
  per kind and day) until a manual run is requested with ``request_run``
  (``POST /api/routines/{kind}/run``).
* Attempts already under way are followed up whatever day it is now: every
  tick also looks at the kind's rows of other days that still have an attempt
  due (a retry, a manual request, or a ``running`` row whose lease ran out)
  and runs them in a task of their own, so a retry that falls after midnight
  still runs and its last failure still notifies, and none of them holds up
  today's run. Days that were never started are not run later.
* A kind may give its attempts a deadline (``retry_until(trading_date)``): an
  attempt still due after it is not run but recorded as the final failure
  (category ``expired``, with the notification).
* ``waiting(reason, not_before)`` is not a failure: the run could not be done
  yet (the close snapshot before a market's close is final). The row is
  ``pending`` with category ``waiting`` (``status_text`` 等待中), the reason as
  its summary and ``next_attempt_at`` = ``not_before``; it runs again at the
  first tick at or after that moment, not at a retry slot, or at once after
  a manual request (``request_run``; still the same row, waiting again if the
  run cannot be done yet). The attempt does
  not count against ``retry_delays`` and nothing is notified. A wait that
  would end after the kind's deadline is recorded as the final failure
  (category ``expired``, with the notification).
* ``ok``, ``skipped`` and ``unavailable`` are final for the day.

Storage follows ``core.desk_store``'s rules: the schema is registered at
import (the start-up upgrade covers it), every write ensures it, and reads
(``run_for``, ``runs``, ``last_success``, the status) use ``ready()`` and never
create ``desk.sqlite3``; neither does a tick before a kind is due.

Nothing escapes the loop: errors are logged with the kind, the date, the
attempt and an error category or exception type only.

Demo mode (``TRACKER_DEMO_MODE=1``) is read-only: no scheduler is started, a
tick claims nothing and a manual run is refused, so the demo directory gets
no ``routine_runs`` row (nor a ``desk.sqlite3`` it did not have).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
import inspect
import json
import logging
import os
import re
import threading
import time as monotonic_time
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core import desk_store, notify, provider_errors
from core.market.calendar import SHANGHAI, SessionClock, load_holidays

logger = logging.getLogger(__name__)

# The summary of an attempt the backend's stop interrupted (it is retried at once after the restart).
INTERRUPTED = "运行被中断：后端在运行期间停止"

STATUSES = ("pending", "running", "ok", "failed", "skipped", "unavailable")
OUTCOMES = ("ok", "failed", "skipped", "unavailable")
STATUS_TEXT = {"pending": "等待运行", "running": "运行中", "ok": "已完成", "failed": "失败",
               "skipped": "已跳过", "unavailable": "不可用"}
# The category of a ``pending`` row that waits for a moment (``waiting``), and its status text.
WAITING = "waiting"
WAITING_TEXT = "等待中"
# The longest wait a ``waiting`` row may hold (``_claim``: a later one was set before the clock went back).
WAIT_LIMIT = timedelta(hours=24)
DEFAULT_RETRY_DELAYS = (timedelta(minutes=15), timedelta(minutes=30))
INTERVAL_SECONDS = 60.0
LEASE = timedelta(minutes=5)
# A claim's lease or a retry time this much further ahead than they can be set
# was written before the clock went back (``_claim``).
CLOCK_SLACK = timedelta(seconds=1)
MAX_SUMMARY = 500
MAX_DETAIL = 20000
MAX_LIMIT = 200
_SEARCH_DAYS = 40

SCHEMA = "routine_runs"
MIGRATIONS = [[
    """CREATE TABLE routine_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        trading_date TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK (status IN ('pending', 'running', 'ok', 'failed', 'skipped', 'unavailable')),
        attempts INTEGER NOT NULL DEFAULT 0,
        due_at TEXT,
        next_attempt_at TEXT,
        started_at TEXT,
        finished_at TEXT,
        lease_until TEXT,
        owner TEXT,
        summary TEXT NOT NULL DEFAULT '',
        error_category TEXT,
        detail TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (kind, trading_date))""",
]]

_KIND = re.compile(r"[a-z][a-z0-9_]{0,63}")

desk_store.register_schema(SCHEMA, MIGRATIONS)


def _ensure() -> None:
    """For writers: create or upgrade the ``routine_runs`` table."""
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)


def _stored() -> bool:
    """For readers: whether runs can be read. Never creates or migrates the store."""
    return desk_store.ready(SCHEMA, MIGRATIONS)


class UnknownRoutine(LookupError):
    """No routine of that kind is registered."""


class RunRefused(RuntimeError):
    """A manual run cannot be accepted now; the message says why, for the user."""


# -- results ------------------------------------------------------------------

@dataclass(frozen=True)
class Outcome:
    """What one run of a kind came to. Build it with the helpers below.

    ``commit`` (optional) publishes the run's result (a brief, say): the
    scheduler calls it inside the transaction that records the attempt, and
    only while this run still owns its claim, so a run whose claim another
    scheduler took over never publishes. It returns ``None`` to record this
    outcome, or another ``Outcome`` to record instead (having published
    nothing).
    """
    status: str
    summary: str = ""
    error_category: Optional[str] = None
    detail: Optional[dict] = None
    commit: Optional[Callable[[], Optional["Outcome"]]] = field(default=None, compare=False, repr=False)
    # ``waiting`` only: the moment the run is due again (aware).
    not_before: Optional[datetime] = None


def ok(summary: str = "", detail: Optional[dict] = None) -> Outcome:
    return Outcome("ok", summary, None, detail)


def failed(summary: str, category: str = "error", detail: Optional[dict] = None) -> Outcome:
    """Retried as the kind's ``retry_delays`` allow."""
    return Outcome("failed", summary, category, detail)


def skipped(reason: str, category: Optional[str] = None, detail: Optional[dict] = None) -> Outcome:
    """Nothing to do today (for example demo mode, or no data yet)."""
    return Outcome("skipped", reason, category, detail)


def unavailable(reason: str, category: str = "unavailable", detail: Optional[dict] = None) -> Outcome:
    """This installation cannot run the kind (for example a missing component)."""
    return Outcome("unavailable", reason, category, detail)


def waiting(reason: str, not_before: datetime, detail: Optional[dict] = None) -> Outcome:
    """Not done yet, and not a failure: run again at the first tick at or after ``not_before`` (aware).

    The row waits as ``pending`` (category ``WAITING``) with ``reason`` as its
    summary; the attempt uses up no retry and nothing is notified.
    """
    if not isinstance(not_before, datetime) or not_before.tzinfo is None:
        raise ValueError("waiting() needs an aware datetime")
    return Outcome("pending", reason, WAITING, detail, not_before=not_before)


# -- registry -----------------------------------------------------------------

@dataclass(frozen=True)
class Routine:
    kind: str
    due: Callable[[SessionClock], Optional[datetime]]
    run: Callable[[str], Any]
    title: str = ""
    retry_delays: Tuple[timedelta, ...] = field(default=DEFAULT_RETRY_DELAYS)
    # ``retry_until(trading_date)``: the moment after which an attempt of that day is
    # given up instead of run (None: no deadline).
    retry_until: Optional[Callable[[str], Optional[datetime]]] = None
    # ``refuse()``: why a manual run cannot be accepted now (a message for the user), or None.
    refuse: Optional[Callable[[], Optional[str]]] = None

    def __post_init__(self):
        if not isinstance(self.kind, str) or not _KIND.fullmatch(self.kind):
            raise ValueError("routine kind must be lowercase letters, digits and underscores")
        if not callable(self.due) or not callable(self.run):
            raise TypeError("a routine needs callable due and run")
        if self.retry_until is not None and not callable(self.retry_until):
            raise TypeError("retry_until must be callable")
        if not self.title:
            object.__setattr__(self, "title", self.kind)
        object.__setattr__(self, "retry_delays", tuple(self.retry_delays))

    @property
    def max_attempts(self) -> int:
        return 1 + len(self.retry_delays)


_registry: Dict[str, Routine] = {}
_registry_lock = threading.Lock()


def register(kind: str, due: Callable[[SessionClock], Optional[datetime]], run: Callable[[str], Any], *,
             title: str = "", retry_delays: Sequence[timedelta] = DEFAULT_RETRY_DELAYS,
             retry_until: Optional[Callable[[str], Optional[datetime]]] = None,
             refuse: Optional[Callable[[], Optional[str]]] = None) -> Routine:
    """Add a kind to the routines every scheduler runs; see the module docstring.

    Registering the same functions again has no further effect; another
    registration under a kind that is taken raises ``ValueError``.
    """
    routine = Routine(kind, due, run, title, tuple(retry_delays), retry_until, refuse)
    with _registry_lock:
        existing = _registry.get(kind)
        if existing is not None and (existing.due, existing.run) != (due, run):
            raise ValueError(f"routine kind {kind!r} is already registered")
        _registry.setdefault(kind, routine)
        return _registry[kind]


def registered() -> List[Routine]:
    with _registry_lock:
        return list(_registry.values())


# -- storage ------------------------------------------------------------------

def _aware(moment: datetime) -> datetime:
    return moment.replace(tzinfo=SHANGHAI) if moment.tzinfo is None else moment.astimezone(SHANGHAI)


def _iso(moment: datetime) -> str:
    """Shanghai wall time with its offset: every stored time has the same form."""
    return _aware(moment).isoformat(timespec="seconds")


def _parse(text: Optional[str]) -> Optional[datetime]:
    return None if not text else _aware(datetime.fromisoformat(text))


def _cut(text: str, limit: int = MAX_SUMMARY) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _encode_detail(detail: Optional[dict]) -> Optional[str]:
    if detail is None:
        return None
    try:
        encoded = desk_store.encode(detail if isinstance(detail, dict) else {"value": str(detail)})
    except (TypeError, ValueError):
        return desk_store.encode({"note": "detail was not JSON"})
    return encoded if len(encoded) <= MAX_DETAIL else desk_store.encode({"note": "detail was too large"})


def _decode(row) -> Optional[dict]:
    if row is None:
        return None
    item = dict(row)
    item["detail"] = json.loads(item["detail"]) if item["detail"] else None
    item["status_text"] = (WAITING_TEXT if item["status"] == "pending" and item.get("error_category") == WAITING
                           else STATUS_TEXT.get(item["status"], item["status"]))
    item.pop("owner", None)
    item.pop("lease_until", None)
    return item


def _select(db, kind: str, trading_date: str):
    return db.execute("SELECT * FROM routine_runs WHERE kind=? AND trading_date=?", (kind, trading_date)).fetchone()


def run_for(kind: str, trading_date: str) -> Optional[dict]:
    """The row of one kind and trading day, or None. Never creates the store."""
    if not _stored():
        return None
    with desk_store.connect() as db:
        return _decode(_select(db, kind, trading_date))


def runs(kind: Optional[str] = None, limit: int = 20) -> List[dict]:
    """Newest trading day first. Never creates the store."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    if not _stored():
        return []
    with desk_store.connect() as db:
        if kind is None:
            rows = db.execute("SELECT * FROM routine_runs ORDER BY trading_date DESC, id DESC LIMIT ?", (limit,))
        else:
            rows = db.execute("SELECT * FROM routine_runs WHERE kind=? ORDER BY trading_date DESC, id DESC LIMIT ?",
                              (kind, limit))
        return [_decode(row) for row in rows]


def last_success(kind: str) -> Optional[dict]:
    """The newest ``ok`` row of ``kind``, or None. Never creates the store."""
    if not _stored():
        return None
    with desk_store.connect() as db:
        return _decode(db.execute("SELECT * FROM routine_runs WHERE kind=? AND status='ok' "
                                  "ORDER BY trading_date DESC LIMIT 1", (kind,)).fetchone())


# Rows with an attempt still to come: a retry or a manual request, or a claim that may have been interrupted.
_OUTSTANDING = ("(status IN ('failed', 'pending') AND next_attempt_at IS NOT NULL) OR status = 'running'")


def outstanding(kind: str, exclude: Optional[str] = None) -> List[dict]:
    """Rows of ``kind`` (other than the day ``exclude``) with an attempt still to come, oldest first.

    Never creates the store.
    """
    if not _stored():
        return []
    with desk_store.connect() as db:
        rows = db.execute(f"SELECT * FROM routine_runs WHERE kind=? AND trading_date IS NOT ? AND ({_OUTSTANDING}) "
                          "ORDER BY trading_date", (kind, exclude))
        return [_decode(row) for row in rows]


# -- the scheduler ------------------------------------------------------------

_current: Optional["Scheduler"] = None


def current() -> Optional["Scheduler"]:
    """The scheduler this process started, if it is running."""
    return _current


def wall_clock() -> datetime:
    """The time a scheduler uses when none is given: the market runtime's session clock.

    That is the clock the pages and the routines read (the scripted ``now`` of
    fake quotes, real time otherwise), so all of them agree on the trading day.
    """
    try:
        from core.market.runtime import get_market_runtime
        return get_market_runtime().clock.now()
    except Exception as exc:   # a runtime that cannot be built must not stop the loop
        logger.warning("routine scheduler: the market clock failed: %s", type(exc).__name__)
        return datetime.now(SHANGHAI)


class Scheduler:
    """Runs the registered routines; see the module docstring.

    ``now`` (a callable returning the current time), ``holidays`` (a table as
    ``load_holidays`` returns it), ``sleep`` (an async callable taking seconds)
    and ``routines`` (instead of the registry) are for tests.
    """

    def __init__(self, routines: Optional[Sequence[Routine]] = None, *, now: Optional[Callable[[], datetime]] = None,
                 holidays=None, sleep: Optional[Callable[[float], Any]] = None, interval: float = INTERVAL_SECONDS,
                 lease: timedelta = LEASE, owner: Optional[str] = None):
        self._fixed = None if routines is None else {routine.kind: routine for routine in routines}
        self._now = now or wall_clock
        self._holidays = load_holidays() if holidays is None else holidays
        self._sleep = sleep
        self.interval = interval
        self.lease = lease
        self.owner = owner or uuid.uuid4().hex
        # (kind, trading_date) -> (task, run id): one task per day of a kind.
        self._active: Dict[Tuple[str, str], Tuple[asyncio.Task, int]] = {}
        # (kind, trading_date) -> (routine, claimed row), for a task cancelled before it began (``stop``).
        self._claimed: Dict[Tuple[str, str], Tuple["Routine", dict]] = {}
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._wake: Optional[asyncio.Event] = None
        self._beat: Optional[float] = None
        self.started_at: Optional[str] = None
        self.last_tick_at: Optional[str] = None
        self.last_error: Optional[str] = None

    # -- time and kinds ------------------------------------------------------

    def routines(self) -> Dict[str, Routine]:
        if self._fixed is not None:
            return dict(self._fixed)
        return {routine.kind: routine for routine in registered()}

    def clock(self, at: Optional[datetime] = None) -> SessionClock:
        return SessionClock(self._now if at is None else (lambda: at), self._holidays)

    def now(self) -> datetime:
        return self.clock().now()

    # -- the loop ------------------------------------------------------------

    def start(self) -> asyncio.Task:
        """Start the loop on the running event loop and make this the ``current()`` scheduler."""
        global _current
        if self._task is not None and not self._task.done():
            return self._task
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self.started_at = _iso(self.now())
        self._task = self._loop.create_task(self.run_forever(), name="routine-scheduler")
        _current = self
        return self._task

    async def stop(self) -> None:
        """Stop the loop and interrupt running routines (their rows are retried later)."""
        global _current
        if _current is self:
            _current = None
        tasks = [task for task in (self._task, *(task for task, _ in self._active.values())) if task is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
        # A task cancelled before its first step never entered ``_execute``, so its claimed row
        # would stay 'running' until its lease ran out: record the interruption for it here.
        for key, (task, run_id) in list(self._active.items()):
            if not task.done():
                continue
            routine, row = self._claimed.pop(key, (None, None))
            del self._active[key]
            if routine is not None and row is not None and row["id"] == run_id:
                with suppress(Exception):
                    await asyncio.to_thread(self._finish, routine, row, failed(INTERRUPTED, "interrupted"),
                                            retry_now=True)
        self._task = None

    def alive(self) -> bool:
        if self._task is None or self._task.done() or self._beat is None:
            return False
        return monotonic_time.monotonic() - self._beat < 3 * self.interval + 30

    def wake(self) -> None:
        """Make the loop look at the routines now (safe from any thread)."""
        if self._loop is not None and self._wake is not None:
            with suppress(RuntimeError):  # the loop has closed
                self._loop.call_soon_threadsafe(self._wake.set)

    async def run_forever(self) -> None:
        while True:
            if self._wake is not None:
                self._wake.clear()
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # tick() handles each kind; this is the last line of defence
                self.last_error = type(exc).__name__
                logger.warning("routine scheduler: tick failed: %s", type(exc).__name__)
            try:
                await self._pause()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("routine scheduler: sleep failed: %s", type(exc).__name__)
                await asyncio.sleep(1)

    async def _pause(self) -> None:
        if self._sleep is not None:
            await self._sleep(self.interval)
        elif self._wake is not None:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.interval)
        else:
            await asyncio.sleep(self.interval)

    async def tick(self) -> List[dict]:
        """Look at every kind once: renew running claims, claim and start what is due.

        Returns the rows claimed in this tick. Runs go on in their own tasks;
        ``settle()`` waits for them.
        """
        self._beat = monotonic_time.monotonic()
        self.last_tick_at = _iso(self.now())
        if mode() == "demo":
            return []   # the demo is read-only: no claim, no run, no row
        claimed = []
        for routine in self.routines().values():
            try:
                claimed += await self._tick_routine(routine)
            except Exception as exc:
                self.last_error = type(exc).__name__
                logger.warning("routine %s: scheduling failed: %s", routine.kind, type(exc).__name__)
        return claimed

    async def settle(self) -> None:
        """Wait until every routine started so far has finished (tests, shutdown)."""
        while self._active:
            tasks = [task for task, _ in self._active.values()]
            await asyncio.gather(*tasks, return_exceptions=True)

    def running_kinds(self) -> List[str]:
        return list(dict.fromkeys(kind for (kind, _day), (task, _) in self._active.items() if not task.done()))

    async def _tick_routine(self, routine: Routine) -> List[dict]:
        running = {day: run_id for (kind, day), (task, run_id) in list(self._active.items())
                   if kind == routine.kind and not task.done()}
        for run_id in running.values():
            await asyncio.to_thread(self._renew, run_id)
        rows = await asyncio.to_thread(self._claim_all, routine, frozenset(running))
        for row in rows:
            task = asyncio.get_running_loop().create_task(self._execute(routine, row),
                                                          name=f"routine-{routine.kind}-{row['trading_date']}")
            self._active[(routine.kind, row["trading_date"])] = (task, row["id"])
            self._claimed[(routine.kind, row["trading_date"])] = (routine, row)
        return rows

    # -- claiming ------------------------------------------------------------

    def _claim_all(self, routine: Routine, running: frozenset) -> List[dict]:
        """Today's run when it is due, and every attempt of another day that is due by now."""
        claimed = []
        today = self._claim_due(routine, running)
        if today is not None:
            claimed.append(today)
        busy = running | {row["trading_date"] for row in claimed}
        for row in outstanding(routine.kind):
            if row["trading_date"] in busy:
                continue
            taken = self._claim(routine, row["trading_date"], None, self.now())
            if taken is not None:
                claimed.append(taken)
        return claimed

    def _claim_due(self, routine: Routine, running: frozenset = frozenset()) -> Optional[dict]:
        clock = self.clock()
        now = clock.now()
        due_at = routine.due(clock)
        if due_at is None:
            return None
        due_at = _aware(due_at)
        if now < due_at:
            return None   # nothing can be claimed yet (a manual run is refused before then too): touch nothing
        if due_at.date().isoformat() in running:
            return None
        return self._claim(routine, due_at.date().isoformat(), due_at, now)

    def _deadline(self, routine: Routine, trading_date: str) -> Optional[datetime]:
        if routine.retry_until is None:
            return None
        try:
            moment = routine.retry_until(trading_date)
        except Exception as exc:   # a broken deadline must not stop the retries
            logger.warning("routine %s: retry_until failed: %s", routine.kind, type(exc).__name__)
            return None
        return None if moment is None else _aware(moment)

    def _claim(self, routine: Routine, trading_date: str, due_at: Optional[datetime],
               now: datetime) -> Optional[dict]:
        """Atomically take the day's row for this scheduler, or return None.

        ``due_at`` is needed only to create the day's row; ``None`` claims an existing row only.
        """
        _ensure()
        stamp, lease_until = _iso(now), _iso(now + self.lease)
        with desk_store.transaction() as db:
            row = _select(db, routine.kind, trading_date)
            if row is None:
                if due_at is None or now < due_at:
                    return None
                db.execute("INSERT INTO routine_runs(kind, trading_date, status, attempts, due_at, started_at, "
                           "lease_until, owner, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (routine.kind, trading_date, "running", 1, _iso(due_at), stamp, lease_until,
                            self.owner, stamp, stamp))
                return _decode(_select(db, routine.kind, trading_date))
            status = row["status"]
            deadline = self._deadline(routine, trading_date)
            expired = deadline is not None and now >= deadline
            if status == "running":
                lease = _parse(row["lease_until"])
                # Valid only up to one lease length ahead: a later one was renewed before the
                # clock went back, and holding the day for the size of the step is not bounded.
                if lease is not None and now < lease <= now + max(self.lease, LEASE) + CLOCK_SLACK:
                    return None  # another scheduler is running it
                logger.warning("routine %s %s: attempt %s was interrupted (its claim expired)",
                               routine.kind, trading_date, row["attempts"])
                if expired:
                    self._expire(db, routine, row, now, deadline, "运行被中断：后端在运行期间停止")
                    return None
                if row["attempts"] >= routine.max_attempts:
                    self._record(db, routine, row, now, failed("运行被中断：后端在运行期间停止", "interrupted"))
                    return None
            elif status in ("failed", "pending"):
                next_attempt = _parse(row["next_attempt_at"])
                if next_attempt is None:
                    return None
                if expired:
                    self._expire(db, routine, row, now, deadline, row["summary"])
                    return None
                # A retry time further ahead than the longest retry delay (a wait: than ``WAIT_LIMIT``)
                # was set before the clock went back: it is due now rather than after the size of the step.
                limit = max(routine.retry_delays, default=timedelta(0))
                if row["error_category"] == WAITING:
                    limit = max(limit, WAIT_LIMIT)
                if now < next_attempt <= now + limit + CLOCK_SLACK:
                    return None
            else:
                return None  # ok, skipped and unavailable are final for the day
            db.execute("UPDATE routine_runs SET status='running', attempts=attempts+1, started_at=?, "
                       "finished_at=NULL, next_attempt_at=NULL, lease_until=?, owner=?, updated_at=? WHERE id=?",
                       (stamp, lease_until, self.owner, stamp, row["id"]))
            return _decode(_select(db, routine.kind, trading_date))

    def _renew(self, run_id: int) -> None:
        now = self.now()
        _ensure()
        with desk_store.transaction() as db:
            db.execute("UPDATE routine_runs SET lease_until=?, updated_at=? "
                       "WHERE id=? AND owner=? AND status='running'",
                       (_iso(now + self.lease), _iso(now), run_id, self.owner))

    # -- running -------------------------------------------------------------

    async def _execute(self, routine: Routine, row: dict) -> None:
        trading_date = row["trading_date"]
        try:
            try:
                outcome = await self._call(routine, trading_date)
            except asyncio.CancelledError:
                with suppress(Exception):
                    self._finish(routine, row, failed(INTERRUPTED, "interrupted"), retry_now=True)
                raise
            except Exception as exc:
                logger.warning("routine %s %s attempt %s raised %s", routine.kind, trading_date, row["attempts"],
                               type(exc).__name__)
                # The class name and message stay in the log; the run row and its notice get a fixed text.
                outcome = failed(f"内部错误：{provider_errors.classify(exc)}", "internal")
            try:
                await asyncio.to_thread(self._finish, routine, row, outcome)
            except Exception as exc:
                self.last_error = type(exc).__name__
                logger.warning("routine %s %s: recording the result failed: %s", routine.kind, trading_date,
                               type(exc).__name__)
        finally:
            key = (routine.kind, trading_date)
            active = self._active.get(key)
            if active is not None and active[1] == row["id"]:
                del self._active[key]
                self._claimed.pop(key, None)

    @staticmethod
    async def _call(routine: Routine, trading_date: str) -> Outcome:
        run = routine.run
        if inspect.iscoroutinefunction(run) or inspect.iscoroutinefunction(getattr(run, "__call__", None)):
            result = await run(trading_date)
        else:
            result = await asyncio.to_thread(run, trading_date)
            if inspect.isawaitable(result):
                result = await result
        if not isinstance(result, Outcome) or not (
                result.status in OUTCOMES or (result.status == "pending" and result.not_before is not None)):
            return failed("内部错误（运行结果无效）", "internal")
        return result

    def _finish(self, routine: Routine, row: dict, outcome: Outcome, *, retry_now: bool = False) -> None:
        now = self.now()
        _ensure()
        with desk_store.transaction() as db:
            current_row = db.execute("SELECT * FROM routine_runs WHERE id=?", (row["id"],)).fetchone()
            if current_row is None or current_row["status"] != "running" or current_row["owner"] != self.owner:
                logger.warning("routine %s %s: another scheduler took the run over; result not recorded",
                               routine.kind, row["trading_date"])
                return
            if outcome.commit is not None:
                # Published only while this run owns the claim, atomically with its row.
                try:
                    outcome = outcome.commit() or outcome
                except Exception as exc:
                    logger.warning("routine %s %s: publishing the result failed: %s", routine.kind,
                                   row["trading_date"], type(exc).__name__)
                    outcome = failed("结果未能保存", "internal")
            self._record(db, routine, current_row, now, outcome, retry_now=retry_now)

    def _record(self, db, routine: Routine, row, now: datetime, outcome: Outcome, *, retry_now: bool = False,
                final: bool = False) -> None:
        """Write a finished attempt; after the last failed one, publish the notification too.

        ``final`` gives up even when attempts are left (a deadline has passed).
        """
        stamp = _iso(now)
        if outcome.status == "pending" and outcome.not_before is not None and not final:
            deadline = self._deadline(routine, row["trading_date"])
            if deadline is not None and _aware(outcome.not_before) > deadline:
                # It could only run after the deadline: given up now, as an expired attempt would be.
                outcome, final = failed(f"{_cut(outcome.summary, 200)}；但已过补做期限（{deadline:%m-%d %H:%M}）",
                                        "expired"), True
        if outcome.status == "pending" and outcome.not_before is not None and not final:
            # A wait: no retry used up (the claim counted one), nothing notified.
            db.execute("UPDATE routine_runs SET status='pending', attempts=MAX(attempts-1, 0), finished_at=?, "
                       "summary=?, error_category=?, detail=?, next_attempt_at=?, lease_until=NULL, updated_at=? "
                       "WHERE id=?",
                       (stamp, _cut(outcome.summary), WAITING, _encode_detail(outcome.detail),
                        _iso(outcome.not_before), stamp, row["id"]))
            return
        next_attempt = None
        if outcome.status == "failed":
            attempts = row["attempts"]
            if attempts < routine.max_attempts and not final:
                next_attempt = stamp if retry_now else _iso(now + routine.retry_delays[attempts - 1])
            logger.warning("routine %s %s attempt %s failed: %s%s", routine.kind, row["trading_date"], attempts,
                           outcome.error_category, "" if next_attempt else " (no more automatic retries)")
        db.execute("UPDATE routine_runs SET status=?, finished_at=?, summary=?, error_category=?, detail=?, "
                   "next_attempt_at=?, lease_until=NULL, updated_at=? WHERE id=?",
                   (outcome.status, stamp, _cut(outcome.summary), outcome.error_category,
                    _encode_detail(outcome.detail), next_attempt, stamp, row["id"]))
        if outcome.status == "failed" and next_attempt is None:
            # In the same transaction: "gave up" and "told the user" commit together, once per day.
            ending = ("已过自动补做的期限，不会再自动重试。" if outcome.error_category == "expired"
                      else "不会再自动重试；问题解决后可手动重试。")
            notify.publish(
                f"routine.{routine.kind}", "warning", f"{routine.title}未完成",
                f"{row['trading_date']} 的{routine.title}尝试 {row['attempts']} 次后仍未完成："
                f"{_cut(outcome.summary, 300)}。{ending}",
                data={"kind": routine.kind, "trading_date": row["trading_date"], "run_id": row["id"]},
                dedupe_key=f"routine:{routine.kind}:{row['trading_date']}:failed")

    def _expire(self, db, routine: Routine, row, now: datetime, deadline: datetime, reason: Optional[str]) -> None:
        """Give up an attempt that is due only after the kind's deadline (with the one notification)."""
        logger.warning("routine %s %s: the retry deadline has passed; giving up", routine.kind, row["trading_date"])
        before = _cut(reason or "", 200)
        summary = f"已过补做期限（{deadline:%m-%d %H:%M}），未再运行" + (f"；上次结果：{before}" if before else "")
        self._record(db, routine, row, now, failed(summary, "expired"), final=True)

    # -- manual runs ---------------------------------------------------------

    def request_run(self, kind: str) -> dict:
        """Ask for a run of today's ``kind`` now (also after the last automatic retry).

        Raises ``UnknownRoutine`` or ``RunRefused`` (with a message for the
        user) when the run cannot be accepted. The loop picks the request up at
        once; the returned row is ``pending``. A row that waits (``waiting``)
        keeps its reason and category and is run now instead of at its
        ``not_before``.
        """
        routine = self.routines().get(kind)
        if routine is None:
            raise UnknownRoutine(kind)
        if mode() == "demo":
            raise RunRefused("演示模式只读，例行任务不会运行。")
        if routine.refuse is not None:
            reason = routine.refuse()
            if reason:
                raise RunRefused(reason)
        clock = self.clock()
        now = clock.now()
        due_at = routine.due(clock)
        if due_at is None:
            raise RunRefused(f"今天不运行{routine.title}（非交易日）。")
        due_at = _aware(due_at)
        if now < due_at:
            raise RunRefused(f"还没到{routine.title}的运行时间（{due_at:%H:%M} 之后）。")
        trading_date, stamp = due_at.date().isoformat(), _iso(now)
        _ensure()
        with desk_store.transaction() as db:
            row = _select(db, kind, trading_date)
            if row is None:
                db.execute("INSERT INTO routine_runs(kind, trading_date, status, attempts, due_at, next_attempt_at, "
                           "summary, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           (kind, trading_date, "pending", 0, _iso(due_at), stamp, "已请求手动运行", stamp, stamp))
            elif row["status"] == "running":
                raise RunRefused(f"{routine.title}正在运行，请稍后查看结果。")
            elif row["status"] == "ok":
                raise RunRefused(f"今天的{routine.title}已经完成，无需再次运行。")
            elif row["status"] != "pending":
                db.execute("UPDATE routine_runs SET status='pending', next_attempt_at=?, summary=?, updated_at=? "
                           "WHERE id=?", (stamp, "已请求手动运行", stamp, row["id"]))
            elif row["error_category"] == WAITING:
                # A wait is looked at again now, on the same row: it still uses up no retry and notifies nothing,
                # and the run waits on with the same row if it cannot be done yet.
                db.execute("UPDATE routine_runs SET next_attempt_at=?, updated_at=? WHERE id=?",
                           (stamp, stamp, row["id"]))
            result = _decode(_select(db, kind, trading_date))
        self.wake()
        return result

    # -- status --------------------------------------------------------------

    def routine_status(self, routine: Routine) -> dict:
        clock = self.clock()
        now = clock.now()
        due_at = routine.due(clock)
        due_at = None if due_at is None else _aware(due_at)
        trading_date = None if due_at is None else due_at.date().isoformat()
        today = None if trading_date is None else run_for(routine.kind, trading_date)
        earlier = outstanding(routine.kind, exclude=trading_date)
        for row in [today, *earlier]:
            if row is not None and isinstance(row["detail"], dict):
                # Status is polled; the output tail stays in ``runs()``.
                row["detail"] = {key: value for key, value in row["detail"].items() if key != "log_tail"}
        success = last_success(routine.kind)
        following = self._next_due(routine, now, due_at, today)
        retries = [row["next_attempt_at"] for row in earlier if row["next_attempt_at"]]
        if retries:
            following = min([following, *retries] if following else retries, key=_parse)
        return {
            "kind": routine.kind,
            "title": routine.title,
            "trading_date": trading_date,
            "due_at": None if due_at is None else _iso(due_at),
            "next_due_at": following,
            "today": today,
            "last_success": None if success is None else {
                key: success[key] for key in ("trading_date", "finished_at", "summary")},
            # Runs of earlier days with an attempt still to come (a retry after midnight, say).
            "outstanding": earlier,
        }

    def _next_due(self, routine: Routine, now: datetime, due_at: Optional[datetime], today: Optional[dict]):
        """When the scheduler will next run this kind by itself, or None."""
        if today is not None:
            if today["status"] in ("failed", "pending") and today["next_attempt_at"]:
                return today["next_attempt_at"]
        elif due_at is not None:
            return _iso(due_at)  # in the past: it runs at the next tick
        day = now.date()
        for offset in range(1, _SEARCH_DAYS + 1):
            moment = datetime.combine(day + timedelta(days=offset), time(0, 0), tzinfo=SHANGHAI)
            following = routine.due(self.clock(moment))
            if following is not None:
                return _iso(following)
        return None


def mode() -> str:
    """How the backend was started: routines run only in ``normal``.

    Demo wins over lab-only: a process with both flags set is read-only.
    """
    if os.environ.get("TRACKER_DEMO_MODE") == "1":
        return "demo"
    if os.environ.get("TRACKER_LAB_ONLY") == "1":
        return "lab_only"
    return "normal"


def status_report() -> dict:
    """``GET /api/routines/status``: every registered kind and whether the loop runs."""
    from core.routines import load_kinds
    load_kinds()
    running = current()
    view = running or Scheduler()
    alive = bool(running and running.alive())
    items = []
    for routine in view.routines().values():
        try:
            items.append(view.routine_status(routine))
        except Exception as exc:
            logger.warning("routine %s: status failed: %s", routine.kind, type(exc).__name__)
            items.append({"kind": routine.kind, "title": routine.title, "error": "无法读取状态"})
    note = None
    if mode() == "demo":
        note = "演示模式：例行任务不运行，也不写入任何记录；页面显示的是演示数据。"
    elif not alive:
        note = ("后台调度器未运行：例行任务只在后端运行期间自动执行，研究模式（TRACKER_LAB_ONLY=1）不运行。"
                if mode() == "lab_only" else "后台调度器未运行：例行任务只在后端运行期间自动执行。")
    return {
        "now": _iso(view.now()),
        "scheduler": {"alive": alive, "mode": mode(), "started_at": running.started_at if running else None,
                      "last_tick_at": running.last_tick_at if running else None, "note": note},
        "routines": items,
    }


@asynccontextmanager
async def background(app):
    """``features.background_tasks()`` factory: the scheduler runs while the app runs.

    Not in demo mode: the demo is read-only, so no scheduler starts and no
    run row is written (``tick`` and ``request_run`` refuse there as well).
    """
    if mode() == "demo":
        yield None
        return
    from core.routines import load_kinds
    load_kinds()
    scheduler = Scheduler()
    scheduler.start()
    with suppress(AttributeError):
        app.state.routine_scheduler = scheduler
    try:
        yield scheduler
    finally:
        await scheduler.stop()
