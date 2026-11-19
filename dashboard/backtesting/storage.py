"""Where the two backtest databases live.

    backtesting.db         bar cache (DataStore)
    backtest_history.db    saved runs (BacktestHistoryStore)

Both are resolved on every call, so tests and the demo can point them elsewhere
without restarting:

* ``TRACKER_DEMO_MODE=1``: ``<PORTFOLIO_DIR>/backtest_demo``, always. The demo
  must never show or change the user's own runs, including when
  ``TRACKER_BACKTEST_DIR`` is inherited from the normal launch environment.
* ``TRACKER_BACKTEST_DIR``: that directory.
* otherwise ``dashboard/data`` (unchanged from earlier versions).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data"
BARS_FILE = "backtesting.db"
HISTORY_FILE = "backtest_history.db"
DEMO_SUBDIR = "backtest_demo"


def demo_mode() -> bool:
    return os.environ.get("TRACKER_DEMO_MODE") == "1"


def demo_dir() -> Path:
    from core.ledger import portfolio_directory
    return Path(portfolio_directory()) / DEMO_SUBDIR


def backtest_dir() -> Path:
    if demo_mode():
        return demo_dir()
    configured = os.environ.get("TRACKER_BACKTEST_DIR", "").strip()
    return Path(configured) if configured else DEFAULT_DIR


def bars_db_path() -> str:
    return str(backtest_dir() / BARS_FILE)


def history_db_path() -> str:
    return str(backtest_dir() / HISTORY_FILE)


# Inside ``for_reading()`` a store whose database does not exist yet is opened absent: its reads answer
# empty and nothing is created (a GET before the first download or the first saved run).
_reading: ContextVar[bool] = ContextVar("backtest_store_reading", default=False)


@contextmanager
def for_reading() -> Iterator[None]:
    """Stores constructed inside the block only read: a missing database is not created."""
    token = _reading.set(True)
    try:
        yield
    finally:
        _reading.reset(token)


def reading() -> bool:
    """True inside ``for_reading()``: a store constructed now only reads (it neither creates nor migrates)."""
    return _reading.get()


def absent_for_reading(db_path) -> bool:
    """True inside ``for_reading()`` when ``db_path`` does not exist: the store must not create it."""
    return _reading.get() and not Path(db_path).is_file()
