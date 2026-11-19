"""Shared core of the dashboard."""
from core import sqlite_guard as _sqlite_guard

_sqlite_guard.install()    # before the first connection; does nothing except on SQLite 3.51.0 and 3.51.1
