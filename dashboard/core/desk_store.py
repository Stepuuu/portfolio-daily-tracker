"""Shared SQLite file for the daily-desk features: ``PORTFOLIO_DIR/desk.sqlite3``.

Notifications, proposals, the watchlist, alert rules and routine runs keep
their tables here. The ledger keeps its own file and is not touched.

The location is resolved on every call, so tests and the demo can point
``PORTFOLIO_DIR`` at a temporary directory at any time. The file is created
with mode 0600 in WAL mode. Every time a connection is opened, the file and
its ``-wal``/``-shm`` side files lose any group or other permission they have,
also when they existed before.

How a feature module uses it::

    from core import desk_store

    MIGRATIONS = [
        ["CREATE TABLE watchlist (ticker TEXT PRIMARY KEY, added_at TEXT NOT NULL)"],
        # A later release appends a step. Never edit a step that has shipped.
        ["ALTER TABLE watchlist ADD COLUMN note TEXT"],
    ]
    desk_store.register_schema("watchlist", MIGRATIONS)   # upgraded at start-up

    def add(ticker):                                      # a write
        desk_store.ensure_schema("watchlist", MIGRATIONS)
        with desk_store.transaction() as db:
            db.execute("INSERT OR IGNORE INTO watchlist(ticker, added_at) VALUES (?,?)",
                       (ticker, desk_store.now()))

    def tickers():                                        # a read
        if not desk_store.ready("watchlist", MIGRATIONS):
            return []                                     # nothing stored yet
        with desk_store.connect() as db:
            return [row["ticker"] for row in db.execute("SELECT ticker FROM watchlist")]

Reading never creates or changes the file. This keeps the read-only demo
read-only and lets a GET request run against any copy of the data:

* ``ready()`` is ``False`` while the file or the schema does not exist yet, so
  the reader returns its empty or default result. It raises
  ``DeskStoreError`` when the schema is older than the code (or newer).
* Schemas are upgraded by the first write (``ensure_schema()``) and, for an
  existing file, once when the application starts outside demo mode
  (``migrate_registered()``, run by ``backend/features.py``). So an older
  schema is only seen by a read in the demo, where nothing may be written.

Rules:

* ``connect()`` is for reading. It gives one consistent snapshot and refuses
  writes. It never creates the file: on a missing file it raises
  ``DeskStoreError`` (check ``ready()`` first).
* ``transaction()`` is for writing. It takes the write lock immediately, so two
  processes or threads never interleave; the second waits up to
  ``BUSY_TIMEOUT_MS``. It commits when the block ends and rolls back when the
  block raises.
* Blocks nest. Inside a ``transaction()`` block, a nested ``transaction()``,
  ``connect()`` or ``ensure_schema()`` joins the open transaction on the same
  connection instead of opening a second one. This is what lets a function
  that writes on its own also be called as one step of a larger atomic write,
  with no ``db=`` argument to pass around. A nested block that raises undoes
  only its own writes; the outer block decides what happens next.
* Never ``await`` inside a block: the write lock would be held while other
  coroutines run.
* A block belongs to the thread and asyncio task that opened it. An asyncio
  task created inside it, or a thread running a copy of its context, does not
  join it: there ``connect()`` and ``transaction()`` open their own connection
  (which sees only committed data, and waits for the write lock), and
  ``after_commit()`` raises.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from core.ledger import portfolio_directory

logger = logging.getLogger(__name__)

FILENAME = "desk.sqlite3"
SIDE_FILES = ("-wal", "-shm", "-journal")
BUSY_TIMEOUT_MS = 10000


class DeskStoreError(RuntimeError):
    """The store cannot be used as asked (for example, the file is newer than the code)."""


def _task():
    try:
        return asyncio.current_task()
    except RuntimeError:  # no event loop running in this thread
        return None


class _Transaction:
    """An open ``transaction()``, owned by the thread and asyncio task that opened it."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.depth = 0
        self.after_commit: List[Callable[[], None]] = []
        self.thread = threading.get_ident()
        self.task = _task()
        self.open = True

    def owned_here(self) -> bool:
        return self.open and self.thread == threading.get_ident() and self.task is _task()


# A ContextVar is copied into every asyncio task and every copied context, so
# a child can see its parent's transaction here. ``_owned()`` hides it from
# everybody but the owner: a child opens its own connection instead.
_active: ContextVar[Optional[_Transaction]] = ContextVar("desk_store_transaction", default=None)
_initialised = {}
_initialise_lock = threading.Lock()
_registered: Dict[str, Tuple[Tuple[str, ...], ...]] = {}


def path() -> Path:
    """Where the store lives right now (follows ``PORTFOLIO_DIR``)."""
    return portfolio_directory() / FILENAME


_pinned_now: ContextVar[Optional[datetime]] = ContextVar("desk_store_pinned_now", default=None)


def now() -> str:
    """Current UTC time as an ISO 8601 string, the format used for ``*_at`` columns."""
    pinned = _pinned_now.get()
    return (pinned or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def pinned_moment() -> Optional[datetime]:
    """The moment the innermost ``pinned_now`` block set here (UTC), or None outside one. The ledger's
    proposal rows follow it too (``core.ledger``), so a seed's ledger entries carry their own day."""
    return _pinned_now.get()


@contextmanager
def pinned_now(moment: datetime) -> Iterator[None]:
    """Make ``now()`` answer ``moment`` (an aware datetime, shown in UTC) inside the block.

    For writing history that did not happen just now, such as the demo's seed
    (``core.demo_seed``): the ``*_at`` columns of everything written inside the
    block carry ``moment``, the ledger's proposal rows included. It only affects the calling thread and asyncio task
    (and what copies its context), and nests; the innermost block wins.
    """
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise ValueError("pinned_now() needs an aware datetime")
    token = _pinned_now.set(moment.astimezone(timezone.utc))
    try:
        yield
    finally:
        _pinned_now.reset(token)


def encode(value) -> str:
    """Canonical JSON for payload columns."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _raw_connection(target: Path) -> sqlite3.Connection:
    # isolation_level=None: this module issues BEGIN/COMMIT itself, the sqlite3
    # module never opens or commits a transaction behind our back.
    db = sqlite3.connect(target, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return db


def _initialise(target: Path) -> None:
    """Create the file (0600, WAL, ``schema_versions``) once per process and file."""
    key = str(target)
    try:
        inode = target.stat().st_ino
    except FileNotFoundError:
        inode = None
    if inode is not None and _initialised.get(key) == inode:
        return
    with _initialise_lock:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Create the file ourselves so it is never world-readable, not even briefly.
        os.close(os.open(target, os.O_CREAT | os.O_RDWR, 0o600))
        os.chmod(target, 0o600)
        deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
        while True:
            db = _raw_connection(target)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE IF NOT EXISTS schema_versions ("
                           "name TEXT PRIMARY KEY, version INTEGER NOT NULL, updated_at TEXT NOT NULL)")
                break
            except sqlite3.OperationalError as exc:
                # Another process is creating the same file right now.
                busy = "locked" in str(exc) or "busy" in str(exc)
                if not busy or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
            finally:
                db.close()
        _initialised[key] = target.stat().st_ino


def _secure(target: Path) -> None:
    """Take group and other access away from the file and its side files.

    SQLite gives a new ``-wal``/``-shm`` file the mode of the database, but it
    never changes one that already exists (left by another program, restored
    from a backup, or created while the database was still loose). The WAL
    holds recent writes, so every connection checks all of them.
    """
    for candidate in (target, *(target.with_name(target.name + suffix) for suffix in SIDE_FILES)):
        try:
            current = stat.S_IMODE(os.stat(candidate).st_mode)
            if current & 0o077:
                os.chmod(candidate, current & 0o700)
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("desk store: %s could not be made private: %s", candidate.name, type(exc).__name__)
            raise DeskStoreError(f"cannot make {candidate.name} private") from None


def _open() -> sqlite3.Connection:
    """A connection for writing; creates and initialises the file when needed."""
    target = path()
    _initialise(target)
    _secure(target)
    return _raw_connection(target)


# An absolute ``time.monotonic()`` moment after which reads of this context stop waiting for locks.
_read_deadline: ContextVar[Optional[float]] = ContextVar("desk_store_read_deadline", default=None)


@contextmanager
def read_deadline(seconds: Optional[float]) -> Iterator[None]:
    """Bound how long reads started inside the block wait for a locked store.

    For a request that has a time budget: a snapshot opened here waits for a
    lock at most until the deadline (instead of ``BUSY_TIMEOUT_MS`` each) and
    raises ``DeskStoreError`` once it has passed. Nested blocks keep the
    earlier deadline. ``read_deadline(None)`` lifts the bound (a background
    thread that must not give up). Writes are not affected.
    """
    deadline = None if seconds is None else time.monotonic() + max(float(seconds), 0.0)
    current = _read_deadline.get()
    if deadline is not None and current is not None:
        deadline = min(deadline, current)
    token = _read_deadline.set(deadline)
    try:
        yield
    finally:
        _read_deadline.reset(token)


def _read_timeout_ms() -> int:
    deadline = _read_deadline.get()
    if deadline is None:
        return BUSY_TIMEOUT_MS
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DeskStoreError("the time allowed for this read has passed")
    return max(1, min(BUSY_TIMEOUT_MS, int(remaining * 1000)))


def _open_existing() -> Optional[sqlite3.Connection]:
    """A connection to the file as it is, or ``None`` when it does not exist. Never creates it."""
    target = path()
    if not target.is_file():
        return None
    timeout_ms = _read_timeout_ms()
    _secure(target)
    try:
        # mode=rw: unlike a plain path, the URI form never creates a missing file.
        db = sqlite3.connect(target.absolute().as_uri() + "?mode=rw", uri=True,
                             timeout=timeout_ms / 1000, isolation_level=None)
    except sqlite3.OperationalError:
        if not target.exists():  # removed in the meantime
            return None
        raise
    db.row_factory = sqlite3.Row
    db.execute(f"PRAGMA busy_timeout={timeout_ms}")
    return db


@contextmanager
def side_database(filename: str, create: bool = True) -> Iterator[Optional[sqlite3.Connection]]:
    """A connection to another SQLite file next to the store, as private as the store.

    For the few records that must be committed on their own while a
    ``transaction()`` holds the store's write lock (``proposals`` records the
    external effects of ``apply`` this way). The connection is in autocommit
    mode: every statement outside an explicit ``BEGIN`` commits at once.
    ``filename`` is ``desk-<name>.sqlite3``. With ``create=False`` a missing
    file yields ``None`` instead of being created. Never use it for data that
    belongs to a desk transaction.
    """
    if not isinstance(filename, str) or not re.fullmatch(r"desk-[a-z0-9_]{1,40}\.sqlite3", filename):
        raise DeskStoreError("a side database is named desk-<name>.sqlite3")
    target = path().with_name(filename)
    if not target.is_file():
        if not create:
            yield None
            return
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.close(os.open(target, os.O_CREAT | os.O_RDWR, 0o600))
    _secure(target)
    db = _raw_connection(target)
    try:
        yield db
    finally:
        db.close()


def _owned() -> Optional[_Transaction]:
    """The open transaction of the calling thread and task, if there is one."""
    active = _active.get()
    return active if active is not None and active.owned_here() else None


@contextmanager
def _snapshot() -> Iterator[Optional[sqlite3.Connection]]:
    """Like ``connect()``, but yields ``None`` instead of raising for a missing file."""
    active = _owned()
    if active is not None:
        yield active.db
        return
    db = _open_existing()
    if db is None:
        yield None
        return
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        try:
            yield db
        finally:
            if db.in_transaction:
                db.execute("ROLLBACK")
    finally:
        db.close()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Read from the store: one consistent snapshot, writes are refused.

    Inside a ``transaction()`` block this yields that transaction's
    connection, so the block reads its own uncommitted writes. It never
    creates the file; on a missing file it raises ``DeskStoreError``, so check
    ``ready()`` first.
    """
    with _snapshot() as db:
        if db is None:
            raise DeskStoreError("the desk store does not exist yet: nothing has been written to it")
        yield db


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Write to the store atomically. See the module docstring for nesting."""
    active = _owned()
    if active is not None:
        active.depth += 1
        savepoint = f"desk_{active.depth}"
        hooks = len(active.after_commit)
        active.db.execute(f"SAVEPOINT {savepoint}")
        try:
            yield active.db
        except BaseException:
            active.db.execute(f"ROLLBACK TO {savepoint}")
            active.db.execute(f"RELEASE {savepoint}")
            del active.after_commit[hooks:]
            raise
        else:
            active.db.execute(f"RELEASE {savepoint}")
        finally:
            active.depth -= 1
        return

    db = _open()
    current = _Transaction(db)
    try:
        db.execute("BEGIN IMMEDIATE")
        token = _active.set(current)
        try:
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            current.open = False
            _active.reset(token)
    finally:
        db.close()
    for hook in current.after_commit:
        try:
            hook()
        except Exception as exc:  # the data is committed; a hook must not undo that
            logger.warning("desk store after-commit hook failed: %s", type(exc).__name__)


def after_commit(hook: Callable[[], None]) -> None:
    """Run ``hook()`` once the open transaction has committed.

    Must be called inside a ``transaction()`` block opened by the calling
    thread and asyncio task; anywhere else (including a child task or thread
    that inherited the context of such a block) it raises ``DeskStoreError``.
    The hook is dropped if the block (or the nested block it was registered in)
    rolls back. A hook that raises is logged and ignored.
    """
    active = _owned()
    if active is None:
        raise DeskStoreError("after_commit() needs an open desk_store.transaction() of the calling thread and task")
    active.after_commit.append(hook)


def backup(destination: Path) -> bool:
    """Copy the store into the new file ``destination`` with SQLite's backup API; False when there is no store.

    The copy is one consistent state: the last committed one, also while another thread or process
    writes (a write that has not committed is not in it). Like every read it never creates or changes
    the store. ``destination`` is a single self-contained file (no ``-wal`` beside it).
    """
    source = _open_existing()
    if source is None:
        return False
    try:
        target = sqlite3.connect(str(destination))
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            target.close()
    finally:
        source.close()
    return True


def schema_version(name: str) -> int:
    """Version of the named schema in the current file; 0 when it was never created.

    Never creates the file: a missing file has every schema at version 0.
    """
    with _snapshot() as db:
        if db is None or db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_versions'"
                                    ).fetchone() is None:
            return 0
        row = db.execute("SELECT version FROM schema_versions WHERE name=?", (name,)).fetchone()
    return row["version"] if row else 0


def _check_name(name) -> None:
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
        raise DeskStoreError("schema name must be lowercase letters, digits and underscores")


def _newer(name, current, target) -> DeskStoreError:
    return DeskStoreError(
        f"desk store schema '{name}' is at version {current}, but this code only knows "
        f"version {target}. Update the application before using this data.")


def ready(name: str, migrations: Sequence[Sequence[str]]) -> bool:
    """Whether a reader can query the named schema now. Never creates or migrates.

    ``False`` while nothing of it is stored (no file, or the schema was never
    created): the reader returns its empty or default result. ``True`` when
    the schema is at the version of ``migrations``. Raises ``DeskStoreError``
    when it is older (it is upgraded at start-up outside demo mode and by the
    next write) or newer than the code.
    """
    _check_name(name)
    target = len(migrations)
    current = schema_version(name)
    if current == 0:
        return False
    if current > target:
        raise _newer(name, current, target)
    if current < target:
        raise DeskStoreError(
            f"desk store schema '{name}' is at version {current} and this code needs version {target}. "
            "It is upgraded when the application starts (not in demo mode) or by the next write.")
    return True


def register_schema(name: str, migrations: Sequence[Sequence[str]]) -> None:
    """Declare a feature's schema at import time, so start-up can upgrade it.

    Registering the same history again changes nothing; a different history
    under a name that is already registered raises ``ValueError``.
    """
    _check_name(name)
    history = tuple(tuple(step) for step in migrations)
    known = _registered.get(name)
    if known is not None and known != history:
        raise ValueError(f"desk store schema '{name}' is already registered with a different history")
    _registered[name] = history


def migrate_registered() -> Dict[str, int]:
    """Upgrade every registered schema of an existing file; returns the versions reached.

    Runs once at application start-up outside demo mode. Creates nothing when
    the file does not exist. A schema that cannot be upgraded (for example, the
    file is newer than the code) is logged and skipped; its readers then raise
    ``DeskStoreError``.
    """
    if not path().is_file():
        return {}
    versions = {}
    for name, history in sorted(_registered.items()):
        try:
            versions[name] = ensure_schema(name, history)
        except (DeskStoreError, sqlite3.Error) as exc:
            logger.warning("desk store schema %s was not upgraded at start-up: %s: %s", name, type(exc).__name__, exc)
    return versions


def ensure_schema(name: str, migrations: Sequence[Sequence[str]]) -> int:
    """Bring the named schema up to date and return its version.

    ``migrations`` is the full, ordered history of the schema: step ``i``
    (a list of SQL statements) takes it from version ``i`` to ``i + 1``, and the
    version is the number of steps applied. Each step runs exactly once per
    file, also when several processes start at the same moment, so plain
    ``CREATE TABLE`` / ``ALTER TABLE`` statements are fine. To change a schema,
    append a step; never edit one that has shipped.

    Cheap when nothing is to do (one read). Call it at the start of every
    public function of a feature module that writes, because the file can
    change between calls when ``PORTFOLIO_DIR`` changes. It creates the file
    when it is missing, so a function that only reads uses ``ready()``.
    """
    _check_name(name)
    target = len(migrations)
    current = schema_version(name)
    if current == target:
        return current
    with transaction() as db:
        row = db.execute("SELECT version FROM schema_versions WHERE name=?", (name,)).fetchone()
        current = row["version"] if row else 0
        if current > target:
            raise _newer(name, current, target)
        for step in migrations[current:]:
            for statement in step:
                db.execute(statement)
        db.execute("INSERT INTO schema_versions(name, version, updated_at) VALUES (?,?,?) "
                   "ON CONFLICT(name) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at",
                   (name, target, now()))
    return target
