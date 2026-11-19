"""Shared error mapping for the routes backed by ``desk.sqlite3``.

Every desk route runs its work through ``desk_call`` so that a busy store and
an unusable store answer the same way everywhere:

* another writer held the write lock longer than ``desk_store.BUSY_TIMEOUT_MS``
  -> 503 "系统正忙", the client may retry;
* ``desk_store.DeskStoreError`` (for example, the file is newer than the code,
  or a read-only demo meets a schema that needs an upgrade) -> 503.

Any other error is left alone: it is a bug and must surface as one.
"""
import sqlite3
from typing import Callable, TypeVar

from fastapi import HTTPException

from core import desk_store

T = TypeVar('T')

BUSY = '系统正忙，暂时无法处理。请稍后重试。'


def is_busy(exc: BaseException) -> bool:
    """Whether ``exc`` is SQLite giving up on a lock held by somebody else."""
    text = str(exc)
    return isinstance(exc, sqlite3.OperationalError) and ('locked' in text or 'busy' in text)


def desk_call(operation: Callable[[], T]) -> T:
    """Run ``operation()`` and turn store trouble into a 503."""
    try:
        return operation()
    except desk_store.DeskStoreError as exc:
        raise HTTPException(503, f'数据存储暂时不可用：{exc}') from None
    except sqlite3.OperationalError as exc:
        if not is_busy(exc):
            raise
        raise HTTPException(503, BUSY) from None
