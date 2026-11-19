"""The log of fired alerts: one row per fire, newest first when read.

Rows are written by the monitor in the transaction that changes the rule's
state and publishes its notification. ``delivery`` says how the user was told:

``notified``    a notification of kind ``alert`` (``notification_seq``)
``suppressed``  over the global cap: recorded here and summarised in the next
                ``alert.digest`` notification instead of being pushed alone
``duplicate``   a notification with the same key already existed (another
                process fired it first); kept for the record

Notifications have no backwards paging and no kind filter, so the alert page
reads this table rather than the notification inbox.
"""
from __future__ import annotations

from typing import List, Optional

from core import desk_store

from . import rules

MAX_LIMIT = 200
COLUMNS = ('id', 'rule_id', 'ticker', 'name', 'account', 'kind', 'threshold', 'trading_date', 'n', 'value',
           'price', 'prev_close', 'quote_source', 'quote_time', 'quote_status', 'fired_at', 'delivery',
           'notification_seq', 'title', 'body')


def record(db, event: dict) -> Optional[int]:
    """Insert ``event`` (inside the caller's transaction); ``None`` when it was already there."""
    names = [name for name in COLUMNS if name != 'id'] + ['fired_ts']
    cursor = db.execute(
        f"INSERT INTO alert_events({', '.join(names)}) VALUES ({', '.join('?' for _ in names)}) "
        "ON CONFLICT(rule_id, trading_date, n) DO NOTHING", [event.get(name) for name in names])
    return cursor.lastrowid if cursor.rowcount == 1 else None


def notified_since(db, since_ts: float, until_ts: Optional[float] = None) -> int:
    """Alerts pushed on their own after ``since_ts`` (and not after ``until_ts``: one dated later than
    the clock was recorded before the clock went back and must not fill the cap for that long)."""
    if until_ts is None:
        return db.execute("SELECT COUNT(*) FROM alert_events WHERE delivery='notified' AND fired_ts > ?",
                          (since_ts,)).fetchone()[0]
    return db.execute("SELECT COUNT(*) FROM alert_events WHERE delivery='notified' AND fired_ts > ? "
                      "AND fired_ts <= ?", (since_ts, until_ts)).fetchone()[0]


def suppressed_after(db, event_id: int) -> List[dict]:
    rows = db.execute(f"SELECT {', '.join(COLUMNS)} FROM alert_events WHERE delivery='suppressed' AND id > ? "
                      "ORDER BY id", (event_id,))
    return [dict(row) for row in rows]


def list_events(limit: int = 50, before: Optional[int] = None, rule_id: Optional[int] = None) -> dict:
    """``{items, next_before}``: fires newest first; pass ``next_before`` back to page."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    where, parameters = [], []
    if before is not None:
        where.append('id < ?')
        parameters.append(int(before))
    if rule_id is not None:
        where.append('rule_id = ?')
        parameters.append(int(rule_id))
    if not rules.available():
        return {'items': [], 'next_before': None}
    with desk_store.connect() as db:
        rows = [dict(row) for row in db.execute(
            f"SELECT {', '.join(COLUMNS)} FROM alert_events {'WHERE ' + ' AND '.join(where) if where else ''} "
            "ORDER BY id DESC LIMIT ?", (*parameters, limit + 1))]
    more = len(rows) > limit
    items = rows[:limit]
    return {'items': items, 'next_before': items[-1]['id'] if more else None}
