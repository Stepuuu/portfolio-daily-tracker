"""Finished close reports, told to listeners (extensions) once per report content.

The engine pipeline (``engine/scripts/portfolio_daily_update.py``) makes the
day's snapshot, its ``history.csv`` row and the Markdown report, and records in
its checkpoint ``PORTFOLIO_DIR/pipeline-state/<day>.json`` which snapshot each
stage was made from (``snapshot_sha``, ``history_snapshot``,
``report_snapshot``) and the hash of the report file it wrote
(``report_sha``). Every route that makes a report writes that checkpoint: the
backend's close snapshot routine (``auto-pipeline``), a cron job or the
engine's own scheduler, ``--action pipeline`` after a late entry, the Feishu
workbench's report and the assistant's ``run_portfolio_pipeline``. That is why
a finished report is read from the checkpoint and not from the routine's run
row: the routine makes a day once (``ok`` is final for the day), while a late
entry makes the day again by one of the other routes, often in another process.

``completed(day)`` is the day's report when it is finished: the checkpoint says
that the history row and the report were made from the snapshot now on disk,
and the report file is still the one the pipeline made. A checkpoint of a
release before 4.0.0 (without those hashes), a report made by hand without the
pipeline, and a report being made right now are not finished.

``Watcher`` looks every ``POLL_SECONDS`` at the checkpoints written in the last
``LOOKBACK`` and calls each listener once per ``(trading day, report_sha)``:

* in a thread of its own (a plain function, or an ``async def``, which then
  runs in its own event loop in that thread), never on the application's event
  loop. The watcher only reads the pipeline's files and takes none of its
  locks, so it can neither break nor delay the snapshot. One call per listener
  at a time: a listener that hangs holds up only itself;
* a call that raises is made again after each of ``RETRY_DELAYS`` while that
  report is still the day's, then given up (logged). A newer report of the same
  day is another report and is delivered on its own;
* the first time a listener is seen for a portfolio directory, the reports
  already finished then are recorded as known and not delivered: enabling a
  listener does not resend the last days' reports;
* at least once: a process that stops during a call leaves its claim, which is
  taken up again when its lease (``LEASE``) has run out, so a listener can see
  the same report twice after a crash or a restart in the middle of a call. A
  listener that must not repeat itself keys its effect by ``report_sha``.

What each listener was told is kept in ``desk-extensions.sqlite3`` beside
``desk.sqlite3`` (``desk_store.side_database``), so ``desk.sqlite3`` is the same
with or without extensions. The claim is atomic, so two backends on one
portfolio directory do not both call a listener for one report (unless a call
outlives the lease).

Logs carry the listener's name, the day, the attempt and exception types only.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import logging
from pathlib import Path
import re
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import desk_store
from core.ledger import portfolio_directory

logger = logging.getLogger(__name__)

POLL_SECONDS = 60.0
# Checkpoints written longer ago than this are not looked at.
LOOKBACK = timedelta(days=7)
# How long a claim holds; a ``running`` row older than this was left by a process that stopped.
LEASE = timedelta(minutes=15)
RETRY_DELAYS = (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2))
MAX_ATTEMPTS = 1 + len(RETRY_DELAYS)
# How long ``stop`` waits for calls in progress before it gives up waiting (their threads run on).
STOP_SECONDS = 5.0
FILENAME = "desk-extensions.sqlite3"
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")

_TABLES = (
    """CREATE TABLE IF NOT EXISTS report_listeners (
        listener TEXT PRIMARY KEY,
        first_seen_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS report_deliveries (
        listener TEXT NOT NULL,
        trading_date TEXT NOT NULL,
        report_sha TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('known', 'running', 'delivered', 'failed')),
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT,
        lease_until TEXT,
        owner TEXT,
        error TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (listener, trading_date, report_sha))""",
)

Listener = Callable[[dict], Any]


# -- finished reports ---------------------------------------------------------

def _sha(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def completed(trading_date: str, *, portfolio_dir=None, engine_dir=None) -> Optional[dict]:
    """The day's finished report, or None; see the module docstring.

    ``{trading_date, report, report_sha, snapshot_sha, report_file, snapshot_file}``:
    ``report`` is the Markdown text whose SHA-256 is ``report_sha``. Never writes.
    """
    from core.routines import close_snapshot
    engine = engine_dir if engine_dir is not None else close_snapshot.default_engine_dir()
    if engine is None or not isinstance(trading_date, str) or not _DAY.fullmatch(trading_date):
        return None   # no engine in this installation (the Docker backend image): no pipeline reports here
    portfolio = Path(portfolio_dir if portfolio_dir is not None else portfolio_directory())
    state = close_snapshot._checkpoint(portfolio, trading_date)
    made_from, report_sha = state.get("snapshot_sha"), state.get("report_sha")
    if not (state.get("history") and state.get("report") and isinstance(made_from, str) and made_from
            and isinstance(report_sha, str) and report_sha
            and state.get("history_snapshot") == made_from and state.get("report_snapshot") == made_from):
        return None
    snapshot = portfolio / "snapshots" / f"{trading_date}.json"
    if _sha(snapshot) != made_from:
        return None   # another snapshot of the day is being made, or was made by another route since
    report = close_snapshot._report_file(engine, trading_date)
    try:
        content = report.read_bytes()
        text = content.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if hashlib.sha256(content).hexdigest() != report_sha:
        return None   # replaced or changed since the pipeline made it
    return {"trading_date": trading_date, "report": text, "report_sha": report_sha, "snapshot_sha": made_from,
            "report_file": str(report.resolve()), "snapshot_file": str(snapshot.resolve())}


def recent(now: datetime, *, portfolio_dir=None, engine_dir=None, lookback: timedelta = LOOKBACK) -> List[dict]:
    """The finished reports of the days whose checkpoint was written in the last ``lookback``, oldest day first."""
    portfolio = Path(portfolio_dir if portfolio_dir is not None else portfolio_directory())
    cutoff = now.timestamp() - lookback.total_seconds()
    days = []
    for path in (portfolio / "pipeline-state").glob("*.json"):
        if not _DAY.fullmatch(path.stem):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        days.append(path.stem)
    found = (completed(day, portfolio_dir=portfolio, engine_dir=engine_dir) for day in sorted(days))
    return [report for report in found if report is not None]


# -- telling the listeners ----------------------------------------------------

def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(text: Optional[str]) -> Optional[datetime]:
    return None if not text else datetime.fromisoformat(text)


async def _awaited(awaitable):
    return await awaitable


def _in_thread(function: Listener, report: dict, name: str) -> "asyncio.Future":
    """Call ``function(report)`` in a new daemon thread; the future ends when it returns or raises.

    A daemon thread, not the default executor: a listener that hangs must not
    use up the threads the scheduler and the requests need, nor keep the
    process from exiting.
    """
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def settle(error):
        if future.done():
            return
        if error is None:
            future.set_result(None)
        else:
            future.set_exception(error)

    def target():
        error = None
        try:
            result = function(report)
            if inspect.isawaitable(result):
                asyncio.run(_awaited(result))
        except BaseException as exc:   # also SystemExit: it ends this thread only
            error = exc if isinstance(exc, Exception) else RuntimeError("the listener ended its thread")
        with suppress(RuntimeError):   # the event loop has closed meanwhile
            loop.call_soon_threadsafe(settle, error)

    threading.Thread(target=target, name=name, daemon=True).start()
    return future


class Watcher:
    """Tells each listener about every finished close report once; see the module docstring.

    ``listeners`` maps a stable name (the extension's module name) to its
    function. ``failed(name, report, exc)``, when given, is called when a call
    raises. ``now`` (aware datetimes), ``interval``, ``portfolio_dir``,
    ``engine_dir`` and ``owner`` are for tests.
    """

    def __init__(self, listeners: Dict[str, Listener], *, failed: Optional[Callable] = None,
                 now: Optional[Callable[[], datetime]] = None, interval: Optional[float] = None,
                 portfolio_dir=None, engine_dir=None, owner: Optional[str] = None):
        self.listeners = dict(listeners)
        self._failed = failed
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.interval = POLL_SECONDS if interval is None else interval
        self.portfolio_dir, self.engine_dir = portfolio_dir, engine_dir
        self.owner = owner or uuid.uuid4().hex
        self._calls: Dict[str, asyncio.Task] = {}
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._wake: Optional[asyncio.Event] = None

    def now(self) -> datetime:
        return self._now().astimezone(timezone.utc)

    # -- the loop ------------------------------------------------------------

    def start(self) -> asyncio.Task:
        if self._task is not None and not self._task.done():
            return self._task
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._task = self._loop.create_task(self.run_forever(), name="close-report-watcher")
        return self._task

    async def stop(self) -> None:
        """Stop looking; wait up to ``STOP_SECONDS`` for calls in progress (a call cut off keeps its claim)."""
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        calls = [task for task in self._calls.values() if not task.done()]
        if calls:
            await asyncio.wait(calls, timeout=STOP_SECONDS)
        for task in calls:
            task.cancel()
        for task in calls:
            with suppress(asyncio.CancelledError, Exception):
                await task

    def wake(self) -> None:
        if self._loop is not None and self._wake is not None:
            with suppress(RuntimeError):   # the loop has closed
                self._loop.call_soon_threadsafe(self._wake.set)

    async def run_forever(self) -> None:
        while True:
            if self._wake is not None:
                self._wake.clear()
            try:
                await self.poll()
            except asyncio.CancelledError:
                raise
            except Exception as exc:   # poll() handles each listener; this is the last line of defence
                logger.warning("close report watcher: a look failed: %s", type(exc).__name__)
            if self._wake is None:
                await asyncio.sleep(self.interval)
            else:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), self.interval)

    async def poll(self) -> List[Tuple[str, str, str]]:
        """Look once and start the calls that are due; ``[(listener, trading_date, report_sha)]`` started."""
        now = self.now()
        reports = await asyncio.to_thread(recent, now, portfolio_dir=self.portfolio_dir, engine_dir=self.engine_dir)
        started = []
        for name, listener in self.listeners.items():
            call = self._calls.get(name)
            if call is not None and not call.done():
                continue   # one call per listener at a time
            try:
                report = await asyncio.to_thread(self._claim, name, reports, now)
            except Exception as exc:
                logger.warning("close report listener %s: claiming failed: %s", name, type(exc).__name__)
                continue
            if report is not None:
                self._calls[name] = asyncio.get_running_loop().create_task(
                    self._call(name, listener, report), name=f"close-report-{name}")
                started.append((name, report["trading_date"], report["report_sha"]))
        return started

    async def settle(self) -> None:
        """Wait until every call started so far has ended (tests)."""
        while any(not task.done() for task in self._calls.values()):
            await asyncio.gather(*self._calls.values(), return_exceptions=True)

    # -- one call ------------------------------------------------------------

    async def _call(self, name: str, listener: Listener, report: dict) -> None:
        error = None
        try:
            # A copy: what the listener does to it changes nothing here.
            await _in_thread(listener, dict(report), f"close-report-{name}")
        except asyncio.CancelledError:
            raise   # stop(): the claim stays until its lease runs out
        except Exception as exc:
            error = exc
            if self._failed is not None:
                try:
                    self._failed(name, report, exc)
                except Exception as hook:
                    logger.warning("close report listener %s: recording a failure failed: %s", name,
                                   type(hook).__name__)
        try:
            await asyncio.to_thread(self._finish, name, report, error)
        except Exception as exc:
            logger.warning("close report listener %s %s: recording the result failed: %s", name,
                           report["trading_date"], type(exc).__name__)
        self.wake()   # another report may be due for this listener: look again now

    # -- storage -------------------------------------------------------------

    def _transaction(self, write: Callable[[Any], Any]):
        with desk_store.side_database(FILENAME) as db:
            for statement in _TABLES:
                db.execute(statement)
            db.execute("BEGIN IMMEDIATE")
            try:
                result = write(db)
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")
            return result

    def _claim(self, name: str, reports: List[dict], now: datetime) -> Optional[dict]:
        """Atomically take the first report that is due for ``name``; the report with ``replaces`` and ``attempt``."""
        return self._transaction(lambda db: self._claim_in(db, name, reports, now))

    def _claim_in(self, db, name: str, reports: List[dict], now: datetime) -> Optional[dict]:
        stamp = _iso(now)
        if db.execute("SELECT 1 FROM report_listeners WHERE listener=?", (name,)).fetchone() is None:
            # The first look: what is finished now is known already, not news.
            db.execute("INSERT INTO report_listeners(listener, first_seen_at) VALUES (?,?)", (name, stamp))
            for report in reports:
                db.execute("INSERT OR IGNORE INTO report_deliveries(listener, trading_date, report_sha, status, "
                           "updated_at) VALUES (?,?,?,'known',?)",
                           (name, report["trading_date"], report["report_sha"], stamp))
            if reports:
                logger.info("close report listener %s: first look, %s finished report(s) recorded as known",
                            name, len(reports))
            return None
        lease = _iso(now + LEASE)
        for report in reports:
            key = (name, report["trading_date"], report["report_sha"])
            row = db.execute("SELECT * FROM report_deliveries WHERE listener=? AND trading_date=? AND report_sha=?",
                             key).fetchone()
            if row is None:
                attempt = 1
                db.execute("INSERT INTO report_deliveries(listener, trading_date, report_sha, status, attempts, "
                           "lease_until, owner, updated_at) VALUES (?,?,?,'running',1,?,?,?)",
                           (*key, lease, self.owner, stamp))
            else:
                if row["status"] in ("known", "delivered"):
                    continue
                if row["status"] == "running":
                    until = _parse(row["lease_until"])
                    # A lease further ahead than one lease length was set before the clock went back.
                    if until is not None and now < until <= now + LEASE:
                        continue
                    logger.warning("close report listener %s %s: attempt %s was interrupted", name,
                                   report["trading_date"], row["attempts"])
                    if row["attempts"] >= MAX_ATTEMPTS:
                        db.execute("UPDATE report_deliveries SET status='failed', error='interrupted', "
                                   "next_attempt_at=NULL, lease_until=NULL, updated_at=? "
                                   "WHERE listener=? AND trading_date=? AND report_sha=?", (stamp, *key))
                        continue
                else:   # failed
                    due = _parse(row["next_attempt_at"])
                    if due is None:
                        continue   # given up
                    if now < due <= now + max(RETRY_DELAYS):
                        continue
                attempt = row["attempts"] + 1
                db.execute("UPDATE report_deliveries SET status='running', attempts=?, lease_until=?, owner=?, "
                           "updated_at=? WHERE listener=? AND trading_date=? AND report_sha=?",
                           (attempt, lease, self.owner, stamp, *key))
            earlier = db.execute("SELECT report_sha FROM report_deliveries WHERE listener=? AND trading_date=? "
                                 "AND report_sha<>? AND status IN ('known', 'delivered') "
                                 "ORDER BY updated_at DESC LIMIT 1", key).fetchone()
            return {**report, "replaces": earlier[0] if earlier else None, "attempt": attempt}
        return None

    def _finish(self, name: str, report: dict, error: Optional[BaseException]) -> None:
        now = self.now()
        stamp = _iso(now)
        key = (name, report["trading_date"], report["report_sha"])

        def write(db):
            row = db.execute("SELECT * FROM report_deliveries WHERE listener=? AND trading_date=? AND report_sha=?",
                             key).fetchone()
            if row is None or row["status"] != "running" or row["owner"] != self.owner:
                logger.warning("close report listener %s %s: another process took the call over; result not "
                               "recorded", name, report["trading_date"])
                return
            if error is None:
                db.execute("UPDATE report_deliveries SET status='delivered', error=NULL, next_attempt_at=NULL, "
                           "lease_until=NULL, updated_at=? WHERE listener=? AND trading_date=? AND report_sha=?",
                           (stamp, *key))
                return
            attempts = row["attempts"]
            retry = _iso(now + RETRY_DELAYS[attempts - 1]) if attempts <= len(RETRY_DELAYS) else None
            db.execute("UPDATE report_deliveries SET status='failed', error=?, next_attempt_at=?, lease_until=NULL, "
                       "updated_at=? WHERE listener=? AND trading_date=? AND report_sha=?",
                       (type(error).__name__, retry, stamp, *key))
            logger.warning("close report listener %s %s attempt %s failed: %s%s", name, report["trading_date"],
                           attempts, type(error).__name__,
                           f"; next attempt at {retry}" if retry else "; given up")

        self._transaction(write)


def deliveries(listener: Optional[str] = None) -> List[dict]:
    """What the listeners were told, newest first (tests and diagnostics). Never creates the file."""
    with desk_store.side_database(FILENAME, create=False) as db:
        if db is None:
            return []
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_deliveries'").fetchone()
        if exists is None:
            return []
        query = ("SELECT listener, trading_date, report_sha, status, attempts, next_attempt_at, error, updated_at "
                 "FROM report_deliveries")
        rows = (db.execute(query + " ORDER BY updated_at DESC, trading_date DESC", ()) if listener is None
                else db.execute(query + " WHERE listener=? ORDER BY updated_at DESC, trading_date DESC", (listener,)))
        return [dict(row) for row in rows]
