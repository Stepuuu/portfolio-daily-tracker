"""``instrument_aliases`` in ``desk.sqlite3``: a code or name → ledger ticker.

Learned from choices the person makes during an import (for example that the US
symbol ``EXAMPLE`` trades on NYSE), so the same choice is not asked twice. It
never stores IPO subscription or rights codes (7xxxxx): those are reused for
different securities over time.

Keys are namespaced: ``US:<SYMBOL>``, ``NAME:<名称>``. The schema is named
``instrument_aliases``; other features (instrument search) should read and
extend it through this module, appending migration steps, rather than create a
second table.

``lookup`` and ``remember`` are for writers (they create the table when it is
missing); ``entries`` is the read-only form for GET requests and the demo.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from core import desk_store

SCHEMA = 'instrument_aliases'
MIGRATIONS = [[
    """CREATE TABLE instrument_aliases (
        alias TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        name TEXT,
        source TEXT NOT NULL,
        updated_at TEXT NOT NULL)""",
]]
desk_store.register_schema(SCHEMA, MIGRATIONS)


def entries() -> List[dict]:
    """Every alias as ``{alias, ticker, name, source}``. Never creates the store; ``[]`` when nothing is stored."""
    if not desk_store.ready(SCHEMA, MIGRATIONS):
        return []
    with desk_store.connect() as db:
        return [dict(row) for row in db.execute(
            'SELECT alias, ticker, name, source FROM instrument_aliases ORDER BY alias')]


def lookup(keys: Optional[Iterable[str]] = None) -> Dict[str, str]:
    """``{alias: ticker}`` for the given keys, or for every alias when ``keys`` is None.

    A read: nothing is created while no alias was ever stored."""
    if not desk_store.ready(SCHEMA, MIGRATIONS):
        return {}
    with desk_store.connect() as db:
        if keys is None:
            rows = db.execute('SELECT alias, ticker FROM instrument_aliases').fetchall()
        else:
            wanted = sorted(set(keys))
            rows = []
            for start in range(0, len(wanted), 500):
                chunk = wanted[start:start + 500]
                rows += db.execute(f"SELECT alias, ticker FROM instrument_aliases WHERE alias IN ({','.join('?' * len(chunk))})",
                                   chunk).fetchall()
    return {row['alias']: row['ticker'] for row in rows}


def remember(alias: str, ticker: str, name: Optional[str] = None, source: str = 'import') -> None:
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)
    with desk_store.transaction() as db:
        db.execute('INSERT INTO instrument_aliases(alias, ticker, name, source, updated_at) VALUES (?,?,?,?,?) '
                   'ON CONFLICT(alias) DO UPDATE SET ticker=excluded.ticker, name=excluded.name, '
                   'source=excluded.source, updated_at=excluded.updated_at',
                   (alias, ticker, name, source, desk_store.now()))
