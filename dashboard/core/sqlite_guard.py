"""Keep SQLite 3.51.0 and 3.51.1 from deadlocking when threads open and close one file.

Those two releases can deadlock when one thread opens a database file while
another thread of the same process closes a connection to the same file
(3.51.2: "Fix an obscure deadlock in the new broken-posix-lock detection
logic"). Neither call returns, no error is raised, and every later reader of
that file in the process waits behind them. The server reads its stores from
worker threads all the time, so on those releases it would simply stop
answering. conda's Python 3.11 shipped SQLite 3.51.1 in early 2026.

On an affected release ``install()`` routes ``sqlite3.connect`` through one
process-wide gate and gives the connections a ``close`` that takes the same
gate. Only the open and the close are serialised; statements still run side by
side. On every other release ``install()`` changes nothing.

``core/__init__.py`` installs the guard, so any process that imports ``core``
has it before its first connection.
"""
import sqlite3
import threading
from typing import Optional, Sequence

AFFECTED_RELEASES = frozenset({(3, 51, 0), (3, 51, 1)})
FIXED_IN = "3.51.2"
_FACTORY_POSITION = 5        # sqlite3.connect(database, timeout, detect_types, isolation_level, check_same_thread, factory)

# Re-entrant: the collector may finalise a forgotten connection while this thread is inside connect().
_gate = threading.RLock()
_original_connect = None


def affected(version: Optional[Sequence[int]] = None) -> bool:
    """Whether this SQLite release (default: the one in use) has the deadlock."""
    return tuple((sqlite3.sqlite_version_info if version is None else version)[:3]) in AFFECTED_RELEASES


class GuardedConnection(sqlite3.Connection):
    """A connection whose close waits for the gate."""

    def close(self) -> None:
        with _gate:
            super().close()

    def __del__(self) -> None:
        # ``with sqlite3.connect(...)`` commits and leaves the close to the collector, which would
        # close outside the gate. Close here first; a connection that cannot be closed from this
        # thread, or never opened, is left to the collector as before.
        try:
            self.close()
        except Exception:
            pass


def _connect(*args, **kwargs):
    if "factory" not in kwargs and len(args) <= _FACTORY_POSITION:
        kwargs["factory"] = GuardedConnection
    with _gate:
        return _original_connect(*args, **kwargs)


def installed() -> bool:
    return _original_connect is not None


def install(force: bool = False) -> bool:
    """Serialise opens and closes if this SQLite release needs it. Returns whether the guard is on.

    ``force`` turns it on for any release (the tests use it).
    """
    global _original_connect
    if installed():
        return True
    if not (force or affected()):
        return False
    _original_connect = sqlite3.connect
    sqlite3.connect = _connect
    return True


def uninstall() -> None:
    """Put ``sqlite3.connect`` back (for the tests)."""
    global _original_connect
    if installed():
        sqlite3.connect = _original_connect
        _original_connect = None


def notice() -> Optional[str]:
    """One line for the start-up log while the guard is on, else ``None``."""
    if not installed():
        return None
    return (f"SQLite {sqlite3.sqlite_version} can deadlock when threads open and close one database file; "
            f"opening and closing are serialised in this process. SQLite {FIXED_IN} or later does not need this.")
