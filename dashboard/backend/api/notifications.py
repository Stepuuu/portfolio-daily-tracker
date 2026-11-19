"""Notification inbox for the bell in the web app. Mounted at ``/api/notifications``.

A busy or unusable store is a 503 (see ``desk_errors``).
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Path, Query

from backend.api.desk_errors import desk_call
from core import notify

router = APIRouter()

MAX_SEQ = 2 ** 53  # exact in JSON numbers and well inside SQLite's integer range


@router.get('')
def inbox(after: Optional[int] = Query(None, ge=0, le=MAX_SEQ), before: Optional[int] = Query(None, ge=1, le=MAX_SEQ),
          limit: int = Query(50, ge=1, le=notify.MAX_LIMIT)):
    """One page of the inbox; ``unread`` and ``last_seq`` describe the whole inbox.

    * No cursor: the newest ``limit`` notifications, newest first.
    * ``after=<seq>``: the notifications with a higher ``seq``, **oldest first**.
    * ``before=<seq>``: the notifications with a lower ``seq``, newest first.

    How a client calls it:

    * The bell (a poller): load the first page without a cursor and set
      ``cursor = last_seq``. Then, every poll, ``GET ?after=<cursor>&limit=200``,
      append the items, and set ``cursor`` to the ``seq`` of the last item.
      If the page was full (``limit`` items), ask again right away: more are
      waiting. A page never skips a row.
    * An infinite-scroll list: show the first page, then
      ``GET ?before=<smallest seq shown>&limit=<n>`` for each older page,
      until a page has fewer than ``limit`` items.

    Seqs have gaps (duplicates are not stored, old read notifications are
    pruned); use them only as cursors. ``after`` and ``before`` together are
    a 422.
    """
    if after is not None and before is not None:
        raise HTTPException(422, '只能使用 after 或 before 其中之一。')
    return desk_call(lambda: notify.inbox(after, limit, before_seq=before))


@router.post('/read-all')
def read_all(up_to: Optional[int] = Query(None, ge=0, le=MAX_SEQ)):
    """Mark everything as read, or only ``seq <= up_to`` (what the user has been shown)."""
    return desk_call(lambda: {'marked': notify.mark_all_read(up_to), 'unread': notify.unread_count()})


@router.post('/{seq}/read')
def read(seq: int = Path(..., ge=1, le=MAX_SEQ)):
    if not desk_call(lambda: notify.mark_read(seq)):
        raise HTTPException(404, '通知不存在。请刷新通知列表。')
    return {'seq': seq, 'read': True, 'unread': desk_call(notify.unread_count)}
