"""Notifications: one durable row per event, then best-effort delivery.

Publishing::

    from core import notify

    seq = notify.publish("alert", "warning", "止损提醒", "SHA:600000 跌破止损价",
                         data={"ticker": "SHA:600000", "rule_id": 7},
                         dedupe_key="alert:7:2026-08-03:1")
    # seq is None when a notification with this dedupe_key already exists.

The row in ``desk.sqlite3`` is the source of truth (it feeds the bell in the
web app). ``seq`` only ever grows, and rows commit in ``seq`` order, so a
client can ask for "everything after the last one I saw" (``after_seq``,
oldest first) and for "the ones before the oldest I show" (``before_seq``,
newest first) and so reach every row; see ``list_notifications``.

``publish`` may be called inside a ``desk_store.transaction()`` block. The row
then commits or rolls back together with the caller's other writes, which is
how a background task makes "state changed" and "user was told" one atomic
step that is safe to repeat after a restart.

Delivery to other channels (a chat push, for example) is optional and never
part of the write::

    def push(notification: dict, timeout: float) -> None:   # same fields as an item of list()
        requests.post(..., timeout=timeout)                  # stay within the budget

    notify.register_deliverer(push)            # or register_deliverer(push, timeout=10)

Each deliverer (a channel) has its own worker thread and its own bounded
queue (``DELIVERY_QUEUE_SIZE``), so a channel that hangs or is slow never
delays another one. A channel receives the committed notifications in
publish order, only after the row has been committed. A deliverer that raises
or hangs cannot fail or delay ``publish``.

Time budget: a thread cannot be interrupted, so the budget (``timeout``,
default ``DELIVERY_TIMEOUT`` seconds) is enforced where possible: a deliverer
that accepts a ``timeout`` keyword is called with it and must give up in
time. A delivery that takes longer than its budget is counted as ``slow``
and logged. While a channel hangs, its queue fills; further notifications
for that channel are dropped, counted and logged.

Logs carry the channel name, ``seq``, ``kind`` and exception type only: never
titles, bodies or data, which can hold personal amounts.
``delivery_stats()`` gives per-channel counts (delivered, failed, slow,
dropped). Delivery is not retried and is lost if the process exits first; the
stored row is not.

Retention: every write (``publish``, ``mark_read``, ``mark_all_read``) also
deletes up to ``PRUNE_BATCH`` notifications that were read more than
``RETENTION_DAYS`` days ago. Unread notifications are kept however old they
are, and so is the newest row (``last_seq`` never goes down). A
``dedupe_key`` therefore suppresses repeats for as long as its row is kept.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
import inspect
import json
import logging
import queue
import re
import threading
import time
from typing import Callable, Dict, List, Optional

from core import desk_store

logger = logging.getLogger(__name__)

LEVELS = ("info", "warning", "critical")
MAX_TITLE = 200
MAX_BODY = 4000
MAX_DATA = 20000
MAX_LIMIT = 200
DELIVERY_QUEUE_SIZE = 1000   # per channel
DELIVERY_TIMEOUT = 30.0      # seconds per delivery, default budget
RETENTION_DAYS = 90
PRUNE_BATCH = 200

MIGRATIONS = [[
    """CREATE TABLE notifications (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        level TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        data TEXT,
        dedupe_key TEXT UNIQUE,
        created_at TEXT NOT NULL,
        read_at TEXT)""",
    "CREATE INDEX notifications_unread ON notifications(seq) WHERE read_at IS NULL",
], [
    "CREATE INDEX notifications_read ON notifications(read_at, seq) WHERE read_at IS NOT NULL",
]]
SCHEMA = "notifications"
desk_store.register_schema(SCHEMA, MIGRATIONS)

Deliverer = Callable[..., None]


# -- writing ------------------------------------------------------------------

def publish(kind: str, level: str, title: str, body: str, data: Optional[dict] = None,
            dedupe_key: Optional[str] = None) -> Optional[int]:
    """Store a notification and return its ``seq``, or ``None`` for a duplicate.

    kind        short machine name of the event family, e.g. ``alert`` or
                ``routine.postmarket`` (lowercase letters, digits and ``_.:-``).
    level       one of ``LEVELS``.
    title/body  text for the user. Overlong text is cut, never rejected.
    data        optional JSON object for the UI (ids, tickers, links).
    dedupe_key  optional identity of the event. A second publish with the same
                key stores nothing and returns ``None``.

    Raises ``ValueError`` for arguments that are wrong in the code that calls it
    (unknown level, non-JSON data). Storage errors propagate: the caller must
    know when nothing was recorded.
    """
    if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_.:\-]{0,63}", kind):
        raise ValueError("notification kind must be a short lowercase name")
    if level not in LEVELS:
        raise ValueError("notification level must be one of: " + ", ".join(LEVELS))
    if not isinstance(title, str) or not title.strip():
        raise ValueError("notification title is required")
    if not isinstance(body, str):
        raise ValueError("notification body must be text")
    if data is not None and not isinstance(data, dict):
        raise ValueError("notification data must be a JSON object")
    try:
        encoded = None if data is None else desk_store.encode(data)
    except (TypeError, ValueError):
        raise ValueError("notification data must be JSON serialisable") from None
    if encoded is not None and len(encoded) > MAX_DATA:
        raise ValueError("notification data is too large")
    if dedupe_key is not None and (not isinstance(dedupe_key, str) or not 0 < len(dedupe_key) <= 200):
        raise ValueError("dedupe_key must be a non-empty string of at most 200 characters")

    title, body = _cut(title.strip(), MAX_TITLE), _cut(body, MAX_BODY)
    created_at = desk_store.now()
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)
    with desk_store.transaction() as db:
        _prune(db)
        cursor = db.execute(
            "INSERT INTO notifications(kind, level, title, body, data, dedupe_key, created_at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(dedupe_key) DO NOTHING",
            (kind, level, title, body, encoded, dedupe_key, created_at))
        if cursor.rowcount != 1:
            return None
        seq = cursor.lastrowid
        notification = {"seq": seq, "kind": kind, "level": level, "title": title, "body": body,
                        "data": None if encoded is None else json.loads(encoded),
                        "created_at": created_at, "read_at": None}
        desk_store.after_commit(lambda: _enqueue(notification))
    return seq


def mark_read(seq: int) -> bool:
    """Mark one notification as read. ``False`` when it does not exist."""
    if desk_store.schema_version(SCHEMA) == 0:
        return False  # nothing was ever stored, and nothing needs to be created for that
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)
    with desk_store.transaction() as db:
        _prune(db)
        db.execute("UPDATE notifications SET read_at=? WHERE seq=? AND read_at IS NULL", (desk_store.now(), seq))
        return db.execute("SELECT 1 FROM notifications WHERE seq=?", (seq,)).fetchone() is not None


def mark_all_read(up_to_seq: Optional[int] = None) -> int:
    """Mark unread notifications as read and return how many changed.

    Pass the newest ``seq`` the user has actually been shown as ``up_to_seq``,
    so that one arriving in the meantime stays unread.
    """
    if desk_store.schema_version(SCHEMA) == 0:
        return 0
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)
    with desk_store.transaction() as db:
        _prune(db)
        if up_to_seq is None:
            cursor = db.execute("UPDATE notifications SET read_at=? WHERE read_at IS NULL", (desk_store.now(),))
        else:
            cursor = db.execute("UPDATE notifications SET read_at=? WHERE read_at IS NULL AND seq<=?",
                                (desk_store.now(), up_to_seq))
        return cursor.rowcount


def _prune(db) -> None:
    """Delete a bounded batch of notifications read more than ``RETENTION_DAYS`` ago."""
    cutoff = (datetime.fromisoformat(desk_store.now()) - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")
    db.execute("DELETE FROM notifications WHERE seq IN (SELECT seq FROM notifications "
               "WHERE read_at IS NOT NULL AND read_at < ? AND seq < (SELECT MAX(seq) FROM notifications) "
               "ORDER BY read_at, seq LIMIT ?)", (cutoff, PRUNE_BATCH))


# -- reading ------------------------------------------------------------------

def list_notifications(after_seq: Optional[int] = None, limit: int = 50,
                       before_seq: Optional[int] = None) -> List[dict]:
    """One page of notifications, at most ``limit`` (1 to ``MAX_LIMIT``).

    * no cursor: the newest ones, newest first (the first page);
    * ``after_seq=N``: the ones with ``seq > N``, **oldest first**. A poller
      passes the highest ``seq`` it has and continues from the last item of
      the page; a page cut off at ``limit`` leaves no gap;
    * ``before_seq=N``: the ones with ``seq < N``, newest first, to scroll
      from the oldest row shown to older ones.

    Passing both cursors raises ``ValueError``. Each item is
    ``{seq, kind, level, title, body, data, created_at, read_at}``; ``read_at``
    is ``None`` while unread.
    """
    _check_cursors(after_seq, before_seq)
    if not desk_store.ready(SCHEMA, MIGRATIONS):
        return []
    with desk_store.connect() as db:
        return _items(db, after_seq, before_seq, limit)


def unread_count() -> int:
    if not desk_store.ready(SCHEMA, MIGRATIONS):
        return 0
    with desk_store.connect() as db:
        return _unread(db)


def last_seq() -> int:
    """The highest ``seq`` ever stored; 0 when there is none."""
    if not desk_store.ready(SCHEMA, MIGRATIONS):
        return 0
    with desk_store.connect() as db:
        return _last(db)


def inbox(after_seq: Optional[int] = None, limit: int = 50, before_seq: Optional[int] = None) -> Dict[str, object]:
    """``{items, unread, last_seq}`` read from one consistent snapshot.

    ``items`` is ``list_notifications(after_seq, limit, before_seq)``;
    ``unread`` and ``last_seq`` describe the whole inbox.
    """
    _check_cursors(after_seq, before_seq)
    if not desk_store.ready(SCHEMA, MIGRATIONS):
        return {"items": [], "unread": 0, "last_seq": 0}
    with desk_store.connect() as db:
        return {"items": _items(db, after_seq, before_seq, limit), "unread": _unread(db), "last_seq": _last(db)}


def _check_cursors(after_seq, before_seq):
    if after_seq is not None and before_seq is not None:
        raise ValueError("pass after_seq or before_seq, not both")


_COLUMNS = "SELECT seq, kind, level, title, body, data, created_at, read_at FROM notifications "


def _items(db, after_seq, before_seq, limit):
    limit = max(1, min(int(limit), MAX_LIMIT))
    if after_seq is not None:
        rows = db.execute(_COLUMNS + "WHERE seq>? ORDER BY seq LIMIT ?", (int(after_seq), limit))
    elif before_seq is not None:
        rows = db.execute(_COLUMNS + "WHERE seq<? ORDER BY seq DESC LIMIT ?", (int(before_seq), limit))
    else:
        rows = db.execute(_COLUMNS + "ORDER BY seq DESC LIMIT ?", (limit,))
    return [{**dict(row), "data": json.loads(row["data"]) if row["data"] else None} for row in rows]


def _unread(db):
    return db.execute("SELECT COUNT(*) FROM notifications WHERE read_at IS NULL").fetchone()[0]


def _last(db):
    return db.execute("SELECT COALESCE(MAX(seq), 0) FROM notifications").fetchone()[0]


def _cut(text, limit):
    return text if len(text) <= limit else text[:limit - 1] + "…"


# -- delivery -----------------------------------------------------------------

_STOP = object()


class _Channel:
    """One deliverer with its own bounded queue and worker thread."""

    def __init__(self, deliverer: Deliverer, name: str, timeout: float):
        self.deliverer = deliverer
        self.name = name
        self.timeout = timeout
        self.takes_timeout = _accepts_timeout(deliverer)
        self.queue: "queue.Queue[object]" = queue.Queue(maxsize=DELIVERY_QUEUE_SIZE)
        self.stats = {"delivered": 0, "failed": 0, "slow": 0, "dropped": 0}
        self.closed = False
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None

    def put(self, notification: dict) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait(notification)
        except queue.Full:
            dropped = self._count("dropped")
            logger.warning("notification deliverer %s dropped seq=%s kind=%s: its queue is full (%s dropped)",
                           self.name, notification["seq"], notification["kind"], dropped)
            return
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, name=f"notify-{self.name}", daemon=True)
                self.thread.start()

    def close(self) -> None:
        self.closed = True
        try:
            self.queue.put_nowait(_STOP)
        except queue.Full:
            pass  # the worker sees ``closed`` at its next item and discards the rest

    def _count(self, key: str) -> int:
        with self.lock:
            self.stats[key] += 1
            return self.stats[key]

    def _run(self) -> None:
        while True:
            item = self.queue.get()
            try:
                if item is _STOP:
                    return
                if not self.closed:
                    self._deliver(item)
            finally:
                self.queue.task_done()

    def _deliver(self, notification: dict) -> None:
        started = time.monotonic()
        try:
            if self.takes_timeout:
                self.deliverer(copy.deepcopy(notification), timeout=self.timeout)
            else:
                self.deliverer(copy.deepcopy(notification))
        except Exception as exc:
            self._count("failed")
            logger.warning("notification deliverer %s failed for seq=%s kind=%s: %s",
                           self.name, notification["seq"], notification["kind"], type(exc).__name__)
        else:
            self._count("delivered")
        elapsed = time.monotonic() - started
        if elapsed > self.timeout:
            self._count("slow")
            logger.warning("notification deliverer %s took %.1fs for seq=%s kind=%s, over its %.1fs budget",
                           self.name, elapsed, notification["seq"], notification["kind"], self.timeout)


_deliverers: List[_Channel] = []
_registry_lock = threading.Lock()


def _accepts_timeout(function) -> bool:
    try:
        parameters = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(parameter.kind == parameter.VAR_KEYWORD
               or (parameter.name == "timeout" and parameter.kind in (parameter.KEYWORD_ONLY,
                                                                      parameter.POSITIONAL_OR_KEYWORD))
               for parameter in parameters)


def register_deliverer(deliverer: Deliverer, timeout: Optional[float] = None) -> Deliverer:
    """Add a delivery channel; see the module docstring for the contract.

    ``timeout`` is the channel's time budget per delivery in seconds (default
    ``DELIVERY_TIMEOUT``). Registering the same function twice has no further
    effect. Returns the function, so it can be used as a decorator.
    """
    if not callable(deliverer):
        raise TypeError("a deliverer must be callable")
    budget = DELIVERY_TIMEOUT if timeout is None else timeout
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not 0 < budget <= 3600:
        raise ValueError("a deliverer timeout must be a number of seconds between 0 and 3600")
    with _registry_lock:
        if any(channel.deliverer == deliverer for channel in _deliverers):
            return deliverer
        base = getattr(deliverer, "__name__", type(deliverer).__name__)
        taken = {channel.name for channel in _deliverers}
        name, number = base, 1
        while name in taken:
            number += 1
            name = f"{base}-{number}"
        _deliverers.append(_Channel(deliverer, name, float(budget)))
    return deliverer


def unregister_deliverer(deliverer: Deliverer) -> None:
    """Remove a channel; notifications still queued for it are not delivered."""
    with _registry_lock:
        for channel in [channel for channel in _deliverers if channel.deliverer == deliverer]:
            _deliverers.remove(channel)
            channel.close()


def delivery_stats() -> Dict[str, Dict[str, int]]:
    """Per registered channel: how many notifications were delivered, failed, slow or dropped."""
    out = {}
    for channel in tuple(_deliverers):
        with channel.lock:
            out[channel.name] = dict(channel.stats)
    return out


def flush(timeout: float = 5.0) -> bool:
    """Wait until every channel has handed its queued notifications to its deliverer.

    For tests and orderly shutdown. ``False`` when ``timeout`` seconds were not
    enough (for example, because a deliverer hangs).
    """
    deadline = time.monotonic() + timeout
    for channel in tuple(_deliverers):
        with channel.queue.all_tasks_done:
            while channel.queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                channel.queue.all_tasks_done.wait(remaining)
    return True


def _enqueue(notification):
    for channel in tuple(_deliverers):
        channel.put(notification)


# The public name asked for by callers is ``notify.list``. It is bound last, and
# nothing in this module may use the built-in of the same name.
list = list_notifications
